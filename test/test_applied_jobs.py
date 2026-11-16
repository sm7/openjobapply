"""Applied jobs tab: what the ledger proves, what the page never receives, and that the route only reads.

Isolated temporary state and an ephemeral-port server; no worker, browser or real database.
Run: python3 -m unittest discover -s test -p 'test_applied_jobs.py'
"""
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
import applied_jobs
import campaign
import campaign_monitor

TOKEN = 'fixture-token-' + 'x' * 24

PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9i0AAAAASUVORK5CYII=')
WEBP = base64.b64decode('UklGRiIAAABXRUJQVlA4IBYAAAAwAQCdASoBAAEADsD+JaQAA3AAAAAA')
JPEG = base64.b64decode(
    '/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////'
    '2wBDAf//////////////////////////////////////////////////////////////////////////////////////'
    'wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAf/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/'
    '9oADAMBAAIQAxAAAAF//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABBQJ//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/'
    'aAAgBAwEBPwF//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAgEBPwF//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/'
    'aAAgBAQAGPwJ//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPyF//9oADAMBAAIAAwAAABAf/'
    '8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAwEBPxB//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAgEBPxB/'
    '/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxB//9k=')


class AppliedFixture(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        (self.root / 'data').mkdir()
        self.receipts = self.root / 'logs/campaign/receipts'
        self.receipts.mkdir(parents=True)
        self.stack.enter_context(patch.object(applied_jobs.db, 'ROOT', self.root))
        (self.root / 'data/campaign.json').write_text(json.dumps({'target_total': 50}))
        for name, value in [('ROOT', self.root), ('CONFIG', self.root / 'data/campaign.json'),
                            ('PACE', self.root / 'data/ratelimit.json')]:
            self.stack.enter_context(patch.object(campaign, name, value))
        self.stack.enter_context(patch.object(campaign.db, 'DB_PATH', self.root / 'data/jobs.db'))
        self.con = campaign.connect()
        self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)

    def attempt(self, job_id, result, attempted, evidence):
        self.con.execute('INSERT INTO applications(job_id,attempted,result,reason,evidence,run_id) VALUES(?,?,?,?,?,?)',
                         (job_id, attempted, result, None, evidence, 'campaign-50'))
        self.con.commit()

    def job(self, job_id, status='applied', evidence='Your application was sent to Employer!', attempted=100.0,
            reservation='applied', result='applied', url=None):
        self.con.execute(
            'INSERT INTO jobs(id,source,source_id,url,title,company,location,status,first_seen,last_seen) '
            'VALUES(?,?,?,?,?,?,?,?,?,?)',
            (job_id, 'fixture', job_id, url or 'https://example.invalid/' + job_id, 'Analyst ' + job_id,
             'Employer ' + job_id, 'Boston, MA', status, 1, 1))
        self.attempt(job_id, result, attempted, evidence)
        if reservation:
            self.con.execute('INSERT INTO campaign_reservations(job_id,reserved_at,state,reason) VALUES(?,?,?,?)', (job_id, 50.0, reservation, None))
            self.con.commit()
        return job_id


