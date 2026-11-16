"""Isolated controller tests: no browser, relay, credentials, or real workers.

Run: python3 -m unittest discover -s test -p 'test_campaign.py'
"""
import contextlib
import io
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import campaign


class Clock:
    def __init__(self):
        self.now = 100000.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        (self.root / 'data').mkdir()
        self.settings = {'target_total': 50, 'max_loads_per_day': None,
                         'max_details_per_hour': None, 'max_submissions_per_day': None}
        (self.root / 'data/campaign.json').write_text(json.dumps(self.settings))
        (self.root / 'data/resume.pdf').write_bytes(b'%PDF-1.7\nFixture only')
        self.profile = {
            'candidate': {'name': 'Fixture Candidate', 'first_name': 'Fixture', 'last_name': 'Candidate',
                          'email': 'fixture@example.invalid', 'phone': '555-0100', 'location': 'Boston, MA',
                          'country': 'United States', 'work_authorization': 'authorized',
                          'requires_sponsorship': False, 'resume_path': 'data/resume.pdf'},
            'preferences': {'min_salary_usd': 1},
            'search': {'queries': ['fixture analyst']},
            'application_permissions': {},
            'application_instructions': [],
            'answers': {},
        }
        (self.root / 'profile.json').write_text(json.dumps(self.profile))
        for name, value in [('ROOT', self.root), ('CONFIG', self.root / 'data/campaign.json'),
                            ('PACE', self.root / 'data/ratelimit.json')]:
            self.stack.enter_context(patch.object(campaign, name, value))
        self.stack.enter_context(patch.object(campaign.db, 'DB_PATH', self.root / 'data/jobs.db'))
        self.clock = Clock()
        for name, value in [('time', self.clock.time), ('monotonic', self.clock.time),
                            ('sleep', self.clock.sleep)]:
            self.stack.enter_context(patch.object(campaign.time, name, value))
        self.con = campaign.connect()
        self.stack.enter_context(patch.object(campaign, 'process_table', return_value={}))
        self.addCleanup(self.con.close)

    def job(self, job_id='job', status='queued'):
        self.con.execute('''INSERT INTO jobs
            (id,source,source_id,url,title,company,status,first_seen,last_seen)
            VALUES(?,?,?,?,?,?,?,?,?)''', (job_id, 'fixture', job_id, 'https://example.invalid/' + job_id,
                                         'Analyst', 'Fixture Employer', status, self.clock.now, self.clock.now))
        self.con.commit()
        return job_id

    def snapshot(self, **values):
        runtime = {'state': 'working', 'heartbeat_at': self.clock.now,
                   'supervisor_pid': 123, 'worker_pid': 456,
                   'worker_started_at': self.clock.now, 'started_at': self.clock.now,
                   'last_exit_reason': None, 'restart_count': 0}
        runtime.update(values)
        campaign.atomic_json(self.root / 'data/campaign-runtime.json', runtime)
        return runtime

    def events(self):
        return [json.loads(line) for line in (self.root / 'logs/campaign/events.jsonl').read_text().splitlines()]

    def fake_worker(self, leader_exited=False):
        process = Mock(pid=456)
        process.returncode = 0 if leader_exited else None
        process.poll.side_effect = lambda: process.returncode
        process.wait.side_effect = lambda **kwargs: process.returncode
        alive = {'group': True}
        signals = []

        def killpg(pid, number):
            self.assertEqual(pid, 456)
            if number == 0:
                if not alive['group']:
                    raise ProcessLookupError
                return
            signals.append(number)
            if number == signal.SIGKILL:
                alive['group'] = False
                process.returncode = -signal.SIGKILL if not leader_exited else 0

        self.stack.enter_context(patch.object(campaign.os, 'killpg', side_effect=killpg))
        return process, signals, alive

    def test_zero_caps_deny_actions_without_crashing_status_or_claiming_a_reset_time(self):
        for field, kind, reason in [
            ('max_submissions_per_day', 'apply', 'daily_submission_limit'),
            ('max_loads_per_day', 'load', 'daily_page_limit'),
            ('max_details_per_hour', 'detail', 'hourly_detail_limit'),
        ]:
            for events in [[], [{'k': kind, 't': self.clock.now * 1000}]]:
                with self.subTest(field=field, events=events):
                    self.settings[field] = 0
                    campaign.atomic_json(campaign.CONFIG, self.settings)
                    campaign.atomic_json(campaign.PACE, {'events': events, 'lockedUntil': 0})
                    self.assertIsNone(campaign.status(self.con)['budget']['retry_at'])
                    self.assertEqual(campaign.gate(kind),
                                     {'allowed': False, 'reason': reason, 'retry_at': None})
                    self.assertEqual(campaign.pacing()['events'], events)
                    self.settings[field] = None

    def test_parent_deadline_kills_entire_group_and_reaps(self):
        process, signals, alive = self.fake_worker()
        runtime = self.snapshot()
        beat_times = []
        with patch.object(campaign, 'heartbeat', side_effect=lambda *a, **kw: beat_times.append(self.clock.now)):
            code, reason = campaign.supervise_worker(process, self.con, runtime, lambda: None, deadline_seconds=12)
        self.assertEqual(reason, 'worker_timeout')
        self.assertEqual(code, -signal.SIGKILL)
        self.assertEqual(signals, [signal.SIGTERM, signal.SIGKILL])
        self.assertFalse(alive['group'])
        process.wait.assert_called_once_with(timeout=campaign.TERMINATE_GRACE_SECONDS)
        self.assertLessEqual(max(b - a for a, b in zip(beat_times, beat_times[1:])), 5.25)
        self.assertEqual(self.events()[0]['event'], 'timeout')

    def test_exited_leader_does_not_leave_background_descendants(self):
        process, signals, alive = self.fake_worker(leader_exited=True)
        code, reason = campaign.supervise_worker(process, self.con, self.snapshot(), lambda: None)
        self.assertEqual((code, reason), (0, 'worker_exit'))
        self.assertEqual(signals, [signal.SIGTERM, signal.SIGKILL])
        self.assertFalse(alive['group'])

    def test_heartbeat_failure_cannot_skip_group_kill(self):
        process, signals, alive = self.fake_worker()
        with self.assertRaisesRegex(OSError, 'snapshot unavailable'):
            campaign.terminate_group(456, Mock(side_effect=OSError('snapshot unavailable')), process)
        self.assertFalse(alive['group'])
        self.assertEqual(signals[-1], signal.SIGKILL)

    def test_persistent_group_fails_closed_instead_of_overlapping(self):
        with patch.object(campaign.os, 'killpg'):
            with self.assertRaisesRegex(RuntimeError, 'refusing overlap'):
                campaign.terminate_group(456, lambda: None)

    def test_stop_request_kills_group_without_waiting_for_deadline(self):
        process, signals, alive = self.fake_worker()
        code, reason = campaign.supervise_worker(process, self.con, self.snapshot(), lambda: 'stop_file')
        self.assertEqual(reason, 'stop_file')
        self.assertFalse(alive['group'])
        self.assertEqual(signals[-1], signal.SIGKILL)
        self.assertLess(self.clock.now, 100000 + campaign.WORKER_DEADLINE_SECONDS)

    def test_stale_heartbeat_even_when_pid_is_alive(self):
        self.snapshot(heartbeat_at=self.clock.now - campaign.HEARTBEAT_STALE_SECONDS - 1)
        with patch.object(campaign, 'pid_alive', return_value=True):
            runtime = campaign.status(self.con)['runtime']
        self.assertEqual(runtime['state'], 'stale')
        self.assertEqual(runtime['heartbeat_age_seconds'], 21)

    def test_missing_pid_marks_fresh_heartbeat_stale(self):
        self.snapshot()
        with patch.object(campaign, 'pid_alive', return_value=False):
            self.assertEqual(campaign.status(self.con)['runtime']['state'], 'stale')

    def test_terminal_state_remains_stopped(self):
        self.snapshot(state='stopped', heartbeat_at=self.clock.now - 1000)
        with patch.object(campaign, 'pid_alive', return_value=False):
            self.assertEqual(campaign.status(self.con)['runtime']['state'], 'stopped')

    def test_recovery_is_manual_idempotent_and_not_automatically_retryable(self):
        job_id = self.job()
        campaign.reserve(self.con, job_id)
        campaign.recover(self.con)
        campaign.recover(self.con)
        self.assertEqual(campaign.confirmed(self.con), 0)
        self.assertEqual(self.con.execute('SELECT status FROM jobs').fetchone()[0], 'manual')
        self.assertEqual(self.con.execute('SELECT state FROM campaign_reservations').fetchone()[0], 'manual')
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM applications').fetchone()[0], 1)
        with self.assertRaisesRegex(ValueError, 'manual_review_or_explicit_requeue_required'):
            campaign.reserve(self.con, job_id)
        result = [row for row in self.events() if row['event'] == 'recorded_outcome']
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['result'], 'manual')
        self.assertNotIn('reason', result[0])
        self.assertNotIn('evidence', result[0])

    def requeue_data(self):
        return {'reason': 'Reviewed employer draft; password=SECRET',
                'evidence': 'PRIVATE employer draft is incomplete, with no submitted application',
                'submission_verified_absent': True}

    def test_reviewed_requeue_preserves_history_and_requires_a_new_reservation(self):
        other = self.job('other')
        campaign.reserve(self.con, other)
        campaign.record(self.con, {'job_id': other, 'result': 'failed', 'reason': 'Other attempt'})
        for outcome in ('manual', 'skipped', 'failed'):
            with self.subTest(outcome=outcome):
                job_id = self.job(outcome)
                campaign.reserve(self.con, job_id)
                campaign.record(self.con, {'job_id': job_id, 'result': outcome,
                                          'reason': 'Original outcome', 'evidence': 'Original evidence'})
                original = dict(self.con.execute('SELECT * FROM applications WHERE job_id=?',
                                                 (job_id,)).fetchone())
                data = self.requeue_data()
                self.assertEqual(campaign.requeue(self.con, job_id, data),
                                 {'requeued': True, 'job_id': job_id})
                history = self.con.execute('SELECT * FROM applications WHERE job_id=? ORDER BY id',
                                           (job_id,)).fetchall()
                self.assertEqual(dict(history[0]), original)
                self.assertEqual(len(history), 2)
                self.assertEqual(history[1]['result'], 'deferred')
                self.assertEqual(history[1]['run_id'], 'campaign-requeue')
                self.assertEqual(history[1]['reason'], data['reason'])
                self.assertEqual(json.loads(history[1]['evidence']), {
                    'submission_verified_absent': True, 'evidence': data['evidence']})
                self.assertEqual(self.con.execute('SELECT status FROM jobs WHERE id=?',
                                                  (job_id,)).fetchone()[0], 'queued')
                self.assertIsNone(self.con.execute('SELECT * FROM campaign_reservations WHERE job_id=?',
                                                  (job_id,)).fetchone())
                self.assertEqual(self.con.execute('SELECT state FROM campaign_reservations WHERE job_id=?',
                                                  (other,)).fetchone()[0], 'failed')
                with self.assertRaisesRegex(ValueError, 'application_requires_active_reservation'):
                    campaign.record(self.con, {'job_id': job_id, 'result': 'applied', 'evidence': 'Not a receipt'})
                self.assertTrue(campaign.reserve(self.con, job_id)['reserved'])
                self.assertEqual([dict(row) for row in self.con.execute(
                    'SELECT * FROM applications WHERE job_id=? ORDER BY id', (job_id,))],
                    [dict(row) for row in history])
        self.assertEqual(campaign.confirmed(self.con), 0)
        public = json.dumps(campaign.status(self.con)) + json.dumps(self.events())
        self.assertNotIn('SECRET', public)
        self.assertNotIn('PRIVATE', public)
        self.assertEqual([row['job_id'] for row in self.events() if row['event'] == 'requeued'],
                         ['manual', 'skipped', 'failed'])

    def test_reviewed_requeue_rejects_applied_identity_in_every_durable_location(self):
        for identity in ('exact', 'url', 'source_id'):
            for protection in ('history', 'job_status', 'reservation'):
                with self.subTest(identity=identity, protection=protection):
                    job_id = self.job(identity + '-' + protection, 'manual')
                    protected = job_id if identity == 'exact' else self.job(job_id + '-protected')
                    if identity == 'url':
                        self.con.execute('UPDATE jobs SET url=(SELECT url FROM jobs WHERE id=?) WHERE id=?',
                                         (protected, job_id))
                    elif identity == 'source_id':
                        self.con.execute('UPDATE jobs SET source_id=? WHERE id=?', (protected, job_id))
                    if protection == 'history':
                        self.con.execute('''INSERT INTO applications(job_id,attempted,result,evidence)
                            VALUES(?,?,'applied','Existing receipt')''', (protected, self.clock.now))
                    elif protection == 'job_status':
                        self.con.execute("UPDATE jobs SET status='applied' WHERE id=?", (protected,))
                    else:
                        self.con.execute("INSERT INTO campaign_reservations VALUES(?,?,'applied','Existing receipt')",
                                         (protected, self.clock.now))
                    self.con.commit()
                    before = list(self.con.iterdump())
                    with self.assertRaisesRegex(ValueError, 'already_sent_or_reserved'):
                        campaign.requeue(self.con, job_id, self.requeue_data())
                    self.assertEqual(list(self.con.iterdump()), before)
                    with self.assertRaisesRegex(ValueError, 'already_sent_or_reserved'):
                        campaign.reserve(self.con, job_id)

    def test_reviewed_requeue_rejects_active_equivalent_reservations(self):
        for identity in ('exact', 'url', 'source_id'):
            with self.subTest(identity=identity):
                job_id = self.job(identity, 'manual')
                protected = job_id if identity == 'exact' else self.job(identity + '-protected')
                self.con.execute("INSERT INTO campaign_reservations VALUES(?,?,'reserved',NULL)",
                                 (protected, self.clock.now))
                if identity == 'url':
                    self.con.execute('UPDATE jobs SET url=(SELECT url FROM jobs WHERE id=?) WHERE id=?',
                                     (protected, job_id))
                elif identity == 'source_id':
                    self.con.execute('UPDATE jobs SET source_id=? WHERE id=?', (protected, job_id))
                self.con.commit()
                before = list(self.con.iterdump())
                with self.assertRaisesRegex(ValueError, 'already_sent_or_reserved'):
                    campaign.requeue(self.con, job_id, self.requeue_data())
                self.assertEqual(list(self.con.iterdump()), before)
                with self.assertRaisesRegex(ValueError, 'already_sent_or_reserved'):
                    campaign.reserve(self.con, job_id)

    def test_reviewed_requeue_requires_explicit_attestation_reason_and_evidence(self):
        job_id = self.job(status='manual')
        invalid = [None, [], {}, {'submission_verified_absent': True}]
        for key in ('submission_verified_absent', 'reason', 'evidence'):
            data = self.requeue_data()
            del data[key]
            invalid.append(data)
            values = (False, 1, 'true', None) if key == 'submission_verified_absent' else ('', ' \n ', None, 1, {})
            for value in values:
                invalid.append(dict(self.requeue_data(), **{key: value}))
        for data in invalid:
            with self.subTest(data=data):
                with self.assertRaisesRegex(ValueError, 'requeue_requires_'):
                    campaign.requeue(self.con, job_id, data)
                self.assertFalse(self.con.in_transaction)
        self.assertEqual(self.con.execute('SELECT status FROM jobs').fetchone()[0], 'manual')
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM applications').fetchone()[0], 0)

    def test_finished_jobs_without_reservations_cannot_bypass_reviewed_requeue(self):
        for state in ('manual', 'skipped', 'failed'):
            with self.subTest(state=state):
                job_id = self.job(state, state)
                with self.assertRaisesRegex(ValueError, 'manual_review_or_explicit_requeue_required'):
                    campaign.reserve(self.con, job_id)
                campaign.requeue(self.con, job_id, self.requeue_data())
                self.assertTrue(campaign.reserve(self.con, job_id)['reserved'])

    def test_reviewed_requeue_rejects_unknown_invalid_states_and_cross_connection_replay(self):
        with self.assertRaisesRegex(ValueError, 'unknown_job'):
            campaign.requeue(self.con, 'missing', self.requeue_data())
        for state in ('discovered', 'scored', 'queued', 'applying'):
            with self.subTest(state=state):
                job_id = self.job(state, state)
                with self.assertRaisesRegex(ValueError, 'requeue_requires_finished_non_applied_job'):
                    campaign.requeue(self.con, job_id, self.requeue_data())
        job_id = self.job('reviewed', 'manual')
        competitor = campaign.connect()
        self.addCleanup(competitor.close)
        campaign.requeue(self.con, job_id, self.requeue_data())
        with self.assertRaisesRegex(ValueError, 'requeue_requires_finished_non_applied_job'):
            campaign.requeue(competitor, job_id, self.requeue_data())
        campaign.reserve(competitor, job_id)
        with self.assertRaisesRegex(ValueError, 'already_sent_or_reserved'):
            campaign.requeue(self.con, job_id, self.requeue_data())
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM applications').fetchone()[0], 1)
        self.assertEqual(campaign.confirmed(self.con), 0)

    def test_reviewed_requeue_rolls_back_audit_and_reservation_cleanup_together(self):
        job_id = self.job()
        campaign.reserve(self.con, job_id)
        campaign.record(self.con, {'job_id': job_id, 'result': 'manual', 'reason': 'Original'})
        self.con.execute('''CREATE TRIGGER reject_requeue BEFORE UPDATE OF status ON jobs
            WHEN NEW.status='queued' BEGIN SELECT RAISE(ABORT, 'fixture write failure'); END''')
        self.con.commit()
        before = list(self.con.iterdump())
        with self.assertRaisesRegex(campaign.db.sqlite3.IntegrityError, 'fixture write failure'):
            campaign.requeue(self.con, job_id, self.requeue_data())
        self.assertEqual(list(self.con.iterdump()), before)
        self.assertFalse(any(row['event'] == 'requeued' for row in self.events()))

    def test_reviewed_requeue_cli_uses_existing_json_success_and_error_formats(self):
        job_id = self.job(status='manual')
        with patch.object(campaign.sys, 'argv', ['campaign.py', 'requeue', job_id]), \
                patch.object(campaign.sys, 'stdin', io.StringIO(json.dumps(self.requeue_data()))), \
                patch.object(campaign.sys, 'stdout', io.StringIO()) as stdout, \
                patch.object(campaign.sys, 'stderr', io.StringIO()) as stderr, \
                patch.object(campaign, 'connect', return_value=self.con):
            self.assertEqual(campaign.main(), 0)
        self.assertEqual(stdout.getvalue(), '{"requeued": true, "job_id": "job"}\n')
        self.assertEqual(stderr.getvalue(), '')
        with patch.object(campaign.sys, 'argv', ['campaign.py', 'requeue', job_id]), \
                patch.object(campaign.sys, 'stdin', io.StringIO(json.dumps(self.requeue_data()))), \
                patch.object(campaign.sys, 'stdout', io.StringIO()) as stdout, \
                patch.object(campaign.sys, 'stderr', io.StringIO()) as stderr, \
                patch.object(campaign, 'connect', return_value=self.con):
            self.assertEqual(campaign.main(), 2)
        self.assertEqual(stdout.getvalue(), '')
        self.assertEqual(stderr.getvalue(), '{"error": "requeue_requires_finished_non_applied_job"}\n')

    def reconcile_data(self):
        return {'reason': 'Reviewed historical receipt; password=SECRET',
                'evidence': 'PRIVATE employer receipt confirms application already submitted',
                'employer_confirmation_verified': True}

    def test_reconcile_records_real_receipts_without_new_submission_or_target_permission(self):
        originals = {}
        for outcome in ('manual', 'failed'):
            job_id = self.job(outcome)
            campaign.reserve(self.con, job_id)
            campaign.record(self.con, {'job_id': job_id, 'result': outcome,
                                      'reason': 'Original outcome', 'evidence': 'Original evidence'})
            originals[job_id] = dict(self.con.execute('SELECT * FROM applications WHERE job_id=?',
                                                     (job_id,)).fetchone())
        self.settings.update(target_total=0, max_submissions_per_day=0)
        campaign.CONFIG.write_text(json.dumps(self.settings))
        for count, job_id in enumerate(('manual', 'failed'), 1):
            with self.subTest(job_id=job_id):
                data = self.reconcile_data()
                with patch.object(campaign, 'gate', side_effect=AssertionError('No new submission')):
                    value = campaign.reconcile(self.con, job_id, data)
                self.assertEqual(value, {'reconciled': True, 'job_id': job_id, 'already_recorded': False,
                                         'confirmed': count, 'target': 0})
                history = self.con.execute('SELECT * FROM applications WHERE job_id=? ORDER BY id',
                                           (job_id,)).fetchall()
                self.assertEqual(len(history), 2)
                self.assertEqual(dict(history[0]), originals[job_id])
                self.assertEqual(history[1]['result'], 'applied')
                self.assertEqual(history[1]['run_id'], 'campaign-reconcile')
                self.assertEqual(history[1]['reason'], data['reason'])
                self.assertEqual(json.loads(history[1]['evidence']), {
                    'employer_confirmation_verified': True, 'evidence': data['evidence']})
                self.assertEqual(self.con.execute('SELECT status FROM jobs WHERE id=?',
                                                  (job_id,)).fetchone()[0], 'applied')
                self.assertEqual(self.con.execute('SELECT state FROM campaign_reservations WHERE job_id=?',
                                                  (job_id,)).fetchone()[0], 'applied')
        public = json.dumps(campaign.status(self.con)) + json.dumps(self.events())
        self.assertNotIn('PRIVATE', public)
        self.assertNotIn('SECRET', public)
        self.assertEqual(len([row for row in self.events() if row['event'] == 'reconciled']), 2)

    def test_reconcile_replay_is_idempotent_across_connections(self):
        job_id = self.job()
        campaign.reserve(self.con, job_id)
        campaign.recover(self.con)
        campaign.reconcile(self.con, job_id, self.reconcile_data())
        before = list(self.con.iterdump())
        competitor = campaign.connect()
        self.addCleanup(competitor.close)
        data = dict(self.reconcile_data(), evidence='Do not overwrite the first receipt')
        value = campaign.reconcile(competitor, job_id, data)
        self.assertEqual(value, {'reconciled': True, 'job_id': job_id, 'already_recorded': True,
                                 'confirmed': 1, 'target': 50})
        self.assertEqual(list(self.con.iterdump()), before)
        self.assertEqual(len([row for row in self.events() if row['event'] == 'reconciled']), 1)
        with self.assertRaisesRegex(ValueError, 'application_requires_active_reservation'):
            campaign.record(self.con, {'job_id': job_id, 'result': 'applied', 'evidence': 'Replay'})

    def test_reconcile_rejects_active_same_job_and_applied_or_active_equivalent(self):
        for identity in ('exact', 'url', 'source_id'):
            protections = ('reserved',) if identity == 'exact' else (
                'reserved', 'applied_history', 'applied_status', 'applied_reservation')
            for protection in protections:
                with self.subTest(identity=identity, protection=protection):
                    job_id = self.job(identity + '-' + protection)
                    campaign.reserve(self.con, job_id)
                    campaign.record(self.con, {'job_id': job_id, 'result': 'manual'})
                    protected = job_id if identity == 'exact' else self.job(job_id + '-protected')
                    if identity == 'url':
                        self.con.execute('UPDATE jobs SET url=(SELECT url FROM jobs WHERE id=?) WHERE id=?',
                                         (protected, job_id))
                    elif identity == 'source_id':
                        self.con.execute('UPDATE jobs SET source_id=? WHERE id=?', (protected, job_id))
                    if protection == 'applied_history':
                        self.con.execute('''INSERT INTO applications(job_id,attempted,result,evidence)
                            VALUES(?,?,'applied','Existing receipt')''', (protected, self.clock.now))
                    elif protection == 'applied_status':
                        self.con.execute("UPDATE jobs SET status='applied' WHERE id=?", (protected,))
                    elif identity == 'exact':
                        self.con.execute("UPDATE campaign_reservations SET state='reserved' WHERE job_id=?",
                                         (protected,))
                    else:
                        state = 'reserved' if protection == 'reserved' else 'applied'
                        self.con.execute('INSERT INTO campaign_reservations VALUES(?,?,?,NULL)',
                                         (protected, self.clock.now, state))
                    self.con.commit()
                    before = list(self.con.iterdump())
                    with self.assertRaisesRegex(ValueError, 'already_sent_or_reserved'):
                        campaign.reconcile(self.con, job_id, self.reconcile_data())
                    self.assertEqual(list(self.con.iterdump()), before)

    def test_reconcile_requires_explicit_employer_attestation_reason_and_evidence(self):
        job_id = self.job()
        campaign.reserve(self.con, job_id)
        campaign.recover(self.con)
        invalid = [None, [], {}]
        for key in ('employer_confirmation_verified', 'reason', 'evidence'):
            data = self.reconcile_data()
            del data[key]
            invalid.append(data)
            values = (False, 1, 'true', None) if key == 'employer_confirmation_verified' else ('', ' \n ', None, 1, {})
            for value in values:
                invalid.append(dict(self.reconcile_data(), **{key: value}))
        before = list(self.con.iterdump())
        for data in invalid:
            with self.subTest(data=data):
                with self.assertRaisesRegex(ValueError, 'reconcile_requires_'):
                    campaign.reconcile(self.con, job_id, data)
                self.assertFalse(self.con.in_transaction)
                self.assertEqual(list(self.con.iterdump()), before)

    def test_reconcile_rejects_unknown_invalid_states_and_unattempted_manual_notes(self):
        with self.assertRaisesRegex(ValueError, 'unknown_job'):
            campaign.reconcile(self.con, 'missing', self.reconcile_data())
        for state in ('discovered', 'scored', 'queued', 'applying', 'skipped'):
            with self.subTest(state=state):
                job_id = self.job(state, state)
                with self.assertRaisesRegex(ValueError, 'reconcile_requires_manual_or_failed_job'):
                    campaign.reconcile(self.con, job_id, self.reconcile_data())
        for state in ('manual', 'failed'):
            with self.subTest(state=state):
                job_id = self.job(state)
                campaign.record(self.con, {'job_id': job_id, 'result': state, 'reason': 'Screening note only'})
                with self.assertRaisesRegex(ValueError, 'reconcile_requires_previous_reserved_attempt'):
                    campaign.reconcile(self.con, job_id, self.reconcile_data())
        self.assertEqual(campaign.confirmed(self.con), 0)

    def test_reconcile_rolls_back_receipt_and_reservation_together(self):
        job_id = self.job()
        campaign.reserve(self.con, job_id)
        campaign.recover(self.con)
        self.con.execute('''CREATE TRIGGER reject_reconcile BEFORE UPDATE OF status ON jobs
            WHEN NEW.status='applied' BEGIN SELECT RAISE(ABORT, 'fixture write failure'); END''')
        self.con.commit()
        before = list(self.con.iterdump())
        with self.assertRaisesRegex(campaign.db.sqlite3.IntegrityError, 'fixture write failure'):
            campaign.reconcile(self.con, job_id, self.reconcile_data())
        self.assertEqual(list(self.con.iterdump()), before)
        self.assertFalse(any(row['event'] == 'reconciled' for row in self.events()))

    def test_reconcile_cli_uses_existing_json_format(self):
        job_id = self.job()
        campaign.reserve(self.con, job_id)
        campaign.recover(self.con)
        with patch.object(campaign.sys, 'argv', ['campaign.py', 'reconcile', job_id]), \
                patch.object(campaign.sys, 'stdin', io.StringIO(json.dumps(self.reconcile_data()))), \
                patch.object(campaign.sys, 'stdout', io.StringIO()) as stdout, \
                patch.object(campaign.sys, 'stderr', io.StringIO()) as stderr, \
                patch.object(campaign, 'connect', return_value=self.con):
            self.assertEqual(campaign.main(), 0)
        self.assertEqual(stdout.getvalue(), '{"reconciled": true, "job_id": "job", "already_recorded": false, "confirmed": 1, "target": 50}\n')
        self.assertEqual(stderr.getvalue(), '')

    def test_progress_uses_persisted_actions_not_liveness(self):
        self.snapshot(started_at=self.clock.now - 1000)
        with patch.object(campaign, 'pid_alive', return_value=True):
            before = campaign.status(self.con)['runtime']
            self.assertTrue(before['no_progress'])
            self.assertIsNone(before['last_progress_at'])
            campaign.PACE.write_text(json.dumps({'events': [{'t': self.clock.now * 1000, 'k': 'detail'}],
                                                 'last': self.clock.now * 1000}))
            after = campaign.status(self.con)['runtime']
        self.assertEqual(after['state'], 'working')
        self.assertEqual(after['last_progress_at'], self.clock.now)
        self.assertEqual(after['progress_age_seconds'], 0)
        self.assertFalse(after['no_progress'])

    def test_status_contract_and_telemetry_redaction(self):
        job_id = self.job()
        campaign.record(self.con, {'job_id': job_id, 'result': 'manual',
                                  'reason': 'Employer verification required; password=SECRET', 'evidence': 'SECRET evidence'})
        campaign.PACE.write_text(json.dumps({'events': [], 'lockedUntil': (self.clock.now + 60) * 1000,
                                             'lockReason': 'SECRET platform details'}))
        value = campaign.status(self.con)
        self.assertTrue({'confirmed', 'target', 'remaining', 'complete', 'budget', 'outcomes',
                         'runtime', 'activity', 'latest_actions', 'counts_by_job_status'} <= value.keys())
        self.assertEqual(value['latest_actions'][0]['job_id'], job_id)
        self.assertEqual(set(value['latest_actions'][0]), {'at', 'job_id', 'company', 'title', 'result', 'reason'})
        self.assertEqual(value['latest_actions'][0]['reason'], 'Employer verification required; password=[redacted]')
        self.assertEqual(value['counts_by_job_status'], {'manual': 1})
        self.assertNotIn('SECRET', json.dumps(value))
        self.assertNotIn('SECRET', json.dumps(self.events()))

    def test_pending_reservations_protect_exact_target_and_null_caps(self):
        self.settings['target_total'] = 1
        campaign.CONFIG.write_text(json.dumps(self.settings))
        campaign.reserve(self.con, self.job('first'))
        with self.assertRaisesRegex(ValueError, 'target_reached_or_reserved'):
            campaign.reserve(self.con, self.job('second'))
        campaign.record(self.con, {'job_id': 'first', 'result': 'applied', 'evidence': 'fixture receipt'})
        self.assertEqual(campaign.status(self.con)['confirmed'], 1)
        self.assertTrue(campaign.status(self.con)['complete'])
        self.assertIsNone(campaign.budget()['retry_at'])

    def test_unlimited_target_reserves_past_fifty_and_preserves_receipts_and_duplicates(self):
        self.settings['target_total'] = None
        campaign.CONFIG.write_text(json.dumps(self.settings))
        for index in range(51):
            job_id = self.job('confirmed-' + str(index))
            reserved = campaign.reserve(self.con, job_id)
            self.assertIsNone(reserved['target'])
            value = campaign.record(self.con, {'job_id': job_id, 'result': 'applied',
                                               'evidence': 'Fixture employer receipt'})
            self.assertIsNone(value['target'])
        self.assertEqual(campaign.confirmed(self.con), 51)
        for job_id in ('pending-first', 'pending-second'):
            campaign.reserve(self.con, self.job(job_id))
        for job_id in ('confirmed-50', 'pending-first'):
            with self.subTest(job_id=job_id), self.assertRaisesRegex(ValueError, 'already_sent_or_reserved'):
                campaign.reserve(self.con, job_id)
        with self.assertRaisesRegex(ValueError, 'confirmed_application_requires_evidence'):
            campaign.record(self.con, {'job_id': 'pending-first', 'result': 'applied'})
        with self.assertRaisesRegex(ValueError, 'application_requires_active_reservation'):
            campaign.record(self.con, {'job_id': self.job('unreserved'), 'result': 'applied',
                                      'evidence': 'Not sufficient without reservation'})
        campaign.recover(self.con)
        reconciled = campaign.reconcile(self.con, 'pending-first', self.reconcile_data())
        self.assertIsNone(reconciled['target'])
        self.assertEqual(reconciled['confirmed'], 52)
        snapshot = campaign.status(self.con)
        self.assertEqual(snapshot['confirmed'], 52)
        self.assertIsNone(snapshot['target'])
        self.assertIsNone(snapshot['remaining'])
        self.assertFalse(snapshot['complete'])

    def test_unlimited_target_still_enforces_configured_daily_submission_cap(self):
        self.settings.update(target_total=None, max_submissions_per_day=1)
        campaign.CONFIG.write_text(json.dumps(self.settings))
        campaign.reserve(self.con, self.job('first'))
        with self.assertRaisesRegex(ValueError, 'daily_submission_limit'):
            campaign.reserve(self.con, self.job('second'))

    def test_monitor_preserves_unlimited_status_fields(self):
        import campaign_monitor
        self.settings['target_total'] = None
        campaign.CONFIG.write_text(json.dumps(self.settings))
        snapshot = campaign_monitor.read_status()
        self.assertEqual(snapshot['confirmed'], 0)
        self.assertIsNone(snapshot['target'])
        self.assertIsNone(snapshot['remaining'])
        self.assertFalse(snapshot['complete'])

    def test_supervisor_continues_unlimited_rounds_but_finishes_a_reached_finite_target(self):
        self.worker_fixture()
        campaign.reserve(self.con, self.job('confirmed'))
        campaign.record(self.con, {'job_id': 'confirmed', 'result': 'applied',
                                  'evidence': 'Fixture employer receipt'})
        for target in (None, 1):
            with self.subTest(target=target):
                (self.root / 'data/campaign.stop').unlink(missing_ok=True)
                self.settings['target_total'] = target
                campaign.CONFIG.write_text(json.dumps(self.settings))
                rounds = []

                def supervise(*args, **kwargs):
                    rounds.append(campaign.status(self.con))
                    if len(rounds) == 2:
                        (self.root / 'data/campaign.stop').touch()
                    return 0, 'worker_exit'

                with patch.object(campaign.shutil, 'which', return_value='/fixture/omp'), \
                        patch.object(campaign, 'relay_connected', return_value=True), \
                        patch.object(campaign, 'recover_worker', return_value=False), \
                        patch.object(campaign, 'keep_awake', return_value=None), \
                        patch.object(campaign.subprocess, 'Popen', return_value=Mock(pid=456)) as spawn, \
                        patch.object(campaign, 'supervise_worker', side_effect=supervise):
                    campaign.loop()
                self.assertEqual(spawn.call_count, 2 if target is None else 0)
                if target is None:
                    self.assertTrue(all(not row['complete'] and row['remaining'] is None for row in rounds))
                    self.assertEqual(campaign.read_runtime()['state'], 'stopped')
                    self.assertEqual(campaign.read_runtime()['last_exit_reason'], 'stop_file')
                else:
                    self.assertEqual(campaign.read_runtime()['state'], 'complete')
                    self.assertEqual(campaign.read_runtime()['last_exit_reason'], 'target_reached')

    def test_singleton_lock_fails_immediately(self):
        path = self.root / 'data/campaign-worker.lock'
        with campaign.locked(path):
            with self.assertRaisesRegex(RuntimeError, 'already running'):
                with campaign.locked(path, blocking=False):
                    self.fail('duplicate supervisor acquired singleton lock')

    def test_missing_relay_waits_and_heartbeats_without_spawning(self):
        starts = []
        beats = []
        original_heartbeat = campaign.heartbeat

        def beat(con, runtime, **changes):
            original_heartbeat(con, runtime, **changes)
            beats.append((self.clock.now, runtime['state']))
            if sum(state == 'waiting_relay' for _, state in beats) == 3:
                (self.root / 'data/campaign.stop').touch()

        with patch.object(campaign.shutil, 'which', return_value='/fixture/omp'), \
                patch.object(campaign, 'relay_connected', return_value=False), \
                patch.object(campaign, 'heartbeat', side_effect=beat), \
                patch.object(campaign.subprocess, 'Popen', side_effect=lambda *a, **kw: starts.append(a)):
            campaign.loop()
        self.assertEqual(starts, [])
        waits = [at for at, state in beats if state == 'waiting_relay']
        self.assertGreaterEqual(len(waits), 3)
        self.assertLessEqual(max(b - a for a, b in zip(waits, waits[1:])), 5)
        self.assertEqual(campaign.read_runtime()['state'], 'stopped')

    def test_orphan_descendants_are_cleaned_when_leader_is_already_gone(self):
        process, signals, alive = self.fake_worker(leader_exited=True)
        with patch.object(campaign, 'pid_alive', return_value=False), \
                patch.object(campaign.subprocess, 'run') as inspect:
            self.assertTrue(campaign.recover_worker({'worker_pid': 456}, lambda: None))
        inspect.assert_not_called()
        self.assertFalse(alive['group'])
        self.assertEqual(signals[-1], signal.SIGKILL)

    def test_previous_worker_pid_reuse_fails_closed(self):
        with patch.object(campaign, 'group_alive', return_value=True), \
                patch.object(campaign, 'pid_alive', return_value=True), \
                patch.object(campaign.subprocess, 'run', return_value=Mock(stdout='Thu Jan 01 00:00:00 1970')), \
                patch.object(campaign.os, 'getpgid', return_value=456), \
                patch.object(campaign.os, 'killpg') as kill:
            with self.assertRaisesRegex(RuntimeError, 'identity does not match'):
                campaign.recover_worker({'worker_pid': 456, 'worker_started_at': self.clock.now}, lambda: None)
        kill.assert_not_called()

    def test_platform_wait_rereads_changed_caps_within_one_heartbeat(self):
        self.settings['max_loads_per_day'] = 1
        campaign.CONFIG.write_text(json.dumps(self.settings))
        campaign.PACE.write_text(json.dumps({'events': [{'t': self.clock.now * 1000, 'k': 'load'}]}))
        states = []
        original_heartbeat = campaign.heartbeat

        def beat(con, runtime, **changes):
            original_heartbeat(con, runtime, **changes)
            states.append((self.clock.now, runtime['state']))
            if runtime['state'] == 'waiting_platform':
                self.settings['max_loads_per_day'] = None
                campaign.CONFIG.write_text(json.dumps(self.settings))
            if runtime['state'] == 'waiting_relay':
                (self.root / 'data/campaign.stop').touch()

        with patch.object(campaign.shutil, 'which', return_value='/fixture/omp'), \
                patch.object(campaign, 'relay_connected', return_value=False), \
                patch.object(campaign, 'heartbeat', side_effect=beat), \
                patch.object(campaign.subprocess, 'Popen') as spawn:
            campaign.loop()
        spawn.assert_not_called()
        platform_at = next(at for at, state in states if state == 'waiting_platform')
        relay_at = next(at for at, state in states if state == 'waiting_relay')
        self.assertLessEqual(relay_at - platform_at, campaign.HEARTBEAT_SECONDS)

    def test_group_permission_denied_with_no_exact_member_is_absent(self):
        with patch.object(campaign.os, 'killpg', side_effect=PermissionError), \
                patch.object(campaign.subprocess, 'run', return_value=Mock(stdout=' 1456\n4560\n')) as inspect:
            self.assertFalse(campaign.group_alive(456))
        inspect.assert_called_once_with(['ps', '-axo', 'pgid='], capture_output=True,
                                        text=True, check=True, timeout=3)

    def test_group_permission_denied_with_exact_member_is_alive(self):
        with patch.object(campaign.os, 'killpg', side_effect=PermissionError), \
                patch.object(campaign.subprocess, 'run', return_value=Mock(stdout=' 1456\n 456\n')) as inspect:
            self.assertTrue(campaign.group_alive(456))
        inspect.assert_called_once()

    def test_group_permission_denied_probe_errors_fail_closed(self):
        errors = [campaign.subprocess.CalledProcessError(1, ['ps']),
                  campaign.subprocess.TimeoutExpired(['ps'], 3), OSError('ps unavailable')]
        for error in errors:
            with self.subTest(error=type(error).__name__), \
                    patch.object(campaign.os, 'killpg', side_effect=PermissionError), \
                    patch.object(campaign.subprocess, 'run', side_effect=error):
                self.assertTrue(campaign.group_alive(456))
        with patch.object(campaign.os, 'killpg', side_effect=PermissionError), \
                patch.object(campaign.subprocess, 'run', return_value=Mock(stdout='unparseable')):
            self.assertTrue(campaign.group_alive(456))


    def test_detached_descendant_group_is_retained_after_leader_exits(self):
        process = Mock(pid=456)
        process.returncode = None
        process.poll.side_effect = lambda: process.returncode
        process.wait.side_effect = lambda **kwargs: process.returncode
        alive = {456, 789}
        signals = []
        captured_before_term = []

        def topology():
            # The eval executor independently sessioned itself; Chrome is not
            # in this worker tree and must never be signalled.
            table = {999: (1, 999, 'chrome-start')}
            if 456 in alive:
                table[456] = (123, 456, 'worker-start')
            if 789 in alive:
                table[789] = (456 if 456 in alive else 1, 789, 'eval-start')
                table[790] = (789, 789, 'eval-child-start')
            captured_before_term.append(not signals)
            return table

        def killpg(pid, number):
            self.assertIn(pid, {456, 789})
            if number == 0:
                if pid not in alive:
                    raise ProcessLookupError
                return
            signals.append((pid, number))
            if pid == 456 and number == signal.SIGTERM:
                alive.remove(456)
                process.returncode = -signal.SIGTERM
            if pid == 789 and number == signal.SIGKILL:
                alive.remove(789)

        with patch.object(campaign, 'process_table', side_effect=topology), \
                patch.object(campaign.os, 'killpg', side_effect=killpg):
            tree = campaign.WorkerTree(456)
            tree.capture()  # Supervision observes the tree before normal leader exit.
            alive.remove(456)
            process.returncode = 0
            campaign.terminate_group(456, lambda: None, process, tree)
        self.assertFalse(alive)
        self.assertIn((789, signal.SIGTERM), signals)
        self.assertIn((789, signal.SIGKILL), signals)
        self.assertTrue(captured_before_term[0])
        self.assertNotIn(999, tree.groups)

    def test_descendant_group_identity_reuse_is_never_signalled(self):
        tree = campaign.WorkerTree(456, {
            'processes': {'789': 'old-eval-start'},
            'groups': {'789': 'old-eval-start'}})
        with patch.object(campaign, 'process_table', return_value={789: (1, 789, 'unrelated-start')}), \
                patch.object(campaign, 'group_alive', return_value=False), \
                patch.object(campaign.os, 'killpg') as kill:
            with self.assertRaisesRegex(RuntimeError, 'identity changed'):
                campaign.terminate_group(456, lambda: None, tree=tree)
        kill.assert_not_called()

    def test_process_tree_probe_failure_fails_closed(self):
        with patch.object(campaign, 'process_table', side_effect=RuntimeError('tree unavailable')), \
                patch.object(campaign, 'group_alive', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'tree unavailable'):
                campaign.terminate_group(456, lambda: None)


    def test_descendant_cannot_confer_ownership_of_external_group(self):
        table = {456: (123, 456, 'worker-start'),
                 789: (456, 999, 'eval-start'),
                 999: (1, 999, 'chrome-start')}
        tree = campaign.WorkerTree(456)
        with patch.object(campaign, 'process_table', return_value=table):
            with self.assertRaisesRegex(RuntimeError, 'ownership is uncertain'):
                tree.capture()
        self.assertNotIn(999, tree.groups)

    def activity_worker(self):
        runtime = self.snapshot(worker_run_id='fixture-run')
        started = campaign.time.strftime('%a %b %d %H:%M:%S %Y', campaign.time.localtime(self.clock.now))
        self.stack.enter_context(patch.dict(campaign.os.environ, {'CAMPAIGN_RUN_ID': 'fixture-run'}))
        self.stack.enter_context(patch.object(campaign, 'pid_alive', return_value=True))
        self.stack.enter_context(patch.object(campaign.os, 'getpid', return_value=789))
        self.stack.enter_context(patch.object(campaign, 'process_table', return_value={
            456: (123, 456, started), 789: (456, 789, started)}))
        return runtime

    def test_activity_accepts_all_stages_and_resolves_job_labels(self):
        self.activity_worker()
        self.job()
        expected = {'stage', 'job_id', 'company', 'title', 'reported_at', 'stage_started_at',
                    'stale', 'action', 'action_label', 'page', 'pages'}
        for stage in sorted(campaign.ACTIVITY_STAGES):
            with self.subTest(stage=stage):
                data = {'stage': stage}
                if stage not in ('searching', 'idle'):
                    data['job_id'] = 'job'
                value = campaign.report_activity(self.con, data)['activity']
                self.assertEqual(set(value), expected)
                self.assertEqual(value['stage'], stage)
                self.assertEqual(value['company'], 'Fixture Employer' if data.get('job_id') else None)
                self.assertEqual(value['title'], 'Analyst' if data.get('job_id') else None)
                self.assertFalse(value['stale'])
                self.assertIsNone(value['action'])
                self.assertEqual(value['action_label'], campaign.ACTIVITY_STAGE_LABELS[stage])
        self.assertNotIn('Fixture Employer', json.dumps(self.events()))
        self.assertNotIn('Analyst', json.dumps(self.events()))
        self.assertTrue(all(set(item) <= {'at', 'event', 'stage', 'job_id', 'run_id', 'action'}
                            for item in self.events()))

    def test_activity_rejects_unknown_job_invalid_stage_and_arbitrary_text(self):
        self.activity_worker()
        self.job()
        invalid = [
            ({'stage': 'filling', 'job_id': 'missing'}, 'unknown_job'),
            ({'stage': 'finished', 'job_id': 'job'}, 'invalid_activity_stage'),
            ({'stage': 'filling'}, 'activity_requires_job_id'),
            ({'stage': 'filling', 'job_id': 'job', 'company': 'SECRET'}, 'invalid_activity'),
            ({'stage': 'filling', 'job_id': 'job', 'action': 'SECRET question'}, 'invalid_activity_action'),
            ({'stage': []}, 'invalid_activity_stage'),
            ({'stage': 'filling', 'job_id': ['job']}, 'invalid_activity_job_id'),
        ]
        for data, error in invalid:
            with self.subTest(data=data), self.assertRaisesRegex(ValueError, error):
                campaign.report_activity(self.con, data)
        self.assertIsNone(campaign.read_activity())
        self.assertFalse((self.root / 'logs/campaign/events.jsonl').exists())

    def test_activity_actions_pages_and_stage_clock(self):
        runtime = self.activity_worker()
        self.job()
        first = campaign.report_activity(self.con, {'stage': 'filling', 'job_id': 'job',
                                                    'action': 'filling_contact', 'page': 1, 'pages': 4})['activity']
        self.clock.now += 8
        updated = campaign.report_activity(self.con, {'stage': 'filling', 'job_id': 'job',
                                                      'action': 'filling_work_history', 'page': 2, 'pages': 4})['activity']
        self.assertEqual(updated['stage_started_at'], first['stage_started_at'])
        self.assertEqual(updated['reported_at'], self.clock.now)
        self.assertEqual(updated['action_label'], 'Filling work history')
        self.assertEqual((updated['page'], updated['pages']), (2, 4))
        self.assertEqual(campaign.status(self.con)['activity'], updated)
        self.clock.now += 1
        changed = campaign.report_activity(self.con, {'stage': 'reviewing', 'job_id': 'job'})['activity']
        self.assertEqual(changed['stage_started_at'], self.clock.now)
        self.assertIsNone(changed['page'])
        self.assertIsNone(changed['pages'])
        self.job('other')
        self.clock.now += 1
        other = campaign.report_activity(self.con, {'stage': 'reviewing', 'job_id': 'other'})['activity']
        self.assertEqual(other['stage_started_at'], self.clock.now)
        for action, label in campaign.ACTIVITY_ACTIONS.items():
            value = campaign.report_activity(self.con, {'stage': 'reviewing', 'job_id': 'other',
                                                        'action': action})['activity']
            self.assertEqual(value['action_label'], label)
        self.clock.now += 1
        campaign.heartbeat(self.con, runtime, worker_run_id='next-run')
        with patch.dict(campaign.os.environ, {'CAMPAIGN_RUN_ID': 'next-run'}):
            value = campaign.report_activity(self.con, {'stage': 'reviewing', 'job_id': 'other'})['activity']
        self.assertEqual(value['stage_started_at'], self.clock.now)

    def test_activity_rejects_invalid_pages(self):
        self.activity_worker()
        self.job()
        for values in ({'page': 0}, {'pages': -1}, {'page': True}, {'pages': 1.5},
                       {'page': '2'}, {'page': 3, 'pages': 2}):
            with self.subTest(values=values), self.assertRaisesRegex(ValueError, 'invalid_activity_page'):
                campaign.report_activity(self.con, {'stage': 'filling', 'job_id': 'job', **values})
        for values in ({'page': 2}, {'pages': 4}, {'page': None, 'pages': None}):
            with self.subTest(values=values):
                value = campaign.report_activity(self.con, {'stage': 'filling', 'job_id': 'job', **values})['activity']
                self.assertEqual(value['page'], values.get('page'))
                self.assertEqual(value['pages'], values.get('pages'))

    def test_activity_rejects_foreign_stale_and_unsupervised_reports(self):
        runtime = self.activity_worker()
        data = {'stage': 'searching', 'action': 'searching_jobs'}
        for run_id in ('foreign-run', ''):
            with patch.dict(campaign.os.environ, {'CAMPAIGN_RUN_ID': run_id}), \
                    self.assertRaisesRegex(ValueError, 'current_worker_run'):
                campaign.report_activity(self.con, data)
        with patch.object(campaign, 'process_table', return_value={789: (1, 789, 'outside')}), \
                self.assertRaisesRegex(ValueError, 'supervised_worker'):
            campaign.report_activity(self.con, data)
        with patch.object(campaign, 'process_table', return_value={
                456: (123, 456, 'Thu Jan 01 00:00:00 1970'), 789: (456, 789, 'outside')}), \
                self.assertRaisesRegex(ValueError, 'supervised_worker'):
            campaign.report_activity(self.con, data)
        self.clock.now += campaign.HEARTBEAT_STALE_SECONDS + 1
        with self.assertRaisesRegex(ValueError, 'current_worker_run'):
            campaign.report_activity(self.con, data)
        campaign.heartbeat(self.con, runtime, state='between_rounds')
        with self.assertRaisesRegex(ValueError, 'current_worker_run'):
            campaign.report_activity(self.con, data)
        self.assertIsNone(campaign.read_activity())

    def test_activity_staleness_age_and_supervisor_health(self):
        runtime = self.activity_worker()
        self.job()
        campaign.report_activity(self.con, {'stage': 'filling', 'job_id': 'job'})
        self.clock.now += campaign.ACTIVITY_STALE_SECONDS
        campaign.heartbeat(self.con, runtime)
        self.assertFalse(campaign.status(self.con)['activity']['stale'])
        self.clock.now += 1
        campaign.heartbeat(self.con, runtime)
        self.assertTrue(campaign.status(self.con)['activity']['stale'])
        campaign.report_activity(self.con, {'stage': 'filling', 'job_id': 'job'})
        with patch.object(campaign, 'pid_alive', side_effect=lambda pid: pid == 456):
            value = campaign.status(self.con)
        self.assertEqual(value['runtime']['state'], 'stale')
        self.assertTrue(value['activity']['stale'])

    def test_activity_worker_death_new_run_and_terminal_states_invalidate(self):
        self.activity_worker()
        for state in ('working', 'starting', 'between_rounds', 'stopped', 'error', 'complete'):
            with self.subTest(state=state):
                self.snapshot(worker_run_id='fixture-run')
                campaign.report_activity(self.con, {'stage': 'searching'})
                self.snapshot(state=state, worker_run_id='next-run' if state == 'working' else 'fixture-run')
                self.assertIsNone(campaign.status(self.con)['activity'])
                self.assertEqual(campaign.read_activity()['run_id'], 'fixture-run')
        self.snapshot(worker_run_id='fixture-run')
        campaign.report_activity(self.con, {'stage': 'searching'})
        with patch.object(campaign, 'pid_alive', side_effect=lambda pid: pid == 123):
            self.assertIsNone(campaign.status(self.con)['activity'])
        self.assertEqual(campaign.read_activity()['run_id'], 'fixture-run')

    def test_activity_outcomes_clear_matching_job_only(self):
        self.activity_worker()
        self.job('other')
        for outcome in ('applied', 'manual', 'failed', 'skipped', 'deferred'):
            with self.subTest(outcome=outcome):
                job_id = self.job(outcome)
                campaign.report_activity(self.con, {'stage': 'reviewing', 'job_id': job_id})
                campaign.record(self.con, {'job_id': 'other', 'result': 'skipped', 'reason': 'fixture'})
                self.assertEqual(campaign.status(self.con)['activity']['job_id'], job_id)
                if outcome == 'applied':
                    campaign.reserve(self.con, job_id)
                campaign.record(self.con, {'job_id': job_id, 'result': outcome, 'evidence': 'fixture receipt'})
                self.assertIsNone(campaign.status(self.con)['activity'])
                self.assertIsNone(campaign.read_activity())

    def test_activity_external_matching_result_invalidates_and_reserve_does_not_infer(self):
        self.activity_worker()
        self.job()
        campaign.reserve(self.con, 'job')
        self.assertIsNone(campaign.status(self.con)['activity'])
        campaign.report_activity(self.con, {'stage': 'reviewing', 'job_id': 'job'})
        campaign.db.record_application(self.con, 'job', 'manual', 'fixture')
        self.assertIsNone(campaign.status(self.con)['activity'])
        self.assertEqual(campaign.read_activity()['job_id'], 'job')

    def test_stale_runtime_snapshot_does_not_erase_new_run_activity(self):
        self.activity_worker()
        old_runtime = campaign.runtime_status(self.con)
        self.snapshot(worker_run_id='new-run')
        with patch.dict(campaign.os.environ, {'CAMPAIGN_RUN_ID': 'new-run'}):
            campaign.report_activity(self.con, {'stage': 'searching', 'action': 'searching_jobs'})
        saved = campaign.read_activity()
        with patch.object(campaign, 'atomic_json') as write:
            self.assertIsNone(campaign.activity_status(self.con, old_runtime))
        write.assert_not_called()
        self.assertEqual(campaign.read_activity(), saved)
        self.assertEqual(campaign.status(self.con)['activity']['action'], 'searching_jobs')

    def test_status_reads_never_write_activity_file(self):
        self.activity_worker()
        campaign.report_activity(self.con, {'stage': 'searching'})
        saved = campaign.read_activity()
        with patch.object(campaign, 'atomic_json') as write:
            self.assertIsNotNone(campaign.status(self.con)['activity'])
        write.assert_not_called()
        self.assertEqual(campaign.read_activity(), saved)
        for state in ('starting', 'stopped', 'error'):
            with self.subTest(state=state):
                self.snapshot(state=state, worker_run_id='fixture-run')
                with patch.object(campaign, 'atomic_json') as write:
                    self.assertIsNone(campaign.status(self.con)['activity'])
                write.assert_not_called()
                self.assertEqual(campaign.read_activity(), saved)

    def test_supervisor_exit_clears_before_detached_process_cleanup(self):
        runtime = self.activity_worker()
        campaign.report_activity(self.con, {'stage': 'searching'})
        process = Mock(pid=456, returncode=0)
        process.poll.return_value = 0

        def cleanup(*args):
            self.assertIsNone(campaign.read_activity())
            self.assertEqual(campaign.read_runtime()['state'], 'stopping')

        with patch.object(campaign, 'terminate_group', side_effect=cleanup) as terminate:
            self.assertEqual(campaign.supervise_worker(process, self.con, runtime, lambda: None),
                             (0, 'worker_exit'))
        terminate.assert_called_once()

    def test_supervisor_assigns_run_environment_and_clears_on_end(self):
        self.activity_worker()
        campaign.report_activity(self.con, {'stage': 'searching'})
        (self.root / 'src').mkdir()
        (self.root / 'src/CAMPAIGN_WORKER.md').write_text('Fixture worker prompt')
        process = Mock(pid=456, returncode=0)
        seen_runs = []

        def supervise(*args, **kwargs):
            current = campaign.read_runtime()
            run_id = spawn.call_args.kwargs['env']['CAMPAIGN_RUN_ID']
            self.assertEqual(current['worker_run_id'], run_id)
            self.assertNotEqual(run_id, 'fixture-run')
            self.assertIsNone(campaign.read_activity())
            with patch.dict(campaign.os.environ, {'CAMPAIGN_RUN_ID': run_id}):
                campaign.report_activity(self.con, {'stage': 'searching'})
            seen_runs.append(run_id)
            (self.root / 'data/campaign.stop').touch()
            return 0, 'worker_exit'

        with patch.object(campaign.shutil, 'which', return_value='/fixture/omp'), \
                patch.object(campaign, 'relay_connected', return_value=True), \
                patch.object(campaign, 'recover_worker', return_value=False), \
                patch.object(campaign, 'keep_awake', return_value=None), \
                patch.object(campaign.subprocess, 'Popen', return_value=process) as spawn, \
                patch.object(campaign, 'supervise_worker', side_effect=supervise):
            campaign.loop()
        self.assertEqual(len(seen_runs), 1)
        self.assertIsNone(campaign.read_activity())
        self.assertIsNone(campaign.read_runtime()['worker_run_id'])

    def test_activity_is_progress_only_for_healthy_current_context(self):
        runtime = self.activity_worker()
        runtime['started_at'] = self.clock.now - 1000
        campaign.heartbeat(self.con, runtime)
        self.assertTrue(campaign.status(self.con)['runtime']['no_progress'])
        campaign.report_activity(self.con, {'stage': 'searching', 'action': 'searching_jobs'})
        value = campaign.status(self.con)['runtime']
        self.assertEqual(value['last_progress_at'], self.clock.now)
        self.assertFalse(value['no_progress'])
        self.clock.now += campaign.ACTIVITY_STALE_SECONDS + 1
        campaign.heartbeat(self.con, runtime)
        value = campaign.status(self.con)['runtime']
        self.assertIsNone(value['last_progress_at'])
        self.assertTrue(value['no_progress'])
        campaign.report_activity(self.con, {'stage': 'searching'})
        with patch.object(campaign, 'pid_alive', side_effect=lambda pid: pid == 456):
            self.assertIsNone(campaign.progress_at(self.con))
        self.snapshot(worker_run_id='new-run')
        self.assertIsNone(campaign.progress_at(self.con))

    def test_supervisor_exception_clears_before_cleanup(self):
        runtime = self.activity_worker()
        campaign.report_activity(self.con, {'stage': 'searching'})
        process = Mock(pid=456)

        def cleanup(*args):
            self.assertIsNone(campaign.read_activity())
            self.assertEqual(campaign.read_runtime()['state'], 'stopping')

        with patch.object(campaign, 'terminate_group', side_effect=cleanup) as terminate, \
                self.assertRaisesRegex(RuntimeError, 'fixture crash'):
            campaign.supervise_worker(process, self.con, runtime, Mock(side_effect=RuntimeError('fixture crash')))
        terminate.assert_called_once()

    def test_activity_cli_accepts_json_stdin(self):
        self.activity_worker()
        with patch.object(campaign.sys, 'argv', ['campaign.py', 'activity']), \
                patch.object(campaign.sys, 'stdin', io.StringIO('{"stage":"searching","action":"searching_jobs"}')), \
                patch.object(campaign.sys, 'stdout', io.StringIO()) as stdout, \
                patch.object(campaign, 'connect', return_value=self.con):
            self.assertEqual(campaign.main(), 0)
        self.assertEqual(json.loads(stdout.getvalue())['activity']['action'], 'searching_jobs')

    def test_outcome_reason_retention_bounding_and_secret_redaction(self):
        reason = ('Not eligible; https://example.invalid/path?token=URLSECRET contact person@example.invalid '
                  'password="tiny secret" api_key=KEYSECRET authorization: Bearer BEARERSECRET '
                  'token=TOKENVALUE sk-FAKETOKEN "client_secret":"JSONSECRET" apiToken=APISECRET ' + 'X' * 40)
        for outcome in ('manual', 'failed', 'deferred', 'skipped'):
            with self.subTest(outcome=outcome):
                job_id = self.job(outcome)
                # Deferred rows can exist in history, though record(deferred)
                # intentionally emits only metadata rather than an attempt.
                self.con.execute('INSERT INTO applications(job_id,attempted,result,reason,evidence) VALUES(?,?,?,?,?)',
                                 (job_id, self.clock.now, outcome, reason, 'PRIVATE evidence'))
                self.con.commit()
                action = campaign.status(self.con)['latest_actions'][0]
                self.assertEqual(action['result'], outcome)
                self.assertIn('Not eligible', action['reason'])
                for secret in ('example.invalid', 'URLSECRET', 'tiny secret', 'KEYSECRET', 'BEARERSECRET',
                               'TOKENVALUE', 'sk-FAKETOKEN', 'JSONSECRET', 'APISECRET', 'X' * 40, 'PRIVATE evidence'):
                    self.assertNotIn(secret, json.dumps(action))
                self.assertEqual(self.con.execute('SELECT reason FROM applications WHERE job_id=?',
                                                 (job_id,)).fetchone()[0], reason)
        campaign.record(self.con, {'job_id': self.job('long'), 'result': 'skipped', 'reason': 'Too far. ' * 100})
        self.assertEqual(len(campaign.status(self.con)['latest_actions'][0]['reason']), 600)
        campaign.record(self.con, {'job_id': self.job('missing'), 'result': 'manual'})
        self.assertIsNone(campaign.status(self.con)['latest_actions'][0]['reason'])
        job_id = self.job('applied')
        campaign.reserve(self.con, job_id)
        campaign.record(self.con, {'job_id': job_id, 'result': 'applied',
                                  'reason': 'PRIVATE applied reason', 'evidence': 'PRIVATE applied evidence'})
        action = campaign.status(self.con)['latest_actions'][0]
        self.assertIsNone(action['reason'])
        self.assertNotIn('PRIVATE', json.dumps(action))

    def worker_fixture(self):
        source = Path(campaign.__file__).parent / 'CAMPAIGN_WORKER.md'
        (self.root / 'src').mkdir(exist_ok=True)
        (self.root / 'src/CAMPAIGN_WORKER.md').write_text(source.read_text())
        for name in ('first', 'second'):
            executable = self.root / 'bin' / name
            executable.parent.mkdir(exist_ok=True)
            executable.write_text('#!/bin/sh\nexit 0\n')
            executable.chmod(0o700)

    def test_runtime_reloads_paths_deadlines_profile_and_pauses_between_serial_rounds(self):
        self.worker_fixture()
        for progressed in (False, True):
            with self.subTest(progressed=progressed):
                (self.root / 'data/campaign.stop').unlink(missing_ok=True)
                self.settings['runtime'] = {
                    'omp_executable': './bin/first', 'worker_timeout_seconds': 19,
                    'success_pause_seconds': 100, 'idle_pause_seconds': 100,
                    'credentials_file': '~/private/first.json',
                }
                campaign.atomic_json(campaign.CONFIG, self.settings)
                self.profile['candidate']['name'] = 'First Fixture'
                campaign.atomic_json(self.root / 'profile.json', self.profile)
                seen = []
                job_id = self.job('runtime-' + str(progressed)) if progressed else None
                process = Mock(pid=456, returncode=0)

                def supervise(process, con, runtime, stop_reason, deadline_seconds):
                    command = spawn.call_args.args[0]
                    environment = spawn.call_args.kwargs['env']
                    seen.append((self.clock.now, command, environment, deadline_seconds))
                    self.assertEqual(spawn.call_args.kwargs['cwd'], self.root)
                    self.assertTrue(spawn.call_args.kwargs['start_new_session'])
                    if len(seen) == 1:
                        if progressed:
                            campaign.reserve(con, job_id)
                            campaign.record(con, {'job_id': job_id, 'result': 'applied',
                                                  'evidence': 'Fixture receipt, no real submission'})
                        self.settings['runtime'].update(
                            omp_executable='bin/second', worker_timeout_seconds=37,
                            success_pause_seconds=7, idle_pause_seconds=11,
                            credentials_file='private/second.json')
                        campaign.atomic_json(campaign.CONFIG, self.settings)
                        self.profile['candidate']['name'] = 'Second Fixture'
                        campaign.atomic_json(self.root / 'profile.json', self.profile)
                    else:
                        (self.root / 'data/campaign.stop').touch()
                    return 0, 'worker_exit'

                with patch.dict(campaign.os.environ, {'HOME': str(self.root)}), \
                        patch.object(campaign, 'relay_connected', return_value=True), \
                        patch.object(campaign, 'recover_worker', return_value=False), \
                        patch.object(campaign, 'keep_awake', return_value=None), \
                        patch.object(campaign.subprocess, 'Popen', return_value=process) as spawn, \
                        patch.object(campaign, 'supervise_worker', side_effect=supervise):
                    campaign.loop()
                self.assertEqual(len(seen), 2)
                self.assertEqual(seen[1][0] - seen[0][0], 7 if progressed else 11)
                for index, (name, seconds) in enumerate((('first', 19), ('second', 37))):
                    _, command, environment, deadline = seen[index]
                    self.assertEqual(command[0], str(self.root / 'bin' / name))
                    self.assertEqual(command[command.index('--max-time') + 1], str(seconds) + 's')
                    self.assertEqual(deadline, seconds)
                    self.assertEqual(environment['ATS_CREDENTIALS_FILE'],
                                     str(self.root / 'private' / (name + '.json')))
                    self.assertNotIn(environment['ATS_CREDENTIALS_FILE'], command[-1])
                    self.assertNotIn('~/private/' + name + '.json', command[-1])
                    self.assertNotIn('private/' + name + '.json', command[-1])
                    self.assertIn(str(self.root), command[-1])
                    self.assertNotIn('Jane Doe', command[-1])
                    self.assertNotIn('5aa8228acee47c45', command[-1])
                self.assertIn('First Fixture', seen[0][1][-1])
                self.assertIn('Second Fixture', seen[1][1][-1])
                self.assertNotIn('First Fixture', seen[1][1][-1])


    def test_invalid_profile_or_pdf_fails_closed_without_worker_or_reservation(self):
        self.worker_fixture()
        valid = json.loads(json.dumps(self.profile))
        for failure in ('missing_name', 'bad_pdf', 'invalid_permission'):
            with self.subTest(failure=failure):
                self.profile = json.loads(json.dumps(valid))
                (self.root / 'data/resume.pdf').write_bytes(b'%PDF-1.7\nFixture')
                if failure == 'missing_name':
                    del self.profile['candidate']['name']
                elif failure == 'bad_pdf':
                    (self.root / 'data/resume.pdf').write_bytes(b'not a PDF')
                else:
                    self.profile['application_permissions'] = {'account_creation': 'true'}
                campaign.atomic_json(self.root / 'profile.json', self.profile)
                with patch.object(campaign, 'relay_connected', return_value=True), \
                        patch.object(campaign, 'recover_worker', return_value=False), \
                        patch.object(campaign.subprocess, 'Popen') as spawn, \
                        self.assertRaises(campaign.settings.ConfigError):
                    campaign.loop()
                spawn.assert_not_called()
                self.assertEqual(self.con.execute('SELECT COUNT(*) FROM campaign_reservations').fetchone()[0], 0)
                self.assertEqual(json.loads((self.root / 'profile.json').read_text()), self.profile)

    def test_invalid_runtime_fails_closed_without_worker(self):
        self.settings['runtime'] = {'worker_timeout_seconds': 0}
        campaign.atomic_json(campaign.CONFIG, self.settings)
        with patch.object(campaign.subprocess, 'Popen') as spawn, \
                self.assertRaises(campaign.settings.ConfigError):
            campaign.loop()
        spawn.assert_not_called()

    def test_worker_executable_resolves_project_relative_path_and_home(self):
        self.worker_fixture()
        cases = [('bin/first', self.root / 'bin/first'),
                 (str(self.root / 'bin/first'), self.root / 'bin/first'),
                 ('~/bin/second', self.root / 'bin/second'),
                 ('first', self.root / 'bin/first')]
        with patch.dict(campaign.os.environ, {'HOME': str(self.root), 'PATH': 'bin'}):
            for name, expected in cases:
                with self.subTest(name=name):
                    self.assertEqual(campaign.worker_executable({'omp_executable': name}), str(expected))
            with self.assertRaisesRegex(RuntimeError, 'configured omp executable unavailable'):
                campaign.worker_executable({'omp_executable': 'missing'})

