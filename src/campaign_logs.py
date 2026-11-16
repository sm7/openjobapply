"""Bounded, read-only projection of operational campaign events.

Only complete records in the final 256 KiB and final 200 lines are considered.
The byte budget includes the byte used to recognize a leading record boundary.
No worker output, arbitrary event messages, or live campaign state is read.
"""
import contextlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time

import campaign
import db

MAX_TAIL_BYTES = 256 * 1024
MAX_LINES = 200
MAX_RECORD_BYTES = 16 * 1024
_RESULT_LABELS = {
    'applied': 'Recorded outcome: applied',
    'manual': 'Recorded outcome: manual',
    'failed': 'Recorded outcome: failed',
    'skipped': 'Recorded outcome: skipped',
    'deferred': 'Recorded outcome: deferred',
}
_EVENT_LABELS = {
    'reserved': 'Application reserved',
    'requeued': 'Application requeued after review',
    'reconciled': 'Employer receipt reconciled',
    'start': 'Campaign started',
    'end': 'Campaign ended',
    'worker_start': 'Worker started',
    'worker_end': 'Worker ended',
    'restart': 'Worker restarting',
    'timeout': 'Worker timed out',
}
_JOB_ID = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')


def _tail(path):
    try:
        with path.open('rb') as source:
            size = source.seek(0, 2)
            # When clipped, reserve one byte for the preceding newline. This
            # distinguishes a whole first record from the end of a huge record.
            start = max(0, size - (MAX_TAIL_BYTES - 1)) if size > MAX_TAIL_BYTES else 0
            source.seek(start - 1 if start else 0)
            data = source.read(min(size, MAX_TAIL_BYTES))
    except FileNotFoundError:
        return [], False
    truncated = bool(start)
    if start:
        previous, data = data[:1], data[1:]
        if previous != b'\n':
            _, _, data = data.partition(b'\n')
    lines = data.split(b'\n')
    unfinished = lines.pop()
    truncated = (truncated or bool(unfinished) or len(lines) > MAX_LINES
                 or any(len(line) > MAX_RECORD_BYTES for line in lines[-MAX_LINES:]))
    return lines[-MAX_LINES:], truncated


def _reject_constant(value):
    raise ValueError('non-finite JSON number')


def _project(line):
    if not line or len(line) > MAX_RECORD_BYTES:
        return None
    try:
        value = json.loads(line, parse_constant=_reject_constant)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return None
    if not isinstance(value, dict):
        return None
    at, kind, job_id = value.get('at'), value.get('event'), value.get('job_id')
    if type(at) not in (int, float) or at < 0:
        return None
    try:
        if not math.isfinite(at):
            return None
    except OverflowError:
        return None
    if not isinstance(kind, str):
        return None
    if job_id is not None and (not isinstance(job_id, str) or not _JOB_ID.fullmatch(job_id)):
        return None
    if kind == 'activity':
        stage, action = value.get('stage'), value.get('action')
        if not isinstance(stage, str) or stage not in campaign.ACTIVITY_STAGE_LABELS:
            return None
        if action is not None and (not isinstance(action, str) or action not in campaign.ACTIVITY_ACTIONS):
            return None
        message = campaign.ACTIVITY_ACTIONS[action] if action else campaign.ACTIVITY_STAGE_LABELS[stage]
    elif kind == 'recorded_outcome':
        result = value.get('result')
        if not job_id or not isinstance(result, str) or result not in _RESULT_LABELS:
            return None
        message = _RESULT_LABELS[result]
    elif kind in _EVENT_LABELS:
        if kind in ('reserved', 'requeued', 'reconciled') and not job_id:
            return None
        message = _EVENT_LABELS[kind]
        reason = value.get('exit_reason')
        if reason is not None:
            if not isinstance(reason, str) or reason not in campaign.EXIT_REASONS:
                return None
            message += ': ' + reason.replace('_', ' ')
    else:
        return None
    return {'at': at, 'event': kind, 'message': message,
            'job_id': job_id, 'company': None, 'title': None}


def _enrich(entries, path):
    job_ids = sorted({entry['job_id'] for entry in entries if entry['job_id']})
    if not job_ids:
        return
    try:
        path.stat()
    except FileNotFoundError:
        return
    # Do not use db.connect(): it creates directories, initializes and migrates.
    with contextlib.closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True,
                                          timeout=0.2)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA query_only = ON')
        placeholders = ','.join('?' for _ in job_ids)
        jobs = {row['id']: row for row in connection.execute(
            'SELECT id,substr(company,1,240) AS company,substr(title,1,240) AS title '
            f'FROM jobs WHERE id IN ({placeholders})', job_ids)}
        for entry in entries:
            job = jobs.get(entry['job_id'])
            if job:
                for field in ('company', 'title'):
                    entry[field] = job[field] if isinstance(job[field], str) else None
            if entry['event'] != 'recorded_outcome' or entry['message'] == _RESULT_LABELS['applied']:
                continue
            # Only the latest preceding attempt can explain this event, and its
            # outcome must match the canonical label projected from the event.
            # Bound text before handing it to the shared campaign sanitizer.
            attempt = connection.execute(
                'SELECT result,substr(reason,1,4096) AS reason FROM applications '
                'WHERE job_id=? AND attempted<=? ORDER BY attempted DESC,id DESC LIMIT 1',
                (entry['job_id'], float(entry['at']))).fetchone()
            if attempt and _RESULT_LABELS.get(attempt['result']) == entry['message']:
                reason = campaign.outcome_reason(attempt['reason'])
                if reason:
                    entry['message'] += ': ' + reason


def read_logs(log_path=None, db_path=None):
    """Return a fresh bounded tail; optional paths are for isolated callers/tests.

    Missing files yield no invented activity or enrichment. Other filesystem or
    SQLite errors propagate so the HTTP layer can report an unavailable source.
    ``truncated`` also indicates a trailing, not-yet-complete record was omitted.
    """
    path = Path(log_path) if log_path is not None else db.ROOT / 'logs/campaign/events.jsonl'
    lines, truncated = _tail(path)
    entries = [entry for line in lines if (entry := _project(line)) is not None]
    entries.sort(key=lambda entry: entry['at'])
    _enrich(entries, Path(db_path) if db_path is not None else db.DB_PATH)
    for entry in entries:
        if entry['message'] in _RESULT_LABELS.values() and entry['message'] != _RESULT_LABELS['applied']:
            entry['message'] += ': No reason recorded'
    return {'entries': entries, 'truncated': truncated, 'source': 'activity', 'served_at': time.time()}