class ListAppliedTests(AppliedFixture):
    def listed(self):
        return applied_jobs.list_applied(self.con, self.receipts)

    def test_only_applied_jobs_are_listed_newest_application_first(self):
        self.job('old', attempted=100.0)
        self.job('new', attempted=300.0)
        self.job('manual-one', status='manual', result='manual', reservation=None)
        self.job('queued-one', status='queued', result='deferred', reservation=None)
        self.assertEqual([j['job_id'] for j in self.listed()], ['new', 'old'])

    def test_a_job_with_several_attempts_is_listed_once_with_its_applied_attempt(self):
        self.job('retried', attempted=300.0, evidence='Your application was sent to Retried!')
        self.attempt('retried', 'deferred', 100.0, 'earlier evidence')
        listed = self.listed()
        self.assertEqual(len(listed), 1)
        self.assertEqual((listed[0]['applied_at'], listed[0]['attempts']), (300.0, 2))
        self.assertEqual(listed[0]['confirmation'], 'Your application was sent to Retried!')

    def test_a_job_with_several_applied_attempts_shows_the_newest_one(self):
        self.job('twice', attempted=100.0, evidence='Your application was sent to Old!')
        self.attempt('twice', 'applied', 500.0, 'Your application was received at New!')
        listed = self.listed()
        self.assertEqual((listed[0]['applied_at'], listed[0]['confirmation']),
                         (500.0, 'Your application was received at New!'))

    def test_a_job_that_left_the_applied_status_is_not_listed_even_with_an_old_applied_attempt(self):
        self.job('moved', status='manual', result='applied', reservation=None)
        self.assertEqual(self.listed(), [])

    def test_the_controller_check_shows_only_for_an_applied_reservation(self):
        self.job('checked', reservation='applied')
        self.job('legacy', reservation=None)
        self.job('odd', reservation='reserved')
        checked = {j['job_id']: j['controller_checked'] for j in self.listed()}
        self.assertEqual(checked, {'checked': True, 'legacy': False, 'odd': False})

    def test_the_list_works_on_a_database_that_predates_reservations(self):
        self.job('legacy', reservation=None)
        self.con.execute('DROP TABLE campaign_reservations')
        self.con.commit()
        self.assertEqual([(j['job_id'], j['controller_checked']) for j in self.listed()], [('legacy', False)])

    def test_listing_reads_only(self):
        self.job('a')
        before = list(self.con.iterdump())
        self.listed()
        self.assertEqual(list(self.con.iterdump()), before)


class PrivacyTests(AppliedFixture):
    def test_the_page_never_receives_contact_details_or_the_whole_evidence(self):
        evidence = ('LinkedIn Easy Apply dialog showed: "Your application was sent to Employer!" contact email '
                    'person@example.com, phone 617-555-0100, street 1 Example Road. password=hunter2')
        self.job('contact', evidence=evidence)
        listed = applied_jobs.list_applied(self.con, self.receipts)
        blob = json.dumps(listed)
        for secret in ('person@example.com', '617-555-0100', 'Example Road', 'hunter2', 'contact email'):
            self.assertNotIn(secret, blob)
        self.assertNotIn('evidence', listed[0])

    def test_a_phone_number_or_email_inside_the_confirmation_clause_is_redacted(self):
        text = applied_jobs.confirmation_sentence('Your application was sent to 617-555-0100 and person@example.com now.')
        self.assertTrue(text)
        self.assertNotIn('617-555-0100', text)
        self.assertNotIn('person@example.com', text)

    def test_the_confirmation_is_one_bounded_clause_or_none(self):
        self.assertEqual(applied_jobs.confirmation_sentence('Thank you for applying! More text here.'),
                         'Thank you for applying!')
        self.assertIsNone(applied_jobs.confirmation_sentence('Resume uploaded. Nothing else happened.'))
        self.assertIsNone(applied_jobs.confirmation_sentence(None))
        long = 'Your application was received ' + 'word ' * 300
        self.assertLessEqual(len(applied_jobs.confirmation_sentence(long)), applied_jobs.MAX_CONFIRMATION)

    def test_an_unsafe_posting_address_is_never_sent_as_a_link(self):
        self.job('bad', url='javascript:alert(1)')
        self.assertIsNone(applied_jobs.list_applied(self.con, self.receipts)[0]['posting_url'])


