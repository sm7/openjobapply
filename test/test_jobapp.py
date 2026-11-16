"""Local CLI/configuration safety: temporary files only, no worker or service starts."""
import argparse
import fcntl
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import jobapp
from settings import (ConfigError, atomic_write_json, default_profile, load_campaign,
                      load_profile, validate_campaign, validate_profile)


class JobAppTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='jobapp configuration ')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / 'data').mkdir()
        self.profile = default_profile()
        self.profile['candidate'].update(name='Fixture Candidate', first_name='Fixture', last_name='Candidate',
                                         email='fixture@example.invalid', phone='555-0100', location='Boston, MA',
                                         country='United States', work_authorization='authorized',
                                         requires_sponsorship=False)
        self.profile['search']['queries'] = ['healthcare data analyst']
        self.resume = self.root / 'data/resume.pdf'
        self.resume.write_bytes(b'%PDF-1.7\nfixture resume\n')
        atomic_write_json(self.root / 'profile.json', self.profile)
        atomic_write_json(self.root / 'data/campaign.json', {'target_total': 7})

    def setup_args(self, **values):
        return argparse.Namespace(profile=values.get('profile'), resume=values.get('resume'))

    def test_setup_does_not_reset_history_pacing_or_approved_documents(self):
        protected = {
            self.root / 'data/jobs.db': b'opaque durable application ledger',
            self.root / 'data/ratelimit.json': b'{"lockedUntil":123456789}',
            self.resume: self.resume.read_bytes(),
            self.root / 'profile.json': (self.root / 'profile.json').read_bytes(),
            self.root / 'data/campaign.json': (self.root / 'data/campaign.json').read_bytes(),
        }
        for path, content in protected.items():
            path.write_bytes(content)
        result = jobapp.setup(self.setup_args(resume=str(self.resume)), self.root)
        self.assertTrue(result['profile_ready'])
        for path, content in protected.items():
            self.assertEqual(path.read_bytes(), content, path)

    def test_fresh_setup_has_no_invented_candidate_and_cannot_pass_readiness(self):
        (self.root / 'profile.json').unlink()
        (self.root / 'data/campaign.json').unlink()
        self.resume.unlink()
        result = jobapp.setup(self.setup_args(), self.root)
        self.assertFalse(result['profile_ready'])
        with self.assertRaises(ConfigError):
            load_profile(self.root, ready=True)
        self.assertFalse((self.root / 'data/jobs.db').exists())
        self.assertEqual(load_profile(self.root)['candidate']['name'], '')

    def test_setup_refuses_replacing_an_existing_candidate(self):
        imported = copy.deepcopy(self.profile)
        imported['candidate']['name'] = 'Different Candidate'
        source = self.root / 'incoming.json'
        atomic_write_json(source, imported)
        original = (self.root / 'profile.json').read_bytes()
        with self.assertRaises(ConfigError):
            jobapp.setup(self.setup_args(profile=str(source)), self.root)
        self.assertEqual((self.root / 'profile.json').read_bytes(), original)

    def test_setup_refuses_replacing_the_approved_resume(self):
        incoming = self.root / 'incoming.pdf'
        incoming.write_bytes(b'%PDF-1.7\ndifferent resume\n')
        approved = self.resume.read_bytes()
        with self.assertRaises(ConfigError):
            jobapp.setup(self.setup_args(resume=str(incoming)), self.root)
        self.assertEqual(self.resume.read_bytes(), approved)

    def test_invalid_setting_updates_leave_configuration_unchanged(self):
        for key, value in [
            ('campaign.targte_total', 10),
            ('campaign.max_submissions_per_day', True),
            ('campaign.runtime.dashboard_port', 70000),
            ('profile.preferences.salary_minimum', 70000),
            ('profile.candidate.us_citizen', 'false'),
            ('profile.answers', {'salary_expectation_usd': 1}),
        ]:
            with self.subTest(key=key):
                before = {path: path.read_bytes() for path in
                          (self.root / 'profile.json', self.root / 'data/campaign.json')}
                with self.assertRaises(ConfigError):
                    jobapp.config_set(key, value, self.root)
                for path, contents in before.items():
                    self.assertEqual(path.read_bytes(), contents)

    def test_credentials_cannot_be_written_into_the_profile_or_echoed_in_errors(self):
        before = (self.root / 'profile.json').read_bytes()
        with self.assertRaises(ConfigError) as failure:
            jobapp.config_set('profile.answers.password', 'PRIVATE_SECRET', self.root)
        self.assertNotIn('PRIVATE_SECRET', str(failure.exception))
        self.assertEqual((self.root / 'profile.json').read_bytes(), before)

    def test_corrupt_configuration_is_not_replaced_by_setup(self):
        path = self.root / 'data/campaign.json'
        path.write_text('{incomplete')
        with self.assertRaises(ConfigError):
            jobapp.setup(self.setup_args(), self.root)
        self.assertEqual(path.read_text(), '{incomplete')

    def test_stop_uses_the_actual_singleton_lock_without_starting_a_worker(self):
        import services
        self.assertFalse(services.supervisor_running(self.root))
        self.assertFalse(jobapp.stop(self.root)['stop_requested'])
        with (self.root / 'data/campaign-worker.lock').open('a+') as holder:
            fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertTrue(services.supervisor_running(self.root))
            self.assertTrue(jobapp.stop(self.root)['stop_requested'])
            self.assertTrue((self.root / 'data/campaign.stop').is_file())
        self.assertFalse(services.supervisor_running(self.root))

    def test_zero_target_is_closed_even_before_database_initialization(self):
        jobapp.config_set('campaign.target_total', 0, self.root)
        snapshot = jobapp.status(self.root)
        self.assertTrue(snapshot['complete'])
        self.assertEqual(snapshot['remaining'], 0)
        self.assertFalse((self.root / 'data/jobs.db').exists())

    def test_unlimited_target_before_first_run_is_read_only_and_never_complete(self):
        jobapp.config_set('campaign.target_total', None, self.root)
        self.assertIsNone(load_campaign(self.root)['target_total'])
        snapshot = jobapp.status(self.root)
        self.assertEqual(snapshot['confirmed'], 0)
        self.assertIsNone(snapshot['target'])
        self.assertIsNone(snapshot['remaining'])
        self.assertFalse(snapshot['complete'])
        self.assertEqual(snapshot['runtime']['state'], 'not_started')
        self.assertFalse((self.root / 'data/jobs.db').exists())
        self.assertFalse((self.root / 'data/campaign-worker.lock').exists())

    def test_status_before_first_run_is_read_only(self):
        snapshot = jobapp.status(self.root)
        self.assertEqual((snapshot['confirmed'], snapshot['remaining']), (0, 7))
        self.assertFalse(snapshot['complete'])
        self.assertFalse((self.root / 'data/jobs.db').exists())
        self.assertFalse((self.root / 'data/campaign-worker.lock').exists())

    def test_failed_atomic_write_preserves_the_previous_file(self):
        path = self.root / 'data/campaign.json'
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            atomic_write_json(path, {'target_total': float('nan')})
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(path.parent.glob('.campaign.json-*')), [])

    def test_readiness_requires_real_queries_and_a_readable_pdf(self):
        for change in ('queries', 'resume'):
            profile = copy.deepcopy(self.profile)
            if change == 'queries':
                profile['search']['queries'] = []
            else:
                profile['candidate']['resume_path'] = 'missing.pdf'
            with self.subTest(change=change), self.assertRaises(ConfigError):
                validate_profile(profile, self.root, ready=True)

    def test_malformed_years_and_nonfinite_cli_values_are_rejected(self):
        for years in ([], {'sql': True}, {'sql': -1}, {'sql': float('inf')}):
            profile = copy.deepcopy(self.profile)
            profile['answers']['years_experience'] = years
            with self.subTest(years=years), self.assertRaises(ConfigError):
                validate_profile(profile, self.root)
        for value in ('NaN', 'Infinity', '-Infinity'):
            with self.subTest(value=value), self.assertRaises(ConfigError):
                jobapp._parse_value(value)

    def test_closed_target_and_zero_caps_are_valid_but_bool_caps_are_not(self):
        result = validate_campaign({'target_total': 0, 'max_submissions_per_day': 0})
        self.assertEqual(result['target_total'], 0)
        self.assertEqual(result['max_submissions_per_day'], 0)
        with self.assertRaises(ConfigError):
            validate_campaign({'max_submissions_per_day': False})

    def test_invalid_targets_cannot_replace_configuration(self):
        before = (self.root / 'data/campaign.json').read_bytes()
        for value in (-1, True, False, 1.5, '50', [], {}, float('inf'), float('nan')):
            with self.subTest(value=repr(value)):
                with self.assertRaisesRegex(ConfigError, 'campaign.target_total'):
                    jobapp.config_set('campaign.target_total', value, self.root)
                self.assertEqual((self.root / 'data/campaign.json').read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
