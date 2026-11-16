"""Candidate facts live in profile.json. The source must not carry one person's state, field or employers."""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))
import score  # noqa: E402

# A word that points at one candidate's home or field. Built from parts so this file is not itself a hit.
FORBIDDEN = re.compile(r'\b(?:' + '|'.join([
    'massa' + 'chusetts', 'bos' + 'ton', 'hed' + 'is', 'healthy ' + 'planet',
]) + r')\b', re.IGNORECASE)

# Reference data every user needs is allowed: the table of all states and the list of named US metro areas.
ALLOWED_LINE = re.compile(r'STATE_NAMES|_NAME_TO_ABBR|"MA": "|\bMA: "|"new york city"|_US_METROS|^\s*"alabama"', re.IGNORECASE)


def source_files():
    for pattern in ('*.py', '*.js', '*.md'):
        yield from sorted((ROOT / 'src').rglob(pattern))


class NoHardCodedCandidateFactsTests(unittest.TestCase):
    def test_source_names_no_home_state_city_or_field(self):
        hits = []
        for path in source_files():
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if FORBIDDEN.search(line) and not ALLOWED_LINE.search(line):
                    hits.append(f'{path.relative_to(ROOT)}:{number}: {line.strip()[:100]}')
        self.assertEqual(hits, [])

    def test_the_scorer_takes_its_word_lists_from_the_profile(self):
        nurse = {
            'candidate': {'state': 'TX'},
            'preferences': {'location_mode': 'united_states', 'accept_remote': True, 'min_salary_usd': 50000,
                            'unknown_salary': 'allow', 'employment_types': ['permanent_full_time']},
            'search': {'title_include': ['nurse'], 'title_exclude': [], 'skills': ['triage'], 'seniority_max_years_required': 8,
                       'core_title_words': ['registered nurse'], 'domain_words': ['telehealth'], 'title_out_of_lane': ['travel']},
        }
        job = {'title': 'Registered Nurse', 'location': 'Austin, TX', 'remote': 0, 'closed': 0, 'employment_type': 'Full-time',
               'description': 'Triage and telehealth nursing. Salary $80,000 - $90,000 per year.'}
        points, notes, rejected = score.evaluate(job, nurse)
        self.assertIsNone(rejected)
        self.assertEqual(points, 40 + 5 + 5)  # core title, one skill, one domain word. No stated-salary bonus: the job has no salary fields.
        self.assertIn('triage', notes)
        excluded = score.evaluate(dict(job, title='Travel Registered Nurse'), nurse)
        self.assertEqual(excluded[2], 'out_of_lane:travel')

    def test_the_example_profile_names_every_word_list_the_scorer_reads(self):
        example = json.loads((ROOT / 'profile.example.json').read_text())
        for key in ('core_title_words', 'role_words', 'context_words', 'domain_words', 'title_out_of_lane'):
            self.assertIn(key, example['search'])


if __name__ == '__main__':
    unittest.main()
