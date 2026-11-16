"""profile.example.json is the template users fill in: complete, valid, never submittable as shipped."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))
from settings import ConfigError, validate_profile  # noqa: E402


def key_paths(value, prefix=''):
    """Every dict key path. Contents of free-form maps are not part of the template contract."""
    paths = set()
    if isinstance(value, dict):
        for key, item in value.items():
            path = prefix + '/' + key
            paths.add(path)
            paths |= key_paths(item, path)
    return paths


class ExampleProfileTests(unittest.TestCase):
    def setUp(self):
        self.example = json.loads((ROOT / 'profile.example.json').read_text())
        self.fixture = json.loads((ROOT / 'test/fixtures/profile.fixture.json').read_text())

    def test_the_template_is_valid_but_cannot_start_a_campaign(self):
        validate_profile(self.example, ROOT)
        with self.assertRaises(ConfigError):
            validate_profile(self.example, ROOT, ready=True)

    def test_the_template_holds_no_candidate_facts(self):
        candidate = self.example['candidate']
        for key in ('name', 'first_name', 'last_name', 'email', 'phone', 'street', 'city', 'zip', 'linkedin_url'):
            self.assertEqual(candidate[key], '', key)
        self.assertEqual(self.example['search']['queries'], [])
        self.assertFalse(any(self.example['application_permissions'].values()))

    def test_unknown_facts_are_null_never_true_or_false(self):
        for key in ('requires_sponsorship', 'us_citizen', 'green_card_holder', 'has_ead'):
            self.assertIsNone(self.example['candidate'][key], key)
        for key in ('at_least_18', 'excluded_from_federal_health_programs', 'prior_government_employment',
                    'previously_worked_at_prospective_employer'):
            self.assertIsNone(self.example['answers'][key], key)

    def test_reviewed_questions_accept_yes_no_text_and_numbers_only(self):
        good = json.loads(json.dumps(self.example))
        good['answers']['reviewed_questions'] = {'Have you worked with SQL?': True, 'Years of Epic?': 3, 'Describe Epic': 'Two years'}
        validate_profile(good, ROOT)
        for bad in ({'Question?': ''}, {'Question?': ['yes']}, {'  ': True}):
            broken = json.loads(json.dumps(self.example))
            broken['answers']['reviewed_questions'] = bad
            with self.assertRaises(ConfigError, msg=str(bad)):
                validate_profile(broken, ROOT)
        broken = json.loads(json.dumps(self.example))
        broken['answers']['reviewed_questions'] = ['not', 'an', 'object']
        with self.assertRaises(ConfigError):
            validate_profile(broken, ROOT)

    def test_the_template_lists_every_question_a_complete_profile_answers(self):
        missing = key_paths(self.fixture) - key_paths(self.example)
        # Entries inside user-defined maps are data, not template fields.
        free_form = ('/answers/years_experience/', '/answers/prior_interviews_by_company/', '/answers/python_libraries_used/')
        self.assertEqual({path for path in missing if not path.startswith(free_form)}, set())

    def test_a_filled_in_template_passes_readiness(self):
        filled = json.loads(json.dumps(self.example))
        filled['candidate'].update({
            'name': 'Jane Doe', 'first_name': 'Jane', 'last_name': 'Doe', 'email': 'jane.doe@example.com',
            'phone': '(555) 010-0199', 'country': 'United States', 'location': 'Boston, MA',
            'work_authorization': 'authorized', 'requires_sponsorship': False,
            'resume_path': 'test/fixtures/resume.pdf'})
        filled['search']['queries'] = ['data analyst']
        filled['preferences']['min_salary_usd'] = 70000
        validate_profile(filled, ROOT, ready=True)


if __name__ == '__main__':
    unittest.main()
