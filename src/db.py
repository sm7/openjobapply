"""SQLite index of discovered jobs and application outcomes.

jobs.status lifecycle:
  discovered -> scored -> queued -> applying -> applied | failed | skipped | manual
"""
import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "jobs.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id            TEXT PRIMARY KEY,          -- sha1(norm company|title|location)
  source        TEXT NOT NULL,             -- indeed | linkedin | company
  source_id     TEXT,
  url           TEXT NOT NULL,
  apply_url     TEXT,
  title         TEXT NOT NULL,
  company       TEXT,
  location      TEXT,
  remote        INTEGER DEFAULT 0,
  salary_min    INTEGER,
  salary_max    INTEGER,
  description   TEXT,
  employment_type TEXT,
  distance_miles REAL,
  posted        TEXT,
  easy_apply    INTEGER DEFAULT 0,
  score         REAL,
  score_notes   TEXT,
  status        TEXT NOT NULL DEFAULT 'discovered',
  first_seen    REAL NOT NULL,
  last_seen     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, score DESC);

CREATE TABLE IF NOT EXISTS applications (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id      TEXT NOT NULL REFERENCES jobs(id),
  attempted   REAL NOT NULL,
  result      TEXT NOT NULL,               -- applied | failed | manual | skipped
  reason      TEXT,                        -- failure/skip detail
  evidence    TEXT,                        -- confirmation text or screenshot path
  run_id      TEXT
);
CREATE INDEX IF NOT EXISTS idx_app_job ON applications(job_id);
"""


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    columns = {row[1] for row in con.execute("PRAGMA table_info(jobs)")}
    for name, kind in (("employment_type", "TEXT"), ("distance_miles", "REAL")):
        if name not in columns:
            con.execute(f"ALTER TABLE jobs ADD COLUMN {name} {kind}")
    return con


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def job_key(company, title, location):
    raw = f"{_norm(company)}|{_norm(title)}|{_norm(location)}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def upsert_job(con, j):
    """Insert a discovered job; dedupe on company+title+location. Returns (id, is_new)."""
    now = time.time()
    jid = job_key(j.get("company"), j["title"], j.get("location"))
    row = con.execute("SELECT id FROM jobs WHERE id=?", (jid,)).fetchone()
    if row:
        con.execute("UPDATE jobs SET last_seen=? WHERE id=?", (now, jid))
        return jid, False
    con.execute(
        """INSERT INTO jobs(id,source,source_id,url,apply_url,title,company,location,remote,
           salary_min,salary_max,description,posted,easy_apply,first_seen,last_seen,employment_type,distance_miles)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (jid, j["source"], j.get("source_id"), j["url"], j.get("apply_url"), j["title"],
         j.get("company"), j.get("location"), int(bool(j.get("remote"))),
         j.get("salary_min"), j.get("salary_max"), j.get("description"), j.get("posted"),
         int(bool(j.get("easy_apply"))), now, now, j.get("employment_type"), j.get("distance_miles")),
    )
    return jid, True


def record_application(con, job_id, result, reason=None, evidence=None, run_id=None):
    con.execute(
        "INSERT INTO applications(job_id,attempted,result,reason,evidence,run_id) VALUES(?,?,?,?,?,?)",
        (job_id, time.time(), result, reason, evidence, run_id),
    )
    status = {"applied": "applied", "failed": "failed", "manual": "manual", "skipped": "skipped"}[result]
    con.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id))
    con.commit()


def load_profile():
    return json.loads((ROOT / "profile.json").read_text())
