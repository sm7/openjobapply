"""Static checks on the Manual jobs tab: what the markup and script contain, not how a browser renders them."""
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

import campaign_monitor  # noqa: E402
import manual_jobs  # noqa: E402
import manual_jobs_ui  # noqa: E402

NODE = shutil.which('node') or '/opt/homebrew/bin/node'


class Markup(HTMLParser):
    """Collects ids, labels and form controls of the static markup."""

    def __init__(self):
        super().__init__()
        self.ids, self.controls, self.label_for, self.buttons = [], [], set(), []
        self._label_depth, self._button = 0, None
        self.wrapped = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ids.append(attrs['id'])
        if tag == 'label':
            self._label_depth += 1
            if 'for' in attrs:
                self.label_for.add(attrs['for'])
        if tag in ('input', 'textarea', 'select'):
            self.controls.append((tag, attrs, self._label_depth > 0))
        if tag == 'button':
            self._button = {'attrs': attrs, 'text': ''}
            self.buttons.append(self._button)

    def handle_endtag(self, tag):
        if tag == 'label':
            self._label_depth -= 1
        if tag == 'button':
            self._button = None

    def handle_data(self, data):
        if self._button is not None:
            self._button['text'] += data


def function_body(script, name):
    start = script.index(f'function {name}(')
    return script[start:script.index('\nfunction ', start + 1) if '\nfunction ' in script[start + 1:] else len(script)]


class PanelTests(unittest.TestCase):
    def setUp(self):
        self.markup = Markup()
        self.markup.feed(manual_jobs_ui.PANEL)

    def test_the_panel_has_the_triage_search_and_dialog_controls(self):
        for element_id in ('manual-chips', 'manual-search', 'manual-list', 'manual-hint', 'manual-show-dismissed',
                           'manual-dialog', 'manual-form', 'manual-answers', 'manual-add-answer', 'manual-submit',
                           'manual-confirm-sent', 'manual-dialog-links'):
            self.assertIn(element_id, self.markup.ids)

    def test_element_ids_are_unique_and_every_label_points_at_an_element(self):
        self.assertEqual(len(self.markup.ids), len(set(self.markup.ids)))
        self.assertLessEqual(self.markup.label_for, set(self.markup.ids))

    def test_every_static_form_control_has_an_accessible_name(self):
        self.assertTrue(self.markup.controls)
        for tag, attrs, wrapped in self.markup.controls:
            with self.subTest(control=attrs.get('id')):
                self.assertTrue(attrs.get('aria-label') or attrs.get('id') in self.markup.label_for or wrapped,
                                f'unnamed <{tag}>')

    def test_every_static_button_has_a_name(self):
        for button in self.markup.buttons:
            with self.subTest(button=button['attrs'].get('id')):
                self.assertTrue(button['text'].strip() or button['attrs'].get('aria-label'))

    def test_the_chip_group_and_search_are_named(self):
        self.assertIn('aria-label="Show jobs by kind of stop"', manual_jobs_ui.PANEL)
        self.assertIn('<label for="manual-search">', manual_jobs_ui.PANEL)
        self.assertIn('aria-pressed="false"', manual_jobs_ui.PANEL)

    def test_the_panel_says_the_grouping_is_a_guess_and_what_dismiss_does(self):
        self.assertIn('Grouping is a best guess from the reason text.', manual_jobs_ui.PANEL)
        self.assertIn('Dismiss only hides a job in this browser. It does not change the job.', manual_jobs_ui.PANEL)

    def test_the_intro_says_returning_a_job_submits_nothing(self):
        text = re.sub(r'<[^>]+>', ' ', manual_jobs_ui.PANEL.split('manual-intro">')[1].split('</p>')[0])
        self.assertIn('does not submit anything by itself', text)
        self.assertIn("employer's confirmation", text)

    def test_user_facing_markup_no_longer_uses_the_word_resubmit(self):
        self.assertNotIn('resubmit', manual_jobs_ui.PANEL.lower())

    def test_the_panel_is_the_one_the_page_embeds(self):
        self.assertIn(manual_jobs_ui.PANEL, campaign_monitor._HTML)


