"""Manual-job grouping: which blocker group a stopped job shows under, and the questions the dialog pre-fills.

The group is a best guess from the reason text. It decides only what the dashboard shows first.
Run: python3 -m unittest discover -s test -p 'test_manual_groups.py'
"""
import contextlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import campaign
import manual_jobs

# Reason texts follow the real wording the worker records. Each one names the group a reviewer expects.
EXPECTED = {
    'needs_answer': [
        'Required unanswered question: "Are you comfortable working in a hybrid setting?" (hybrid role).',
        'Required question "Do you live within a commutable distance?" has no reviewed answer.',
        'Required scoped experience-years answer is not established: How many years of PBM experience?',
        'Required Street Address cannot be completed under current profile.',
    ],
    'retry_later': [
        'account_flow_failed: permissioned account creation did not leave the create-account form.',
        'Employer Workday unavailable: An error occurred. Please contact Workday Support.',
        'Application requires new one-time verification code; campaign.skip_new_otp=true.',
        'captcha_required: native application displays Google reCAPTCHA',
        'Taleo requires User Name and Password; no reviewed credential exists.',
    ],
    'decision': [
        'Required combined accuracy/background clause includes prohibited liability waiver.',
        'Mandatory recruitment-message consent has no decline control.',
        'Permanent-work-authorization acceptance of EAD category unknown.',
        'Required qualification unknown: ability to obtain Public Trust clearance.',
    ],
    'not_a_fit': [
        'Employer explicitly requires a local candidate who can travel to its office.',
        'Lumicera restricts remote work to residents of listed states; Massachusetts is excluded.',
        'Required education Degree choices omit both MPH and BMSc; no Other option.',
    ],
    'needs_answer_weak': [
        'Required narrative about experience remains unconfirmed.',
    ],
    'other': [
        'The worker stopped for no stated reason.',
        'No reason recorded',
    ],
}


class ClassifyTests(unittest.TestCase):
    def test_each_known_wording_lands_in_its_group(self):
        for expected, reasons in EXPECTED.items():
            group = 'needs_answer' if expected == 'needs_answer_weak' else expected
            for reason in reasons:
                with self.subTest(reason=reason):
                    self.assertEqual(manual_jobs.classify(reason, 'not_sent'), group)

    def test_a_job_that_may_have_been_sent_is_always_checked_first(self):
        for reason in [r for reasons in EXPECTED.values() for r in reasons]:
            with self.subTest(reason=reason):
                self.assertEqual(manual_jobs.classify(reason, 'possibly_sent'), 'check_employer')

    def test_a_missing_reason_is_other_and_never_an_error(self):
        for reason in (None, ''):
            self.assertEqual(manual_jobs.classify(reason, 'not_sent'), 'other')

    def test_an_unanswered_question_outranks_a_sign_in_word_in_the_same_reason(self):
        reason = 'Required unanswered facts: "Have you applied before?"; the link only reaches a login page.'
        self.assertEqual(manual_jobs.classify(reason, 'not_sent'), 'needs_answer')

    def test_a_clearance_stop_is_a_decision_even_when_the_text_says_unavailable(self):
        reason = 'Required code samples unavailable and federal security-clearance eligibility unknown'
        self.assertEqual(manual_jobs.classify(reason, 'not_sent'), 'decision')


class QuestionTests(unittest.TestCase):
    def test_complete_quoted_questions_are_returned_in_order(self):
        reason = 'Required: "Are you a patient here?" and "Where did you receive services?" (plus follow-up).'
        self.assertEqual(manual_jobs.quoted_questions(reason),
                         ['Are you a patient here?', 'Where did you receive services?'])

    def test_a_question_with_elided_text_is_never_offered(self):
        reason = 'Required: "If you answered yes ... has it been 3 years?" and "Are you a patient?"'
        self.assertEqual(manual_jobs.quoted_questions(reason), ['Are you a patient?'])

    def test_quotes_that_are_not_questions_and_repeats_are_ignored(self):
        reason = 'Box says "I agree." twice. "Are you sure?" "Are you sure?"'
        self.assertEqual(manual_jobs.quoted_questions(reason), ['Are you sure?'])

    def test_no_reason_gives_no_questions_and_the_count_is_bounded(self):
        self.assertEqual(manual_jobs.quoted_questions(None), [])
        many = ' '.join('"Question number %d here?"' % n for n in range(60))
        self.assertEqual(len(manual_jobs.quoted_questions(many)), manual_jobs.MAX_ANSWERS)


class GroupCountTests(unittest.TestCase):
    def test_every_group_is_listed_in_fixed_order_with_zero_counts(self):
        counts = manual_jobs.group_counts([{'group': 'retry_later'}, {'group': 'retry_later'}, {'group': 'decision'}])
        self.assertEqual([c['key'] for c in counts],
                         ['check_employer', 'needs_answer', 'retry_later', 'decision', 'not_a_fit', 'other'])
        self.assertEqual({c['key']: c['count'] for c in counts},
                         {'check_employer': 0, 'needs_answer': 0, 'retry_later': 2, 'decision': 1,
                          'not_a_fit': 0, 'other': 0})

    def test_the_counts_add_up_to_the_listed_jobs_and_every_group_has_text(self):
        jobs = [{'group': key} for key, _label, _hint in manual_jobs.GROUPS for _ in range(2)]
        counts = manual_jobs.group_counts(jobs)
        self.assertEqual(sum(c['count'] for c in counts), len(jobs))
        for entry in counts:
            self.assertTrue(entry['label'] and entry['hint'])


class ListedGroupTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        (root / 'data').mkdir()
        (root / 'data/campaign.json').write_text(json.dumps({'target_total': 50}))
        self.stack.enter_context(patch.object(campaign.db, 'DB_PATH', root / 'data/jobs.db'))
        self.con = campaign.connect()
        self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)

    def job(self, job_id, reason, reservation=None):
        self.con.execute(
            'INSERT INTO jobs(id,source,source_id,url,title,company,status,first_seen,last_seen) VALUES(?,?,?,?,?,?,?,?,?)',
            (job_id, 'fixture', job_id, 'https://example.invalid/' + job_id, 'Analyst', 'Employer', 'manual', 1, 1))
        self.con.execute('INSERT INTO applications(job_id,attempted,result,reason,run_id) VALUES(?,?,?,?,?)',
                         (job_id, 100.0, 'manual', reason, 'campaign-50'))
        if reservation:
            self.con.execute('INSERT INTO campaign_reservations VALUES(?,?,?,?)', (job_id, 50.0, reservation, None))
        self.con.commit()

    def test_each_listed_job_carries_its_group_headline_and_questions(self):
        self.job('ask', 'Required unanswered question: "Are you comfortable commuting?"')
        self.job('sent', 'Worker ended with submission outcome unresolved.', reservation='manual')
        listed = {j['job_id']: j for j in manual_jobs.list_manual(self.con)}
        self.assertEqual((listed['ask']['group'], listed['ask']['questions']),
                         ('needs_answer', ['Are you comfortable commuting?']))
        self.assertEqual((listed['sent']['group'], listed['sent']['sent_risk']), ('check_employer', 'possibly_sent'))
        for job in listed.values():
            self.assertTrue(job['headline'])

    def test_the_group_is_read_only_data_and_changes_nothing_in_the_ledger(self):
        self.job('ask', 'Required unanswered question: "Are you comfortable commuting?"')
        before = list(self.con.iterdump())
        manual_jobs.list_manual(self.con)
        self.assertEqual(list(self.con.iterdump()), before)


if __name__ == '__main__':
    unittest.main()
