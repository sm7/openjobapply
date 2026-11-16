"""Isolated live-log tests; no workers, network, profile, or live database.

Run: python3 -m unittest discover -s test -p 'test_campaign_logs.py'
"""
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import campaign
import campaign_logs
import db


def record(at=1, event='start', **fields):
    return (json.dumps({'at': at, 'event': event, **fields}) + '\n').encode()


class CampaignLogsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.log_path = self.root / 'events.jsonl'
        self.db_path = self.root / 'jobs.db'
        self.connection = sqlite3.connect(self.db_path)
        self.addCleanup(self.connection.close)
        self.connection.executescript(db.SCHEMA)
        self.connection.execute(
            'INSERT INTO jobs(id,source,url,title,company,first_seen,last_seen) VALUES(?,?,?,?,?,?,?)',
            ('job', 'fixture', 'https://example.invalid', 'Analyst', 'Fixture Employer', 1, 1))
        self.connection.commit()

    def read_logs(self):
        with patch.object(campaign_logs.time, 'time', return_value=1234.5):
            return campaign_logs.read_logs(self.log_path, self.db_path)

    def attempt(self, at, result, reason):
        self.connection.execute(
            'INSERT INTO applications(job_id,attempted,result,reason) VALUES(?,?,?,?)',
            ('job', at, result, reason))
        self.connection.commit()

    def test_missing_and_empty_files_have_no_invented_activity(self):
        expected = {'entries': [], 'truncated': False, 'source': 'activity', 'served_at': 1234.5}
        self.assertEqual(self.read_logs(), expected)
        self.assertFalse(self.log_path.exists())
        self.log_path.write_bytes(b'')
        self.assertEqual(self.read_logs(), expected)

    def test_defaults_are_resolved_from_db_without_opening_live_state(self):
        path = self.root / 'logs/campaign/events.jsonl'
        path.parent.mkdir(parents=True)
        path.write_bytes(record())
        with patch.object(db, 'ROOT', self.root), patch.object(db, 'DB_PATH', self.db_path):
            self.assertEqual(campaign_logs.read_logs()['entries'][0]['message'], 'Campaign started')

    def test_latest_two_hundred_complete_lines_are_chronological(self):
        self.log_path.write_bytes(b''.join(record(at) for at in range(250, 0, -1)))
        result = self.read_logs()
        self.assertTrue(result['truncated'])
        self.assertEqual([entry['at'] for entry in result['entries']], list(range(1, 201)))
        self.assertEqual(len(result['entries']), campaign_logs.MAX_LINES)

    def test_append_and_partial_write_becomes_visible_only_on_completion(self):
        self.log_path.write_bytes(record(1))
        self.assertEqual([entry['at'] for entry in self.read_logs()['entries']], [1])
        next_record = record(2, 'worker_start')
        with self.log_path.open('ab') as output:
            output.write(next_record[:-1])
        partial = self.read_logs()
        self.assertEqual([entry['at'] for entry in partial['entries']], [1])
        self.assertTrue(partial['truncated'])
        with self.log_path.open('ab') as output:
            output.write(b'\n' + record(3, 'worker_end', exit_reason='worker_exit'))
        result = self.read_logs()
        self.assertEqual([entry['at'] for entry in result['entries']], [1, 2, 3])
        self.assertFalse(result['truncated'])

    def test_exact_leading_record_boundary_is_retained(self):
        newest = record(2)
        self.log_path.write_bytes(b'old complete line\n' + newest)
        with patch.object(campaign_logs, 'MAX_TAIL_BYTES', len(newest) + 1):
            result = self.read_logs()
        self.assertEqual([entry['at'] for entry in result['entries']], [2])
        self.assertTrue(result['truncated'])

    def test_partial_leading_record_is_discarded(self):
        # The clipped suffix itself is valid JSON, but it is not a whole line.
        newest = record(2)
        false_record = record(99)
        self.log_path.write_bytes(b'x' * 100 + false_record + newest)
        with patch.object(campaign_logs, 'MAX_TAIL_BYTES', len(false_record + newest) + 1):
            result = self.read_logs()
        self.assertEqual([entry['at'] for entry in result['entries']], [2])
        self.assertTrue(result['truncated'])

    def test_byte_budget_includes_boundary_byte_and_never_reads_whole_file(self):
        data = b'x' * (campaign_logs.MAX_TAIL_BYTES * 4) + b'\n' + record(7)

        class BoundedFile(io.BytesIO):
            def read(self, size=-1):
                if size < 0 or size > campaign_logs.MAX_TAIL_BYTES:
                    raise AssertionError('unbounded read')
                self.read_sizes.append(size)
                return super().read(size)

        source = BoundedFile(data)
        source.read_sizes = []
        with patch.object(Path, 'open', return_value=source):
            result = self.read_logs()
        self.assertEqual(source.read_sizes, [campaign_logs.MAX_TAIL_BYTES])
        self.assertEqual([entry['at'] for entry in result['entries']], [7])
        self.assertTrue(result['truncated'])

    def test_oversized_records_are_omitted_without_hiding_following_events(self):
        oversized = record(1, payload='x' * campaign_logs.MAX_RECORD_BYTES)
        self.log_path.write_bytes(oversized + record(2))
        result = self.read_logs()
        self.assertEqual([entry['at'] for entry in result['entries']], [2])
        self.assertTrue(result['truncated'])
        self.log_path.write_bytes(record(1, payload='x' * (campaign_logs.MAX_TAIL_BYTES * 2)) + record(3))
        self.assertEqual([entry['at'] for entry in self.read_logs()['entries']], [3])

    def test_malformed_tail_does_not_backfill_beyond_line_limit(self):
        self.log_path.write_bytes(record(1) + b'not-json\n' * campaign_logs.MAX_LINES)
        result = self.read_logs()
        self.assertEqual(result['entries'], [])
        self.assertTrue(result['truncated'])

    def test_projection_uses_canonical_labels_and_never_raw_event_messages(self):
        secret = 'PRIVATE password=hunter2 https://secret.invalid user@example.invalid'
        self.log_path.write_bytes(b''.join([
            record(1, 'activity', job_id='job', stage='filling', action='uploading_resume',
                   message=secret, reason=secret, evidence=secret, prompt=secret, run_id=secret,
                   company=secret, title=secret, env={'PASSWORD': secret}),
            record(2, 'activity', job_id='job', stage='reviewing'),
            record(3, 'recorded_outcome', job_id='job', result='failed', reason=secret),
            record(4, 'worker_end', exit_reason='worker_failed', exit_code=secret),
            record(5, 'timeout', exit_reason='worker_timeout'),
            record(6, 'restart', exit_reason='recovered_worker', restart_count=secret),
            record(7, 'reserved', job_id='job'),
            record(8, 'end', exit_reason='target_reached'),
            record(9, 'made_up_event', message=secret),
        ]))
        result = self.read_logs()
        self.assertEqual([entry['message'] for entry in result['entries']], [
            campaign.ACTIVITY_ACTIONS['uploading_resume'], campaign.ACTIVITY_STAGE_LABELS['reviewing'],
            'Recorded outcome: failed: No reason recorded', 'Worker ended: worker failed', 'Worker timed out: worker timeout',
            'Worker restarting: recovered worker', 'Application reserved', 'Campaign ended: target reached'])
        self.assertNotIn(secret, json.dumps(result))
        self.assertEqual(result['entries'][0]['company'], 'Fixture Employer')
        self.assertEqual(result['entries'][0]['title'], 'Analyst')
        for entry in result['entries']:
            self.assertEqual(set(entry), {'at', 'event', 'message', 'job_id', 'company', 'title'})
        self.assertIsNone(result['entries'][3]['company'])

    def test_missing_job_or_database_has_null_enrichment_and_creates_nothing(self):
        self.log_path.write_bytes(record(1, 'reserved', job_id='unknown'))
        entry = self.read_logs()['entries'][0]
        self.assertEqual((entry['job_id'], entry['company'], entry['title']), ('unknown', None, None))
        missing_db = self.root / 'missing' / 'jobs.db'
        result = campaign_logs.read_logs(self.log_path, missing_db)
        self.assertIsNone(result['entries'][0]['company'])
        self.assertFalse(missing_db.parent.exists())

    def test_enrichment_opens_read_only_and_does_not_modify_database(self):
        self.log_path.write_bytes(record(1, 'reserved', job_id='job'))
        before = self.db_path.read_bytes()
        connect = sqlite3.connect
        with patch.object(campaign_logs.sqlite3, 'connect', wraps=connect) as opened:
            self.read_logs()
        self.assertIn('?mode=ro', opened.call_args.args[0])
        self.assertTrue(opened.call_args.kwargs['uri'])
        self.assertEqual(self.db_path.read_bytes(), before)

    def test_outcome_reasons_match_preceding_attempt_result_and_are_redacted(self):
        secret_reason = ('Employer verification required: user@example.invalid https://private.invalid/path '
                         'password=hunter2 otp=123456 token=sekret')
        self.attempt(10, 'skipped', 'old skip')
        self.attempt(20, 'manual', secret_reason)
        self.attempt(30, 'failed', 'Employer form unavailable')
        self.attempt(40, 'skipped', 'new skip')
        self.attempt(50, 'deferred', 'Waiting for employer verification')
        self.attempt(60, 'applied', 'PRIVATE applied reason')
        events = [(5, 'manual'), (15, 'skipped'), (17, 'deferred'), (25, 'manual'),
                  (35, 'skipped'), (36, 'failed'), (45, 'skipped'), (55, 'deferred'), (65, 'applied')]
        self.log_path.write_bytes(b''.join(record(at, 'recorded_outcome', job_id='job', result=result)
                                          for at, result in events))
        entries = self.read_logs()['entries']
        self.assertEqual([entry['message'] for entry in entries], [
            'Recorded outcome: manual: No reason recorded',
            'Recorded outcome: skipped: old skip',
            'Recorded outcome: deferred: No reason recorded',
            'Recorded outcome: manual: ' + campaign.outcome_reason(secret_reason),
            'Recorded outcome: skipped: No reason recorded',
            'Recorded outcome: failed: Employer form unavailable',
            'Recorded outcome: skipped: new skip',
            'Recorded outcome: deferred: Waiting for employer verification',
            'Recorded outcome: applied'])
        serialized = json.dumps(entries)
        for secret in ('user@example.invalid', 'private.invalid', 'hunter2', '123456', 'sekret',
                       'PRIVATE applied reason'):
            self.assertNotIn(secret, serialized)

    def test_outcome_reason_ties_are_deterministic_bounded_and_malformed_reason_is_ignored(self):
        self.attempt(10, 'skipped', 'first')
        self.attempt(10, 'skipped', 'second ' + 'word ' * 2000)
        self.attempt(20, 'skipped', sqlite3.Binary(b'not text'))
        self.log_path.write_bytes(record(10, 'recorded_outcome', job_id='job', result='skipped')
                                  + record(20, 'recorded_outcome', job_id='job', result='skipped'))
        entries = self.read_logs()['entries']
        prefix = 'Recorded outcome: skipped'
        self.assertTrue(entries[0]['message'].startswith(prefix + ': second '))
        self.assertLessEqual(len(entries[0]['message']), len(prefix) + 2 + 600)
        self.assertEqual(entries[1]['message'], prefix + ': No reason recorded')

    def test_malformed_types_numbers_and_unknown_enums_are_skipped(self):
        malformed = [b'not json\n', b'\xff\n', b'[]\n', b'null\n', b'42\n', b'"text"\n',
                     b'{"at":NaN,"event":"start"}\n', b'{"at":Infinity,"event":"start"}\n',
                     (b'[' * 2000) + (b']' * 2000) + b'\n']
        for at in (True, False, None, '1', [], {}, -1, float('nan'), float('inf'), -float('inf'), 10**400):
            malformed.append(record(at))
        for kind in (None, [], {}, 3, True, 'unknown'):
            malformed.append(record(1, kind))
        for job_id in (True, 42, [], {}, '', 'https://private.invalid', 'a' * 129):
            malformed.append(record(1, 'reserved', job_id=job_id))
        for stage in (None, [], {}, 'unknown'):
            malformed.append(record(1, 'activity', stage=stage))
        for action in ([], {}, False, 'unknown'):
            malformed.append(record(1, 'activity', stage='filling', action=action))
        for result in (None, [], {}, False, 'unknown'):
            malformed.append(record(1, 'recorded_outcome', job_id='job', result=result))
        for reason in ([], {}, True, 'password=private'):
            malformed.append(record(1, 'worker_end', exit_reason=reason))
        malformed.extend([record(1, 'reserved'), record(1, 'recorded_outcome', result='applied')])
        self.log_path.write_bytes(b''.join(malformed) + record(2))
        self.assertEqual([entry['at'] for entry in self.read_logs()['entries']], [2])

    def test_valid_large_integer_timestamp_does_not_overflow_sqlite_binding(self):
        self.log_path.write_bytes(record(10**30, 'recorded_outcome', job_id='job', result='skipped'))
        self.assertEqual(self.read_logs()['entries'][0]['message'], 'Recorded outcome: skipped: No reason recorded')

    def test_actual_source_io_or_database_failure_propagates(self):
        with patch.object(Path, 'open', side_effect=PermissionError('denied')):
            with self.assertRaises(PermissionError):
                self.read_logs()
        self.log_path.write_bytes(record(1, 'reserved', job_id='job'))
        corrupt = self.root / 'corrupt.db'
        corrupt.write_bytes(b'not a sqlite database')
        with self.assertRaises(sqlite3.DatabaseError):
            campaign_logs.read_logs(self.log_path, corrupt)


if __name__ == '__main__':
    unittest.main()
