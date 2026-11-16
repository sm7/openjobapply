"""Pipeline helper: picks the NEXT single unit of work from the DB so the loop never bulk-fetches.

  python3 src/pipeline.py next-search          # next query due for a LinkedIn search (>=24h apart)
  python3 src/pipeline.py next-detail          # next job needing a detail page
  python3 src/pipeline.py next-apply [easy|external]   # next VERIFIED job ready to apply
  python3 src/pipeline.py mark-search "<query>" [new_jobs]
  python3 src/pipeline.py status
"""
import json
import sys
import time

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import db

SEARCH_COOLDOWN_S = 24 * 3600


def _ensure(con):
    con.execute("CREATE TABLE IF NOT EXISTS search_log (query TEXT PRIMARY KEY, last_run REAL NOT NULL, new_jobs INTEGER DEFAULT 0)")
    cols = {r[1] for r in con.execute("PRAGMA table_info(jobs)")}
    if "apply_method" not in cols:
        con.execute("ALTER TABLE jobs ADD COLUMN apply_method TEXT")
    if "verified_at" not in cols:
        con.execute("ALTER TABLE jobs ADD COLUMN verified_at REAL")


def next_search(con, profile):
    _ensure(con)
    now = time.time()
    done = {r["query"]: r["last_run"] for r in con.execute("SELECT query,last_run FROM search_log")}
    due = [q for q in profile["search"]["queries"] if now - done.get(q, 0) >= SEARCH_COOLDOWN_S]
    due.sort(key=lambda q: done.get(q, 0))  # never-run first, then oldest
    return due[0] if due else None


def mark_search(con, query, new_jobs=0):
    _ensure(con)
    con.execute("INSERT INTO search_log(query,last_run,new_jobs) VALUES(?,?,?) "
                "ON CONFLICT(query) DO UPDATE SET last_run=excluded.last_run,new_jobs=excluded.new_jobs",
                (query, time.time(), new_jobs))
    con.commit()


def next_detail(con):
    """Jobs that passed title/location filters but have no description or apply method yet."""
    _ensure(con)
    r = con.execute("""SELECT id,title,company,url FROM jobs
        WHERE status IN ('discovered','scored','queued') AND verified_at IS NULL
          AND COALESCE(score_notes,'') NOT LIKE 'rejected:%'
        ORDER BY (status='queued') DESC, score DESC, first_seen DESC LIMIT 1""").fetchone()
    return dict(r) if r else None


def next_apply(con, min_score=50, method=None):
    """Verified jobs not yet attempted. Unverified (unknown method) are NEVER returned."""
    _ensure(con)
    where = ("status='queued' AND score>=? AND apply_method IS NOT NULL AND verified_at IS NOT NULL "
             "AND id NOT IN (SELECT job_id FROM applications)")
    args = [min_score]
    if method:
        where += " AND apply_method=?"
        args.append(method)
    r = con.execute(f"SELECT id,title,company,url,apply_url,source,score,apply_method,salary_min,salary_max "
                    f"FROM jobs WHERE {where} ORDER BY score DESC LIMIT 1", args).fetchone()
    return dict(r) if r else None


def status(con):
    _ensure(con)
    return {
        "jobs": {r["status"]: r["n"] for r in con.execute("SELECT status,COUNT(*) n FROM jobs GROUP BY status")},
        "apply_method": {r["m"]: r["n"] for r in con.execute("SELECT COALESCE(apply_method,'unknown') m,COUNT(*) n FROM jobs GROUP BY 1")},
        "applications": {r["result"]: r["n"] for r in con.execute("SELECT result,COUNT(*) n FROM applications GROUP BY result")},
        "searches": [dict(r) for r in con.execute(
            "SELECT query,datetime(last_run,'unixepoch','localtime') last_run,new_jobs FROM search_log ORDER BY last_run DESC")],
    }


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    con, profile = db.connect(), db.load_profile()
    if cmd == "next-search":
        print(json.dumps({"query": next_search(con, profile)}))
    elif cmd == "next-detail":
        print(json.dumps(next_detail(con)))
    elif cmd == "next-apply":
        print(json.dumps(next_apply(con, method=sys.argv[2] if len(sys.argv) > 2 else None)))
    elif cmd == "mark-search":
        mark_search(con, sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 0)
        print("ok")
    elif cmd == "status":
        print(json.dumps(status(con), indent=1))
    else:
        sys.exit(__doc__)