class ScriptTests(unittest.TestCase):
    script = manual_jobs_ui.SCRIPT

    def test_no_server_value_is_written_as_markup(self):
        for name in ('innerHTML', 'outerHTML', 'insertAdjacentHTML', 'document.write', 'eval('):
            self.assertNotIn(name, self.script)
        self.assertNotRegex(self.script, r'\bon[a-z]+\s*=')

    def test_the_only_write_is_the_unchanged_resubmit_request(self):
        self.assertEqual(self.script.count("method: 'POST'"), 1)
        self.assertEqual(self.script.count('/api/manual/resubmit'), 1)
        self.assertEqual(self.script.count('fetch('), 2)
        body = re.search(r'body: JSON\.stringify\(\{(.*?)\}\),', self.script).group(1)
        self.assertEqual([part.split(':')[0].strip() for part in body.split(', ')],
                         ['job_id', 'answers', 'nothing_sent_confirmed'])
        rows = function_body(self.script, 'collectAnswers')
        self.assertIn('{question: row.querySelector', rows)
        self.assertIn('answer: row.querySelector', rows)
        self.assertIn("'X-Monitor-Token': TOKEN", self.script)

    def test_dismiss_and_restore_never_touch_the_server(self):
        for name in ('setDismissed', 'readDismissed', 'writeDismissed'):
            with self.subTest(function=name):
                body = function_body(self.script, name)
                for forbidden in ('fetch', 'XMLHttpRequest', 'sendBeacon', 'TOKEN'):
                    self.assertNotIn(forbidden, body)
        self.assertIn("addEventListener('click', () => setDismissed(job.job_id, !dismissed))", self.script)

    def test_dismissed_ids_live_in_local_storage_under_the_agreed_key_and_never_throw(self):
        self.assertIn("const DISMISSED_KEY = 'manual-dismissed'", self.script)
        lines = [line for line in self.script.splitlines() if 'localStorage' in line]
        self.assertEqual(len(lines), 2)
        for body in (function_body(self.script, 'readDismissed'), function_body(self.script, 'writeDismissed')):
            self.assertRegex(body, r'try \{[^}]*localStorage[^}]*\}\s*catch')
        self.assertNotIn('sessionStorage', self.script)

    def test_a_dismissed_job_that_left_the_list_is_forgotten(self):
        body = function_body(self.script, 'applyManualData')
        self.assertIn('manualDismissed.delete(id)', body)
        self.assertIn('writeDismissed()', body)

    def test_the_refresh_skips_an_open_dialog_and_only_redraws_changed_data(self):
        body = function_body(self.script, 'refreshManual')
        self.assertIn("$('manual-dialog').open", body.split('\n')[1])
        self.assertIn('text !== manualLastText', body)
        self.assertIn('updateManualAges()', body)
        self.assertNotIn('manual-search', body)

    def test_the_search_box_is_never_cleared_by_a_redraw(self):
        for name in ('renderManual', 'applyManualData', 'manualChips'):
            self.assertNotRegex(function_body(self.script, name), r"manual-search'\)\.value\s*=")

    def test_the_group_filter_is_kept_in_the_hash_without_adding_history_or_leaving_the_tab(self):
        self.assertNotIn('pushState', self.script)
        self.assertNotRegex(self.script, r'location\.hash\s*=[^=]')
        self.assertNotRegex(self.script, r'location\.(assign|replace)\(')
        sync = function_body(self.script, 'syncManualHash')
        self.assertIn("activeTab !== 'manual'", sync)
        self.assertIn("'#manual' + (manualFilter === 'all' ? '' : '/' + manualFilter)", sync)
        self.assertIn('history.replaceState(null', sync)
        read = function_body(self.script, 'manualHashFilter')
        self.assertIn("location.hash.slice(1).split('/')", read)
        self.assertIn("tab !== 'manual'", read)
        self.assertIn('[a-z_]+', read, 'only a plain group key is ever read from the address')
        self.assertIn("let manualFilter = manualHashFilter() || 'all'", self.script)

    def test_questions_are_prefilled_one_row_each_capped_at_the_servers_limit(self):
        self.assertIn(f'const MANUAL_MAX_ROWS = {manual_jobs.MAX_ANSWERS};', self.script)
        body = function_body(self.script, 'openManualDialog')
        self.assertIn('job.questions', body)
        self.assertIn('.slice(0, MANUAL_MAX_ROWS)', body)
        self.assertEqual(body.count('answerRow()'), 1, 'exactly one blank row when no question was found')
        self.assertIn('if (!questions.length)', body)
        self.assertIn('>= MANUAL_MAX_ROWS', function_body(self.script, 'updateDialogControls'))

    def test_dialog_rows_have_accessible_names(self):
        body = function_body(self.script, 'answerRow')
        self.assertIn("setAttribute('aria-label', 'Question ' + n)", body)
        self.assertIn("setAttribute('aria-label', 'Answer ' + n)", body)
        self.assertIn('.htmlFor =', body)

    def test_the_possibly_sent_confirmation_still_gates_the_request(self):
        body = function_body(self.script, 'submitManual')
        self.assertLess(body.index("$('manual-confirm-sent').checked"), body.index('fetch('))
        self.assertIn('nothing_sent_confirmed: risky && $(\'manual-confirm-sent\').checked', body)

    def test_every_job_can_open_the_dialog_whatever_its_group(self):
        body = function_body(self.script, 'manualCard')
        self.assertIn("button.addEventListener('click', () => openManualDialog(job))", body)
        self.assertNotRegex(body, r'if \([^)]*job\.group[^)]*\)\s*(return|\{\s*return)')
        labelled = self.script.split('const RETURN_LABELS')[1].split('\n')[0]
        for group, *_ in manual_jobs.GROUPS:
            self.assertEqual(group in labelled, group in ('needs_answer', 'check_employer', 'retry_later'))
        self.assertIn('RETURN_LABELS[job.group] || RETURN_ANYWAY', body)

    def test_an_id_copy_failure_is_silent(self):
        self.assertRegex(function_body(self.script, 'copyJobId'), r'try \{ await navigator\.clipboard\.writeText\(jobId\); \} catch')

    def test_the_card_hides_the_raw_reason_and_job_id_behind_a_details_element(self):
        body = function_body(self.script, 'manualCard')
        self.assertIn("document.createElement('details')", body)
        self.assertIn('job.headline', body)
        self.assertNotIn('details.open = true', body)
        self.assertIn("'Show the full reason'", body)

    def test_the_sent_badge_is_only_for_possibly_sent_jobs(self):
        body = function_body(self.script, 'manualCard')
        self.assertIn('if (manualRisky(job)) head.append', body)
        self.assertNotIn('Not submitted', self.script)

    def test_user_facing_script_text_no_longer_says_resubmit(self):
        remainder = self.script.replace('/api/manual/resubmit', '')
        self.assertNotIn('resubmit', remainder.lower())


class PageScriptTests(unittest.TestCase):
    def test_the_page_script_parses(self):
        if not Path(NODE).exists():
            self.skipTest('node is not installed')
        with tempfile.TemporaryDirectory() as folder:
            for name, source in (('manual.js', manual_jobs_ui.SCRIPT), ('page.js', campaign_monitor._PAGE_SCRIPT)):
                path = Path(folder) / name
                path.write_text(source, encoding='utf-8')
                done = subprocess.run([NODE, '--check', str(path)], capture_output=True, text=True, timeout=60)
                self.assertEqual(done.returncode, 0, f'{name}: {done.stderr}')

    def test_the_page_script_is_hashed_with_this_tabs_script(self):
        self.assertIn(manual_jobs_ui.SCRIPT, campaign_monitor._PAGE_SCRIPT)
        self.assertIn(manual_jobs_ui.STYLE, campaign_monitor._HTML)


if __name__ == '__main__':
    unittest.main()
