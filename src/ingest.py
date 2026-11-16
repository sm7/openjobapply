"""Ingest discovered jobs (JSON on stdin or file), dedupe into the index, score them.

  python3 src/ingest.py jobs.json     # insert new + score
  python3 src/ingest.py --rescore     # re-score open/unattempted and auto-rejected jobs
  python3 src/ingest.py --report      # print index summary, including why jobs were rejected
  python3 src/ingest.py --why JOB_ID  # show the text that caused one job's rejection
"""
import json
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import db
import score as sc

# Revisit machine rejections only, never a manual skip or any application history.
RESCORABLE = """(
    status IN ('discovered','scored','queued')
    OR (status='skipped' AND substr(COALESCE(score_notes,''),1,9)='rejected:')
) AND NOT EXISTS (SELECT 1 FROM applications WHERE applications.job_id=jobs.id)"""


def score_row(con, profile, row):
    job = dict(row)
    s, notes, reject = sc.evaluate(job, profile)
    eligible = RESCORABLE
    if reject:
        con.execute(f"UPDATE jobs SET score=0, score_notes=?, status='skipped' WHERE id=? AND {eligible}",
                    (f"rejected:{reject}", job["id"]))
        return s, reject
    if not (job.get("description") or "").strip():
        # Cannot verify years/role fit without a description: hold, do not apply.
        con.execute(f"UPDATE jobs SET score=?, score_notes=?, status='discovered' WHERE id=? AND {eligible}",
                    (s, "needs_description", job["id"]))
        return s, "needs_description"
    status = "queued" if s >= sc.MIN_APPLY_SCORE else "scored"
    con.execute(f"UPDATE jobs SET score=?, score_notes=?, status=? WHERE id=? AND {eligible}",
                (s, notes, status, job["id"]))
    return s, None


def score_safely(con, profile, row):
    """One unscorable posting must not stop the others. Hold it with a visible note, never queue it."""
    try:
        return score_row(con, profile, row)
    except Exception as error:  # noqa: BLE001 - any scorer failure is held for review
        con.execute(f"UPDATE jobs SET score=0, score_notes=?, status='discovered' WHERE id=? AND {RESCORABLE}",
                    (f"score_error:{type(error).__name__}", row["id"]))
        return 0, "score_error"


def ingest(path):
    profile = db.load_profile()
    con = db.connect()
    jobs = json.load(open(path))
    new = 0
    for j in jobs:
        jid, is_new = db.upsert_job(con, j)
        new += is_new
    con.commit()
    for r in con.execute("SELECT * FROM jobs WHERE status='discovered'").fetchall():
        score_safely(con, profile, r)
    con.commit()
    print(f"ingested={len(jobs)} new={new}")


def rescore():
    profile = db.load_profile()
    con = db.connect()
    for r in con.execute(f"SELECT * FROM jobs WHERE {RESCORABLE}").fetchall():
        score_safely(con, profile, r)
    con.commit()


def report():
    con = db.connect()
    print("== status counts ==")
    for r in con.execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status ORDER BY n DESC"):
        print(f"  {r['status']:<11}{r['n']}")
    print("== top queued ==")
    for r in con.execute("SELECT score,title,company,location,salary_min,salary_max,easy_apply FROM jobs WHERE status='queued' ORDER BY score DESC LIMIT 15"):
        sal = f"${r['salary_min']}-{r['salary_max']}" if r["salary_min"] else "n/a"
        print(f"  {r['score']:>5} {r['title'][:48]:<48} {r['company'][:24]:<24} {r['location'][:20]:<20} {sal} {'EA' if r['easy_apply'] else ''}")
    print("== why jobs were rejected (profile rules, no worker review) ==")
    for r in con.execute("""SELECT CASE WHEN instr(substr(score_notes,10),':')>0 THEN substr(score_notes,10,instr(substr(score_notes,10),':')-1)
                                        ELSE substr(score_notes,10) END AS why, COUNT(*) n
                            FROM jobs WHERE status='skipped' AND substr(COALESCE(score_notes,''),1,9)='rejected:'
                            GROUP BY why ORDER BY n DESC"""):
        print(f"  {r['why']:<24}{r['n']}")
    held = con.execute("SELECT COUNT(*) FROM jobs WHERE score_notes LIKE 'score_error:%'").fetchone()[0]
    if held:
        print(f"== held after a scoring error: {held} (run --why JOB_ID) ==")
    print("== applications ==")
    for r in con.execute("SELECT result, COUNT(*) n FROM applications GROUP BY result"):
        print(f"  {r['result']:<9}{r['n']}")


def why(job_id):
    """Print the stored decision for one job and the text that caused a rejection."""
    con = db.connect()
    row = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        sys.exit(f"unknown job {job_id}")
    print(f"{row['title']} | {row['company']} | {row['location']} | status={row['status']} | notes={row['score_notes']}")
    reason, detail = sc.explain(dict(row), db.load_profile())
    print(f"rule now: {reason or 'not rejected'}\n{detail}")


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "--report":
        report()
    elif a and a[0] == "--rescore":
        rescore(); report()
    elif len(a) == 2 and a[0] == "--why":
        why(a[1])
    elif a:
        ingest(a[0]); report()
    else:
        sys.exit(__doc__)