class ReceiptFileTests(AppliedFixture):
    def test_only_exact_image_references_in_the_two_allowed_folders_are_recognized(self):
        for evidence in ('Screenshot logs/campaign/receipts/../../secret.png',
                         'Screenshot logs/campaign/receipts/sub/dir.png',
                         'Screenshot /etc/passwd', 'Screenshot logs/campaign/receipts/file.txt',
                         'Screenshot logs/campaign/receipts/file.png.exe',
                         'Screenshot logs/campaign/receipts/file.png/other',
                         'Screenshot logs/campaign/receipts/file.png?download=true',
                         'Screenshot /tmp/logs/campaign/receipts/file.png',
                         'Screenshot ../logs/campaign/receipts/file.png',
                         'Screenshot https://example.invalid/logs/ats/file.webp',
                         'Screenshot logs/ats/../../secret.webp',
                         'Screenshot logs/ats/file.webp.bak', None, 42, {}):
            with self.subTest(evidence=evidence):
                self.assertIsNone(applied_jobs.receipt_file(evidence))
        self.assertEqual(applied_jobs.receipt_file('Screenshot logs/campaign/receipts/ok-1.webp'), 'ok-1.webp')
        self.assertEqual(applied_jobs.receipt_file('{"screenshot": "logs/ats/ok-1.webp"}'), 'ok-1.webp')

    def test_a_named_file_is_available_only_with_supported_image_bytes(self):
        (self.receipts / 'here.webp').write_bytes(WEBP)
        (self.receipts / 'invalid.png').write_bytes(b'not an image')
        (self.receipts / 'wrong.webp').write_bytes(PNG)
        self.job('here', evidence='Your application was sent! Screenshot logs/campaign/receipts/here.webp')
        self.job('gone', evidence='Your application was sent! Screenshot logs/campaign/receipts/gone.webp')
        self.job('none', evidence='Your application was sent!')
        self.job('invalid', evidence='Your application was sent! Screenshot logs/campaign/receipts/invalid.png')
        self.job('wrong', evidence='Your application was sent! Screenshot logs/campaign/receipts/wrong.webp')
        listed = {j['job_id']: j for j in applied_jobs.list_applied(self.con, self.receipts)}
        self.assertEqual([(listed[k]['receipt_file'], listed[k]['receipt_file_saved']) for k in ('here', 'gone', 'none')],
                         [('here.webp', True), ('gone.webp', False), (None, False)])
        self.assertEqual(listed['here']['receipt_url'], '/api/applied/here/receipt')
        for job_id in ('gone', 'none', 'invalid', 'wrong'):
            self.assertIsNone(listed[job_id]['receipt_url'])
            self.assertIsNone(applied_jobs.receipt_image(self.con, job_id))
        self.assertEqual(applied_jobs.summarize(list(listed.values()))['with_receipt_file'], 1)

    def test_supported_images_return_the_exact_bytes_and_mime_type(self):
        for name, data, content_type in (('image.png', PNG, 'image/png'), ('image.jpg', JPEG, 'image/jpeg'),
                                         ('image.jpeg', JPEG, 'image/jpeg'), ('image.webp', WEBP, 'image/webp')):
            with self.subTest(name=name):
                (self.receipts / name).write_bytes(data)
                self.job(name, evidence='Your application was sent! Screenshot logs/campaign/receipts/' + name)
                self.assertEqual(applied_jobs.receipt_image(self.con, name), (data, content_type))
        before = list(self.con.iterdump())
        self.assertEqual(applied_jobs.receipt_image(self.con, 'image.png'), (PNG, 'image/png'))
        self.assertEqual(list(self.con.iterdump()), before)

    def test_truncated_images_are_not_advertised_or_returned(self):
        for name, data in (('truncated.png', PNG[:24]), ('truncated.jpg', JPEG[:20]),
                           ('truncated.webp', WEBP[:20])):
            with self.subTest(name=name):
                (self.receipts / name).write_bytes(data)
                self.job(name, evidence='Your application was received! logs/campaign/receipts/' + name)
                self.assertIsNone(applied_jobs.receipt_image(self.con, name))
        self.assertTrue(all(job['receipt_url'] is None for job in applied_jobs.list_applied(self.con)))

    def test_ats_evidence_uses_its_exact_path_not_a_campaign_file_with_the_same_name(self):
        ats = self.root / 'logs/ats'
        ats.mkdir()
        (ats / 'confirmation.webp').write_bytes(WEBP)
        (self.receipts / 'confirmation.webp').write_bytes(b'unrelated')
        self.job('ats', evidence='Your application was received! {"screenshot":"logs/ats/confirmation.webp"}')
        self.assertEqual(applied_jobs.receipt_image(self.con, 'ats'), (WEBP, 'image/webp'))
        listed = applied_jobs.list_applied(self.con, self.receipts)[0]
        self.assertEqual((listed['receipt_file'], listed['receipt_url']),
                         ('confirmation.webp', '/api/applied/ats/receipt'))
        (ats / 'confirmation.webp').unlink()
        (self.receipts / 'confirmation.webp').write_bytes(WEBP)
        self.assertIsNone(applied_jobs.receipt_image(self.con, 'ats'))
        self.assertIsNone(applied_jobs.list_applied(self.con, self.receipts)[0]['receipt_url'])

    def test_only_the_latest_applied_attempt_can_supply_a_receipt(self):
        (self.receipts / 'old.png').write_bytes(PNG)
        (self.receipts / 'new.webp').write_bytes(WEBP)
        self.job('latest', evidence='Your application was sent! logs/campaign/receipts/old.png')
        self.attempt('latest', 'applied', 200, 'Your application was sent! logs/campaign/receipts/new.webp')
        self.attempt('latest', 'deferred', 300, 'logs/campaign/receipts/old.png')
        self.assertEqual(applied_jobs.receipt_image(self.con, 'latest'), (WEBP, 'image/webp'))
        self.assertEqual(applied_jobs.list_applied(self.con, self.receipts)[0]['receipt_file'], 'new.webp')
        self.attempt('latest', 'applied', 400, 'Your application was sent! logs/campaign/receipts/missing.png')
        self.assertIsNone(applied_jobs.receipt_image(self.con, 'latest'))
        self.assertIsNone(applied_jobs.list_applied(self.con, self.receipts)[0]['receipt_url'])

    def test_missing_malformed_or_unlinked_evidence_exposes_no_image(self):
        (self.receipts / 'unlinked.png').write_bytes(PNG)
        for index, evidence in enumerate((None, '', 'not a screenshot', 'logs/campaign/receipts/unlinked.png.exe',
                                           '/tmp/logs/campaign/receipts/unlinked.png',
                                           'logs/campaign/receipts/../unlinked.png')):
            job_id = str(index)
            self.job(job_id, evidence=evidence)
            self.assertIsNone(applied_jobs.receipt_image(self.con, job_id))
        self.assertTrue(all(job['receipt_url'] is None for job in applied_jobs.list_applied(self.con, self.receipts)))
        self.assertIsNone(applied_jobs.receipt_image(self.con, 'unknown'))
        self.assertIsNone(applied_jobs.receipt_image(self.con, '../../unlinked.png'))

    def test_non_applied_jobs_never_expose_a_receipt_even_with_an_applied_attempt(self):
        (self.receipts / 'private.png').write_bytes(PNG)
        for status in ('manual', 'queued', 'rejected'):
            self.job(status, status=status, reservation=None,
                     evidence='Your application was sent! logs/campaign/receipts/private.png')
            self.assertIsNone(applied_jobs.receipt_image(self.con, status))
        self.job('no-applied-attempt', result='deferred', evidence='logs/campaign/receipts/private.png')
        self.assertIsNone(applied_jobs.receipt_image(self.con, 'no-applied-attempt'))
        self.assertEqual(applied_jobs.list_applied(self.con, self.receipts), [])

    def test_symlink_escapes_are_not_available_from_either_root(self):
        outside = self.root / 'private.png'
        outside.write_bytes(PNG)
        ats = self.root / 'logs/ats'
        ats.mkdir()
        for index, folder in enumerate((self.receipts, ats)):
            (folder / 'escape.png').symlink_to(outside)
            relative = (folder / 'escape.png').relative_to(self.root).as_posix()
            self.job(str(index), evidence='Your application was sent! ' + relative)
            self.assertIsNone(applied_jobs.receipt_image(self.con, str(index)))
        self.assertTrue(all(not job['receipt_file_saved'] and job['receipt_url'] is None
                            for job in applied_jobs.list_applied(self.con, self.receipts)))

    def test_an_allowed_root_cannot_be_redirected_to_another_directory(self):
        private = self.root / 'private-images'
        private.mkdir()
        (private / 'private.png').write_bytes(PNG)
        for index, folder in enumerate((self.receipts, self.root / 'logs/ats')):
            if folder.exists():
                folder.rmdir()
            folder.symlink_to(private, target_is_directory=True)
            relative = (folder / 'private.png').relative_to(self.root).as_posix()
            self.job(str(index), evidence='Your application was received! ' + relative)
            self.assertIsNone(applied_jobs.receipt_image(self.con, str(index)))
        self.assertTrue(all(job['receipt_url'] is None for job in applied_jobs.list_applied(self.con)))

    def test_receipt_metadata_does_not_expose_local_paths_or_whole_evidence(self):
        (self.receipts / 'receipt.png').write_bytes(PNG)
        self.job('private', evidence='Your application was sent! logs/campaign/receipts/receipt.png '
                 'person@example.com password=secret')
        encoded = json.dumps(applied_jobs.list_applied(self.con, self.receipts))
        for private in (str(self.root), 'logs/campaign/receipts', 'person@example.com', 'secret'):
            self.assertNotIn(private, encoded)

    def test_explicit_receipt_directory_fixture_remains_supported(self):
        custom = self.root / 'custom-receipts'
        custom.mkdir()
        (custom / 'fixture.png').write_bytes(PNG)
        self.job('custom', evidence='Your application was sent! logs/campaign/receipts/fixture.png')
        self.assertTrue(applied_jobs.list_applied(self.con, custom)[0]['receipt_file_saved'])
        self.assertIsNone(applied_jobs.list_applied(self.con)[0]['receipt_url'])

    def test_an_absolute_path_under_the_current_project_root_is_viewable(self):
        ats = self.root / 'logs/ats'
        ats.mkdir()
        (self.receipts / 'abs.png').write_bytes(PNG)
        (ats / 'abs.webp').write_bytes(WEBP)
        for index, (root, folder, name, data, kind) in enumerate((
                (self.root, 'campaign/receipts', 'abs.png', PNG, 'image/png'),
                (self.root.resolve(), 'campaign/receipts', 'abs.png', PNG, 'image/png'),
                (self.root, 'ats', 'abs.webp', WEBP, 'image/webp'))):
            job_id = f'abs-{index}'
            evidence = f'Your application was received! Screenshot saved to {root}/logs/{folder}/{name}.'
            self.assertEqual(applied_jobs.receipt_file(evidence), name)
            self.job(job_id, evidence=evidence)
            self.assertEqual(applied_jobs.receipt_image(self.con, job_id), (data, kind))
        listed = applied_jobs.list_applied(self.con)
        self.assertTrue(all(job['receipt_url'] for job in listed))
        self.assertNotIn(str(self.root), json.dumps(listed))

    def test_an_absolute_path_outside_the_current_project_root_is_not_viewable(self):
        other = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        (other / 'logs/campaign/receipts').mkdir(parents=True)
        (other / 'logs/campaign/receipts/foreign.png').write_bytes(PNG)
        (self.receipts / 'foreign.png').write_bytes(PNG)
        tmp_dir = Path('/tmp/applied-jobs-test-' + self.root.name)
        for index, evidence in enumerate((
                f'Your application was received! {other}/logs/campaign/receipts/foreign.png',
                f'Your application was received! /tmp/logs/campaign/receipts/foreign.png',
                f'Your application was received! {tmp_dir}/logs/campaign/receipts/foreign.png',
                f'Your application was received! {self.root}-other/logs/campaign/receipts/foreign.png',
                f'Your application was received! /prefix{self.root}/logs/campaign/receipts/foreign.png',
                f'Your application was received! {self.root}/../{self.root.name}/logs/campaign/receipts/foreign.png')):
            job_id = f'foreign-{index}'
            with self.subTest(evidence=evidence):
                self.assertIsNone(applied_jobs.receipt_file(evidence))
                self.job(job_id, evidence=evidence)
                self.assertIsNone(applied_jobs.receipt_image(self.con, job_id))
        self.assertTrue(all(job['receipt_url'] is None for job in applied_jobs.list_applied(self.con)))