@unittest.skipUnless(sys.platform == 'darwin', 'macOS idle-sleep assertion')
class WorkerSleepTests(unittest.TestCase):
    def test_idle_sleep_is_blocked_until_the_worker_exits(self):
        worker = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL)
        awake = None
        try:
            awake = campaign.keep_awake(worker.pid)
            deadline = time.monotonic() + 5
            while True:
                assertions = subprocess.check_output(['/usr/bin/pmset', '-g', 'assertions'], text=True)
                owned = [line for line in assertions.splitlines()
                         if f'pid {awake.pid}(caffeinate)' in line]
                if owned or time.monotonic() >= deadline:
                    break
                time.sleep(0.05)
            self.assertTrue(any('PreventUserIdleSystemSleep' in line for line in owned), assertions)
            worker.terminate()
            worker.wait(timeout=5)
            self.assertEqual(awake.wait(timeout=5), 0)
            assertions = subprocess.check_output(['/usr/bin/pmset', '-g', 'assertions'], text=True)
            self.assertNotIn(f'pid {awake.pid}(caffeinate)', assertions)
        finally:
            if worker.poll() is None:
                worker.terminate()
            worker.wait(timeout=5)
            if awake is not None and awake.poll() is None:
                awake.terminate()
                awake.wait(timeout=5)


if __name__ == '__main__':
    unittest.main()
