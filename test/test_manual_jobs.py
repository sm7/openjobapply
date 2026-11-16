"""Manual-jobs review: the duplicate-submission guard, atomic answers, write protection and the worker handoff.

Isolated temporary state and an ephemeral-port server; no worker, browser or real database.
Run: python3 -m unittest discover -s test -p 'test_manual_jobs.py'
"""
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
import manual_jobs

TOKEN = 'fixture-token-' + 'x' * 24


class ManualJobsTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        (self.root / 'data').mkdir()
        (self.root / 'src').mkdir()
        (self.root / 'data/campaign.json').write_text(json.dumps({'target_total': 50}))
        (self.root / 'src/CAMPAIGN_WORKER.md').write_text('Worker rules.')
        for name, value in [('ROOT', self.root), ('CONFIG', self.root / 'data/campaign.json'),
                            ('PACE', self.root / 'data/ratelimit.json')]:
            self.stack.enter_context(patch.object(campaign, name, value))
        self.stack.enter_context(patch.object(campaign.db, 'DB_PATH', self.root / 'data/jobs.db'))
        self.con = campaign.connect()
        self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)

    def job(self, job_id, status='manual', reservation=None, reason='Needs an answer'):
        self.con.execute(
            'INSERT INTO jobs(id,source,source_id,url,title,company,status,first_seen,last_seen) VALUES(?,?,?,?,?,?,?,?,?)',
            (job_id, 'fixture', job_id, 'https://example.invalid/' + job_id, 'Analyst ' + job_id, 'Employer', status, 1, 1))
        if status == 'manual':
            self.con.execute("INSERT INTO applications(job_id,attempted,result,reason,run_id) VALUES(?,?,?,?,?)",
                             (job_id, 100.0, 'manual', reason, 'campaign-50'))
        if reservation:
            self.con.execute('INSERT INTO campaign_reservations VALUES(?,?,?,?)', (job_id, 50.0, reservation, None))
        self.con.commit()
        return job_id

    def dump(self):
        return list(self.con.iterdump())

    # --- what the reviewer sees -------------------------------------------------------------------------

    def test_a_job_that_reserved_a_submit_slot_is_marked_possibly_sent(self):
        self.job('stopped-before-submit')
        self.job('reserved-then-stopped', reservation='manual')
        self.job('failed-after-reserving', reservation='failed')
        risk = {job['job_id']: job['sent_risk'] for job in manual_jobs.list_manual(self.con)}
        self.assertEqual(risk, {'stopped-before-submit': 'not_sent', 'reserved-then-stopped': 'possibly_sent',
                                'failed-after-reserving': 'possibly_sent'})

    def test_the_reason_shown_is_sanitized_and_never_empty(self):
        self.job('leaky', reason='Blocked at https://secret.example/x?token=abc by user@example.com')
        self.job('silent', reason=None)
        shown = {job['job_id']: job['reason'] for job in manual_jobs.list_manual(self.con)}
        self.assertNotIn('secret.example', shown['leaky'])
        self.assertNotIn('user@example.com', shown['leaky'])
        self.assertEqual(shown['silent'], 'No reason recorded')

    def test_the_list_works_on_a_database_that_predates_the_new_tables(self):
        self.job('legacy')
        self.con.execute('DROP TABLE job_answers')
        self.con.execute('DROP TABLE campaign_reservations')
        self.con.commit()
        listed = manual_jobs.list_manual(self.con)
        self.assertEqual([(j['job_id'], j['sent_risk'], j['saved_answers']) for j in listed], [('legacy', 'not_sent', 0)])

    def test_only_manual_jobs_are_listed(self):
        self.job('manual-one')
        self.job('queued-one', status='queued')
        self.job('applied-one', status='applied')
        self.assertEqual([j['job_id'] for j in manual_jobs.list_manual(self.con)], ['manual-one'])

    # --- the duplicate-submission guard -----------------------------------------------------------------

    def test_a_possibly_sent_job_is_not_requeued_without_explicit_confirmation(self):
        self.job('risky', reservation='manual')
        before = self.dump()
        for confirmed in (False, None, 'true', 1):
            with self.subTest(confirmed=confirmed):
                with self.assertRaises(manual_jobs.ManualJobError) as refused:
                    manual_jobs.resubmit(self.con, 'risky', [{'question': 'Q', 'answer': 'A'}], confirmed)
                self.assertEqual(refused.exception.code, 'confirmation_required')
        self.assertEqual(self.dump(), before)

    def test_a_confirmed_possibly_sent_job_is_requeued_and_its_reservation_cleared(self):
        self.job('risky', reservation='manual')
        manual_jobs.resubmit(self.con, 'risky', [], nothing_sent_confirmed=True)
        self.assertEqual(self.con.execute("SELECT status FROM jobs WHERE id='risky'").fetchone()[0], 'queued')
        self.assertIsNone(self.con.execute("SELECT state FROM campaign_reservations WHERE job_id='risky'").fetchone())

    def test_a_job_already_applied_elsewhere_cannot_be_requeued(self):
        self.job('manual-copy')
        self.con.execute("UPDATE jobs SET url='https://example.invalid/same' WHERE id='manual-copy'")
        self.job('applied-copy', status='applied')
        self.con.execute("UPDATE jobs SET url='https://example.invalid/same' WHERE id='applied-copy'")
        self.con.commit()
        before = self.dump()
        with self.assertRaises(manual_jobs.ManualJobError) as refused:
            manual_jobs.resubmit(self.con, 'manual-copy', [{'question': 'Q', 'answer': 'A'}])
        self.assertEqual(refused.exception.code, 'already_sent_or_reserved')
        self.assertEqual(self.dump(), before)

    def test_a_requeued_job_cannot_be_resubmitted_again(self):
        self.job('once')
        manual_jobs.resubmit(self.con, 'once', [{'question': 'Q', 'answer': 'A'}])
        with self.assertRaises(manual_jobs.ManualJobError) as refused:
            manual_jobs.resubmit(self.con, 'once', [{'question': 'Q2', 'answer': 'A2'}])
        self.assertEqual(refused.exception.code, 'not_manual')

    # --- answers are saved with the requeue, or not at all ----------------------------------------------

    def test_a_rejected_requeue_leaves_no_answers_behind(self):
        self.job('manual-copy')
        self.con.execute("UPDATE jobs SET url='https://example.invalid/same' WHERE id='manual-copy'")
        self.job('applied-copy', status='applied')
        self.con.execute("UPDATE jobs SET url='https://example.invalid/same' WHERE id='applied-copy'")
        self.con.commit()
        with self.assertRaises(manual_jobs.ManualJobError):
            manual_jobs.resubmit(self.con, 'manual-copy', [{'question': 'Q', 'answer': 'A'}])
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM job_answers').fetchone()[0], 0)

    def test_answers_are_normalized_and_blank_rows_are_ignored(self):
        self.job('tidy')
        manual_jobs.resubmit(self.con, 'tidy', [
            {'question': '  Have you   ever  worked here?  ', 'answer': '  No  '},
            {'question': '', 'answer': ''}])
        self.assertEqual([tuple(r) for r in self.con.execute('SELECT question, answer FROM job_answers')],
                         [('Have you ever worked here?', 'No')])

    def test_malformed_answers_are_refused_without_changing_anything(self):
        self.job('strict')
        before = self.dump()
        for bad in ('text', [{'question': 'only a question', 'answer': ''}], [{'question': '', 'answer': 'orphan'}],
                    [{'question': 'q', 'answer': 'a', 'extra': 1}], [{'question': 1, 'answer': 'a'}],
                    [{'question': 'q', 'answer': 'x' * (manual_jobs.MAX_ANSWER + 1)}],
                    [{'question': 'q', 'answer': 'a'}] * (manual_jobs.MAX_ANSWERS + 1)):
            with self.subTest(bad=str(bad)[:40]), self.assertRaises(manual_jobs.ManualJobError) as refused:
                manual_jobs.resubmit(self.con, 'strict', bad)
            self.assertEqual(refused.exception.code, 'invalid_answers')
        self.assertEqual(self.dump(), before)

    def test_resubmitting_replaces_earlier_answers_for_that_job(self):
        self.job('again')
        manual_jobs.resubmit(self.con, 'again', [{'question': 'Old', 'answer': 'one'}])
        self.con.execute("UPDATE jobs SET status='manual' WHERE id='again'")
        self.con.commit()
        manual_jobs.resubmit(self.con, 'again', [{'question': 'New', 'answer': 'two'}])
        self.assertEqual([tuple(r) for r in self.con.execute('SELECT question, answer FROM job_answers')], [('New', 'two')])

    # --- the worker actually receives the answers -------------------------------------------------------

    def test_the_worker_prompt_carries_answers_only_for_jobs_waiting_to_be_applied(self):
        self.job('waiting')
        manual_jobs.resubmit(self.con, 'waiting', [{'question': 'Is the sky blue?', 'answer': 'Yes'}])
        self.job('stale')
        self.con.execute("INSERT INTO job_answers VALUES('stale','Old question','STALE',1.0)")
        self.con.commit()
        answers = campaign.reviewed_answers(self.con)
        self.assertEqual(list(answers), ['waiting'])
        prompt = campaign.worker_prompt({'target_total': 50}, {}, {}, answers)
        self.assertIn('Is the sky blue?', prompt)
        self.assertNotIn('STALE', prompt)

    def test_a_round_without_reviewed_answers_has_no_answers_block(self):
        self.assertNotIn('Reviewed per-job answers (a person',
                         campaign.worker_prompt({'target_total': 50}, {}, {}, {}))

    def test_answers_stop_reaching_the_worker_once_the_job_is_applied(self):
        self.job('done')
        manual_jobs.resubmit(self.con, 'done', [{'question': 'Q', 'answer': 'A'}])
        self.con.execute("UPDATE jobs SET status='applied' WHERE id='done'")
        self.con.commit()
        self.assertEqual(campaign.reviewed_answers(self.con), {})


