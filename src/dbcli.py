"""DB CLI used by run.js. All input is JSON on stdin (no string interpolation into code or SQL).

  get-job            {"id":...}                                   -> job row JSON
  save-verification  {"id","description","salary_min","salary_max","apply_method","closed"}
  record             {"id","result","reason","evidence","run_id"}
"""
import json
import sys
import time

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import db

# `applied` is deliberately absent. Only `campaign.py result` may record it, because only that path requires an
# active reservation, employer receipt text, the duplicate guard and the target check. This CLI has none of those.
RESULTS = {"failed", "manual", "skipped"}


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    p = json.load(sys.stdin)
    con = db.connect()
    if cmd == "get-job":
        r = con.execute("SELECT * FROM jobs WHERE id=?", (p["id"],)).fetchone()
        print(json.dumps(dict(r) if r else None))
    elif cmd == "save-verification":
        con.execute(
            "UPDATE jobs SET description=COALESCE(?,description), salary_min=COALESCE(?,salary_min), "
            "salary_max=COALESCE(?,salary_max), apply_url=COALESCE(?,apply_url), apply_method=?, verified_at=? WHERE id=?",
            (p.get("description"), p.get("salary_min"), p.get("salary_max"), p.get("apply_url"), p["apply_method"], time.time(), p["id"]))
        if p.get("closed"):
            con.execute("UPDATE jobs SET status='skipped', score_notes='rejected:closed' WHERE id=?", (p["id"],))
        con.commit()
        print("ok")
    elif cmd == "record":
        if p["result"] == "applied":
            sys.exit("dbcli.py cannot record 'applied'. Use: python3 src/campaign.py result (needs a reservation and a receipt).")
        if p["result"] not in RESULTS:
            sys.exit(f"bad result {p['result']!r}")
        db.record_application(con, p["id"], p["result"], reason=p.get("reason"), evidence=p.get("evidence"), run_id=p.get("run_id"))
        print("ok")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
