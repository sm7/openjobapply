"""Skipped-job recovery behavior, using only isolated temporary SQLite state."""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import contextlib
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import campaign
import db
import manual_jobs
import skipped_jobs


class SkippedJobsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'jobs.db'
        self.con = sqlite3.connect(self.path)
        self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)
        self.con.executescript(db.SCHEMA + '''
            CREATE TABLE campaign_reservations (
                job_id TEXT PRIMARY KEY REFERENCES jobs(id), reserved_at REAL NOT NULL,
                state TEXT NOT NULL, reason TEXT);
            CREATE TABLE job_answers (
                job_id TEXT NOT NULL REFERENCES jobs(id), question TEXT NOT NULL,
                answer TEXT NOT NULL, created_at REAL NOT NULL, PRIMARY KEY(job_id,question));
        ''')
        events = patch.object(campaign, 'event')
        self.events = events.start()
        self.addCleanup(events.stop)

    def job(self, job_id, status='skipped', notes='rejected:location', url=None, source_id=None, reservation=None):
        self.con.execute('''INSERT INTO jobs
            (id,source,source_id,url,apply_url,title,company,location,score_notes,status,first_seen,last_seen)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
            (job_id, 'fixture', source_id or job_id, url or 'https://example.invalid/' + job_id,
             'https://employer.invalid/' + job_id, 'Analyst ' + job_id, 'Employer', 'Boston, MA', notes, status, 1, 1))
        if reservation:
            self.con.execute('INSERT INTO campaign_reservations VALUES(?,?,?,?)', (job_id, 50, reservation, 'private evidence'))
        self.con.commit()
        return job_id

    def outcome(self, job_id, result='skipped', reason='No application opened. Not submitted.', attempted=100, evidence='PRIVATE EVIDENCE'):
        self.con.execute('''INSERT INTO applications(job_id,attempted,result,reason,evidence,run_id)
            VALUES(?,?,?,?,?,?)''', (job_id, attempted, result, reason, evidence, 'fixture'))
        self.con.commit()

    def listed(self):
        return {job['job_id']: job for job in skipped_jobs.list_skipped(self.con)}

    def dump(self):
        return list(self.con.iterdump())

    def queue(self, job_id, answers=None, note='The location restriction changed; please check again.', confirmed=False):
        return skipped_jobs.requeue_skipped(self.con, job_id, [] if answers is None else answers,
                                            review_note=note, nothing_sent_confirmed=confirmed)

    def assert_rejected(self, job_id, code, **kwargs):
        before = self.dump()
        with self.assertRaises(manual_jobs.ManualJobError) as refused:
            self.queue(job_id, **kwargs)
        self.assertEqual(refused.exception.code, code)
        self.assertEqual(self.dump(), before)

    def test_all_skipped_including_machine_only_reasons_are_visible_without_mutation(self):
        for job_id, notes in [('machine', 'rejected:work_authorization'), ('closed', 'rejected:closed'), ('missing', None)]:
            self.job(job_id, notes=notes)
        for status in ('manual', 'queued', 'applying', 'applied', 'failed', 'discovered'):
            self.job(status, status=status)
        before = self.dump()
        with contextlib.closing(sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True)) as readonly:
            readonly.row_factory = sqlite3.Row
            readonly.execute('PRAGMA query_only=ON')
            rows = {row['job_id']: row for row in skipped_jobs.list_skipped(readonly)}
        self.assertEqual(set(rows), {'machine', 'closed', 'missing'})
        self.assertEqual(rows['machine']['reason'], 'rejected:work_authorization')
        self.assertEqual(rows['closed']['reason'], 'rejected:closed')
        self.assertEqual(rows['missing']['reason'], 'No reason recorded')
        self.assertEqual(rows['machine']['sent_risk'], 'not_sent')
        self.assertEqual(rows['missing']['sent_risk'], 'possibly_sent')
        self.assertEqual(rows['machine']['location'], 'Boston, MA')
        self.assertIsNone(rows['machine']['attempted'])
        self.assertTrue(rows['machine']['requeue_allowed'])
        self.assertEqual(self.dump(), before)
        json.dumps(list(rows.values()))

    def test_legacy_tables_can_be_missing_on_a_read_only_database(self):
        self.job('legacy')
        self.job('duplicate', status='queued', url='https://example.invalid/legacy')
        self.outcome('duplicate', result='applied', reason='Historical receipt')
        self.con.executescript('DROP TABLE job_answers; DROP TABLE campaign_reservations;')
        self.con.commit()
        before = self.dump()
        with contextlib.closing(sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True)) as readonly:
            readonly.row_factory = sqlite3.Row
            rows = skipped_jobs.list_skipped(readonly)
        self.assertEqual(rows[0]['saved_answers'], 0)
        self.assertFalse(rows[0]['requeue_allowed'])
        self.assertEqual(self.dump(), before)

    def test_latest_actual_reason_ignores_newer_deferred_audits_and_sanitizes_private_data(self):
        self.job('reason', notes='rejected:old')
        self.outcome('reason', reason='Earlier skipped reason', attempted=90)
        self.outcome('reason', result='manual', reason='Need answer to "Have you worked here before?" at https://secret.invalid/x?token=abc for user@example.com password=private', attempted=100)
        self.outcome('reason', result='deferred', reason='Reviewer note should not replace the outcome', attempted=110)
        row = self.listed()['reason']
        self.assertIn('Need answer', row['reason'])
        self.assertNotIn('secret.invalid', row['reason'])
        self.assertNotIn('user@example.com', row['reason'])
        self.assertNotIn('password=private', row['reason'])
        self.assertNotIn('PRIVATE EVIDENCE', json.dumps(row))
        self.assertNotIn('evidence', row)
        self.assertEqual(row['questions'], ['Have you worked here before?'])
        self.assertEqual(row['attempted'], 100)
        self.assertEqual(row['sent_risk'], 'possibly_sent')

    def test_safe_links_only_and_duplicate_employer_url_is_not_repeated(self):
        bad_links = ['javascript:alert(1)', 'https://name:secret@example.invalid/',
                     'https://example.invalid/\npath', 'https://example.invalid:wrong/path', '//example.invalid/x']
        for index, url in enumerate(bad_links):
            self.job(str(index))
            self.con.execute('UPDATE jobs SET url=?,apply_url=? WHERE id=?', (url, url, str(index)))
        self.job('safe')
        self.con.execute("UPDATE jobs SET apply_url=url WHERE id='safe'")
        self.con.commit()
        rows = self.listed()
        for index in range(len(bad_links)):
            self.assertIsNone(rows[str(index)]['posting_url'])
            self.assertIsNone(rows[str(index)]['apply_url'])
        self.assertEqual(rows['safe']['posting_url'], 'https://example.invalid/safe')
        self.assertIsNone(rows['safe']['apply_url'])

    def test_actual_safe_presubmit_outcomes_do_not_need_employer_confirmation(self):
        for index, reason in enumerate(('dry_run_complete', 'dry_run_complete: reached Submit; not clicked',
                                        'No application opened. Not submitted.', 'rate limited before Submit; not sent')):
            job_id = self.job('safe-' + str(index))
            self.outcome(job_id, reason=reason)
            self.assertEqual(self.listed()[job_id]['sent_risk'], 'not_sent')
            self.assertTrue(self.queue(job_id)['requeued'])

    def test_unknown_history_is_uncertain_even_if_the_latest_skip_was_safe(self):
        for index, (result, reason) in enumerate((('skipped', 'already_applied'),
                                                ('skipped', 'confirmation_page_already_shown'),
                                                ('failed', 'validation_error_after_submit'),
                                                ('manual', 'Worker ended with submission outcome unresolved'),
                                                ('skipped', 'Location is not eligible'), ('skipped', None))):
            job_id = self.job('unknown-' + str(index))
            self.outcome(job_id, result=result, reason=reason, attempted=90)
            self.outcome(job_id, reason='dry_run_complete', attempted=100)
            row = self.listed()[job_id]
            self.assertTrue(row['requeue_allowed'])
            self.assertEqual(row['sent_risk'], 'possibly_sent')
            self.assert_rejected(job_id, 'confirmation_required', answers=[{'question': 'Q', 'answer': 'A'}])

    def test_uncertain_finished_reservations_require_literal_confirmation(self):
        for state in ('manual', 'failed', 'skipped'):
            job_id = self.job('reservation-' + state, reservation=state)
            if state != 'skipped':
                self.outcome(job_id, reason='dry_run_complete')
            self.assertEqual(self.listed()[job_id]['sent_risk'], 'possibly_sent')
            for confirmed in (False, None, 'true', 1):
                self.assert_rejected(job_id, 'confirmation_required', confirmed=confirmed)
            self.assertTrue(self.queue(job_id, confirmed=True)['requeued'])
            self.assertIsNone(self.con.execute('SELECT state FROM campaign_reservations WHERE job_id=?', (job_id,)).fetchone())

    def test_equivalent_finished_history_without_reservations_remains_uncertain(self):
        for index, result in enumerate(('manual', 'failed', 'skipped')):
            job_id = self.job('original-' + str(index))
            other = self.job('equivalent-' + str(index), status='queued', source_id=job_id)
            self.outcome(other, result=result, reason='Submit outcome unknown')
            self.assertEqual(self.listed()[job_id]['sent_risk'], 'possibly_sent')
            self.assert_rejected(job_id, 'confirmation_required')

    def test_equivalent_finished_reservations_require_confirmation_despite_safe_own_history(self):
        for state in ('manual', 'failed'):
            job_id = self.job('own-' + state)
            self.outcome(job_id)
            self.job('other-' + state, status='queued', url='https://example.invalid/' + job_id, reservation=state)
            self.assertEqual(self.listed()[job_id]['sent_risk'], 'possibly_sent')
            self.assert_rejected(job_id, 'confirmation_required')

    def test_applied_or_reserved_equivalents_are_never_requeued_even_with_confirmation(self):
        variants = ('applied_status', 'applied_history', 'applied_reservation', 'active_reservation')
        for identity in ('url', 'source_id'):
            for variant in variants:
                job_id = self.job(identity + '-' + variant)
                kwargs = {'url': 'https://example.invalid/' + job_id} if identity == 'url' else {'source_id': job_id}
                other = self.job('copy-' + job_id, status='applied' if variant == 'applied_status' else 'queued',
                                 reservation={'applied_reservation': 'applied', 'active_reservation': 'reserved'}.get(variant), **kwargs)
                if variant == 'applied_history':
                    self.outcome(other, result='applied', reason='Old receipt')
                row = self.listed()[job_id]
                self.assertFalse(row['requeue_allowed'])
                self.assertTrue(row['blocked_reason'])
                self.assert_rejected(job_id, 'already_sent_or_reserved', confirmed=True,
                                     answers=[{'question': 'Q', 'answer': 'A'}])
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM job_answers').fetchone()[0], 0)

    def test_own_applied_history_and_active_reservation_always_block(self):
        self.job('historical')
        self.outcome('historical', result='applied', reason='Receipt')
        self.job('active', reservation='reserved')
        for job_id in ('historical', 'active'):
            self.assertFalse(self.listed()[job_id]['requeue_allowed'])
            self.assert_rejected(job_id, 'already_sent_or_reserved', confirmed=True)

    def test_review_note_is_required_and_bounded_without_any_writes(self):
        self.job('note')
        for note in (None, '', '  \n ', 1, 'x' * (skipped_jobs.MAX_REVIEW_NOTE + 1)):
            self.assert_rejected('note', 'invalid_review_note', note=note)

    def test_invalid_answers_never_change_state(self):
        self.job('answers')
        for answers in ('text', [{'question': 'Q', 'answer': ''}], [{'question': '', 'answer': 'A'}],
                        [{'question': 'Q', 'answer': 'A', 'unexpected': 'field'}],
                        [{'question': 1, 'answer': 'A'}],
                        [{'question': 'Q', 'answer': 'x' * (manual_jobs.MAX_ANSWER + 1)}],
                        [{'question': 'Q', 'answer': 'A'}] * (manual_jobs.MAX_ANSWERS + 1)):
            self.assert_rejected('answers', 'invalid_answers', answers=answers)

    def test_success_atomically_queues_answers_and_audits_without_erasing_history(self):
        self.job('review')
        self.outcome('review', reason='dry_run_complete')
        original = tuple(self.con.execute('SELECT * FROM applications').fetchone())
        result = self.queue('review', answers=[{'question': '  Have you   worked here? ', 'answer': ' No '},
                                               {'question': '', 'answer': ''}], note='  The restriction changed.  ')
        self.assertEqual(result, {'requeued': True, 'job_id': 'review', 'saved_answers': 1})
        self.assertEqual(self.con.execute("SELECT status FROM jobs WHERE id='review'").fetchone()[0], 'queued')
        self.assertEqual([tuple(row) for row in self.con.execute('SELECT question,answer FROM job_answers')], [('Have you worked here?', 'No')])
        history = self.con.execute('SELECT * FROM applications ORDER BY id').fetchall()
        self.assertEqual(tuple(history[0]), original)
        self.assertEqual(history[1]['result'], 'deferred')
        self.assertEqual(history[1]['run_id'], 'campaign-requeue')
        self.assertIn('The restriction changed.', history[1]['reason'])
        audit = json.loads(history[1]['evidence'])
        self.assertIs(audit['submission_verified_absent'], True)
        self.assertIn('before final submission', audit['evidence'])
        self.assertEqual(campaign.reviewed_answers(self.con), {'review': [{'question': 'Have you worked here?', 'answer': 'No'}]})
        self.events.assert_called_once_with('requeued', job_id='review')

    def test_scorer_only_audit_does_not_claim_a_worker_attempt(self):
        self.job('machine')
        self.queue('machine')
        row = self.con.execute('SELECT result,evidence FROM applications').fetchone()
        self.assertEqual(row['result'], 'deferred')
        evidence = json.loads(row['evidence'])['evidence']
        self.assertIn('Scorer-only', evidence)
        self.assertIn('fresh eligibility review', evidence)

    def test_confirmed_unknown_audit_records_the_employer_attestation(self):
        self.job('uncertain', reservation='failed')
        self.outcome('uncertain', result='failed', reason='submit_clicked_no_explicit_confirmation')
        self.queue('uncertain', confirmed=True)
        row = self.con.execute("SELECT evidence FROM applications WHERE result='deferred'").fetchone()
        self.assertIn('Reviewer confirmed from the employer account', json.loads(row['evidence'])['evidence'])

    def test_duplicate_post_and_non_skipped_jobs_are_rejected_without_answers(self):
        self.job('once')
        self.queue('once')
        self.assert_rejected('once', 'not_skipped', answers=[{'question': 'Q', 'answer': 'A'}])
        for status in ('manual', 'applying', 'applied', 'queued', 'failed'):
            self.job(status, status=status)
            self.assert_rejected(status, 'not_skipped', confirmed=True)
        self.assert_rejected('missing', 'unknown_job')

    def test_stale_status_between_domain_read_and_atomic_requeue_is_rejected(self):
        self.job('stale')
        original_requeue = campaign.requeue

        def change_status_then_requeue(*args, **kwargs):
            with contextlib.closing(sqlite3.connect(self.path)) as writer:
                writer.execute("UPDATE jobs SET status='manual' WHERE id='stale'")
                writer.commit()
            return original_requeue(*args, **kwargs)

        with patch.object(campaign, 'requeue', side_effect=change_status_then_requeue):
            with self.assertRaises(manual_jobs.ManualJobError) as refused:
                self.queue('stale', answers=[{'question': 'Q', 'answer': 'A'}])
        self.assertEqual(refused.exception.code, 'requeue_requires_expected_status')
        self.assertEqual(self.con.execute("SELECT status FROM jobs WHERE id='stale'").fetchone()[0], 'manual')
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM applications').fetchone()[0], 0)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM job_answers').fetchone()[0], 0)

    def test_risk_is_rechecked_under_lock_after_concurrent_history_or_reservation_change(self):
        original_requeue = campaign.requeue
        for change in ('history', 'reservation'):
            job_id = self.job('race-' + change)
            self.outcome(job_id, reason='dry_run_complete')

            def change_risk_then_requeue(*args, **kwargs):
                with contextlib.closing(sqlite3.connect(self.path)) as writer:
                    if change == 'history':
                        writer.execute('''INSERT INTO applications(job_id,attempted,result,reason)
                            VALUES(?,101,'skipped','Submit outcome unknown')''', (job_id,))
                    else:
                        writer.execute("INSERT INTO campaign_reservations(job_id,reserved_at,state,reason) VALUES(?,101,'failed','Unresolved')", (job_id,))
                    writer.commit()
                return original_requeue(*args, **kwargs)

            with patch.object(campaign, 'requeue', side_effect=change_risk_then_requeue):
                with self.assertRaises(manual_jobs.ManualJobError) as refused:
                    self.queue(job_id, answers=[{'question': 'Q', 'answer': 'A'}])
            self.assertEqual(refused.exception.code, 'confirmation_required')
            self.assertEqual(self.con.execute('SELECT status FROM jobs WHERE id=?', (job_id,)).fetchone()[0], 'skipped')
            self.assertEqual(self.con.execute('SELECT COUNT(*) FROM applications WHERE result=\'deferred\'').fetchone()[0], 0)
            self.assertEqual(self.con.execute('SELECT COUNT(*) FROM job_answers').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
