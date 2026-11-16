"""The scorer rejects only for the reasons it names. Each case below was a wrong rejection or a crash."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))
import score  # noqa: E402

PROFILE = json.loads((ROOT / 'test/fixtures/profile.fixture.json').read_text())
CORE = 'Healthcare data analyst using SQL, Tableau, claims, HEDIS and population health analytics. '


def job(description='', **changes):
    base = {'title': 'Healthcare Data Analyst', 'company': 'Acme Health', 'location': 'United States (Remote)',
            'remote': 1, 'salary_min': None, 'salary_max': None, 'employment_type': 'Full-time', 'closed': 0,
            'description': CORE + description}
    base.update(changes)
    return base


def reject(description='', **changes):
    return score.evaluate(job(description, **changes), PROFILE)[2]


class YearsOfExperienceTests(unittest.TestCase):
    def test_a_real_requirement_above_the_ceiling_still_rejects(self):
        self.assertEqual(reject('Requires 12 years of experience with SQL.'), 'requires_12_years')
        self.assertEqual(reject('Minimum 10+ years of analytics experience.'), 'requires_10_years')

    def test_age_company_history_and_optional_years_do_not_reject(self):
        for text in ('Applicants must be at least 18 years of age.',
                     'For over 25 years, Acme Health has served members.',
                     '10+ years is a plus but not required.',
                     'We have 30 years of history helping patients.'):
            self.assertIsNone(reject(text), text)

    def test_a_range_is_judged_by_its_lower_bound(self):
        self.assertIsNone(reject('5-10 years of analytics experience.'))
        self.assertEqual(reject('9-12 years of analytics experience.'), 'requires_9_years')


class SalaryTests(unittest.TestCase):
    def test_a_budget_in_millions_is_not_pay_and_a_later_real_range_is_used(self):
        self.assertEqual(score._salary_from_text('budget of $30 - $40 million. pay range $90,000 - $110,000 per year.'), (90000.0, 110000.0))
        self.assertIsNone(reject('Budget of $30 - $40 million. Pay range $90,000 - $110,000 per year.'))

    def test_a_pay_range_beats_an_earlier_unrelated_dollar_range(self):
        text = 'Our grant was $100,000 - $200,000 in 2020. Base salary range: $70,000 - $80,000.'
        self.assertEqual(score._salary_from_text(text), (70000.0, 80000.0))

    def test_a_malformed_salary_sentence_does_not_crash(self):
        self.assertEqual(score._salary_from_text('Salary from $80,000 to, at most, $95,000.'), (None, None))
        self.assertIsNone(reject('Salary from $80,000 to, at most, $95,000.'))

    def test_a_low_real_range_still_rejects(self):
        self.assertTrue(reject('Pay range $40,000 - $50,000 per year.').startswith('salary_below_min:'))


class LocationTests(unittest.TestCase):
    def test_a_time_zone_that_includes_massachusetts_is_not_an_exclusion(self):
        self.assertIsNone(reject('Must reside in the Eastern or Central time zone.'))

    def test_a_time_zone_that_excludes_massachusetts_still_rejects(self):
        self.assertEqual(reject('Must reside in the Pacific or Mountain time zone.'), 'location')

    def test_an_employer_team_abroad_is_not_a_candidate_restriction(self):
        self.assertIsNone(reject('Our data engineering team is based in India.'))

    def test_a_candidate_restricted_to_a_foreign_country_still_rejects(self):
        self.assertEqual(reject('This role is remote in India.'), 'location')
        self.assertEqual(reject('Candidates must be based in Canada.'), 'location')


class TitleTests(unittest.TestCase):
    def setUp(self):
        self.profile = json.loads(json.dumps(PROFILE))
        self.profile['search']['title_exclude'] = ['intern', 'vp ', 'nurse']

    def title_reject(self, title):
        return score.evaluate(job(title=title), self.profile)[2]

    def test_a_whole_word_still_excludes(self):
        self.assertEqual(self.title_reject('Nurse Data Analyst'), 'title_excluded:nurse')
        self.assertEqual(self.title_reject('Nurses Data Analyst'), 'title_excluded:nurse')

    def test_a_word_inside_another_word_does_not_exclude(self):
        self.assertIsNone(self.title_reject('Internal Medicine Data Analyst'))
        self.assertIsNone(self.title_reject('Nursery Program Data Analyst'))

    def test_a_literal_fragment_with_a_space_is_kept(self):
        self.assertEqual(self.title_reject('VP Healthcare Analytics'), 'title_excluded:vp')


class ScoringErrorIsolationTests(unittest.TestCase):
    def test_one_unscorable_job_is_held_and_the_others_are_still_scored(self):
        import sqlite3
        from unittest import mock
        import db
        import ingest
        con = sqlite3.connect(':memory:')
        con.row_factory = sqlite3.Row
        con.executescript(db.SCHEMA)
        for number in (1, 2):
            con.execute("INSERT INTO jobs(id,source,url,title,company,location,description,status,first_seen,last_seen) "
                        "VALUES(?,?,?,?,?,?,?,?,0,0)",
                        (f'job{number}', 'linkedin', f'https://example.invalid/{number}', 'Healthcare Data Analyst', 'Acme Health',
                         'United States (Remote)', CORE + 'Salary $90,000 - $110,000.', 'discovered'))
        real = score.evaluate

        def flaky(candidate, profile):
            if candidate['id'] == 'job1':
                raise ValueError('malformed posting')
            return real(candidate, profile)

        with mock.patch.object(ingest.sc, 'evaluate', flaky):
            for row in con.execute("SELECT * FROM jobs WHERE status='discovered'").fetchall():
                ingest.score_safely(con, PROFILE, row)
        rows = {r['id']: (r['status'], r['score_notes']) for r in con.execute('SELECT * FROM jobs')}
        self.assertEqual(rows['job1'], ('discovered', 'score_error:ValueError'))
        self.assertEqual(rows['job2'][0], 'queued')

class ExplainTests(unittest.TestCase):
    def test_the_explanation_names_the_text_that_caused_the_rejection(self):
        reason, detail = score.explain(job('Requires 12 years of experience with SQL.'), PROFILE)
        self.assertEqual(reason, 'requires_12_years')
        self.assertIn('12 years of experience', detail)

    def test_an_accepted_job_has_no_rejection(self):
        self.assertEqual(score.explain(job(), PROFILE)[0], None)


if __name__ == '__main__':
    unittest.main()
