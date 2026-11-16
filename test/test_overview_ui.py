"""Overview tab: the status band, the Manual-jobs link, the folded detail sections and the confirmed-count face.

Builds the page through campaign_monitor.make_server on an ephemeral port with isolated temporary state; no worker,
browser or real database. Script behavior (null versus finite target, band, visibility rules) runs under node
against a tiny DOM stub when node is installed.
Run: python3 -m unittest discover -s test -p 'test_overview_ui.py'
"""
import base64
import contextlib
import hashlib
import http.client
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import campaign
import campaign_monitor

TOKEN = 'fixture-token-' + 'x' * 24
NODE = '/opt/homebrew/bin/node' if Path('/opt/homebrew/bin/node').exists() else shutil.which('node')


class Outline(HTMLParser):
    """Records, for every element id, the chain of ancestors (tag, id, attrs)."""

    def __init__(self):
        super().__init__()
        self.stack, self.ancestors, self.attrs = [], {}, {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get('id'):
            self.ancestors[attrs['id']] = list(self.stack)
            self.attrs[attrs['id']] = attrs
        if tag not in ('meta', 'br', 'input', 'link'):
            self.stack.append((tag, attrs.get('id'), attrs))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break


class OverviewPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stack = contextlib.ExitStack()
        cls.root = Path(cls.stack.enter_context(tempfile.TemporaryDirectory()))
        (cls.root / 'data').mkdir()
        (cls.root / 'data/campaign.json').write_text(json.dumps({'target_total': 50}))
        for name, value in [('ROOT', cls.root), ('CONFIG', cls.root / 'data/campaign.json'),
                            ('PACE', cls.root / 'data/ratelimit.json')]:
            cls.stack.enter_context(patch.object(campaign, name, value))
        cls.stack.enter_context(patch.object(campaign.db, 'DB_PATH', cls.root / 'data/jobs.db'))
        campaign.connect().close()
        cls.server = campaign_monitor.make_server(0, token=TOKEN)
        cls.port = cls.server.server_port
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        conn = http.client.HTTPConnection('127.0.0.1', cls.port, timeout=10)
        conn.request('GET', '/', headers={'Host': f'127.0.0.1:{cls.port}'})
        response = conn.getresponse()
        cls.page = response.read().decode()
        cls.policy = response.getheader('Content-Security-Policy')
        conn.close()
        cls.script = re.search(r'<script>(.*)</script>', cls.page, re.S).group(1)
        cls.outline = Outline()
        cls.outline.feed(cls.page)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.stack.close()

    def inside(self, element_id, tag, **attrs):
        return any(name == tag and all(a.get(key) == value for key, value in attrs.items())
                   for name, _id, a in self.outline.ancestors[element_id])

    def details_containing(self, element_id):
        return [a for name, _id, a in self.outline.ancestors[element_id] if name == 'details']

    def test_the_status_band_comes_first_and_says_the_worker_state_in_words(self):
        for element_id in ('state-dot', 'service-state', 'liveness', 'needs-you'):
            self.assertTrue(self.inside(element_id, 'section', **{'class': 'card ov-band'}), element_id)
        self.assertLess(self.page.index('id="service-state"'), self.page.index('id="confirmed"'))
        self.assertLess(self.page.index('id="needs-you"'), self.page.index('id="job-statuses"'))
        self.assertEqual(self.outline.attrs['state-dot'].get('aria-hidden'), 'true',
                         'the dot is decoration; the state is also written as text')
        for word in ('Working', 'Between rounds', 'Waiting', 'Stopped', 'Error', 'Stale'):
            self.assertIn(word, self.script)

    def test_the_needs_you_control_is_a_button_that_opens_the_manual_jobs_tab_through_the_tab_code(self):
        self.assertEqual(self.outline.attrs['needs-you'].get('type'), 'button')
        self.assertIn("$('needs-you').addEventListener('click', () => { selectTab('manual');", self.script)
        self.assertIn('counts_by_job_status?.manual', self.script)
        self.assertEqual(self.script.count('function selectTab('), 1, 'the tab code is reused, not copied')

    def test_attempt_history_and_technical_details_are_closed_native_details(self):
        summaries = re.findall(r'<details([^>]*)><summary>([^<]*)</summary>', self.page)
        self.assertIn('Attempt history (every try, including repeats)', [text for _, text in summaries])
        self.assertIn('Technical details', [text for _, text in summaries])
        for attrs, text in summaries:
            self.assertNotIn('open', attrs.split(), f'{text} must start closed')
        for element_id in ('attempts', 'submit-attempts', 'outcomes'):
            self.assertEqual(len(self.details_containing(element_id)), 1, element_id)
        note = 'Current job statuses and historical attempt outcomes measure different things.'
        history = re.search(r'<details[^>]*><summary>Attempt history.*?</details>', self.page, re.S).group(0)
        self.assertIn(note, history)
        technical = re.search(r'<details[^>]*><summary>Technical details.*?</details>', self.page, re.S).group(0)
        for element_id in ('heartbeat-age', 'worker-age', 'worker-pid', 'supervisor-pid', 'restarts'):
            self.assertIn(f'id="{element_id}"', technical)

    def test_the_exit_reason_and_retry_time_sit_in_a_block_that_is_hidden_unless_the_worker_is_not_working(self):
        block = self.outline.attrs['not-working']
        self.assertIn('hidden', block)
        for element_id in ('exit-reason', 'next-retry'):
            self.assertTrue(any(_id == 'not-working' for _, _id, _ in self.outline.ancestors[element_id]))

    def test_jobs_by_status_is_the_primary_count_block_and_latest_actions_stay(self):
        self.assertIn('Jobs by current status', self.page)
        self.assertEqual(self.details_containing('job-statuses'), [])
        self.assertLess(self.page.index('id="job-statuses"'), self.page.index('Attempt history'))
        self.assertLess(self.page.index('id="job-statuses"'), self.page.index('id="actions"'))
        self.assertEqual(self.details_containing('actions'), [])
        for element_id in ('refresh-error', 'last-refresh', 'activity-stage'):
            self.assertIn(f'id="{element_id}"', self.page)


    def test_no_server_value_is_written_with_html_parsing_and_the_policy_matches_the_script(self):
        for name in ('innerHTML', 'outerHTML', 'insertAdjacentHTML'):
            self.assertNotIn(name, self.script)
        digest = base64.b64encode(hashlib.sha256(self.script.encode('utf-8')).digest()).decode('ascii')
        self.assertIn(f"'sha256-{digest}'", self.policy)
        self.assertEqual(digest, campaign_monitor._SCRIPT_HASH)
        self.assertNotRegex(self.page, r'\son[a-z]+\s*=')

    def test_both_target_shapes_are_rendered_by_the_script(self):
        self.assertIn("const finite = data.target !== null;", self.script)
        self.assertIn("'confirmed applications'", self.script)
        self.assertIn("'confirmed of the target'", self.script)
        self.assertIn("$('target-part').hidden = !finite; $('remaining-line').hidden = !finite;", self.script)
        self.assertNotIn('submit_attempts_24h', self.script.split('function render(data)')[1].split('async function')[0]
                         .split("set('submit-attempts'")[0],
                         'submission attempts are never presented as confirmed applications')

    @unittest.skipUnless(NODE, 'node is not installed')
    def test_the_page_script_is_valid_javascript(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'page.js'
            path.write_text(self.script)
            result = subprocess.run([NODE, '--check', str(path)], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)


HARNESS = r"""
const els = {}, calls = [];
const mk = () => ({textContent: '', hidden: false, className: '', children: [], listeners: {}, tabIndex: 0,
  classList: {toggle() {}, add() {}, remove() {}},
  addEventListener(type, fn) { this.listeners[type] = fn; }, replaceChildren() { this.children = []; },
  append(...c) { this.children.push(...c); }, setAttribute() {}, focus() { calls.push('focus:' + this.id); }});
globalThis.document = {getElementById: id => els[id] ||= Object.assign(mk(), {id}),
  createElement: () => mk(), createDocumentFragment: () => mk()};
globalThis.history = {replaceState() {}};
globalThis.fetch = () => Promise.reject(new Error('offline'));
function refreshManual() { calls.push('refreshManual'); }
(function () {
%SCRIPT%
const now = Date.now() / 1000;
const base = {confirmed: 7, target: null, remaining: null, complete: false, budget: {submit_attempts_24h: 4},
  outcomes: {applied: 7, manual: 2}, counts_by_job_status: {manual: 2, applied: 7},
  latest_actions: [{at: now - 7200, job_id: 'a', company: 'C', title: 'T', result: 'manual', reason: 'r'},
                   {at: now - 3600, job_id: 'b', company: 'C', title: 'T', result: 'applied', reason: null}],
  activity: null};
const runtime = state => ({state, heartbeat_age_seconds: 1, worker_pid: 5, supervisor_pid: 4, restart_count: 0,
  heartbeat_stale_after_seconds: 20});
function run(overrides, state, extra = {}) {
  const data = {...base, ...overrides, runtime: {...runtime(state), ...extra}};
  snapshot = data; receivedAt = lastSuccess = Date.now(); failed = false;
  render(data);
  const pick = id => ({text: $(id).textContent, hidden: $(id).hidden, className: $(id).className});
  return Object.fromEntries(['confirmed', 'target-part', 'target', 'confirmed-caption', 'remaining-line', 'remaining',
    'last-confirmed-line', 'last-confirmed', 'service-state', 'state-dot', 'needs-you', 'not-working']
    .map(id => [id, pick(id)]));
}
const out = {};
out.nullTarget = run({}, 'working');
out.finite = run({target: 50, remaining: 43}, 'working');
out.between = run({}, 'between_rounds');
out.stopped = run({}, 'stopped');
out.noConfirmedRow = run({latest_actions: base.latest_actions.slice(0, 1)}, 'working');
out.oneManual = run({counts_by_job_status: {manual: 1}}, 'working');
out.stale = run({}, 'working', {heartbeat_age_seconds: 500});
failed = true; renderAges();
out.failed = ['service-state', 'needs-you', 'not-working'].map(id => ({id, text: $(id).textContent, hidden: $(id).hidden}));
failed = false;
$('needs-you').listeners.click();
out.calls = calls; out.tab = $('panel-manual').hidden;
console.log(JSON.stringify(out));
})();
"""


@unittest.skipUnless(NODE, 'node is not installed')
class OverviewScriptBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'harness.js'
            path.write_text(HARNESS.replace('%SCRIPT%', campaign_monitor._SCRIPT))
            result = subprocess.run([NODE, str(path)], capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise AssertionError(result.stderr)
        cls.out = json.loads(result.stdout)

    def test_a_null_target_shows_the_confirmed_count_as_the_big_number_with_no_target_or_remaining(self):
        out = self.out['nullTarget']
        self.assertEqual(out['confirmed']['text'], '7')
        self.assertEqual(out['confirmed-caption']['text'], 'confirmed applications')
        self.assertTrue(out['target-part']['hidden'])
        self.assertTrue(out['remaining-line']['hidden'])

    def test_a_finite_target_keeps_x_of_target_and_remaining(self):
        out = self.out['finite']
        self.assertEqual((out['confirmed']['text'], out['target']['text'], out['remaining']['text']), ('7', '50', '43'))
        self.assertFalse(out['target-part']['hidden'])
        self.assertFalse(out['remaining-line']['hidden'])

    def test_the_last_confirmed_age_comes_only_from_a_confirmed_row_in_the_latest_actions(self):
        shown, absent = self.out['nullTarget'], self.out['noConfirmedRow']
        self.assertFalse(shown['last-confirmed-line']['hidden'])
        self.assertRegex(shown['last-confirmed']['text'], r'^(1h \d+m|59m \d+s) ago$')
        self.assertTrue(absent['last-confirmed-line']['hidden'])

    def test_the_band_names_the_state_in_words_and_marks_the_dot_by_level(self):
        self.assertEqual(self.out['nullTarget']['service-state']['text'], 'Working')
        self.assertEqual(self.out['nullTarget']['state-dot']['className'], 'ov-dot good')
        self.assertEqual(self.out['between']['service-state']['text'], 'Between rounds')
        self.assertEqual(self.out['stopped']['service-state']['text'], 'Stopped')
        self.assertEqual(self.out['stopped']['state-dot']['className'], 'ov-dot warn')
        self.assertTrue(self.out['stale']['service-state']['text'].startswith('Stale'))
        self.assertEqual(self.out['stale']['state-dot']['className'], 'ov-dot bad')

    def test_exit_reason_and_retry_show_only_when_the_worker_is_not_working(self):
        self.assertTrue(self.out['nullTarget']['not-working']['hidden'])
        self.assertFalse(self.out['between']['not-working']['hidden'])
        self.assertFalse(self.out['stopped']['not-working']['hidden'])
        self.assertFalse(self.out['stale']['not-working']['hidden'])

    def test_the_needs_you_count_comes_from_the_manual_status_count(self):
        self.assertEqual(self.out['nullTarget']['needs-you']['text'], 'Needs you: 2 manual jobs')
        self.assertEqual(self.out['oneManual']['needs-you']['text'], 'Needs you: 1 manual job')

    def test_data_from_a_failed_refresh_is_labelled_as_last_known(self):
        failed = {row['id']: row for row in self.out['failed']}
        self.assertEqual(failed['service-state']['text'], 'Unavailable · last known data')
        self.assertIn('(last known)', failed['needs-you']['text'])
        self.assertFalse(failed['not-working']['hidden'])

    def test_clicking_needs_you_selects_the_manual_tab_and_moves_focus_there(self):
        self.assertIn('refreshManual', self.out['calls'])
        self.assertIn('focus:tab-manual', self.out['calls'])
        self.assertFalse(self.out['tab'])


if __name__ == '__main__':
    unittest.main()
