"""dbcli.py is the old standalone recorder. It must never be a way around the campaign controller.

Only `campaign.py result` may record `applied`, because only it needs an active reservation, receipt text, the
duplicate guard and the target check. Isolated temporary database; nothing here touches the real ledger.
Run: python3 -m unittest discover -s test -p 'test_dbcli.py'
"""
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import db


class DbcliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'data').mkdir()
        (self.root / 'src').mkdir()
        (self.root / 'src/dbcli.py').write_text((ROOT / 'src/dbcli.py').read_text())
        (self.root / 'src/db.py').write_text((ROOT / 'src/db.py').read_text())
        (self.root / 'profile.json').write_text('{}')
        self.database = self.root / 'data/jobs.db'
        with sqlite3.connect(self.database) as connection:
            connection.executescript(db.SCHEMA)
            connection.execute(
                "INSERT INTO jobs(id,source,url,title,company,status,first_seen,last_seen) "
                "VALUES('job','fixture','https://example.invalid/job','Analyst','Employer','manual',1,1)")

    def record(self, result, **extra):
        payload = {'id': 'job', 'result': result, 'reason': 'r', 'evidence': 'e', 'run_id': 'test', **extra}
        return subprocess.run([sys.executable, str(self.root / 'src/dbcli.py'), 'record'], cwd=self.root,
                              input=json.dumps(payload), capture_output=True, text=True)

    def ledger(self):
        with sqlite3.connect(self.database) as connection:
            return (connection.execute('SELECT COUNT(*) FROM applications').fetchone()[0],
                    connection.execute("SELECT status FROM jobs WHERE id='job'").fetchone()[0])

    def test_an_applied_outcome_is_refused_and_leaves_the_ledger_unchanged(self):
        before = self.ledger()
        refused = self.record('applied', evidence='Your application was sent to Employer!')
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn('campaign.py result', refused.stderr)
        self.assertEqual(self.ledger(), before)

    def test_a_forged_receipt_does_not_make_an_applied_outcome_acceptable(self):
        for evidence in ('', 'Application Submitted', 'Your application was sent to Employer!'):
            with self.subTest(evidence=evidence):
                self.assertNotEqual(self.record('applied', evidence=evidence).returncode, 0)
        self.assertEqual(self.ledger()[1], 'manual')

    def test_outcomes_that_never_count_toward_the_target_still_record(self):
        for result in ('manual', 'failed', 'skipped'):
            with self.subTest(result=result):
                before = self.ledger()[0]
                self.assertEqual(self.record(result).returncode, 0)
                self.assertEqual(self.ledger()[0], before + 1)

    def test_an_unknown_outcome_is_refused(self):
        before = self.ledger()
        self.assertNotEqual(self.record('success').returncode, 0)
        self.assertEqual(self.ledger(), before)


if __name__ == '__main__':
    unittest.main()