class DashboardWriteProtectionTests(unittest.TestCase):
    """The only state-changing route must refuse every request that is not the dashboard's own page."""

    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        (self.root / 'data').mkdir()
        (self.root / 'data/campaign.json').write_text(json.dumps({'target_total': 50}))
        for name, value in [('ROOT', self.root), ('CONFIG', self.root / 'data/campaign.json'),
                            ('PACE', self.root / 'data/ratelimit.json')]:
            self.stack.enter_context(patch.object(campaign, name, value))
        self.stack.enter_context(patch.object(campaign.db, 'DB_PATH', self.root / 'data/jobs.db'))
        con = campaign.connect()
        for job_id, reservation in (('free', None), ('risky', 'manual')):
            con.execute('INSERT INTO jobs(id,source,source_id,url,title,company,status,first_seen,last_seen) '
                        "VALUES(?,?,?,?,?,?,'manual',1,1)",
                        (job_id, 'fixture', job_id, 'https://example.invalid/' + job_id, 'Analyst', 'Employer'))
            con.execute("INSERT INTO applications(job_id,attempted,result,reason,run_id) VALUES(?,?,?,?,?)",
                        (job_id, 100.0, 'manual', 'Needs an answer', 'campaign-50'))
            if reservation:
                con.execute('INSERT INTO campaign_reservations VALUES(?,?,?,?)', (job_id, 50.0, reservation, None))
        con.commit()
        con.close()
        self.server = campaign_monitor.make_server(0, token=TOKEN)
        self.port = self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (self.server.shutdown(), self.server.server_close()))

    def state(self):
        with contextlib.closing(sqlite3.connect(self.root / 'data/jobs.db')) as con:
            return list(con.iterdump())

    def send(self, method, path, body=None, headers=None):
        merged = {'Host': f'127.0.0.1:{self.port}', 'Content-Type': 'application/json', 'X-Monitor-Token': TOKEN}
        merged.update(headers or {})
        data = json.dumps(body).encode() if body is not None else b''
        merged.setdefault('Content-Length', str(len(data)))
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        for key, value in merged.items():
            conn.putheader(key, value)
        conn.endheaders(data)
        response = conn.getresponse()
        raw = response.read()
        conn.close()
        return response.status, (json.loads(raw) if raw[:1] in (b'{', b'[') else raw)

    def test_requests_that_are_not_the_dashboards_own_page_change_nothing(self):
        body = {'job_id': 'free', 'answers': [{'question': 'Q', 'answer': 'A'}]}
        before = self.state()
        for name, expected, headers in [
            ('no token', 403, {'X-Monitor-Token': ''}),
            ('wrong token', 403, {'X-Monitor-Token': 'guess'}),
            ('another website', 403, {'Origin': 'https://evil.example'}),
            ('DNS rebinding host', 403, {'Host': f'evil.example:{self.port}'}),
            ('form post', 415, {'Content-Type': 'application/x-www-form-urlencoded'}),
            ('simple cross-site text post', 415, {'Content-Type': 'text/plain'}),
            ('huge body', 413, {'Content-Length': str(10 ** 9)}),
        ]:
            with self.subTest(name):
                self.assertEqual(self.send('POST', '/api/manual/resubmit', body, headers)[0], expected)
        self.assertEqual(self.state(), before)

    def test_a_valid_request_requeues_the_job_and_saves_its_answers(self):
        status, result = self.send('POST', '/api/manual/resubmit',
                                   {'job_id': 'free', 'answers': [{'question': 'Q', 'answer': 'A'}]})
        self.assertEqual((status, result['requeued'], result['saved_answers']), (200, True, 1))
        with contextlib.closing(sqlite3.connect(self.root / 'data/jobs.db')) as con:
            self.assertEqual(con.execute("SELECT status FROM jobs WHERE id='free'").fetchone()[0], 'queued')
            self.assertEqual(con.execute('SELECT question, answer FROM job_answers').fetchall(), [('Q', 'A')])

    def test_the_duplicate_guard_holds_over_http_and_the_replay_is_a_conflict(self):
        before = self.state()
        status, result = self.send('POST', '/api/manual/resubmit', {'job_id': 'risky', 'answers': []})
        self.assertEqual((status, result['code']), (409, 'confirmation_required'))
        self.assertEqual(self.state(), before)
        self.assertEqual(self.send('POST', '/api/manual/resubmit', {'job_id': 'free', 'answers': []})[0], 200)
        self.assertEqual(self.send('POST', '/api/manual/resubmit', {'job_id': 'free', 'answers': []})[0], 409)

    def test_malformed_requests_are_client_errors_not_server_errors(self):
        for body in ({'answers': []}, {'job_id': 5}, {'job_id': 'free', 'answers': 'text'}):
            with self.subTest(body=body):
                self.assertEqual(self.send('POST', '/api/manual/resubmit', body)[0], 400)

    def test_no_other_route_accepts_writes_and_reads_do_not_modify_state(self):
        before = self.state()
        self.assertEqual(self.send('POST', '/api/status', {'job_id': 'free'})[0], 404)
        for method in ('PUT', 'DELETE', 'PATCH'):
            self.assertEqual(self.send(method, '/api/manual/resubmit', {'job_id': 'free'})[0], 405)
        status, listing = self.send('GET', '/api/manual')
        self.assertEqual(status, 200)
        self.assertEqual({job['job_id'] for job in listing['jobs']}, {'free', 'risky'})
        self.assertEqual(self.state(), before)

    def test_a_page_requested_under_a_foreign_host_never_receives_the_token_or_any_data(self):
        # DNS rebinding: a web page reaches the dashboard under its own hostname. If any read route answered it,
        # the page could copy the write token out of the HTML and then use it.
        for host in (f'evil.example:{self.port}', 'evil.example', f'127.0.0.1.evil.example:{self.port}',
                     f'127.0.0.1:{self.port + 1}', ''):
            for path in ('/', '/api/status', '/api/logs', '/api/manual', '/nowhere'):
                with self.subTest(host=host, path=path):
                    conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
                    conn.putrequest('GET', path, skip_host=True, skip_accept_encoding=True)
                    if host:
                        conn.putheader('Host', host)
                    conn.endheaders()
                    response = conn.getresponse()
                    body = response.read().decode('utf-8', 'replace')
                    conn.close()
                    self.assertEqual(response.status, 403)
                    self.assertNotIn(TOKEN, body)
                    self.assertNotIn('Analyst', body)

    def test_both_loopback_spellings_of_the_dashboard_address_still_work(self):
        for host in (f'127.0.0.1:{self.port}', f'localhost:{self.port}'):
            with self.subTest(host=host):
                status, body = self.send('GET', '/api/manual', headers={'Host': host})
                self.assertEqual(status, 200)
                self.assertEqual({job['job_id'] for job in body['jobs']}, {'free', 'risky'})

    def test_the_page_embeds_the_token_and_its_script_matches_the_content_security_policy(self):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        conn.request('GET', '/', headers={'Host': f'127.0.0.1:{self.port}'})
        response = conn.getresponse()
        page, policy = response.read().decode(), response.getheader('Content-Security-Policy')
        conn.close()
        self.assertIn(f'name="monitor-token" content="{TOKEN}"', page)
        self.assertNotIn('__MONITOR_TOKEN__', page)
        self.assertIn(f"'sha256-{campaign_monitor._SCRIPT_HASH}'", policy)
        self.assertIn('id="tab-manual"', page)

    def test_the_page_never_sends_the_local_dashboard_address_to_a_site_it_links_to(self):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        conn.request('GET', '/', headers={'Host': f'127.0.0.1:{self.port}'})
        response = conn.getresponse()
        response.read()
        policy = response.getheader('Referrer-Policy')
        conn.close()
        self.assertEqual(policy, 'no-referrer')


