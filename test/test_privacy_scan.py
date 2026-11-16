"""The privacy scan must catch real leaks and accept the project's fictional fixture data."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import check_privacy  # noqa: E402

# Built from parts so that this file itself holds no leak-shaped literal.
REAL_EMAIL = 'someone@' + 'gmail' + '.com'
REAL_PHONE = '(617) ' + '555-1234'
REAL_HOME = '/Users/' + 'alice/work/app'
REAL_KEY = 'AKIA' + 'ABCDEFGHIJKLMNOP'


class PrivacyScanTests(unittest.TestCase):
    def kinds(self, text, denylist=()):
        return {kind for _, _, kind, _ in check_privacy.scan_text('x', text, denylist)}

    def test_reserved_fictional_data_is_accepted(self):
        text = 'jane.doe@example.invalid (555) 010-0199 +1-555-010-0150 /Users/you/project /home/runner/work/x/'
        self.assertEqual(self.kinds(text), set())

    def test_real_looking_email_phone_home_path_and_key_are_found(self):
        self.assertIn('email address', self.kinds('write to ' + REAL_EMAIL))
        self.assertIn('phone number outside 555-0100..0199', self.kinds('call ' + REAL_PHONE))
        self.assertIn('absolute home path', self.kinds('see ' + REAL_HOME))
        self.assertIn('secret-like value', self.kinds('key = "' + REAL_KEY + '"'))

    def test_bare_digit_runs_such_as_job_ids_are_not_phone_numbers(self):
        self.assertEqual(self.kinds('LinkedIn job 4489001523 and data-analyst-4488656624'), set())

    def test_the_privacy_ok_marker_skips_one_line(self):
        self.assertEqual(self.kinds('careers.acme.com@evil.net  privacy-ok'), set())

    def test_denylist_is_case_insensitive_and_reports_the_literal(self):
        found = check_privacy.scan_text('x', 'Lives in Springfield Heights', ['springfield heights'])
        self.assertEqual([(kind, value) for _, _, kind, value in found], [('denylisted text', 'springfield heights')])

    def test_the_published_tree_is_clean(self):
        self.assertEqual(check_privacy.scan(), [])

    def test_scan_walks_a_directory_argument(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'leak.txt').write_text(REAL_EMAIL + '\n')
            found = check_privacy.scan([folder])
        self.assertEqual([kind for _, _, kind, _ in found], ['email address'])


if __name__ == '__main__':
    unittest.main()
