// One serialized pipeline pass. Safe to call repeatedly from a scheduler.
// Run inside omp eval (JS):  const {runOnce} = require('<ROOT>/src/run.js'); await runOnce({browser, env});
//
// Numeric pacing caps come from data/campaign.json; null means unlimited.
// Search all due queries and verify all pending details by default, one gated page at a time.
// Order: verify before apply. A job is applied to only if its apply method + salary were verified from the
// live LinkedIn header. Easy Apply is automatic; external ATS goes through src/ats (if present).
const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");
const ROOT = path.resolve(__dirname, "..");
const rl = require("./ratelimit.js");
const { searchOne, detailOne } = require("./discover_linkedin.js");
const { applyEasy } = require("./apply_easy.js");
const { isLinkedIn, verifyExternal } = require("./verify_external.js");

const run = (script, args = [], input) =>
  execFileSync("python3", [path.join(ROOT, "src", script), ...args], { cwd: ROOT, encoding: "utf8", input });
const pipeline = (...a) => JSON.parse(run("pipeline.py", a));
const dbcli = (cmd, payload) => run("dbcli.py", [cmd], JSON.stringify(payload));

function loadAts() {
  try { return require("./ats/index.js"); } catch { return null; }
}

async function runOnce({ browser, env, dryRun = false, maxDetails = null, doSearch = true, log = console.log } = {}) {
  env("PI_BROWSER_RELAY", "1");
  const profile = JSON.parse(fs.readFileSync(path.join(ROOT, "profile.json"), "utf8"));
  const runId = "run-" + new Date().toISOString().replace(/[-:T]/g, "").slice(0, 12);
  const summary = { runId, searched: null, newJobs: 0, verified: 0, applied: 0, manual: 0, skipped: 0, failed: 0, stop: null };
  const st = rl.status();
  if (st.locked) { summary.stop = `rate-limit lockout until ${st.locked.until}`; return summary; }

  const tab = await browser.open({ name: "pipeline", url: "about:blank", app: "relay", persist: true, timeout: 60 });
  try {
    // 1) discover each due query, serially
    while (doSearch) {
      const { query } = pipeline("next-search");
      if (!query) break;
      const prefs = profile.preferences || {};
      const found = await searchOne(tab, query, prefs.location_mode === "united_states" || !prefs.linkedin_geo_id ? {} : { location: (profile.candidate || {}).location, geoId: prefs.linkedin_geo_id });
      fs.mkdirSync(path.join(ROOT, "data"), { recursive: true });
      fs.writeFileSync(path.join(ROOT, "data", "_incoming.json"), JSON.stringify(found));
      const out = run("ingest.py", ["data/_incoming.json"]);
      const newJobs = Number((out.match(/new=(\d+)/) || [])[1] || 0);
      summary.searched = query;
      summary.newJobs += newJobs;
      pipeline("mark-search", query, String(newJobs));
    }

    // 2) verify pending jobs (apply method, salary, description, freshness): one gated page each
    for (let i = 0; maxDetails === null || i < maxDetails; i++) {
      const j = pipeline("next-detail");
      if (!j) break;
      let row = JSON.parse(dbcli("get-job", { id: j.id }));
      try {
        row = isLinkedIn(row.url) ? await detailOne(tab, row) : await verifyExternal(tab, row);
      } catch (e) {
        if (e && e.name === "RateLimited") throw e;
        if (dryRun) { summary.stop = String(e.message || e); break; }
        dbcli("record", {id: j.id, result: "manual", reason: String(e.message || e), run_id: runId});
        summary.manual++;
        continue;
      }
      const method = row.easy_apply ? "easy" : row.external_apply ? "external" : "unknown";
      dbcli("save-verification", {
        id: j.id, description: row.description || null, salary_min: row.salary_min ?? null,
        salary_max: row.salary_max ?? null, apply_url: row.apply_url || null, apply_method: method, closed: !!row.closed,
      });
      summary.verified++;
    }
    run("ingest.py", ["--rescore"]);   // re-applies salary-midpoint/seniority gates with the verified data

    // 3) apply until the eligible queue is exhausted or an explicit configured cap is reached
    const ats = loadAts();
    let attempts = 0;
    while (rl.CFG.maxAppliesPerRun === null || attempts < rl.CFG.maxAppliesPerRun) {
      let job = pipeline("next-apply", "easy"), how = "easy";
      if (!job && ats) { job = pipeline("next-apply", "external"); how = "ats"; }
      if (!job) break;
      attempts++;
      let r;
      try {
        if (how === "ats" && isLinkedIn(job.apply_url || job.url)) {
          r = {status: "manual", reason: "Employer application URL has not been resolved; not passing a LinkedIn page to an ATS adapter"};
        } else {
          r = how === "easy"
            ? await applyEasy(browser, job, profile, { dryRun, log })
            : await ats.applyOnSite(browser, job, profile, { dryRun, log, app: "relay" });
        }
      } catch (e) {
        if (e && e.name === "RateLimited") { summary.stop = e.message; break; }
        r = { status: "failed", reason: "exception: " + String((e && e.message) || e).slice(0, 200) };
      }
      if (dryRun) { log(`[dry-run] ${job.title}: ${r.status} ${r.reason}`); break; }
      // dbcli cannot record "applied": only the campaign controller can, because only it enforces a reservation and a
      // receipt. A submission this loop made is therefore recorded as manual with its evidence, so it is never lost
      // and a person reconciles it with `campaign.py reconcile` after checking the employer's own record.
      const status = ["applied", "failed", "manual", "skipped"].includes(r.status) ? r.status : "failed";
      const result = status === "applied" ? "manual" : status;
      const reason = status === "applied"
        ? `Submitted by run.js outside the campaign controller; employer receipt needs review. ${r.reason || ""}`.trim()
        : r.reason || "";
      dbcli("record", { id: job.id, result, reason, evidence: r.evidence || "", run_id: runId });
      summary[result]++;
      log(`${result}: ${job.title} @ ${job.company} - ${r.reason}`);
    }
    if (attempts === 0 && !summary.stop) summary.stop = "no verified, eligible job to apply to";
  } finally {
    try { await browser.close({ name: "pipeline" }); } catch {}
  }
  fs.mkdirSync(path.join(ROOT, "logs"), { recursive: true });
  fs.appendFileSync(path.join(ROOT, "logs", "runs.jsonl"), JSON.stringify({ t: new Date().toISOString(), ...summary }) + "\n");
  return summary;
}

module.exports = { runOnce };
