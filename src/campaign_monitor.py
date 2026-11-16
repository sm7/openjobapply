"""Loopback-only campaign dashboard (Python standard library).

Run: python3 src/campaign_monitor.py [--port 8765]
Open: http://127.0.0.1:8765/
GET /api/status exposes a safe projection of campaign.status(connection) and never writes.
GET /api/logs exposes a bounded, sanitized tail of structured campaign activity.
GET /api/manual lists manual jobs read-only.
GET /api/applied lists applied jobs with their confirmation detail, read-only.
GET /api/skipped lists skipped jobs and their reviewed-requeue safeguards.
GET /api/applied/JOB_ID/receipt returns only that job's available receipt image.
POST /api/manual/resubmit and /api/skipped/requeue are guarded by monitor_security
(Host, Origin, JSON content type and a per-install token) and delegate to domain modules.
make_server(port=0) creates an unstarted server for an ephemeral-port smoke check.
"""

import argparse
import base64
import contextlib
import hashlib
import json
import re
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import campaign
import db
import applied_jobs
import applied_jobs_ui
import manual_jobs
import manual_jobs_ui
import monitor_security
import skipped_jobs
import skipped_jobs_ui
from campaign_logs import read_logs


_STATES = frozenset({
    'starting', 'working', 'waiting_relay', 'waiting_platform',
    'between_rounds', 'stopped', 'complete', 'error', 'stale',
})
_RUNTIME_FIELDS = (
    'heartbeat_at', 'heartbeat_age_seconds', 'supervisor_pid', 'worker_pid',
    'worker_started_at', 'last_progress_at', 'next_retry_at', 'restart_count',
    'heartbeat_stale_after_seconds', 'progress_age_seconds', 'no_progress',
)
_ACTIVITY_STAGES = frozenset({
    'searching', 'screening', 'opening_application', 'signing_in', 'filling',
    'reviewing', 'submitting', 'verifying', 'idle',
})
_ACTIVITY_ACTIONS = frozenset({
    'searching_jobs', 'checking_fit', 'opening_job', 'opening_form', 'signing_in',
    'uploading_resume', 'filling_contact', 'filling_work_history', 'filling_education',
    'answering_screening', 'reviewing_answers', 'waiting_email', 'waiting_platform',
    'waiting_pacing',
    'submitting_application', 'checking_confirmation', 'saving_outcome',
})
_REASON_CODE = re.compile(r'[a-zA-Z0-9_:. -]{1,96}\Z')


