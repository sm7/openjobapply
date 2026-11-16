"""Receipt HTTP boundaries and reviewed skipped-job recovery on isolated campaign data."""
import base64
import contextlib
import http.client
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import campaign
import campaign_monitor

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
TOKEN = 'fixture-rework-token-' + 'x' * 24


class DashboardRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        (self.root / 'data').mkdir()
        (self.root / 'data/campaign.json').write_text(json.dumps({'target_total': None}))
        for name, value in [('ROOT', self.root), ('CONFIG', self.root / 'data/campaign.json'),
                            ('PACE', self.root / 'data/ratelimit.json')]:
            self.stack.enter_context(patch.object(campaign, name, value))
        self.stack.enter_context(patch.object(campaign.db, 'ROOT', self.root))
        self.stack.enter_context(patch.object(campaign.db, 'DB_PATH', self.root / 'data/jobs.db'))
        self.con = campaign.connect()
        self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)
        self.job('review', 'skipped', 'No application opened. Not submitted.')
        self.job('applied', 'applied', 'Your application was sent! Screenshot logs/campaign/receipts/saved.png')
        receipts = self.root / 'logs/campaign/receipts'
        receipts.mkdir(parents=True)
        (receipts / 'saved.png').write_bytes(PNG)
        self.server = campaign_monitor.make_server(0, token=TOKEN)
        self.port = self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (self.server.shutdown(), self.server.server_close()))

    def job(self, job_id, status, evidence):
        self.con.execute('INSERT INTO jobs(id,source,source_id,url,title,company,status,score_notes,first_seen,last_seen) '
                         'VALUES(?,?,?,?,?,?,?,?,?,?)',
                         (job_id, 'fixture', job_id, 'https://example.invalid/' + job_id,
                          'Healthcare Data Analyst', 'Fixture Employer', status, 'rejected:location', 1, 1))
        self.con.execute('INSERT INTO applications(job_id,attempted,result,reason,evidence,run_id) VALUES(?,?,?,?,?,?)',
                         (job_id, 100, status, evidence, evidence, 'fixture'))
        self.con.commit()

    def request(self, method, path, value=None, headers=None):
        body = json.dumps(value).encode() if value is not None else b''
        merged = {'Host': f'127.0.0.1:{self.port}', 'Content-Type': 'application/json',
                  'Origin': f'http://127.0.0.1:{self.port}', 'X-Monitor-Token': TOKEN}
        merged.update(headers or {})
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        for key, val in merged.items():
            conn.putheader(key, val)
        conn.putheader('Content-Length', str(len(body)))
        conn.endheaders(body)
        response = conn.getresponse()
        content = response.read()
        result = (response.status, dict(response.getheaders()), content)
        conn.close()
        return result

    def test_receipt_response_contains_real_image_and_private_browser_headers(self):
        status, headers, body = self.request('GET', '/api/applied/applied/receipt')
        self.assertEqual((status, headers['Content-Type'], body), (200, 'image/png', PNG))
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(headers['Cross-Origin-Resource-Policy'], 'same-origin')
        self.assertIn("img-src 'self'", headers['Content-Security-Policy'])

    def test_missing_non_applied_and_path_requests_cannot_serve_arbitrary_files(self):
        for path in ('/api/applied/missing/receipt', '/api/applied/review/receipt',
                     '/api/applied/../receipt', '/api/applied/%2e%2e/receipt',
                     '/api/applied/applied/receipt/saved.png'):
            with self.subTest(path=path):
                status, _, body = self.request('GET', path)
                self.assertEqual(status, 404)
                self.assertNotEqual(body, PNG)

    def test_foreign_hosts_cannot_read_receipts_or_skipped_jobs(self):
        for path in ('/api/applied/applied/receipt', '/api/skipped'):
            with self.subTest(path=path):
                status, _, body = self.request('GET', path, headers={'Host': f'evil.example:{self.port}'})
                self.assertEqual(status, 403)
                self.assertNotIn(b'Fixture Employer', body)
                self.assertNotEqual(body, PNG)

    def test_skipped_read_lists_only_skipped_jobs_and_preserves_database(self):
        before = list(self.con.iterdump())
        status, _, body = self.request('GET', '/api/skipped')
        self.assertEqual(status, 200)
        self.assertEqual([job['job_id'] for job in json.loads(body)['jobs']], ['review'])
        self.assertEqual(list(self.con.iterdump()), before)

    def test_skipped_write_rejects_cross_site_or_unauthenticated_requests_without_mutation(self):
        before = list(self.con.iterdump())
        for headers, expected in [({'X-Monitor-Token': ''}, 403),
                                  ({'Origin': 'https://evil.example'}, 403),
                                  ({'Host': f'evil.example:{self.port}'}, 403),
                                  ({'Content-Type': 'text/plain'}, 415)]:
            with self.subTest(headers=headers):
                status, _, _ = self.request('POST', '/api/skipped/requeue', {
                    'job_id': 'review', 'answers': [], 'review_note': 'Nationwide review approved.'}, headers)
                self.assertEqual(status, expected)
        self.assertEqual(list(self.con.iterdump()), before)

    def test_reviewed_queue_request_preserves_history_answers_and_confirmed_total(self):
        before = campaign.confirmed(self.con)
        payload = {'job_id': 'review', 'review_note': 'Review the updated nationwide geography.',
                   'answers': [{'question': 'Exact reviewed question?', 'answer': 'Reviewed factual answer.'}]}
        status, _, body = self.request('POST', '/api/skipped/requeue', payload)
        self.assertEqual(status, 200, body)
        self.assertTrue(json.loads(body)['requeued'])
        self.assertEqual(self.con.execute("SELECT status FROM jobs WHERE id='review'").fetchone()[0], 'queued')
        self.assertEqual([row[0] for row in self.con.execute(
            "SELECT result FROM applications WHERE job_id='review' ORDER BY id")], ['skipped', 'deferred'])
        self.assertEqual(tuple(self.con.execute(
            "SELECT question,answer FROM job_answers WHERE job_id='review'").fetchone()),
            ('Exact reviewed question?', 'Reviewed factual answer.'))
        self.assertEqual(campaign.confirmed(self.con), before)
        again, _, _ = self.request('POST', '/api/skipped/requeue', payload)
        self.assertEqual(again, 409)

    def test_skipped_route_cannot_requeue_an_applied_job(self):
        before = list(self.con.iterdump())
        status, _, _ = self.request('POST', '/api/skipped/requeue', {
            'job_id': 'applied', 'review_note': 'Do not duplicate.', 'answers': [],
            'nothing_sent_confirmed': True})
        self.assertEqual(status, 409)
        self.assertEqual(list(self.con.iterdump()), before)


if __name__ == '__main__':
    unittest.main()
