"""Durable, serialized job-application campaign. No network except the omp worker.

Commands: status | activity (JSON stdin) | gate load/detail/apply | reserve JOB_ID | requeue JOB_ID (JSON stdin) | reconcile JOB_ID (JSON stdin) | result (JSON stdin) | loop
A reservation MUST precede Submit. Requeue requires reason, evidence, and submission_verified_absent: true
from an operator's inspected employer/draft evidence or documented pre-submission failure, never status alone.
Requeue is not permission to submit: reserve, the apply gate, and an employer receipt remain required.
Reconcile requires reason, evidence, and employer_confirmation_verified: true for an inspected employer
receipt after a manual/failed reserved attempt; it records historical reality without submitting again.
"""
import argparse
import contextlib
import fcntl
import json
import os
from pathlib import Path
import random
import re
import shutil
import signal
import subprocess
import tempfile
import sys
import urllib.error
import urllib.request
import time
import uuid

import db
import settings

ROOT = db.ROOT
CONFIG = ROOT / 'data/campaign.json'
PACE = ROOT / 'data/ratelimit.json'
DAY = 86400
HOUR = 3600
HEARTBEAT_SECONDS = 5
HEARTBEAT_STALE_SECONDS = 20
WORKER_DEADLINE_SECONDS = 20 * 60
TERMINATE_GRACE_SECONDS = 5
CAFFEINATE = '/usr/bin/caffeinate'
NO_PROGRESS_SECONDS = 300
ACTIVITY_STALE_SECONDS = 300
ACTIVITY_STAGES = {'searching', 'screening', 'opening_application', 'signing_in', 'filling',
                   'reviewing', 'submitting', 'verifying', 'idle'}
ACTIVITY_ACTIONS = {
    'searching_jobs': 'Searching for jobs', 'checking_fit': 'Checking job fit',
    'opening_job': 'Opening job details', 'opening_form': 'Opening application form',
    'signing_in': 'Signing in', 'uploading_resume': 'Uploading resume',
    'filling_contact': 'Filling contact details', 'filling_work_history': 'Filling work history',
    'filling_education': 'Filling education', 'answering_screening': 'Answering screening questions',
    'reviewing_answers': 'Reviewing answers', 'waiting_email': 'Waiting for verification email',
    'waiting_pacing': 'Waiting between browser actions',
    'waiting_platform': 'Waiting for the platform', 'submitting_application': 'Submitting application',
    'checking_confirmation': 'Checking employer confirmation', 'saving_outcome': 'Saving outcome',
}
ACTIVITY_STAGE_LABELS = {
    'searching': 'Searching', 'screening': 'Screening', 'opening_application': 'Opening application',
    'signing_in': 'Signing in', 'filling': 'Filling application', 'reviewing': 'Reviewing',
    'submitting': 'Submitting', 'verifying': 'Verifying', 'idle': 'Idle',
}
EXIT_REASONS = {'worker_exit', 'worker_failed', 'worker_timeout', 'stop_file', 'sigterm',
                'sigint', 'supervisor_error', 'recovered_worker', 'target_reached'}


def config():
    return settings.load_campaign(ROOT)