class PostingLinkTests(unittest.TestCase):
    """The scraper writes jobs.url and jobs.apply_url, so both are untrusted input that becomes an href."""

    SAFE = ['https://www.linkedin.com/jobs/view/data-analyst-4488656624',
            'http://careers.example.com/job/1?src=a&b=c',
            'https://careers.example.com/job/USA-Remote-Analyst%2C-Health-%26-Outcomes-%28HEOR%29/1377168957/',
            'https://example.com:8443/path#fragment',
            'https://example.com/path~tilde',
            'https://example.com/caf\u00e9',
            'https://example.com/a\u00a0b',
            'https://xn--bcher-kva.example/job']
    UNSAFE = {
        'javascript scheme': 'javascript:alert(document.querySelector("meta").content)',
        'javascript with capitals': 'JaVaScRiPt:alert(1)',
        'data scheme': 'data:text/html,<script>alert(1)</script>',
        'file scheme': 'file:///etc/passwd',
        'ftp scheme': 'ftp://example.com/job',
        'vbscript scheme': 'vbscript:msgbox(1)',
        'no scheme': 'www.linkedin.com/jobs/view/1',
        'scheme relative': '//evil.example/job',
        'no host': 'https:///job',
        'credentials in the address': 'https://www.linkedin.com@evil.example/job',
        'user and password': 'https://user:secret@example.com/job',
        'tab inside the scheme': 'java\tscript:alert(1)',
        'newline inside': 'https://example.com/\njob',
        'space inside': 'https://example.com/a job',
        'null byte': 'https://example.com/\x00',
        'delete character': 'https://example.com/\x7f',
        'next line control U+0085': 'https://example.com/a\u0085b',
        'single shift control U+008d': 'https://example.com/a\u008db',
        'escape-introducer control U+009b': 'https://example.com/a\u009bb',
        'last C1 control U+009f': 'https://example.com/a\u009fb',
        'broken port': 'https://example.com:notaport/job',
        'port out of range': 'https://example.com:99999/job',
        'unbalanced bracket host': 'https://[::1/job',
        'too long': 'https://example.com/' + 'a' * 2000,
        'empty': '',
        'only spaces': '   ',
    }

    def test_a_normal_web_address_is_kept_unchanged(self):
        for url in self.SAFE:
            with self.subTest(url=url):
                self.assertEqual(manual_jobs.safe_link(url), url)

    def test_every_unsafe_form_is_refused(self):
        for name, url in self.UNSAFE.items():
            with self.subTest(name):
                self.assertIsNone(manual_jobs.safe_link(url))

    def test_a_value_that_is_not_text_is_refused(self):
        for value in (None, 0, 5.5, b'https://example.com', ['https://example.com'], {'u': 'https://example.com'}):
            with self.subTest(value=repr(value)):
                self.assertIsNone(manual_jobs.safe_link(value))

    def test_a_poisoned_database_row_never_reaches_the_browser_as_a_link(self):
        with contextlib.ExitStack() as stack:
            root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
            (root / 'data').mkdir()
            (root / 'data/campaign.json').write_text(json.dumps({'target_total': 50}))
            for name, value in [('ROOT', root), ('CONFIG', root / 'data/campaign.json'), ('PACE', root / 'data/ratelimit.json')]:
                stack.enter_context(patch.object(campaign, name, value))
            stack.enter_context(patch.object(campaign.db, 'DB_PATH', root / 'data/jobs.db'))
            con = campaign.connect()
            con.row_factory = sqlite3.Row
            stack.callback(con.close)
            rows = {
                'good': ('https://www.linkedin.com/jobs/view/1', 'https://careers.example.com/apply/1'),
                'hostile': ('javascript:alert(1)', 'data:text/html,<script>alert(1)</script>'),
                'same': ('https://www.linkedin.com/jobs/view/3', 'https://www.linkedin.com/jobs/view/3'),
                'no-apply-page': ('https://www.linkedin.com/jobs/view/4', None),
                'mixed': ('https://www.linkedin.com/jobs/view/5', 'file:///etc/passwd'),
            }
            for job_id, (url, apply_url) in rows.items():
                con.execute('INSERT INTO jobs(id,source,source_id,url,apply_url,title,company,status,first_seen,last_seen) '
                            "VALUES(?,?,?,?,?,?,?,'manual',1,1)", (job_id, 'fixture', job_id, url, apply_url, 'Analyst', 'Employer'))
                con.execute("INSERT INTO applications(job_id,attempted,result,reason,run_id) VALUES(?,?,?,?,?)",
                            (job_id, 100.0, 'manual', 'Needs an answer', 'campaign-50'))
            con.commit()
            listed = {job['job_id']: (job['posting_url'], job['apply_url']) for job in manual_jobs.list_manual(con)}
        self.assertEqual(listed, {
            'good': ('https://www.linkedin.com/jobs/view/1', 'https://careers.example.com/apply/1'),
            'hostile': (None, None),
            'same': ('https://www.linkedin.com/jobs/view/3', None),
            'no-apply-page': ('https://www.linkedin.com/jobs/view/4', None),
            'mixed': ('https://www.linkedin.com/jobs/view/5', None),
        })

    def test_the_page_sets_href_in_one_place_and_opens_posting_pages_safely(self):
        import manual_jobs_ui
        script = manual_jobs_ui.SCRIPT
        self.assertEqual(script.count('.href ='), 1, 'href must be set only inside manualLink')
        self.assertNotIn('innerHTML', script)
        self.assertNotIn('outerHTML', script)
        self.assertNotIn('insertAdjacentHTML', script)
        self.assertIn("link.rel = 'noopener noreferrer'", script)
        self.assertIn("link.target = '_blank'", script)
        self.assertIn("parsed.protocol !== 'https:' && parsed.protocol !== 'http:'", script)
        self.assertIn('id="manual-dialog-links"', manual_jobs_ui.PANEL)

    def test_every_link_the_page_builds_goes_through_the_one_checked_helper_with_a_hint(self):
        import re
        import manual_jobs_ui
        script = manual_jobs_ui.SCRIPT
        self.assertNotIn("createElement('a')", script.replace("const link = document.createElement('a');", ''),
                         'an anchor made outside manualLink would skip the scheme check')
        calls = re.findall(r'manualLink\(([^;]*?)\)\s*(?::|;|\)|\}|$)', script)
        calls = [call for call in calls if not call.startswith('url, text')]
        self.assertEqual(len(calls), 3, 'title, employer and dialog links are the only callers')
        for call in calls:
            arguments = re.split(r',(?![^()]*\))', call)
            self.assertEqual(len(arguments), 3, f'manualLink needs url, text and hint, got: {call}')

    def test_the_card_title_is_the_posting_link_and_stays_plain_text_without_one(self):
        import manual_jobs_ui
        script = manual_jobs_ui.SCRIPT
        self.assertIn("job.posting_url ? manualLink(job.posting_url, titleText,", script)
        self.assertIn("title.textContent = titleText", script, 'a job with no safe address keeps a plain title')
        self.assertNotIn("'Open the job posting'", script.split('function manualCard')[1].split('function renderManual')[0],
                         'the card has no separate posting row now that the title is the link')


if __name__ == '__main__':
    unittest.main()