def read_status():
    """Read without initializing, migrating, or modifying the campaign database."""
    with contextlib.closing(sqlite3.connect(db.DB_PATH.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA query_only = ON')
        snapshot = campaign.status(connection)
    runtime = snapshot.get('runtime') or {}
    safe_runtime = {field: runtime.get(field) for field in _RUNTIME_FIELDS}
    safe_runtime['state'] = runtime.get('state') if runtime.get('state') in _STATES else 'stale'
    reason = runtime.get('last_exit_reason')
    # Only lifecycle codes are public, never worker output or freeform diagnostics.
    safe_runtime['last_exit_reason'] = reason if isinstance(reason, str) and _REASON_CODE.fullmatch(reason) else None
    budget = snapshot.get('budget') or {}
    activity = snapshot.get('activity')
    safe_activity = None
    if isinstance(activity, dict) and isinstance(activity.get('stage'), str) and activity['stage'] in _ACTIVITY_STAGES:
        job_id = activity.get('job_id')
        if activity['stage'] in {'searching', 'idle'} or (
            isinstance(job_id, (str, int)) and not isinstance(job_id, bool) and job_id != ''
        ):
            safe_activity = {
                'stage': activity['stage'],
                'job_id': job_id if isinstance(job_id, (str, int)) and not isinstance(job_id, bool) else None,
                'company': activity.get('company') if isinstance(activity.get('company'), str) else None,
                'title': activity.get('title') if isinstance(activity.get('title'), str) else None,
                'action': activity.get('action') if isinstance(activity.get('action'), str)
                and activity['action'] in _ACTIVITY_ACTIONS else None,
                'action_label': activity.get('action_label') if isinstance(activity.get('action_label'), str)
                and isinstance(activity.get('action'), str) and activity['action'] in _ACTIVITY_ACTIONS else None,
                **{
                    field: activity.get(field) if isinstance(activity.get(field), int)
                    and not isinstance(activity.get(field), bool) and activity[field] > 0 else None
                    for field in ('page', 'pages')
                },
                **{
                    field: activity.get(field) if isinstance(activity.get(field), (str, int, float))
                    and not isinstance(activity.get(field), bool) else None
                    for field in ('reported_at', 'stage_started_at')
                },
                'stale': activity.get('stale') is not False,
            }
    return {
        **{field: snapshot.get(field) for field in ('confirmed', 'target', 'remaining', 'complete')},
        'budget': {field: budget.get(field) for field in (
            'loads_24h', 'details_1h', 'submit_attempts_24h', 'retry_at',
        )},
        'outcomes': snapshot.get('outcomes') or {},
        'runtime': safe_runtime,
        'activity': safe_activity,
        'latest_actions': [
            {field: action.get(field) for field in ('at', 'job_id', 'company', 'title', 'result', 'reason')}
            for action in (snapshot.get('latest_actions') or [])[:20]
        ],
        'counts_by_job_status': snapshot.get('counts_by_job_status') or {},
    }


# Rejections meaning "the job is not in a state this can change", as opposed to a malformed request.
_CONFLICT_CODES = frozenset({
    'confirmation_required', 'not_manual', 'already_sent_or_reserved',
    'requeue_requires_finished_non_applied_job',
    'not_skipped', 'requeue_requires_expected_status',
})


def read_manual():
    """List manual jobs on a read-only connection, like read_status: never initializes or modifies the database."""
    with contextlib.closing(sqlite3.connect(db.DB_PATH.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA query_only = ON')
        return manual_jobs.list_manual(connection)


def read_applied():
    """List applied jobs on a read-only connection, like read_manual."""
    with contextlib.closing(sqlite3.connect(db.DB_PATH.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA query_only = ON')
        jobs = applied_jobs.list_applied(connection)
    return {'jobs': jobs, 'summary': applied_jobs.summarize(jobs)}


def read_skipped():
    """List skipped jobs without creating or changing campaign state."""
    with contextlib.closing(sqlite3.connect(db.DB_PATH.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA query_only = ON')
        return {'jobs': skipped_jobs.list_skipped(connection)}


def read_receipt(job_id):
    """Read only an image linked to an applied job's recorded receipt."""
    with contextlib.closing(sqlite3.connect(db.DB_PATH.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA query_only = ON')
        return applied_jobs.receipt_image(connection, job_id)


_STYLE = """
:root{color-scheme:light;font:15px/1.5 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#152536;background:#f3f5f8}
*{box-sizing:border-box}body{margin:0}main{max-width:1200px;margin:auto;padding:28px 22px 48px}
header{display:flex;justify-content:space-between;gap:20px;align-items:flex-start;margin-bottom:22px}h1{font-size:1.65rem;margin:0 0 4px}h2{font-size:1rem;margin:0 0 14px}p{margin:0}.muted,small{color:#576777}.refresh{text-align:right;font-size:.85rem}.banner{padding:12px 16px;margin-bottom:18px;border:1px solid #a7b2be;border-radius:8px;background:#edf1f6}.banner.error{background:#fff0f0;border-color:#bd4545;color:#8f1717}.banner[hidden]{display:none}.card,.panel{background:#fff;border:1px solid #d6dde5;border-radius:10px;padding:18px}.label{font-size:.82rem;color:#576777;text-transform:uppercase;letter-spacing:.045em}.number{font-size:2.1rem;font-weight:700;line-height:1.2;margin:7px 0}.detail{font-size:.88rem;margin-top:7px;overflow-wrap:anywhere}.state{display:inline-block;font-size:.9rem;font-weight:650;border-radius:5px;padding:4px 9px;margin:8px 0 3px;background:#e8edf3;color:#3d5266}.state.good{background:#e2f3e8;color:#17603a}.state.warn{background:#fff0d6;color:#805000}.state.bad{background:#ffe4e4;color:#941f1f}.panel{min-width:0}.scroll{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:.87rem;text-align:left}th{color:#576777;font-weight:600}td,th{padding:10px 9px;border-bottom:1px solid #e5eaf0;vertical-align:top}td{overflow-wrap:anywhere}th:first-child,td:first-child{padding-left:0}th:last-child,td:last-child{padding-right:0}tbody tr:last-child td{border-bottom:0}.actions{min-width:590px}.job{display:block;font-size:.74rem;color:#657585;margin-top:3px}.list{display:grid;gap:6px}.pair{display:flex;justify-content:space-between;gap:14px;border-bottom:1px solid #e5eaf0;padding:6px 0}.pair span:first-child{overflow-wrap:anywhere}.pair span:last-child,.pair strong{font-variant-numeric:tabular-nums}.subheading{margin-top:24px}.note{font-size:.8rem;margin-top:13px;color:#576777}.unavailable .number{color:#73808e}footer{font-size:.8rem;color:#576777;margin-top:22px}
.current-application{margin-bottom:18px;border-left:5px solid #3b6b99}.current-application h2{font-size:1.15rem}.current-application h3{font-size:1.3rem;margin:7px 0 0;overflow-wrap:anywhere}.current-application .role{font-size:1.05rem;margin-top:3px;overflow-wrap:anywhere}.current-application .state{margin-top:0}.activity-warning{color:#805000;font-weight:600;margin-top:10px}.activity-times{display:flex;flex-wrap:wrap;gap:8px 28px;margin-top:12px}.activity-times p{font-size:.88rem}.current-application [hidden]{display:none}
.actions td:last-child{min-width:200px}.result-reason{display:block;max-width:310px;margin-top:5px;font-size:.82rem;white-space:normal;overflow-wrap:anywhere;color:#576777}
.ov-band{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px 28px;align-items:center;margin-bottom:18px;border-left:5px solid #3b6b99}.ov-state{display:flex;align-items:center;gap:14px}.ov-band .state,.ov-band .state.good,.ov-band .state.warn,.ov-band .state.bad{display:block;background:none;padding:0;margin:2px 0 0;font-size:1.4rem;font-weight:700;overflow-wrap:anywhere}
.ov-dot{flex:none;width:20px;height:20px;border-radius:50%;background:#8795a3;border:4px solid #8795a3}.ov-dot.good{background:#17603a;border-color:#17603a}.ov-dot.warn{background:#fff;border-color:#a06500}.ov-dot.bad{background:#941f1f;border-color:#941f1f;border-radius:3px;transform:rotate(45deg)}
.ov-link{font:inherit;font-weight:600;background:none;border:0;padding:6px 0;margin-top:2px;color:#234d75;text-decoration:underline;cursor:pointer;text-align:left}.ov-link.attention{color:#805000}.ov-link:focus-visible,.ov-fold summary:focus-visible{outline:3px solid #4584bb;outline-offset:3px}.ov-why{grid-column:1/-1;display:flex;flex-wrap:wrap;gap:4px 28px;border-top:1px solid #e5eaf0;padding-top:10px}.ov-why[hidden]{display:none}
.ov-top{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.6fr);gap:14px;margin-bottom:18px;align-items:start}.number.ov-big{font-size:3.4rem;margin:4px 0 0}.ov-caption{font-size:.95rem;color:#3d5266}.ov-sub{font-size:.9rem;margin:16px 0 6px}
.ov-fold{background:#fff;border:1px solid #d6dde5;border-radius:10px;margin-top:14px}.ov-fold summary{cursor:pointer;padding:14px 18px;font-weight:600;border-radius:10px}.ov-fold-body{padding:0 18px 16px}
.tabs{display:flex;gap:8px;margin-bottom:18px;border-bottom:1px solid #c4ced9;padding-bottom:8px}.tabs button,.log-controls button{font:inherit;border:1px solid #9aaaba;border-radius:6px;background:#fff;color:#152536;padding:7px 15px;cursor:pointer}.tabs button[aria-selected="true"]{background:#234d75;color:#fff;border-color:#234d75}.tabs button:focus-visible,.log-controls button:focus-visible,.log-window:focus-visible{outline:3px solid #4584bb;outline-offset:3px}[role="tabpanel"][hidden]{display:none}.log-controls{display:flex;flex-wrap:wrap;align-items:center;gap:12px 20px;margin:14px 0}.log-controls label{display:flex;align-items:center;gap:7px}.log-window{height:480px;max-height:65vh;min-height:200px;overflow:auto;background:#101923;color:#e0e9f2;border:1px solid #314357;border-radius:7px;padding:12px;font:13px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace;overflow-wrap:anywhere}.log-row{padding:5px 0;border-bottom:1px solid #243446;white-space:pre-wrap}.log-time{color:#a4b9cc}.log-event{color:#91c7f5}.log-context{display:block;color:#a9becf;padding-left:2ch}.log-empty{color:#b5c4d3}.log-window.unavailable{opacity:.7}.log-status{font-size:.9rem;color:#44596e}.log-status.offline{color:#9b2222}.log-status.paused{color:#805000}.logs-panel .banner{margin-top:12px}.logs-panel h2{margin-bottom:8px}
@media(max-width:850px){.ov-top{grid-template-columns:1fr}}
.result-reason{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;max-width:560px}
@media(max-width:520px){main{padding:20px 13px}header{display:block}.refresh{text-align:left;margin-top:12px}.number{font-size:1.9rem}.number.ov-big{font-size:2.8rem}.card,.panel{padding:15px}.ov-band{grid-template-columns:1fr}.ov-band .state,.ov-band .state.good,.ov-band .state.warn,.ov-band .state.bad{font-size:1.2rem}.ov-fold summary{padding:12px 14px}.ov-fold-body{padding:0 14px 14px}}
@media(max-width:520px){.tabs{flex-wrap:wrap}}
"""

_SCRIPT = r"""
'use strict';
const $ = id => document.getElementById(id);
const set = (id, value) => { $(id).textContent = value == null ? '—' : String(value); };
let snapshot = null, receivedAt = 0, lastSuccess = null, failed = false, pending = false;
const DEFAULT_STALE_SECONDS = 20;
const ACTIVITY_STALE_SECONDS = 300;
const ACTIVITY_LABELS = {
  searching: 'Searching for jobs', screening: 'Screening job',
  opening_application: 'Opening application', signing_in: 'Signing in',
  filling: 'Filling application', reviewing: 'Reviewing application',
  submitting: 'Submitting application', verifying: 'Verifying submission', idle: 'Worker idle',
};
function timestamp(value) {
  if (value == null || value === '') return null;
  const result = typeof value === 'number' ? value * 1000 : Date.parse(value);
  return Number.isFinite(result) ? result : null;
}
function duration(seconds) {
  if (!Number.isFinite(seconds)) return 'Unknown';
  seconds = Math.max(0, Math.floor(seconds));
  if (seconds < 60) return seconds + 's';
  if (seconds < 3600) return Math.floor(seconds / 60) + 'm ' + (seconds % 60) + 's';
  if (seconds < 86400) return Math.floor(seconds / 3600) + 'h ' + Math.floor(seconds % 3600 / 60) + 'm';
  return Math.floor(seconds / 86400) + 'd ' + Math.floor(seconds % 86400 / 3600) + 'h';
}
function age(value) {
  const time = timestamp(value);
  return time == null ? 'Unknown' : duration((Date.now() - time) / 1000) + ' ago';
}
function code(value) { return value ? String(value).replaceAll('_', ' ') : 'None reported'; }
function list(id, values) {
  const target = $(id);
  target.replaceChildren();
  const entries = Object.entries(values || {}).sort(([a], [b]) => a.localeCompare(b));
  for (const [name, count] of entries) {
    const row = document.createElement('div'); row.className = 'pair';
    const label = document.createElement('span'); label.textContent = code(name);
    const number = document.createElement('span'); number.textContent = String(count);
    row.append(label, number); target.append(row);
  }
  if (!entries.length) target.textContent = 'No records yet.';
}
function actions(values) {
  const target = $('actions'); target.replaceChildren();
  for (const action of values.slice(0, 20)) {
    const row = document.createElement('tr');
    for (const value of [action.at, action.company || '—', action.title || '—', code(action.result)]) {
      const cell = document.createElement('td');
      if (row.children.length === 0) {
        const time = timestamp(value);
        cell.textContent = time == null ? 'Unknown' : new Date(time).toLocaleString();
      } else cell.textContent = String(value);
      row.append(cell);
    }
    if (['manual', 'failed', 'deferred', 'skipped'].includes(action.result)) {
      const reason = document.createElement('span'); reason.className = 'result-reason';
      reason.textContent = 'Reason: ' + (action.reason || 'No reason recorded'); reason.title = action.reason || '';
      row.children[3].append(reason);
    }
    const job = document.createElement('span'); job.className = 'job'; job.textContent = action.job_id || '—';
    row.children[2].append(job); target.append(row);
  }
  if (!values.length) {
    const row = document.createElement('tr'), cell = document.createElement('td');
    cell.colSpan = 4; cell.textContent = 'No job actions recorded.'; row.append(cell); target.append(row);
  }
}
function renderActivity(runtime, runtimeStale) {
  const activity = snapshot?.activity;
  const reportedAt = timestamp(activity?.reported_at);
  const reportedAge = reportedAt == null ? null : Math.max(0, (Date.now() - reportedAt) / 1000);
  const unhealthy = failed || runtimeStale || runtime.state !== 'working' || !runtime.worker_pid;
  const old = Boolean(activity) && (activity.stale || unhealthy || reportedAge == null || reportedAge > ACTIVITY_STALE_SECONDS);
  const hasJob = Boolean(activity) && !['searching', 'idle'].includes(activity.stage);
  set('activity-heading', old ? 'Last reported application' : 'Current application');
  $('activity-job').hidden = !hasJob;
  $('activity-times').hidden = !activity;
  $('activity-warning').hidden = !old && !failed;
  set('activity-company', hasJob ? activity.company || 'Company not recorded' : '');
  set('activity-title', hasJob ? activity.title || 'Role not recorded' : '');
  set('activity-job-id', hasJob ? activity.job_id : '');
  const label = activity ? ACTIVITY_LABELS[activity.stage] || 'Unknown stage' : '';
  set('activity-stage', activity
    ? old ? 'Last reported: ' + label : hasJob ? label : 'No application active · ' + label
    : failed ? 'Current application unavailable'
    : runtime.state === 'working' ? 'Worker has not reported its current job yet'
    : snapshot ? 'No current application reported' : 'Waiting for worker status');
  $('activity-stage').className = 'state ' + (old || failed ? 'warn' : hasJob ? 'good' : '');
  const actionLabel = typeof activity?.action_label === 'string' ? activity.action_label : '';
  $('activity-action').hidden = !actionLabel;
  set('activity-action', actionLabel ? (old ? 'Last reported action: ' : "What's happening: ") + actionLabel : '');
  const pageKnown = Number.isInteger(activity?.page) && Number.isInteger(activity?.pages)
    && activity.page > 0 && activity.page <= activity.pages;
  $('activity-page').hidden = !pageKnown;
  set('activity-page', pageKnown ? (old ? 'Last reported page ' : 'Page ') + activity.page + ' of ' + activity.pages : '');
  set('activity-warning', failed
    ? 'Status refresh failed. This is last reported context, not a live application claim.'
    : 'This report is stale or the worker is not healthy. It does not establish that this job is still being worked on.');
  const startedAt = timestamp(activity?.stage_started_at);
  set('activity-elapsed', startedAt == null ? 'Unknown' : duration((Date.now() - startedAt) / 1000));
  set('activity-reported-age', reportedAge == null ? 'Unknown' : duration(reportedAge) + ' ago');
}
const STATE_WORDS = {
  starting: 'Starting', working: 'Working', between_rounds: 'Between rounds',
  waiting_relay: 'Waiting for the browser connection', waiting_platform: 'Waiting for a job site',
  stopped: 'Stopped', complete: 'Finished: target reached', error: 'Error', stale: 'Stale',
};
function overviewState(text, level) {
  set('service-state', text);
  $('service-state').className = 'state ' + level;
  $('state-dot').className = 'ov-dot ' + level;
}
function renderNeeds() {
  const count = snapshot ? Number(snapshot.counts_by_job_status?.manual) || 0 : null;
  $('needs-you').textContent = count == null ? 'Manual jobs: waiting for status'
    : 'Needs you: ' + count + (count === 1 ? ' manual job' : ' manual jobs') + (failed ? ' (last known)' : '');
  $('needs-you').className = 'ov-link' + (count ? ' attention' : '');
}
function renderAges() {
  set('last-refresh', lastSuccess == null ? 'Not yet' : new Date(lastSuccess).toLocaleTimeString() + ' (' + duration((Date.now() - lastSuccess) / 1000) + ' ago)');
  renderNeeds();
  if (!snapshot) { renderActivity({}, true); return; }
  const runtime = snapshot.runtime || {};
  const heartbeatTime = timestamp(runtime.heartbeat_at);
  const heartbeatAge = typeof runtime.heartbeat_age_seconds === 'number'
    ? Math.max(0, runtime.heartbeat_age_seconds + (Date.now() - receivedAt) / 1000)
    : heartbeatTime == null ? null : Math.max(0, (Date.now() - heartbeatTime) / 1000);
  const staleAfter = typeof runtime.heartbeat_stale_after_seconds === 'number' && runtime.heartbeat_stale_after_seconds > 0
    ? runtime.heartbeat_stale_after_seconds : DEFAULT_STALE_SECONDS;
  const inactive = ['stopped', 'complete', 'error'].includes(runtime.state);
  // Preserve backend terminal states; age only nonterminal snapshots between polls.
  // Backend state also accounts for a missing supervisor PID, which a browser cannot inspect.
  const stale = runtime.state === 'stale' || (!inactive && (heartbeatAge == null || heartbeatAge > staleAfter));
  const noProgress = runtime.state === 'working' && runtime.no_progress;
  const word = STATE_WORDS[runtime.state] || code(runtime.state);
  const level = failed || stale || runtime.state === 'error' ? 'bad' : inactive || noProgress || runtime.state !== 'working' ? 'warn' : 'good';
  overviewState(failed ? 'Unavailable · last known data'
    : runtime.state === 'stale' ? 'Stale'
    : stale ? 'Stale · last reported: ' + word.toLowerCase()
    : word + (noProgress ? ' · no recent activity' : ''), level);
  set('liveness', failed ? 'The page could not refresh. The worker status is unknown.'
    : stale ? 'No recent heartbeat from the worker service. This does not show whether it is working.'
    : inactive ? 'The campaign is not running.'
    : noProgress ? 'The worker service is running, but nothing new was recorded lately. This is a warning, not proof of a problem.'
    : 'The worker service sent a recent heartbeat. This does not mean an application was sent.');
  $('not-working').hidden = !(failed || stale || runtime.state !== 'working');
  set('heartbeat-age', heartbeatAge == null ? 'Unknown' : duration(heartbeatAge) + ' ago');
  set('worker-age', failed || stale || heartbeatAge == null ? 'Worker status unknown' : runtime.worker_pid ? (timestamp(runtime.worker_started_at) == null ? 'Unknown' : duration((Date.now() - timestamp(runtime.worker_started_at)) / 1000)) : 'No active worker');
  set('progress-age', age(runtime.last_progress_at));
  set('action-age', age(snapshot.latest_actions?.[0]?.at));
  const retry = timestamp(runtime.next_retry_at ?? snapshot.budget?.retry_at);
  set('next-retry', retry == null ? 'Not scheduled' : new Date(retry).toLocaleString() + (retry > Date.now() ? ' (in ' + duration((retry - Date.now()) / 1000) + ')' : ' (due)'));
  const confirmedAt = timestamp((snapshot.latest_actions || []).find(action => action.result === 'applied')?.at);
  $('last-confirmed-line').hidden = confirmedAt == null;
  set('last-confirmed', confirmedAt == null ? null : duration((Date.now() - confirmedAt) / 1000) + ' ago');
  renderActivity(runtime, stale);
  $('content').classList.toggle('unavailable', failed || stale);
}
function render(data) {
  const finite = data.target !== null;
  set('confirmed', data.confirmed);
  set('target', finite ? data.target : null);
  set('remaining', finite ? data.remaining : null);
  $('target-part').hidden = !finite; $('remaining-line').hidden = !finite;
  set('confirmed-caption', finite ? 'confirmed of the target' : 'confirmed applications');
  set('progress-note', data.complete ? 'Target reached. Counts unique confirmed jobs.' : finite ? 'Counts unique jobs with a confirmed application.' : 'No target is set. The campaign keeps going.');
  const runtime = data.runtime || {};
  set('supervisor-pid', runtime.supervisor_pid); set('worker-pid', runtime.worker_pid);
  set('restarts', runtime.restart_count ?? 0);
  set('wait-reason', code(runtime.state)); set('exit-reason', code(runtime.last_exit_reason));
  const outcomes = data.outcomes || {};
  set('attempts', Object.values(outcomes).reduce((total, count) => total + (typeof count === 'number' ? count : 0), 0));
  set('submit-attempts', data.budget?.submit_attempts_24h);
  set('unique-jobs', Object.values(data.counts_by_job_status || {}).reduce((total, count) => total + (typeof count === 'number' ? count : 0), 0));
  list('outcomes', outcomes); list('job-statuses', data.counts_by_job_status);
  actions(data.latest_actions || []); renderAges();
}
async function refresh() {
  if (pending) return;
  pending = true;
  const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 4000);
  try {
    const response = await fetch('/api/status', {cache: 'no-store', credentials: 'omit', signal: controller.signal});
    if (!response.ok) throw new Error('HTTP ' + response.status);
    const data = await response.json();
    const validTarget = data && (data.target === null
      ? data.remaining === null && data.complete === false
      : typeof data.target === 'number' && typeof data.remaining === 'number');
    if (!data || typeof data !== 'object' || typeof data.confirmed !== 'number' || !validTarget || !data.runtime || !Array.isArray(data.latest_actions)) throw new Error('Invalid status response');
    snapshot = data; receivedAt = lastSuccess = Date.now(); failed = false;
    $('refresh-error').hidden = true; render(data);
  } catch (error) {
    failed = true;
    $('refresh-error').hidden = false;
    $('refresh-error').className = 'banner error';
    $('refresh-error').textContent = 'Status refresh failed' + (error.name === 'AbortError' ? ' (request timed out)' : '') + '. Service liveness is unknown. Displayed values are last known data, not live confirmation. Retrying every 5 seconds.';
    if (!snapshot) { overviewState('Unavailable', 'bad'); set('liveness', 'Cannot read campaign status.'); }
    renderAges();
  } finally { clearTimeout(timer); pending = false; }
}
$('needs-you').addEventListener('click', () => { selectTab('manual'); $('tab-manual').focus(); });
let activeTab = 'overview', logsPaused = false, logsPending = false, logsFailed = false;
let logsLastSuccess = null, logsTimer = null, logsController = null;
function renderLogStatus() {
  const updated = logsLastSuccess == null ? 'No successful refresh yet' : 'Last refreshed ' + new Date(logsLastSuccess).toLocaleTimeString() + ' (' + duration((Date.now() - logsLastSuccess) / 1000) + ' ago)';
  const status = $('logs-status');
  status.className = 'log-status' + (logsFailed ? ' offline' : logsPaused ? ' paused' : '');
  status.textContent = (logsFailed ? 'Offline · displayed entries are not live' : logsPaused ? 'Paused · automatic refresh stopped' : logsLastSuccess == null ? 'Loading operational activity' : 'Live · refreshing every 2 seconds') + ' · ' + updated;
}
function renderLogs(data) {
  const viewport = $('logs-window'), target = $('logs-output'), scrollTop = viewport.scrollTop;
  const rows = document.createDocumentFragment();
  const entries = data.entries.slice(-200);
  for (const entry of entries) {
    if (!entry || typeof entry.message !== 'string' || typeof entry.event !== 'string') continue;
    const row = document.createElement('div'); row.className = 'log-row'; row.setAttribute('role', 'listitem');
    const time = document.createElement('span'); time.className = 'log-time';
    const at = timestamp(entry.at); time.textContent = (at == null ? 'Unknown time' : new Date(at).toLocaleString()) + ' ';
    const event = document.createElement('span'); event.className = 'log-event'; event.textContent = '[' + entry.event + '] ';
    const message = document.createElement('span'); message.textContent = entry.message;
    row.append(time, event, message);
    const context = [entry.company, entry.title, entry.job_id == null ? null : 'Job ' + entry.job_id].filter(value => value != null && value !== '');
    if (context.length) {
      const job = document.createElement('span'); job.className = 'log-context'; job.textContent = context.join(' · '); row.append(job);
    }
    rows.append(row);
  }
  if (!rows.childNodes.length) {
    const empty = document.createElement('p'); empty.className = 'log-empty'; empty.textContent = 'No operational activity recorded in this tail.'; rows.append(empty);
  }
  target.replaceChildren(rows);
  set('logs-tail-note', data.truncated ? 'Showing a bounded tail of the activity log; older entries are omitted.' : 'Showing the latest activity entries, oldest first (up to 200).');
  viewport.scrollTop = $('logs-follow').checked ? viewport.scrollHeight : scrollTop;
}
async function refreshLogs() {
  if (activeTab !== 'logs' || logsPaused || logsPending) return;
  logsPending = true;
  const controller = new AbortController(); logsController = controller;
  const timer = setTimeout(() => controller.abort(), 4000);
  try {
    const response = await fetch('/api/logs', {cache: 'no-store', credentials: 'omit', signal: controller.signal});
    if (!response.ok) throw new Error('Log request failed');
    const data = await response.json();
    if (!data || !Array.isArray(data.entries) || data.source !== 'activity' || typeof data.truncated !== 'boolean') throw new Error('Invalid log response');
    if (activeTab !== 'logs' || logsPaused) return;
    renderLogs(data); logsLastSuccess = Date.now(); logsFailed = false;
    $('logs-error').hidden = true; $('logs-window').classList.remove('unavailable');
  } catch (error) {
    if (activeTab !== 'logs' || logsPaused) return;
    logsFailed = true; $('logs-error').hidden = false; $('logs-window').classList.add('unavailable');
    set('logs-error', 'Operational log refresh failed' + (error.name === 'AbortError' ? ' (request timed out)' : '') + '. Displayed entries are last fetched data, not a live feed. Retrying every 2 seconds while this tab is active and not paused.');
  } finally {
    clearTimeout(timer); logsPending = false;
    if (logsController === controller) logsController = null;
    renderLogStatus();
  }
}
function scheduleLogs() {
  if (logsTimer != null) { clearInterval(logsTimer); logsTimer = null; }
  if (activeTab === 'logs' && !logsPaused) {
    refreshLogs(); logsTimer = setInterval(refreshLogs, 2000);
  } else if (logsController) logsController.abort();
  renderLogStatus();
}
const TAB_NAMES = ['overview', 'applied', 'manual', 'skipped', 'logs'];
function selectTab(name, updateHash = true) {
  activeTab = TAB_NAMES.includes(name) ? name : 'overview';
  for (const tab of TAB_NAMES) {
    const selected = activeTab === tab;
    $('tab-' + tab).setAttribute('aria-selected', String(selected));
    $('tab-' + tab).tabIndex = selected ? 0 : -1;
    $('panel-' + tab).hidden = !selected;
  }
  if (updateHash) history.replaceState(null, '', '#' + activeTab);
  scheduleLogs();
  if (activeTab === 'manual') refreshManual();
  if (activeTab === 'applied') refreshApplied();
  if (activeTab === 'skipped') refreshSkipped();
}
const tabs = TAB_NAMES.map(name => $('tab-' + name));
for (const tab of tabs) {
  tab.addEventListener('click', () => selectTab(TAB_NAMES[tabs.indexOf(tab)]));
  tab.addEventListener('keydown', event => {
    let index = tabs.indexOf(tab);
    if (event.key === 'ArrowRight') index = (index + 1) % tabs.length;
    else if (event.key === 'ArrowLeft') index = (index + tabs.length - 1) % tabs.length;
    else if (event.key === 'Home') index = 0;
    else if (event.key === 'End') index = tabs.length - 1;
    else return;
    event.preventDefault(); tabs[index].focus(); selectTab(TAB_NAMES[index]);
  });
}
$('logs-pause').addEventListener('click', () => {
  logsPaused = !logsPaused;
  $('logs-pause').textContent = logsPaused ? 'Resume' : 'Pause';
  $('logs-pause').setAttribute('aria-pressed', String(logsPaused));
  scheduleLogs();
});
$('logs-follow').addEventListener('change', () => {
  if ($('logs-follow').checked) $('logs-window').scrollTop = $('logs-window').scrollHeight;
});
"""

# Runs after every tab's functions are defined, so a page opened at #manual or #logs finds them all.
_BOOT = r"""
const hashTab = () => location.hash.slice(1).split('/')[0];
window.addEventListener('hashchange', () => selectTab(hashTab(), false));
selectTab(hashTab(), false);
setInterval(renderLogStatus, 1000);
refresh(); setInterval(refresh, 5000); setInterval(renderAges, 1000);
"""
_PAGE_SCRIPT = _SCRIPT + manual_jobs_ui.SCRIPT + applied_jobs_ui.SCRIPT + skipped_jobs_ui.SCRIPT + _BOOT

_HTML = ("""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="monitor-token" content="__MONITOR_TOKEN__"><title>Campaign monitor</title><style>""" + _STYLE + manual_jobs_ui.STYLE + applied_jobs_ui.STYLE + skipped_jobs_ui.STYLE + """</style></head>
<body><main><header><div><h1>Campaign monitor</h1><p class="muted">Local dashboard. Progress and logs are read-only. Use Manual jobs or Skipped jobs to request a reviewed retry.</p></div><div class="refresh">Last successful overview refresh<br><strong id="last-refresh">Not yet</strong></div></header>
<div class="tabs" role="tablist" aria-label="Campaign dashboard"><button id="tab-overview" type="button" role="tab" aria-controls="panel-overview" aria-selected="true">Overview</button><button id="tab-applied" type="button" role="tab" aria-controls="panel-applied" aria-selected="false" tabindex="-1">Applied jobs</button><button id="tab-manual" type="button" role="tab" aria-controls="panel-manual" aria-selected="false" tabindex="-1">Manual jobs</button><button id="tab-skipped" type="button" role="tab" aria-controls="panel-skipped" aria-selected="false" tabindex="-1">Skipped jobs</button><button id="tab-logs" type="button" role="tab" aria-controls="panel-logs" aria-selected="false" tabindex="-1">Live logs</button></div>
<div id="panel-overview" role="tabpanel" aria-labelledby="tab-overview" tabindex="0"><div id="refresh-error" class="banner" role="alert">Loading campaign status. Service liveness is not yet known.</div>
<section class="card ov-band" aria-label="Worker status and what needs you">
<div><div class="ov-state"><span id="state-dot" class="ov-dot" aria-hidden="true"></span><div><div class="label">Is it working?</div><div id="service-state" class="state">Unknown</div></div></div><p id="liveness" class="detail">Waiting for first heartbeat.</p></div>
<div><div class="label">Does anything need you?</div><button id="needs-you" type="button" class="ov-link" title="Open the Manual jobs tab">Manual jobs: waiting for status</button></div>
<div id="not-working" class="ov-why" hidden><p class="detail">Last exit reason: <strong id="exit-reason">—</strong></p><p class="detail">Next retry: <strong id="next-retry">—</strong></p></div>
</section>
<div id="content"><section class="ov-top" aria-label="Progress and jobs">
<div class="card"><div class="label">Progress</div><div class="number ov-big"><span id="confirmed">—</span><span id="target-part" hidden> / <span id="target">—</span></span></div><p id="confirmed-caption" class="ov-caption">confirmed applications</p><p id="remaining-line" class="detail" hidden>Remaining: <strong id="remaining">—</strong></p><p id="last-confirmed-line" class="detail" hidden>Last confirmed application: <strong id="last-confirmed">—</strong></p><p id="progress-note" class="note">Counts unique jobs with a confirmed application.</p></div>
<section class="panel" aria-labelledby="job-statuses-heading"><h2 id="job-statuses-heading">Jobs by current status</h2><p class="detail"><strong id="unique-jobs">—</strong> jobs in total</p><div id="job-statuses" class="list"></div></section>
</section><section class="panel current-application" aria-labelledby="activity-heading">
<h2 id="activity-heading">Current application</h2><p id="activity-stage" class="state">Waiting for worker status</p>
<p id="activity-action" class="detail" hidden></p><p id="activity-page" class="detail" hidden></p>
<div id="activity-job" hidden><h3 id="activity-company"></h3><p id="activity-title" class="role"></p><p class="detail">Job ID: <span id="activity-job-id"></span></p></div>
<div id="activity-times" class="activity-times" hidden><p>Since reported stage began: <strong id="activity-elapsed">—</strong></p><p>Worker reported: <strong id="activity-reported-age">—</strong></p></div>
<p id="activity-warning" class="activity-warning" role="status" hidden></p><p class="note">Explicit worker-reported context, not confirmation of submission. Completed results appear below.</p>
</section>
<section class="panel"><h2>Latest 20 job actions <small>· tries, not unique jobs</small></h2><div class="scroll"><table class="actions"><thead><tr><th scope="col">Recorded at</th><th scope="col">Company</th><th scope="col">Role / job ID</th><th scope="col">Result</th></tr></thead><tbody id="actions"><tr><td colspan="4">Waiting for status.</td></tr></tbody></table></div><p class="note">An attempt may be unsuccessful or repeat a job. Only confirmed unique jobs count toward the target.</p></section>
<details class="ov-fold"><summary>Attempt history (every try, including repeats)</summary><div class="ov-fold-body"><p class="detail"><strong id="attempts">—</strong> recorded attempts · <strong id="submit-attempts">—</strong> submission attempts in the last 24h</p><h3 class="ov-sub">Attempt outcomes</h3><div id="outcomes" class="list"></div><p class="note">Current job statuses and historical attempt outcomes measure different things.</p></div></details>
<details class="ov-fold"><summary>Technical details</summary><div class="ov-fold-body"><div class="list">
<div class="pair"><span>Heartbeat</span><strong id="heartbeat-age">—</strong></div>
<div class="pair"><span>Worker running for</span><strong id="worker-age">—</strong></div>
<div class="pair"><span>Last recorded activity</span><strong id="progress-age">—</strong></div>
<div class="pair"><span>Latest recorded job action</span><strong id="action-age">—</strong></div>
<div class="pair"><span>Worker PID</span><span id="worker-pid">—</span></div>
<div class="pair"><span>Supervisor PID</span><span id="supervisor-pid">—</span></div>
<div class="pair"><span>Worker state code</span><span id="wait-reason">—</span></div>
<div class="pair"><span>Restarts</span><strong id="restarts">—</strong></div>
</div><p class="note">Recorded activity includes pacing and browser activity. It does not show submission progress.</p></div></details></div>
</div><section id="panel-logs" class="panel logs-panel" role="tabpanel" aria-labelledby="tab-logs" tabindex="0" hidden>
<h2>Live operational logs</h2><p class="muted">Operational activity logs; not private LLM reasoning or credential/mailbox logs.</p>
<div class="log-controls"><button id="logs-pause" type="button" aria-pressed="false">Pause</button><label for="logs-follow"><input id="logs-follow" type="checkbox" checked> Auto-follow latest</label><p id="logs-status" class="log-status" role="status" aria-live="polite">Select this tab to load operational activity.</p></div>
<div id="logs-error" class="banner error" role="alert" hidden></div>
<div id="logs-window" class="log-window" role="region" aria-label="Operational activity tail, oldest first" tabindex="0"><div id="logs-output" role="list"><p class="log-empty">No activity fetched yet.</p></div></div>
<p id="logs-tail-note" class="note">Showing the latest activity entries, oldest first (up to 200).</p></section>
""" + applied_jobs_ui.PANEL + manual_jobs_ui.PANEL + skipped_jobs_ui.PANEL + """
<footer>Credentials, prompts and raw ledger evidence are never shown here. Receipt screenshots can contain personal details. Bound to 127.0.0.1 only. Returning a job to the queue needs this page's per-install token. It never submits an application by itself.</footer></main><script>""" + _PAGE_SCRIPT + """</script></body></html>""")
_SCRIPT_HASH = base64.b64encode(hashlib.sha256(_PAGE_SCRIPT.encode('utf-8')).digest()).decode('ascii')


class CampaignMonitorHandler(BaseHTTPRequestHandler):
    server_version = 'CampaignMonitor'
    sys_version = ''

    def _send(self, status, content, content_type):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
        self.send_header('Content-Security-Policy',
                         "default-src 'none'; connect-src 'self'; img-src 'self'; style-src 'unsafe-inline'; "
                         f"script-src 'sha256-{_SCRIPT_HASH}'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(content)

    def do_GET(self):
        try:
            monitor_security.check_host(self.headers, self.server.server_port)
        except monitor_security.Rejected as error:
            self._json(error.status, {'error': str(error)})
            return
        path = urlsplit(self.path).path
        if path == '/':
            page = _HTML.replace('__MONITOR_TOKEN__', self.server.token)
            self._send(200, page.encode('utf-8'), 'text/html; charset=utf-8')
        elif path == '/api/status':
            try:
                content = json.dumps(read_status(), allow_nan=False).encode('utf-8')
            except Exception:
                # Do not expose exception messages, filesystem paths, or campaign data.
                self._send(503, b'{"error":"Campaign status is temporarily unavailable"}', 'application/json; charset=utf-8')
                return
            self._send(200, content, 'application/json; charset=utf-8')
        elif path == '/api/logs':
            try:
                content = json.dumps(read_logs(), allow_nan=False).encode('utf-8')
            except Exception:
                # Never return exception details, paths, or raw log data.
                self._send(503, b'{"error":"Campaign logs are temporarily unavailable"}', 'application/json; charset=utf-8')
                return
            self._send(200, content, 'application/json; charset=utf-8')
        elif path == '/api/applied':
            try:
                content = json.dumps(read_applied(), allow_nan=False).encode('utf-8')
            except Exception:
                self._send(503, b'{"error":"Applied jobs are temporarily unavailable"}', 'application/json; charset=utf-8')
                return
            self._send(200, content, 'application/json; charset=utf-8')
        elif re.fullmatch(r'/api/applied/[A-Za-z0-9_-]{1,128}/receipt', path):
            try:
                image = read_receipt(path.split('/')[3])
            except Exception:
                self._json(503, {'error': 'Receipt image is temporarily unavailable.'})
                return
            if image is None:
                self._json(404, {'error': 'No receipt image is available for this application.'})
            else:
                content, content_type = image
                self._send(200, content, content_type)
        elif path == '/api/skipped':
            try:
                content = json.dumps(read_skipped(), allow_nan=False).encode('utf-8')
            except Exception:
                self._json(503, {'error': 'Skipped jobs are temporarily unavailable.'})
                return
            self._send(200, content, 'application/json; charset=utf-8')
        elif path == '/api/manual':
            try:
                jobs = read_manual()
                content = json.dumps({'jobs': jobs, 'groups': manual_jobs.group_counts(jobs)}, allow_nan=False).encode('utf-8')
            except Exception:
                self._send(503, b'{"error":"Manual jobs are temporarily unavailable"}', 'application/json; charset=utf-8')
                return
            self._send(200, content, 'application/json; charset=utf-8')
        else:
            self._send(404, b'Not found\n', 'text/plain; charset=utf-8')

    def _json(self, status, value):
        self._send(status, json.dumps(value, allow_nan=False).encode('utf-8'), 'application/json; charset=utf-8')

    def do_POST(self):
        path = urlsplit(self.path).path
        if path not in ('/api/manual/resubmit', '/api/skipped/requeue'):
            self._json(404, {'error': 'Not found'})
            return
        try:
            monitor_security.check_write(self.headers, self.server.server_port, self.server.token)
            request = json.loads(monitor_security.read_body(self.headers, self.rfile))
            if not isinstance(request, dict) or not isinstance(request.get('job_id'), str):
                raise manual_jobs.ManualJobError('invalid_request', 'A job_id is required.')
            with contextlib.closing(campaign.connect()) as connection:
                connection.row_factory = sqlite3.Row
                if path == '/api/skipped/requeue':
                    result = skipped_jobs.requeue_skipped(
                        connection, request['job_id'], request.get('answers', []),
                        review_note=request.get('review_note'),
                        nothing_sent_confirmed=request.get('nothing_sent_confirmed') is True)
                else:
                    result = manual_jobs.resubmit(
                        connection, request['job_id'], request.get('answers', []),
                        nothing_sent_confirmed=request.get('nothing_sent_confirmed') is True)
        except monitor_security.Rejected as error:
            self._json(error.status, {'error': str(error)})
        except manual_jobs.ManualJobError as error:
            self._json(409 if error.code in _CONFLICT_CODES else 400, {'error': str(error), 'code': error.code})
        except sqlite3.OperationalError:
            self._json(503, {'error': 'The campaign database is busy. Try again in a moment.'})
        except ValueError:
            self._json(400, {'error': 'The request body is not valid JSON.'})
        except Exception:
            # Never return exception details, paths or campaign data.
            self._json(500, {'error': 'The job could not be resubmitted.'})
        else:
            self._json(200, result)

    def _method_not_allowed(self):
        self.send_response(405)
        self.send_header('Allow', 'GET, POST')
        self.send_header('Content-Length', '0')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()

    do_HEAD = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_CONNECT = do_TRACE = _method_not_allowed

    def log_message(self, format, *args):
        # Request paths can contain sensitive query strings; never log them.
        pass


class MonitorServer(ThreadingHTTPServer):
    """The HTTP server plus the per-install write token that handlers check requests against."""

    def __init__(self, address, handler, token):
        super().__init__(address, handler)
        self.token = token


def make_server(port=8765, token=None):
    """Return an unstarted HTTP server, always bound to IPv4 loopback.

    ``token`` defaults to the per-install secret; tests pass their own so they never read or create it.
    """
    return MonitorServer(('127.0.0.1', port), CampaignMonitorHandler, token or monitor_security.load_token())


def main(argv=None):
    parser = argparse.ArgumentParser(description='Local campaign dashboard.')
    parser.add_argument('--port', type=int, default=8765, help='loopback port (default: 8765)')
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error('--port must be between 0 and 65535')
    with make_server(args.port) as server:
        print(f'Campaign monitor: http://127.0.0.1:{server.server_port}/', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