class AppliedRouteTests(AppliedFixture):
    def setUp(self):
        super().setUp()
        self.job('shown', evidence='Your application was sent to Employer!')
        self.job('hidden', status='manual', result='manual', reservation=None)
        self.server = campaign_monitor.make_server(0, token=TOKEN)
        self.port = self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (self.server.shutdown(), self.server.server_close()))

    def get(self, path, host=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        conn.putrequest('GET', path, skip_host=True, skip_accept_encoding=True)
        conn.putheader('Host', host or f'127.0.0.1:{self.port}')
        conn.endheaders()
        response = conn.getresponse()
        body = response.read()
        conn.close()
        return response.status, body

    def test_the_route_lists_applied_jobs_with_a_summary(self):
        status, body = self.get('/api/applied')
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual([j['job_id'] for j in data['jobs']], ['shown'])
        self.assertEqual(data['summary']['count'], 1)

    def test_a_foreign_host_gets_no_applied_data(self):
        status, body = self.get('/api/applied', host=f'evil.example:{self.port}')
        self.assertEqual(status, 403)
        self.assertNotIn(b'shown', body)

    def test_receipt_route_returns_only_the_applied_jobs_evidence_linked_image(self):
        (self.receipts / 'saved.png').write_bytes(PNG)
        self.attempt('shown', 'applied', 500, 'Your application was received! logs/campaign/receipts/saved.png')
        self.attempt('hidden', 'applied', 500, 'Your application was received! logs/campaign/receipts/saved.png')
        before = list(self.con.iterdump())
        status, body = self.get('/api/applied/shown/receipt')
        self.assertEqual((status, body), (200, PNG))
        for path in ('/api/applied/hidden/receipt', '/api/applied/unknown/receipt',
                     '/api/applied/%2e%2e%2fsaved.png/receipt'):
            with self.subTest(path=path):
                status, body = self.get(path)
                self.assertEqual(status, 404)
                self.assertNotEqual(body, PNG)
        status, body = self.get('/api/applied/shown/receipt', host=f'evil.example:{self.port}')
        self.assertEqual(status, 403)
        self.assertNotEqual(body, PNG)
        self.assertEqual(list(self.con.iterdump()), before)

    def test_receipt_route_reports_a_missing_screenshot_without_changing_the_application(self):
        self.attempt('shown', 'applied', 500, 'Your application was received! logs/campaign/receipts/missing.png')
        status, body = self.get('/api/applied/shown/receipt')
        self.assertEqual(status, 404)
        status, body = self.get('/api/applied')
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(data['jobs'][0]['job_id'], 'shown')
        self.assertIsNone(data['jobs'][0]['receipt_url'])
        self.assertEqual(data['jobs'][0]['confirmation'], 'Your application was received!')

    def test_the_route_accepts_no_write(self):
        before = list(self.con.iterdump())
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        conn.request('POST', '/api/applied', body=b'{}', headers={'Content-Type': 'application/json',
                                                                  'X-Monitor-Token': TOKEN})
        self.assertIn(conn.getresponse().status, (403, 404))
        conn.close()
        self.assertEqual(list(self.con.iterdump()), before)



if __name__ == '__main__':
    unittest.main()
