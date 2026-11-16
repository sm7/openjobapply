"""Description verification: the worker reads a job page, the controller stores it and re-scores that one job.

Why this exists: jobs that pass every rule but have no description are held as `needs_description`, and nothing
used to fetch the text. `next-verify` picks the next such job and `save-description` stores what the worker read.
Both must stay narrow: they never record an outcome, never touch a reservation, and never move a job that
someone already worked or decided on. Isolated temporary state; the real ledger and profile are never read.
Run: python3 -m unittest discover -s test -p 'test_verify.py'
"""
import contextlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import campaign
import db

PROFILE = {
    'preferences': {'location_mode': 'united_states', 'accept_remote': True, 'min_salary_usd': 75000,
                    'unknown_salary': 'allow', 'allow_senior_ic': True, 'employment_types': ['permanent']},
    'search': {'title_include': ['analyst'], 'title_exclude': ['intern', 'director', 'manager'],
               'seniority_max_years_required': 8, 'skills': ['sql', 'tableau', 'hedis', 'claims', 'medicare',
                                                              'power bi', 'excel', 'dashboard']},
}
GOOD = ('We need an analyst with SQL, Tableau, HEDIS, claims, Medicare, Power BI, Excel and dashboard experience. '
        'Population health and quality measures work. 3 years of experience required. Salary $90,000 - $110,000.')


class VerifyTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        (self.root / 'data').mkdir()
        (self.root / 'profile.json').write_text(json.dumps(PROFILE))
        (self.root / 'data/campaign.json').write_text(json.dumps({'target_total': 50}))
        for name, value in [('ROOT', self.root), ('CONFIG', self.root / 'data/campaign.json'),
                            ('PACE', self.root / 'data/ratelimit.json')]:
            self.stack.enter_context(patch.object(campaign, name, value))
        self.stack.enter_context(patch.object(db, 'ROOT', self.root))
        self.stack.enter_context(patch.object(db, 'DB_PATH', self.root / 'data/jobs.db'))
        self.con = campaign.connect()
        self.con.row_factory = sqlite3.Row
        campaign._ensure_verification_columns(self.con)
        self.addCleanup(self.con.close)

    def job(self, job_id, status='discovered', title='Healthcare Data Analyst', location='Boston, MA',
            notes='needs_description', score=50.0, source='linkedin', verified_at=None, description=None):
        self.con.execute(
            'INSERT INTO jobs(id,source,source_id,url,title,company,location,status,score,score_notes,description,'
            'verified_at,first_seen,last_seen) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (job_id, source, job_id, 'https://www.linkedin.com/jobs/view/' + job_id, title, 'Employer', location,
             status, score, notes, description, verified_at, 1, 1))
        self.con.commit()
        return job_id

    def attempt(self, job_id, result='skipped'):
        self.con.execute("INSERT INTO applications(job_id,attempted,result,reason,run_id) VALUES(?,?,?,?,?)",
                         (job_id, 100.0, result, 'Worker looked at it', 'campaign-50'))
        self.con.commit()

    def row(self, job_id):
        return dict(self.con.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone())

    def everything(self):
        return list(self.con.iterdump())

    def picked(self):
        job = campaign.next_verify(self.con)['job']
        return job['id'] if job else None

    # --- which job is next -----------------------------------------------------------------------------------

    def test_the_next_job_is_the_best_scored_unverified_unattempted_linkedin_job(self):
        self.job('low', score=45.0)
        self.job('high', score=50.0)
        self.assertEqual(self.picked(), 'high')

    def test_a_job_that_someone_already_worked_is_never_picked(self):
        self.job('worked')
        self.attempt('worked', 'skipped')
        self.job('fresh', score=10.0)
        self.assertEqual(self.picked(), 'fresh')

    def test_only_jobs_that_still_wait_for_a_description_are_picked(self):
        self.job('rejected', status='skipped', notes='rejected:location')
        self.job('queued', status='queued', notes='sql')
        self.job('applied', status='applied', notes=None)
        self.job('manual', status='manual', notes=None)
        self.job('already-verified', verified_at=5.0, description='read already')
        self.job('not-linkedin', source='company')
        self.assertIsNone(self.picked())

    def test_a_discovered_job_that_a_rule_rejected_is_not_picked(self):
        self.job('rule-rejected', notes='rejected:too_senior:senior')
        self.assertIsNone(self.picked())

    def test_picking_changes_nothing(self):
        self.job('a')
        before = self.everything()
        self.picked()
        self.picked()
        self.assertEqual(self.everything(), before)

    # --- what saving a description does ---------------------------------------------------------------------

    def test_a_strong_description_queues_the_job_with_the_apply_route_recorded(self):
        self.job('good')
        result = campaign.save_description(self.con, 'good', {'description': GOOD, 'external_apply': True,
                                                              'apply_url': 'https://careers.example.com/apply/1'})
        self.assertEqual(result, {'verified': True, 'job_id': 'good', 'status': 'queued'})
        row = self.row('good')
        self.assertEqual((row['status'], row['apply_method'], row['apply_url']), ('queued', 'external', 'https://careers.example.com/apply/1'))
        self.assertGreaterEqual(row['score'], 50)
        self.assertIsNotNone(row['verified_at'])
        self.assertEqual(row['description'], GOOD)

    def test_an_easy_apply_job_records_the_easy_route(self):
        self.job('easy')
        campaign.save_description(self.con, 'easy', {'description': GOOD, 'easy_apply': True})
        row = self.row('easy')
        self.assertEqual((row['apply_method'], row['easy_apply']), ('easy', 1))

    def test_a_description_that_fails_a_rule_rejects_the_job_instead_of_queuing_it(self):
        self.job('hard')
        campaign.save_description(self.con, 'hard', {'description': 'Requires 12 years of experience with SQL.'})
        row = self.row('hard')
        self.assertEqual(row['status'], 'skipped')
        self.assertEqual(row['score_notes'], 'rejected:requires_12_years')

    def test_a_description_with_a_low_salary_midpoint_rejects_the_job(self):
        self.job('cheap')
        campaign.save_description(self.con, 'cheap', {'description': GOOD, 'salary_min': 50000, 'salary_max': 60000})
        self.assertEqual(self.row('cheap')['score_notes'], 'rejected:salary_below_min:50000-60000')

    def test_a_closed_job_is_skipped_and_never_queued(self):
        self.job('gone')
        result = campaign.save_description(self.con, 'gone', {'description': None, 'closed': True})
        self.assertEqual(result['status'], 'skipped')
        self.assertEqual((self.row('gone')['status'], self.row('gone')['score_notes']), ('skipped', 'rejected:closed'))

    def test_a_thin_description_scores_below_the_queue_threshold_and_is_not_queued(self):
        self.job('thin')
        campaign.save_description(self.con, 'thin', {'description': 'Data analyst role.'})
        self.assertEqual(self.row('thin')['status'], 'scored')

    def test_a_verified_job_is_not_picked_again(self):
        self.job('once')
        campaign.save_description(self.con, 'once', {'description': GOOD})
        self.assertIsNone(self.picked())

    # --- what it must never do --------------------------------------------------------------------------------

    def test_a_job_with_application_history_is_refused_and_nothing_changes(self):
        self.job('worked')
        self.attempt('worked', 'skipped')
        before = self.everything()
        with self.assertRaisesRegex(ValueError, 'verification_refused_for_job_with_history'):
            campaign.save_description(self.con, 'worked', {'description': GOOD})
        self.assertEqual(self.everything(), before)

    def test_a_job_that_is_not_waiting_is_refused_whatever_its_status(self):
        for status in ('queued', 'applying', 'applied', 'manual', 'failed', 'skipped', 'scored'):
            with self.subTest(status=status):
                job_id = self.job('job-' + status, status=status)
                before = self.everything()
                with self.assertRaisesRegex(ValueError, 'verification_requires_discovered_job'):
                    campaign.save_description(self.con, job_id, {'description': GOOD})
                self.assertEqual(self.everything(), before)

    def test_an_unknown_job_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'unknown_job'):
            campaign.save_description(self.con, 'nothing', {'description': GOOD})

    def test_it_never_records_an_outcome_or_touches_a_reservation(self):
        self.job('good')
        self.con.execute("INSERT INTO jobs(id,source,source_id,url,title,company,status,first_seen,last_seen) "
                         "VALUES('other','linkedin','o','https://x.invalid/o','t','c','applying',1,1)")
        self.con.execute("INSERT INTO campaign_reservations VALUES('other',1.0,'reserved',NULL)")
        self.con.commit()
        applications = self.con.execute('SELECT COUNT(*) FROM applications').fetchone()[0]
        campaign.save_description(self.con, 'good', {'description': GOOD})
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM applications').fetchone()[0], applications)
        self.assertEqual(self.con.execute("SELECT state FROM campaign_reservations WHERE job_id='other'").fetchone()[0], 'reserved')
        self.assertEqual(self.row('other')['status'], 'applying')
        self.assertEqual(campaign.confirmed(self.con), 0)

    def test_a_job_can_never_become_applied_through_this_command(self):
        for index, payload in enumerate(({'description': GOOD}, {'description': GOOD, 'closed': True},
                                       {'description': GOOD, 'external_apply': True}, {'description': 'applied'})):
            with self.subTest(payload=list(payload)):
                job_id = self.job('j' + str(index))
                campaign.save_description(self.con, job_id, payload)
                self.assertNotIn(self.row(job_id)['status'], ('applied', 'applying'))

    # --- bad input is refused before any write -----------------------------------------------------------------

    def test_malformed_input_is_refused_and_leaves_the_database_unchanged(self):
        self.job('good')
        before = self.everything()
        for name, payload, code in [
            ('no description', {}, 'verification_requires_description'),
            ('blank description', {'description': '   '}, 'verification_requires_description'),
            ('description not text', {'description': 5}, 'verification_requires_description'),
            ('unknown field', {'description': GOOD, 'status': 'applied'}, 'verification_has_unknown_fields'),
            ('outcome smuggled in', {'description': GOOD, 'result': 'applied'}, 'verification_has_unknown_fields'),
            ('not an object', ['description'], 'verification_has_unknown_fields'),
            ('too long', {'description': 'x' * (campaign.MAX_DESCRIPTION + 1)}, 'verification_description_too_long'),
            ('salary text', {'description': GOOD, 'salary_min': '90000'}, 'verification_invalid_salary_min'),
            ('salary bool', {'description': GOOD, 'salary_max': True}, 'verification_invalid_salary_max'),
            ('salary zero', {'description': GOOD, 'salary_min': 0}, 'verification_invalid_salary_min'),
            ('salary absurd', {'description': GOOD, 'salary_max': 10 ** 9}, 'verification_invalid_salary_max'),
            ('javascript url', {'description': GOOD, 'apply_url': 'javascript:alert(1)'}, 'verification_invalid_apply_url'),
            ('url with space', {'description': GOOD, 'apply_url': 'https://x.example/a b'}, 'verification_invalid_apply_url'),
            ('url not text', {'description': GOOD, 'apply_url': 7}, 'verification_invalid_apply_url'),
        ]:
            with self.subTest(name):
                with self.assertRaisesRegex(ValueError, code):
                    campaign.save_description(self.con, 'good', payload)
                self.assertEqual(self.everything(), before)

    # --- the command line the worker actually runs ---------------------------------------------------------------

    def run_cli(self, *argv, stdin=''):
        with patch.object(campaign.sys, 'argv', ['campaign.py', *argv]), \
                patch.object(campaign.sys, 'stdin', io.StringIO(stdin)), \
                patch.object(campaign.sys, 'stdout', io.StringIO()) as out, \
                patch.object(campaign.sys, 'stderr', io.StringIO()) as err, \
                patch.object(campaign, 'connect', return_value=self.con):
            code = campaign.main()
        return code, out.getvalue(), err.getvalue()

    def test_the_commands_work_through_the_command_line_and_report_errors_as_json(self):
        self.job('good')
        code, out, err = self.run_cli('next-verify')
        self.assertEqual((code, json.loads(out)['job']['id'], err), (0, 'good', ''))
        code, out, err = self.run_cli('save-description', 'good', stdin=json.dumps({'description': GOOD}))
        self.assertEqual((code, json.loads(out)['status'], err), (0, 'queued', ''))
        code, out, err = self.run_cli('save-description', 'good', stdin=json.dumps({'description': GOOD}))
        self.assertEqual((code, out, json.loads(err)), (2, '', {'error': 'verification_requires_discovered_job'}))
        code, out, err = self.run_cli('next-verify')
        self.assertEqual((code, json.loads(out)), (0, {'job': None}))

    def fresh_database(self, stack):
        root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        (root / 'data').mkdir()
        (root / 'profile.json').write_text(json.dumps(PROFILE))
        (root / 'data/campaign.json').write_text(json.dumps({'target_total': 50}))
        for name, value in [('ROOT', root), ('CONFIG', root / 'data/campaign.json'), ('PACE', root / 'data/ratelimit.json')]:
            stack.enter_context(patch.object(campaign, name, value))
        stack.enter_context(patch.object(db, 'ROOT', root))
        stack.enter_context(patch.object(db, 'DB_PATH', root / 'data/jobs.db'))
        fresh = campaign.connect()
        fresh.row_factory = sqlite3.Row
        stack.callback(fresh.close)
        columns = {row[1] for row in fresh.execute('PRAGMA table_info(jobs)')}
        self.assertTrue({'verified_at', 'apply_method'}.isdisjoint(columns), 'fixture must start without the columns')
        fresh.execute("INSERT INTO jobs(id,source,source_id,url,title,company,location,status,score,score_notes,first_seen,last_seen) "
                      "VALUES('j','linkedin','j','https://www.linkedin.com/jobs/view/j','Healthcare Data Analyst','E','Boston, MA',"
                      "'discovered',50,'needs_description',1,1)")
        fresh.commit()
        return fresh

    def test_picking_a_job_works_on_a_database_that_never_ran_the_old_pipeline(self):
        with contextlib.ExitStack() as stack:
            fresh = self.fresh_database(stack)
            self.assertEqual(campaign.next_verify(fresh)['job']['id'], 'j')

    def test_saving_a_description_works_on_a_database_that_never_ran_the_old_pipeline(self):
        with contextlib.ExitStack() as stack:
            fresh = self.fresh_database(stack)
            self.assertEqual(campaign.save_description(fresh, 'j', {'description': GOOD})['status'], 'queued')


if __name__ == '__main__':
    unittest.main()