@contextlib.contextmanager
def locked(path, blocking=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            raise RuntimeError('campaign supervisor already running') from None
        yield


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as output:
            json.dump(value, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def event(kind, **fields):
    """Metadata only: never worker output, prompts, reasons, evidence, or URLs."""
    allowed = {'job_id', 'result', 'worker_pid', 'supervisor_pid', 'exit_code',
               'exit_reason', 'restart_count', 'confirmed', 'action', 'retry_at', 'stage', 'run_id'}
    value = {'at': time.time(), 'event': kind}
    value.update({key: value for key, value in fields.items() if key in allowed})
    path = ROOT / 'logs/campaign/events.jsonl'
    with locked(ROOT / 'data/campaign-events.lock'):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a') as output:
            output.write(json.dumps(value) + '\n')
            output.flush()


def pid_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def read_runtime():
    try:
        value = json.loads((ROOT / 'data/campaign-runtime.json').read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def read_activity():
    try:
        value = json.loads((ROOT / 'data/campaign-activity.json').read_text())
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def clear_activity(job_id=None):
    with locked(ROOT / 'data/campaign-activity.lock'):
        saved = read_activity()
        if saved and (job_id is None or saved.get('job_id') == job_id):
            atomic_json(ROOT / 'data/campaign-activity.json', None)


def activity_status(con, runtime):
    """Read-only context view: a stale status snapshot must never erase a newer run."""
    with locked(ROOT / 'data/campaign-activity.lock'):
        saved, current = read_activity(), read_runtime()
        if not saved:
            return None
        run_id = saved.get('run_id')
        if (not run_id or run_id != current.get('worker_run_id')
                or run_id != runtime.get('worker_run_id') or current.get('state') != 'working'
                or not pid_alive(current.get('worker_pid'))):
            return None
        stage, job_id = saved.get('stage'), saved.get('job_id')
        reported, started = saved.get('reported_at'), saved.get('stage_started_at')
        if (stage not in ACTIVITY_STAGES or not isinstance(reported, (int, float))
                or not isinstance(started, (int, float))):
            return None
        job = con.execute('SELECT company,title FROM jobs WHERE id=?', (job_id,)).fetchone() if job_id else None
        if ((job_id and not job) or (not job_id and stage not in ('searching', 'idle'))
                or (job_id and con.execute('SELECT 1 FROM applications WHERE job_id=? AND id>? LIMIT 1',
                                          (job_id, saved.get('outcome_id', 0))).fetchone())):
            return None
        return {'stage': stage, 'job_id': job_id, 'company': job['company'] if job else None,
                'title': job['title'] if job else None, 'reported_at': reported,
                'stage_started_at': started, 'action': saved.get('action'),
                'action_label': ACTIVITY_ACTIONS.get(saved.get('action'), ACTIVITY_STAGE_LABELS[stage]),
                'page': saved.get('page'), 'pages': saved.get('pages'),
                'stale': time.time() - reported > ACTIVITY_STALE_SECONDS or runtime.get('state') != 'working'}


def report_activity(con, data):
    if not isinstance(data, dict) or set(data) - {'stage', 'job_id', 'action', 'page', 'pages'}:
        raise ValueError('invalid_activity')
    stage, job_id = data.get('stage'), data.get('job_id')
    if not isinstance(stage, str) or stage not in ACTIVITY_STAGES:
        raise ValueError('invalid_activity_stage')
    if job_id is not None and (not isinstance(job_id, str) or not job_id):
        raise ValueError('invalid_activity_job_id')
    if not job_id and stage not in ('searching', 'idle'):
        raise ValueError('activity_requires_job_id')
    if job_id and not con.execute('SELECT 1 FROM jobs WHERE id=?', (job_id,)).fetchone():
        raise ValueError('unknown_job')
    action, page, pages = data.get('action'), data.get('page'), data.get('pages')
    if action is not None and (not isinstance(action, str) or action not in ACTIVITY_ACTIONS):
        raise ValueError('invalid_activity_action')
    if any(value is not None and (type(value) is not int or value <= 0) for value in (page, pages)):
        raise ValueError('invalid_activity_page')
    if page is not None and pages is not None and page > pages:
        raise ValueError('invalid_activity_page')
    with locked(ROOT / 'data/campaign-activity.lock'):
        current = runtime_status(con)
        run_id = os.environ.get('CAMPAIGN_RUN_ID')
        if (not run_id or run_id != current.get('worker_run_id') or current['state'] != 'working'
                or not pid_alive(current.get('worker_pid'))):
            raise ValueError('activity_requires_current_worker_run')
        # Environment alone is not authority: the caller must belong to this
        # supervised worker's process ancestry, even when Eval owns a new session.
        table = process_table()
        worker, caller, seen = current['worker_pid'], os.getpid(), set()
        while caller in table and caller not in seen and caller != worker:
            seen.add(caller)
            caller = table[caller][0]
        if caller != worker or worker not in table:
            raise ValueError('activity_requires_supervised_worker')
        try:
            created = time.mktime(time.strptime(table[worker][2], '%a %b %d %H:%M:%S %Y'))
        except ValueError:
            raise ValueError('activity_requires_supervised_worker') from None
        if abs(created - (current.get('worker_started_at') or 0)) > 3:
            raise ValueError('activity_requires_supervised_worker')
        now, previous = time.time(), read_activity()
        same = previous and (previous.get('run_id'), previous.get('stage'), previous.get('job_id')) == (run_id, stage, job_id)
        value = {'run_id': run_id, 'stage': stage, 'job_id': job_id, 'reported_at': now,
                 'stage_started_at': previous['stage_started_at'] if same else now,
                 'action': action, 'page': page, 'pages': pages,
                 'outcome_id': con.execute('SELECT COALESCE(MAX(id),0) FROM applications').fetchone()[0]}
        atomic_json(ROOT / 'data/campaign-activity.json', value)
        event('activity', stage=stage, job_id=job_id, run_id=run_id, action=action)
    return {'activity': activity_status(con, current)}


def progress_at(con):
    """Durable actions, not log volume or process existence, measure progress."""
    application = con.execute('SELECT MAX(attempted) FROM applications').fetchone()[0]
    reservation = con.execute('SELECT MAX(reserved_at) FROM campaign_reservations').fetchone()[0]
    pace = pacing()
    pace_at = max([pace.get('last', 0), *[item['t'] for item in pace['events']]], default=0) / 1000
    activity, runtime, now = read_activity(), read_runtime(), time.time()
    activity_at = 0
    if (activity and activity.get('run_id') and activity.get('run_id') == runtime.get('worker_run_id')
            and runtime.get('state') == 'working' and activity.get('stage') in ACTIVITY_STAGES
            and isinstance(activity.get('reported_at'), (int, float))
            and 0 <= now - activity['reported_at'] <= ACTIVITY_STALE_SECONDS
            and isinstance(runtime.get('heartbeat_at'), (int, float))
            and now - runtime['heartbeat_at'] <= HEARTBEAT_STALE_SECONDS
            and pid_alive(runtime.get('supervisor_pid')) and pid_alive(runtime.get('worker_pid'))):
        job_id = activity.get('job_id')
        if ((not job_id and activity['stage'] in ('searching', 'idle'))
                or (job_id and con.execute('SELECT 1 FROM jobs WHERE id=?', (job_id,)).fetchone()
                    and not con.execute('SELECT 1 FROM applications WHERE job_id=? AND id>? LIMIT 1',
                                        (job_id, activity.get('outcome_id', 0))).fetchone())):
            activity_at = activity['reported_at']
    return max(application or 0, reservation or 0, pace_at, activity_at) or None


def runtime_status(con):
    saved, now = read_runtime(), time.time()
    keys = ('state', 'heartbeat_at', 'supervisor_pid', 'worker_pid', 'worker_started_at', 'worker_run_id',
            'last_progress_at', 'next_retry_at', 'restart_count', 'last_exit_reason')
    value = {key: saved.get(key) for key in keys}
    heartbeat = value['heartbeat_at']
    age = max(0, now - heartbeat) if isinstance(heartbeat, (float, int)) else None
    value['heartbeat_age_seconds'] = age
    value['heartbeat_stale_after_seconds'] = HEARTBEAT_STALE_SECONDS
    if value['state'] not in ('stopped', 'complete', 'error'):
        if age is None or age > HEARTBEAT_STALE_SECONDS or not pid_alive(value['supervisor_pid']):
            value['state'] = 'stale'
    if value['last_exit_reason'] not in EXIT_REASONS:
        value['last_exit_reason'] = None
    value['last_progress_at'] = progress_at(con)
    progress = value['last_progress_at']
    value['progress_age_seconds'] = max(0, now - progress) if progress else None
    baseline = progress or saved.get('started_at')
    value['no_progress'] = bool(baseline and now - baseline >= NO_PROGRESS_SECONDS)
    return value


def heartbeat(con, runtime, **changes):
    with locked(ROOT / 'data/campaign-activity.lock'):
        runtime.update(changes)
        runtime['heartbeat_at'] = time.time()
        runtime['last_progress_at'] = progress_at(con)
        atomic_json(ROOT / 'data/campaign-runtime.json', runtime)
        saved = read_activity()
        if saved and (runtime.get('state') != 'working' or not runtime.get('worker_pid')
                      or saved.get('run_id') != runtime.get('worker_run_id')):
            atomic_json(ROOT / 'data/campaign-activity.json', None)


def connect():
    con = db.connect()
    con.execute('''CREATE TABLE IF NOT EXISTS campaign_reservations (
        job_id TEXT PRIMARY KEY REFERENCES jobs(id), reserved_at REAL NOT NULL,
        state TEXT NOT NULL, reason TEXT)''')
    con.execute('''CREATE TABLE IF NOT EXISTS job_answers (
        job_id TEXT NOT NULL REFERENCES jobs(id), question TEXT NOT NULL,
        answer TEXT NOT NULL, created_at REAL NOT NULL, PRIMARY KEY (job_id, question))''')
    con.commit()
    return con


def confirmed(con):
    return con.execute("SELECT COUNT(DISTINCT job_id) FROM applications WHERE result='applied'").fetchone()[0]


def pacing():
    if not PACE.exists():
        return {'events': [], 'lockedUntil': 0, 'sinceBreak': 0, 'nextBreakAt': random.randint(4, 7), 'last': 0}
    # Corrupt pacing history must not reset counters or platform lockouts.
    return json.loads(PACE.read_text())


def budget():
    now = time.time() * 1000
    s, c = pacing(), config()
    events = [e for e in s['events'] if now - e['t'] < DAY * 1000]
    loads = [e for e in events if e['k'] != 'apply']
    details = [e for e in events if e['k'] == 'detail' and now - e['t'] < HOUR * 1000]
    applies = [e for e in events if e['k'] == 'apply']
    retry = s.get('lockedUntil', 0)
    if c['max_submissions_per_day'] is not None and 0 < c['max_submissions_per_day'] <= len(applies):
        retry = max(retry, min(e['t'] for e in applies) + DAY * 1000)
    if c['max_loads_per_day'] is not None and 0 < c['max_loads_per_day'] <= len(loads):
        retry = max(retry, min(e['t'] for e in loads) + DAY * 1000)
    if c['max_details_per_hour'] is not None and 0 < c['max_details_per_hour'] <= len(details):
        retry = max(retry, min(e['t'] for e in details) + HOUR * 1000)
    return {'loads_24h': len(loads), 'details_1h': len(details), 'submit_attempts_24h': len(applies),
            'retry_at': retry / 1000 if retry > now else None,
            'lock_reason': 'site_lockout' if s.get('lockedUntil', 0) > now else None}


def gate(kind):
    if kind not in ('load', 'detail', 'apply'):
        raise ValueError('invalid pacing event')
    with locked(ROOT / 'data/campaign-pacing.lock'):
        s, c = pacing(), config()
        now = time.time() * 1000
        s['events'] = [e for e in s['events'] if now - e['t'] < DAY * 1000]
        retry, reason = s.get('lockedUntil', 0), 'site_lockout'
        selected, span, cap = (None, None, None)
        if kind == 'apply':
            selected, span, cap = [e for e in s['events'] if e['k'] == 'apply'], DAY, c['max_submissions_per_day']
            if cap is not None and len(selected) >= cap:
                reason = 'daily_submission_limit'
        else:
            selected, span, cap = [e for e in s['events'] if e['k'] != 'apply'], DAY, c['max_loads_per_day']
            if cap is not None and len(selected) >= cap:
                reason = 'daily_page_limit'
            if kind == 'detail':
                detail = [e for e in s['events'] if e['k'] == 'detail' and now - e['t'] < HOUR * 1000]
                if c['max_details_per_hour'] is not None and len(detail) >= c['max_details_per_hour']:
                    if c['max_details_per_hour'] <= 0:
                        return {'allowed': False, 'reason': 'hourly_detail_limit', 'retry_at': None}
                    retry = max(retry, min(e['t'] for e in detail) + HOUR * 1000)
                    reason = 'hourly_detail_limit'
        if cap is not None and len(selected) >= cap:
            # A zero cap disables this action; time alone cannot restore capacity.
            if cap <= 0:
                return {'allowed': False, 'reason': reason, 'retry_at': None}
            retry = max(retry, min(e['t'] for e in selected) + span * 1000)
        if retry > now:
            return {'allowed': False, 'reason': reason, 'retry_at': retry / 1000}
        gap = random.uniform(60, 180) if kind == 'apply' else random.uniform(8, 20)
        wait = max(0, s.get('last', 0) / 1000 + gap - time.time())
        if kind != 'apply' and s.get('sinceBreak', 0) >= s.get('nextBreakAt', 4):
            wait = max(wait, random.uniform(45, 120))
            s['sinceBreak'], s['nextBreakAt'] = 0, random.randint(4, 7)
        if wait:
            time.sleep(wait)
        now = int(time.time() * 1000)
        s['events'].append({'t': now, 'k': kind})
        s['last'] = now
        if kind != 'apply':
            s['sinceBreak'] = s.get('sinceBreak', 0) + 1
        temporary = PACE.with_suffix('.tmp')
        temporary.write_text(json.dumps(s))
        temporary.replace(PACE)
        return {'allowed': True, 'kind': kind}


def identity_conflicts(con, job, include_self=True):
    """Share employer identity protections across reservation and reviewed recovery."""
    rows = con.execute('''SELECT r.state,
        j.status='applied' OR EXISTS (
            SELECT 1 FROM applications a WHERE a.job_id=j.id AND a.result='applied') AS applied
        FROM jobs j LEFT JOIN campaign_reservations r ON r.job_id=j.id
        WHERE (j.id=? OR j.url=? OR (j.source=? AND j.source_id=?))
        AND (? OR j.id<>?)''',
        (job['id'], job['url'], job['source'], job['source_id'], include_self, job['id'])).fetchall()
    return (any(row['applied'] or row['state'] == 'applied' for row in rows),
            any(row['state'] == 'reserved' for row in rows))


def reserve(con, job_id):
    c, now = config(), time.time()
    con.execute('BEGIN IMMEDIATE')
    try:
        count = confirmed(con)
        if c['target_total'] is not None:
            pending = con.execute("SELECT COUNT(*) FROM campaign_reservations WHERE state='reserved'").fetchone()[0]
            if count + pending >= c['target_total']:
                raise ValueError('target_reached_or_reserved')
        row = con.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not row:
            raise ValueError('unknown_job')
        sent, active = identity_conflicts(con, row)
        existing = con.execute('SELECT state FROM campaign_reservations WHERE job_id=?', (job_id,)).fetchone()
        # Finished attempts require an explicit re-queue, never an automatic retry.
        if sent or active:
            raise ValueError('already_sent_or_reserved')
        if row['status'] in ('manual', 'skipped', 'failed') or (existing and row['status'] != 'queued'):
            raise ValueError('manual_review_or_explicit_requeue_required')
        if c['max_submissions_per_day'] is not None:
            recent = con.execute('''SELECT COUNT(*) FROM (
                SELECT job_id FROM campaign_reservations WHERE reserved_at>?
                UNION SELECT job_id FROM applications WHERE result='applied' AND attempted>?)''', (now - DAY, now - DAY)).fetchone()[0]
            if recent >= c['max_submissions_per_day']:
                raise ValueError('daily_submission_limit')
        con.execute("DELETE FROM campaign_reservations WHERE job_id=? AND state IN ('manual','failed','skipped')", (job_id,))
        con.execute("INSERT INTO campaign_reservations VALUES(?,?,'reserved',NULL)", (job_id, now))
        con.execute("UPDATE jobs SET status='applying' WHERE id=?", (job_id,))
        con.commit()
        event('reserved', job_id=job_id)
        return {'reserved': True, 'job_id': job_id, 'confirmed': count, 'target': c['target_total']}
    except Exception:
        con.rollback()
        raise


def requeue(con, job_id, data, answers=None, *, expected_status=None, review_check=None):
    """An operator-reviewed deferral, not an application attempt or submission permission.

    Store a deferred audit row in the existing private applications ledger so reason,
    evidence, and the explicit attestation survive the next reservation without a migration.
    Prior outcomes remain untouched; public telemetry contains metadata only.

    ``answers`` ({question: answer}) are saved to job_answers inside the same transaction, so a rejected
    requeue never leaves reviewed answers behind. They are data for the worker, never permission to submit.

    Dashboard-specific review checks run inside the write transaction so a stale page cannot bypass
    changed job status or new submission history. Existing callers keep the same checks by default.
    """
    if not isinstance(data, dict) or data.get('submission_verified_absent') is not True:
        raise ValueError('requeue_requires_submission_verified_absent')
    if not isinstance(data.get('reason'), str) or not data['reason'].strip():
        raise ValueError('requeue_requires_reason')
    if not isinstance(data.get('evidence'), str) or not data['evidence'].strip():
        raise ValueError('requeue_requires_evidence')
    con.execute('BEGIN IMMEDIATE')
    try:
        row = con.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not row:
            raise ValueError('unknown_job')
        sent, active = identity_conflicts(con, row)
        if sent or active:
            raise ValueError('already_sent_or_reserved')
        if row['status'] not in ('manual', 'skipped', 'failed'):
            raise ValueError('requeue_requires_finished_non_applied_job')
        if expected_status is not None and row['status'] != expected_status:
            raise ValueError('requeue_requires_expected_status')
        if review_check is not None:
            review_check(con, row)
        con.execute('''INSERT INTO applications(job_id,attempted,result,reason,evidence,run_id)
            VALUES(?,?,'deferred',?,?,'campaign-requeue')''',
            (job_id, time.time(), data['reason'], json.dumps({
                'submission_verified_absent': True, 'evidence': data['evidence']})))
        con.execute("DELETE FROM campaign_reservations WHERE job_id=? AND state IN ('manual','failed','skipped')", (job_id,))
        con.execute("UPDATE jobs SET status='queued' WHERE id=?", (job_id,))
        if answers:
            con.execute('DELETE FROM job_answers WHERE job_id=?', (job_id,))
            con.executemany('INSERT INTO job_answers(job_id,question,answer,created_at) VALUES(?,?,?,?)',
                            [(job_id, question, answer, time.time()) for question, answer in answers.items()])
        con.commit()
    except Exception:
        con.rollback()
        raise
    event('requeued', job_id=job_id)
    return {'requeued': True, 'job_id': job_id}


def reconcile(con, job_id, data):
    """Record an inspected historical receipt, never authorize or perform a submission."""
    if not isinstance(data, dict) or data.get('employer_confirmation_verified') is not True:
        raise ValueError('reconcile_requires_employer_confirmation_verified')
    if not isinstance(data.get('reason'), str) or not data['reason'].strip():
        raise ValueError('reconcile_requires_reason')
    if not isinstance(data.get('evidence'), str) or not data['evidence'].strip():
        raise ValueError('reconcile_requires_evidence')
    c = config()
    con.execute('BEGIN IMMEDIATE')
    try:
        job = con.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not job:
            raise ValueError('unknown_job')
        sent, active = identity_conflicts(con, job, include_self=False)
        reservation = con.execute('SELECT state FROM campaign_reservations WHERE job_id=?',
                                  (job_id,)).fetchone()
        if sent or active or (reservation and reservation['state'] == 'reserved'):
            raise ValueError('already_sent_or_reserved')
        receipt = con.execute("SELECT 1 FROM applications WHERE job_id=? AND result='applied'",
                              (job_id,)).fetchone()
        if receipt:
            # Replays never append another receipt or change the original private evidence.
            already_recorded = True
        else:
            if job['status'] == 'applied' or (reservation and reservation['state'] == 'applied'):
                raise ValueError('already_sent_or_reserved')
            if job['status'] not in ('manual', 'failed'):
                raise ValueError('reconcile_requires_manual_or_failed_job')
            # A manual screening note alone is not proof that an application was attempted.
            if not reservation or reservation['state'] not in ('manual', 'failed'):
                raise ValueError('reconcile_requires_previous_reserved_attempt')
            con.execute('''INSERT INTO applications(job_id,attempted,result,reason,evidence,run_id)
                VALUES(?,?,'applied',?,?,'campaign-reconcile')''',
                (job_id, time.time(), data['reason'], json.dumps({
                    'employer_confirmation_verified': True, 'evidence': data['evidence']})))
            con.execute("UPDATE campaign_reservations SET state='applied',reason=? WHERE job_id=?",
                        (data['reason'], job_id))
            con.execute("UPDATE jobs SET status='applied' WHERE id=?", (job_id,))
            already_recorded = False
        count = confirmed(con)
        con.commit()
    except Exception:
        con.rollback()
        raise
    if not already_recorded:
        clear_activity(job_id)
        event('reconciled', job_id=job_id, result='applied', confirmed=count)
    return {'reconciled': True, 'job_id': job_id, 'already_recorded': already_recorded,
            'confirmed': count, 'target': c['target_total']}


def record(con, data):
    job_id, outcome = data['job_id'], data['result']
    if outcome == 'deferred':
        # Paced out / waiting on a human OTP: not an attempt. Drop the reservation (no daily-slot cost) and keep the job queued.
        con.execute("DELETE FROM campaign_reservations WHERE job_id=? AND state='reserved'", (job_id,))
        con.execute("UPDATE jobs SET status='queued' WHERE id=? AND status IN ('applying','queued')", (job_id,))
        con.commit()
        clear_activity(job_id)
        event('recorded_outcome', job_id=job_id, result=outcome)
        return {'recorded': 'deferred', 'confirmed': confirmed(con), 'target': config()['target_total']}
    if outcome not in ('applied', 'manual', 'failed', 'skipped'):
        raise ValueError('invalid_result')
    if outcome == 'applied':
        if not data.get('evidence'):
            raise ValueError('confirmed_application_requires_evidence')
        r = con.execute("SELECT state FROM campaign_reservations WHERE job_id=?", (job_id,)).fetchone()
        if not r or r['state'] != 'reserved':
            raise ValueError('application_requires_active_reservation')
    con.execute('UPDATE campaign_reservations SET state=?,reason=? WHERE job_id=?', (outcome, data.get('reason'), job_id))
    db.record_application(con, job_id, outcome, data.get('reason'), data.get('evidence'), 'campaign-50')
    clear_activity(job_id)
    event('recorded_outcome', job_id=job_id, result=outcome)
    return {'recorded': outcome, 'confirmed': confirmed(con), 'target': config()['target_total']}


def recover(con):
    rows = con.execute("SELECT job_id FROM campaign_reservations WHERE state='reserved'").fetchall()
    for row in rows:
        record(con, {'job_id': row['job_id'], 'result': 'manual', 'reason': 'Worker ended with submission outcome unresolved; inspect employer evidence before any retry. Never count as applied.'})


def _ensure_verification_columns(con):
    """A database that never ran pipeline.py lacks verified_at and apply_method; pipeline owns that migration."""
    import pipeline
    pipeline._ensure(con)
    con.commit()


def next_verify(con):
    """Pick the next unverified, unattempted LinkedIn job; initialize legacy schema columns when needed.

    Same pick order as pipeline.next_detail, minus any job with application history: a job someone already
    worked or decided on is never re-described, re-scored or moved. Selecting a job costs nothing; the page load
    that follows must still pass ``gate detail``.
    """
    _ensure_verification_columns(con)
    row = con.execute('''SELECT id, title, company, url, score FROM jobs j
        WHERE j.status='discovered' AND j.source='linkedin' AND j.verified_at IS NULL
          AND COALESCE(j.score_notes,'') NOT LIKE 'rejected:%'
          AND NOT EXISTS (SELECT 1 FROM applications a WHERE a.job_id=j.id)
        ORDER BY j.score DESC, j.first_seen DESC LIMIT 1''').fetchone()
    return {'job': dict(row) if row else None}


_VERIFY_FIELDS = {'description', 'salary_min', 'salary_max', 'apply_url', 'easy_apply', 'external_apply', 'closed'}
MAX_DESCRIPTION = 60000


def save_description(con, job_id, data):
    """Store what the worker read on a job page, then re-score that one job with the existing rules.

    This never records an outcome and never queues a job except through ``ingest.score_row``, which applies
    every rule (location, seniority, salary midpoint, years required) to the new description. It refuses a job
    that has any application history or is not ``discovered``.
    """
    if not isinstance(data, dict) or set(data) - _VERIFY_FIELDS:
        raise ValueError('verification_has_unknown_fields')
    description = data.get('description')
    if description is None and data.get('closed') is True:
        description = ''
    if not isinstance(description, str) or (not description.strip() and data.get('closed') is not True):
        raise ValueError('verification_requires_description')
    if len(description) > MAX_DESCRIPTION:
        raise ValueError('verification_description_too_long')
    for key in ('salary_min', 'salary_max'):
        value = data.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not 0 < value < 10_000_000):
            raise ValueError(f'verification_invalid_{key}')
    apply_url = data.get('apply_url')
    if apply_url is not None and (not isinstance(apply_url, str) or not re.match(r'https?://[^\s]+$', apply_url) or len(apply_url) > 2000):
        raise ValueError('verification_invalid_apply_url')
    _ensure_verification_columns(con)
    con.execute('BEGIN IMMEDIATE')
    try:
        row = con.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not row:
            raise ValueError('unknown_job')
        if row['status'] != 'discovered':
            raise ValueError('verification_requires_discovered_job')
        if con.execute('SELECT 1 FROM applications WHERE job_id=?', (job_id,)).fetchone():
            raise ValueError('verification_refused_for_job_with_history')
        method = 'easy' if data.get('easy_apply') else 'external' if data.get('external_apply') else 'unknown'
        con.execute('''UPDATE jobs SET description=?, salary_min=COALESCE(?,salary_min), salary_max=COALESCE(?,salary_max),
            apply_url=COALESCE(?,apply_url), easy_apply=?, apply_method=?, verified_at=? WHERE id=?''',
            (description, data.get('salary_min'), data.get('salary_max'), apply_url,
             1 if data.get('easy_apply') else 0, method, time.time(), job_id))
        if data.get('closed') is True:
            con.execute("UPDATE jobs SET status='skipped', score=0, score_notes='rejected:closed' WHERE id=?", (job_id,))
            outcome = 'skipped'
        else:
            import ingest
            scored = con.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            ingest.score_row(con, db.load_profile(), scored)
            outcome = con.execute('SELECT status FROM jobs WHERE id=?', (job_id,)).fetchone()['status']
        con.commit()
    except Exception:
        con.rollback()
        raise
    event('verified', job_id=job_id, result=outcome)
    return {'verified': True, 'job_id': job_id, 'status': outcome}


def outcome_reason(reason):
    """Expose a bounded explanation, never contact details or credential material."""
    if not isinstance(reason, str) or not reason:
        return None
    value = re.sub(r'(?:\b(?:https?|ftp)://|\bwww\.)[^\s<>"\']+', '[redacted URL]', reason, flags=re.IGNORECASE)
    value = re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[redacted email]', value)
    value = re.sub(r'(?i)\b(password|passwd|pwd|api[_ -]?key|(?:(?:api|access|refresh|session|auth|id)[_ -]?)?token|'
                   r'(?:client[_ -]?)?secret|authorization|cookie|otp)\b[\"\']?'
                   r'\s*[:=]\s*(?:Bearer\s+)?(?:"[^"]*"|\'[^\']*\'|[^\s,;]+)', r'\1=[redacted]', value)
    value = re.sub(r'(?i)\bBearer\s+\S+', 'Bearer [redacted]', value)
    value = re.sub(r'\b(?:sk-[A-Za-z0-9_-]+|gh[pousr]_[A-Za-z0-9_]+|AKIA[A-Z0-9]{16})\b', '[redacted token]', value)
    value = re.sub(r'\b[A-Za-z0-9_+/=-]{32,}\b', '[redacted token]', value)
    return value[:600]


def status(con):
    c = config()
    n = confirmed(con)
    runtime = runtime_status(con)
    actions = []
    for row in con.execute('''SELECT a.attempted AS at,a.job_id,j.company,j.title,a.result,a.reason
                              FROM applications a JOIN jobs j ON j.id=a.job_id
                              ORDER BY a.attempted DESC,a.id DESC LIMIT 20'''):
        action = dict(row)
        action['reason'] = outcome_reason(action['reason']) if action['result'] in {'manual', 'failed', 'deferred', 'skipped'} else None
        actions.append(action)
    target = c['target_total']
    return {'confirmed': n, 'target': target, 'remaining': None if target is None else max(0, target - n),
            'complete': target is not None and n >= target,
            'budget': budget(), 'outcomes': {r['result']: r['n'] for r in con.execute('SELECT result,COUNT(*) n FROM applications GROUP BY result')},
            'runtime': runtime, 'activity': activity_status(con, runtime), 'latest_actions': actions,
            'counts_by_job_status': {r['status']: r['n'] for r in con.execute('SELECT status,COUNT(*) n FROM jobs GROUP BY status')}}


def relay_connected():
    """Cheap preflight on the omp relay's /json/version: connected means HTTP 200 and no `error` field.
    503 + "relay extension is not connected" (observed) or anything unreachable/unparseable means not connected.
    No browser, model or site is touched."""
    try:
        with urllib.request.urlopen('http://127.0.0.1:9224/json/version', timeout=3) as response:
            body = json.loads(response.read().decode('utf-8', 'replace'))
    except (urllib.error.URLError, OSError, ValueError):
        return False
    return isinstance(body, dict) and not body.get('error')


def group_alive(pid):
    try:
        os.killpg(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # macOS can report EPERM for a vanished group. Probe membership, never
        # interpret permission denial itself as proof that a worker survives.
        try:
            result = subprocess.run(['ps', '-axo', 'pgid='], capture_output=True,
                                    text=True, check=True, timeout=3)
            return pid in {int(value) for value in result.stdout.split()}
        except (OSError, subprocess.SubprocessError, ValueError):
            return True  # Uncertain membership must never permit an overlap.


def process_table():
    """Process identities only; never collect command arguments or environments."""
    try:
        result = subprocess.run(['ps', '-axo', 'pid=,ppid=,pgid=,lstart='],
                                capture_output=True, text=True, check=True, timeout=3,
                                env={**os.environ, 'LC_ALL': 'C'})
        table = {}
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            pid, parent, group, started = line.split(None, 3)
            table[int(pid)] = (int(parent), int(group), started.strip())
        return table
    except (OSError, subprocess.SubprocessError, ValueError):
        raise RuntimeError('cannot establish worker process tree; refusing overlap') from None


class WorkerTree:
    """Retain detached descendants before their parent exits and they reparent."""
    def __init__(self, root, saved=None):
        self.root = root
        saved = saved or {}
        self.processes = {int(pid): value for pid, value in saved.get('processes', {}).items()}
        self.groups = {int(pid): value for pid, value in saved.get('groups', {}).items()}
        self.groups.setdefault(root, None)

    def snapshot(self):
        return {'processes': dict(self.processes), 'groups': dict(self.groups)}

    def capture(self):
        table = process_table()
        # A PID reused by an unrelated process is never authority to signal it.
        for group, identity in list(self.groups.items()):
            if identity is not None and group in table and table[group][2] != identity:
                del self.groups[group]
                raise RuntimeError('worker group identity changed; refusing overlap')
        owned = {pid for pid, identity in self.processes.items()
                 if pid in table and table[pid][2] == identity}
        if self.root in table and self.root not in self.processes:
            owned.add(self.root)
        while True:
            children = {pid for pid, (parent, _, _) in table.items() if parent in owned}
            added = children - owned
            if not added:
                break
            owned.update(added)
        unknown_group = False
        for pid in owned:
            self.processes[pid] = table[pid][2]
            group = table[pid][1]
            # A descendant may join another group; that does not confer ownership
            # of Chrome, the caller, or any group led outside this worker tree.
            if group in owned:
                if group == os.getpgrp():
                    raise RuntimeError('worker shares supervisor group; refusing group termination')
                self.groups[group] = table[group][2]
            elif group not in self.groups:
                unknown_group = True
        if unknown_group:
            raise RuntimeError('worker descendant group ownership is uncertain; refusing overlap')
        return self.snapshot()


def terminate_group(pid, tick, process=None, tree=None):
    """Terminate retained owned groups, including independently sessioned evals."""
    tree = tree or WorkerTree(pid)
    cleanup_error = None
    next_capture = 0
    termed = set()

    def refresh(force=False):
        nonlocal cleanup_error, next_capture
        if force or time.monotonic() >= next_capture:
            try:
                tree.capture()
            except Exception as error:
                cleanup_error = cleanup_error or error
            next_capture = time.monotonic() + 1

    def pulse():
        nonlocal cleanup_error
        try:
            tick()
        except Exception as error:
            cleanup_error = cleanup_error or error

    def signal_owned(number):
        for group in list(tree.groups):
            if group == os.getpgrp():
                raise RuntimeError('refusing to signal supervisor process group')
            if number == signal.SIGTERM and group in termed:
                continue
            if group_alive(group):
                try:
                    os.killpg(group, number)
                except ProcessLookupError:
                    pass
            if number == signal.SIGTERM:
                termed.add(group)

    def any_alive():
        return any(group_alive(group) for group in tree.groups)

    refresh()  # Capture descendants BEFORE TERM can remove/reparent the leader.
    signal_owned(signal.SIGTERM)
    until = time.monotonic() + TERMINATE_GRACE_SECONDS
    while time.monotonic() < until:
        if process is not None:
            process.poll()
        refresh()
        signal_owned(signal.SIGTERM)  # Include groups discovered during cleanup.
        if not any_alive():
            break
        pulse()
        time.sleep(min(0.2, max(0, until - time.monotonic())))
    refresh(force=True)
    signal_owned(signal.SIGKILL)
    if process is not None:
        process.wait(timeout=TERMINATE_GRACE_SECONDS)
    until = time.monotonic() + TERMINATE_GRACE_SECONDS
    while any_alive() and time.monotonic() < until:
        pulse()
        time.sleep(min(0.2, max(0, until - time.monotonic())))
    if any_alive():
        raise RuntimeError('worker process group persists after termination; refusing overlap')
    if cleanup_error is not None:
        raise cleanup_error


def supervise_worker(process, con, runtime, stop_reason, deadline_seconds=WORKER_DEADLINE_SECONDS):
    deadline = time.monotonic() + deadline_seconds
    next_heartbeat = 0
    tree = WorkerTree(process.pid)

    def tick():
        nonlocal next_heartbeat
        if time.monotonic() >= next_heartbeat:
            next_heartbeat = time.monotonic() + HEARTBEAT_SECONDS
            runtime['worker_tree'] = tree.capture()
            heartbeat(con, runtime)

    reason = None
    try:
        while True:
            tick()
            reason = stop_reason()
            if reason:
                break
            code = process.poll()
            if code is not None:
                reason = 'worker_exit' if code == 0 else 'worker_failed'
                break
            if time.monotonic() >= deadline:
                reason = 'worker_timeout'
                event('timeout', worker_pid=process.pid, exit_reason=reason)
                break
            time.sleep(min(0.25, max(0, deadline - time.monotonic())))
    finally:
        # Even a normally exited leader may leave child/background processes behind.
        try:
            heartbeat(con, runtime, state='stopping')
        finally:
            terminate_group(process.pid, tick, process, tree)
    return process.returncode, reason


def recover_worker(saved, tick):
    """Clean a previous supervisor's orphan group before recovering reservations."""
    pid = saved.get('worker_pid')
    if not isinstance(pid, int) or pid <= 0:
        return False
    tree = WorkerTree(pid, saved.get('worker_tree'))
    if not any(group_alive(group) for group in tree.groups):
        return False
    if group_alive(pid) and pid_alive(pid):
        # A reused PID must never cause an unrelated process group to be killed.
        started = subprocess.run(['ps', '-p', str(pid), '-o', 'lstart='],
                                 capture_output=True, text=True, check=False, timeout=3,
                                 env={**os.environ, 'LC_ALL': 'C'})
        try:
            created = time.mktime(time.strptime(started.stdout.strip(), '%a %b %d %H:%M:%S %Y'))
        except ValueError:
            raise RuntimeError('cannot verify previous worker process identity') from None
        try:
            group = os.getpgid(pid)
        except ProcessLookupError:
            group = pid  # Leader exited; any surviving group still needs cleanup.
        if abs(created - (saved.get('worker_started_at') or 0)) > 3 or group != pid:
            raise RuntimeError('previous worker process identity does not match; refusing overlap')
    terminate_group(pid, tick, tree=tree)
    return True


def keep_awake(pid):
    """Prevent idle sleep during a macOS worker round, without a persistent power-setting change.

    The assertion exits with the worker even if the supervisor crashes. It does not prevent lid-close sleep.
    """
    if sys.platform != 'darwin' or not os.access(CAFFEINATE, os.X_OK):
        return None
    return subprocess.Popen([CAFFEINATE, '-i', '-w', str(pid)], stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def worker_executable(runtime):
    """Resolve explicit paths against the project, and bare names through PATH."""
    name = os.path.expanduser(runtime['omp_executable'])
    if os.path.isabs(name) or '/' in name:
        executable = shutil.which(str(settings.resolve_path(name, ROOT)))
    else:
        search_path = os.pathsep.join(str(settings.resolve_path(part or '.', ROOT))
                                     for part in os.environ.get('PATH', os.defpath).split(os.pathsep))
        executable = shutil.which(name, path=search_path)
    if not executable:
        raise RuntimeError('configured omp executable unavailable')
    return str(settings.resolve_path(executable, ROOT))


def reviewed_answers(con):
    """Answers a reviewer saved for jobs that are queued again, keyed by job id.

    Limited to jobs still waiting to be applied to, so a stale answer cannot reach an unrelated form.
    """
    grouped = {}
    for row in con.execute('''SELECT a.job_id, a.question, a.answer FROM job_answers a
                              JOIN jobs j ON j.id = a.job_id WHERE j.status IN ('queued', 'applying')
                              ORDER BY a.job_id, a.rowid'''):
        grouped.setdefault(row['job_id'], []).append({'question': row['question'], 'answer': row['answer']})
    return grouped


def worker_prompt(campaign_config, profile, current, job_answers=None):
    """Render this round's reviewed data without exposing the credential location."""
    public_campaign = {key: value for key, value in campaign_config.items() if key != 'runtime'}
    header = (
        '# Current round configuration\n\n'
        'The repository root below is authoritative; use it as cwd for every local command. '
        'Initialize Eval JavaScript with `const ROOT = ' + json.dumps(str(ROOT.resolve())) + ';`.\n'
        'Read the current files before acting. Only this profile supplies candidate facts, '
        'permissions and job-specific exceptions; absent permissions mean false and absent '
        'facts are unknown. Never inherit facts or authorization from earlier candidates/rounds.\n\n'
        'Repository root: ' + json.dumps(str(ROOT.resolve())) + '\n\n'
        'Current profile.json:\n```json\n' + json.dumps(profile, indent=2) + '\n```\n\n'
        'Current data/campaign.json (runtime paths omitted):\n```json\n'
        + json.dumps(public_campaign, indent=2) + '\n```\n\n'
        'Current durable status:\n```json\n' + json.dumps(current, indent=2) + '\n```\n\n'
        + ('Reviewed per-job answers (a person supplied these for exactly the job_id shown; use one only for '
           'that job and only when a form asks that exact question):\n```json\n'
           + json.dumps(job_answers, indent=2) + '\n```\n\n' if job_answers else '')
    )
    return header + (ROOT / 'src/CAMPAIGN_WORKER.md').read_text()


def loop():
    with locked(ROOT / 'data/campaign-worker.lock', blocking=False):
        # Configuration is reloaded at every round boundary, not captured at service start.
        con, saved = connect(), read_runtime()
        runtime = {'state': 'starting', 'started_at': time.time(), 'heartbeat_at': None,
                   'supervisor_pid': os.getpid(), 'worker_pid': None, 'worker_started_at': None,
                   'worker_run_id': None,
                   'last_progress_at': None, 'next_retry_at': None,
                   'restart_count': saved.get('restart_count') or 0, 'last_exit_reason': None}
        stopped = None
        handlers = {}

        def on_signal(number, frame):
            nonlocal stopped
            stopped = 'sigterm' if number == signal.SIGTERM else 'sigint'

        def stop_reason():
            return stopped or ('stop_file' if (ROOT / 'data/campaign.stop').exists() else None)

        def pause(state, retry_at):
            # Every wait is <=5s; the caller re-reads config and durable progress.
            heartbeat(con, runtime, state=state, next_retry_at=retry_at)
            time.sleep(max(0, min(HEARTBEAT_SECONDS, retry_at - time.time())))

        for number in (signal.SIGTERM, signal.SIGINT):
            handlers[number] = signal.signal(number, on_signal)
        try:
            # Keep orphan identity durable until cleanup succeeds, even if killed during recovery.
            heartbeat(con, runtime, worker_pid=saved.get('worker_pid'),
                      worker_started_at=saved.get('worker_started_at'),
                      worker_tree=saved.get('worker_tree'))
            if recover_worker(saved, lambda: heartbeat(con, runtime)):
                runtime['last_exit_reason'] = 'recovered_worker'
                event('restart', exit_reason='recovered_worker', restart_count=runtime['restart_count'])
            recover(con)
            heartbeat(con, runtime, worker_pid=None, worker_started_at=None)
            event('start', supervisor_pid=os.getpid(), restart_count=runtime['restart_count'])
            logdir = ROOT / 'logs/campaign'
            logdir.mkdir(parents=True, exist_ok=True)
            between_round = None
            while not stop_reason():
                campaign_config = config()
                round_runtime = campaign_config['runtime']
                current = status(con)
                if current['complete']:
                    heartbeat(con, runtime, state='complete', next_retry_at=None,
                              last_exit_reason='target_reached')
                    return
                retry = current['budget']['retry_at']
                if retry:
                    pause('waiting_platform', retry)
                    continue
                if between_round:
                    ended_at, progressed = between_round
                    pause_key = 'success_pause_seconds' if progressed else 'idle_pause_seconds'
                    between_until = ended_at + round_runtime[pause_key]
                    if between_until > time.time():
                        pause('between_rounds', between_until)
                        continue
                heartbeat(con, runtime, next_retry_at=None)
                if not relay_connected():
                    pause('waiting_relay', time.time() + HEARTBEAT_SECONDS)
                    continue
                # Re-check signals/stop-file after the bounded relay preflight.
                if stop_reason():
                    break
                profile = settings.load_profile(ROOT, ready=True)
                executable = worker_executable(round_runtime)
                prompt = worker_prompt(campaign_config, profile, current, reviewed_answers(con))
                command = [executable, '--print', '--no-session', '--wait-async']
                if model := campaign_config.get('worker_model'):
                    command.extend(['--model', model])
                command.extend([
                    '--no-extensions', '--auto-approve', '--tools', 'read,bash,eval,web_search,write',
                    '--max-time', str(round_runtime['worker_timeout_seconds']) + 's', prompt,
                ])
                logfile = logdir / (time.strftime('%Y%m%d-%H%M%S') + '-' + str(time.time_ns()) + '.log')
                if runtime['worker_started_at'] is not None:
                    runtime['restart_count'] += 1
                    event('restart', restart_count=runtime['restart_count'],
                          exit_reason=runtime['last_exit_reason'])
                with logfile.open('w') as output:
                    run_id = uuid.uuid4().hex
                    clear_activity()
                    process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=output,
                                               stderr=subprocess.STDOUT, start_new_session=True,
                                               env={**os.environ, 'PI_BROWSER_RELAY': '1', 'CAMPAIGN_RUN_ID': run_id,
                                                    'ATS_CREDENTIALS_FILE': str(settings.resolve_path(
                                                        round_runtime['credentials_file'], ROOT))})
                    awake = None
                    try:
                        awake = keep_awake(process.pid)
                        heartbeat(con, runtime, state='working', worker_pid=process.pid,
                                  worker_started_at=time.time(), worker_run_id=run_id,
                                  next_retry_at=None, worker_tree=None)
                        event('worker_start', worker_pid=process.pid, run_id=run_id,
                              restart_count=runtime['restart_count'])
                        code, reason = supervise_worker(
                            process, con, runtime, stop_reason,
                            deadline_seconds=round_runtime['worker_timeout_seconds'])
                    except BaseException:
                        terminate_group(process.pid, lambda: heartbeat(con, runtime), process,
                                        WorkerTree(process.pid, runtime.get('worker_tree')))
                        raise
                    finally:
                        if awake is not None:
                            awake.terminate()
                            awake.wait(timeout=TERMINATE_GRACE_SECONDS)
                recover(con)
                heartbeat(con, runtime, state='between_rounds', worker_pid=None,
                          worker_run_id=None, last_exit_reason=reason)
                event('worker_end', worker_pid=process.pid, exit_code=code, exit_reason=reason,
                      confirmed=confirmed(con))
                with (logdir / 'rounds.jsonl').open('a') as output:
                    output.write(json.dumps({
                        'at': time.time(), 'exit': code, 'confirmed': confirmed(con),
                        'log': str(logfile.relative_to(ROOT)), 'log_bytes': logfile.stat().st_size,
                        'exit_reason': reason,
                    }) + '\n')
                # No stdout heuristics: only durable outcomes affect the between-round wait.
                between_round = (time.time(), confirmed(con) > current['confirmed'])
            heartbeat(con, runtime, state='stopped', worker_pid=None, next_retry_at=None,
                      last_exit_reason=stop_reason())
        except BaseException:
            try:
                heartbeat(con, runtime, state='error', next_retry_at=None, last_exit_reason='supervisor_error')
            except Exception:
                pass  # Preserve the original error, including failed process cleanup.
            raise
        finally:
            try:
                event('end', supervisor_pid=os.getpid(), exit_reason=runtime['last_exit_reason'],
                      confirmed=confirmed(con))
            finally:
                for number, handler in handlers.items():
                    signal.signal(number, handler)
                con.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['status', 'activity', 'gate', 'reserve', 'requeue', 'reconcile', 'next-verify', 'save-description', 'result', 'loop'])
    parser.add_argument('argument', nargs='?')
    args = parser.parse_args()
    try:
        if args.command == 'loop':
            loop(); return 0
        con = connect()
        if args.command == 'status': value = status(con)
        elif args.command == 'gate': value = gate(args.argument)
        elif args.command == 'activity': value = report_activity(con, json.load(sys.stdin))
        elif args.command == 'reserve': value = reserve(con, args.argument)
        elif args.command == 'requeue': value = requeue(con, args.argument, json.load(sys.stdin))
        elif args.command == 'reconcile': value = reconcile(con, args.argument, json.load(sys.stdin))
        elif args.command == 'next-verify': value = next_verify(con)
        elif args.command == 'save-description': value = save_description(con, args.argument, json.load(sys.stdin))
        else: value = record(con, json.load(sys.stdin))
        print(json.dumps(value))
        return 0
    except (ValueError, RuntimeError, KeyError, OSError) as error:
        print(json.dumps({'error': str(error)}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
