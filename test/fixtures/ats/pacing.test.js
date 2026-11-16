// Deterministic test for src/ats/pacing.js — no browser, no network, injected clock.
//   bun test/fixtures/ats/pacing.test.js     (or: node test/fixtures/ats/pacing.test.js)
// State goes to a temp file; the real data/ats-pacing.json is never touched.
"use strict";
const fs = require("fs");
const os = require("os");
const path = require("path");

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "ats-pacing-"));
const FILE = path.join(tmp, "ats-pacing.json");
process.env.ATS_PACING_FILE = FILE;
process.env.CAMPAIGN_CONFIG = path.join(tmp, "campaign.json"); // this test needs no real data/campaign.json
fs.writeFileSync(process.env.CAMPAIGN_CONFIG, JSON.stringify({ max_submissions_per_host_per_day: 6, max_submissions_per_day: 25 }));
const pacing = require("../../../src/ats/pacing.js");
const { createPacer, RateLimited } = pacing;

const MIN = 60e3, HOUR = 3600e3, DAY = 24 * HOUR;
let pass = 0, fail = 0;
const check = (name, cond, extra) => { if (cond) { pass++; console.log("  ok  ", name); } else { fail++; console.log("  FAIL", name, extra === undefined ? "" : JSON.stringify(extra)); } };
const rejects = async (p) => { try { await p; return null; } catch (e) { return e; } };

// fake clock: sleep() advances time and records the requested waits
function clock(t0 = 1_800_000_000_000) {
  const c = { t: t0, sleeps: [], now: () => c.t, sleep: async (ms) => { c.sleeps.push(ms); c.t += ms; }, random: () => 0.5 };
  return c;
}
const fresh = (name) => { const f = path.join(tmp, name + ".json"); return f; };
const mk = (c, file, cfg) => createPacer({ file, now: c.now, sleep: c.sleep, random: c.random, cfg });

(async () => {
  console.log("# spacing: >= 90s + jitter between loads on the same host");
  {
    const c = clock(), p = mk(c, fresh("spacing"));
    await p.gate("jobs.example-hospital.org");
    check("first load does not wait", c.sleeps.length === 0, c.sleeps);
    const t1 = c.t;
    await p.gate("jobs.example-hospital.org");
    check("second load waits exactly gap (90s + 0.5*30s = 105s)", c.t - t1 === 105e3 && c.sleeps.join() === "105000", { dt: c.t - t1, s: c.sleeps });
    c.sleeps.length = 0;
    await p.gate("careers.other-hospital.com");
    check("a different host is not delayed", c.sleeps.length === 0);
    c.t += 40e3;
    await p.gate("careers.other-hospital.com");
    check("partial elapsed time is credited (waits 65s after 40s)", c.sleeps.join() === "65000", c.sleeps);
    for (const [rv, want] of [[0, 90e3], [1, 120e3]]) {
      const cc = clock();
      const x = createPacer({ file: fresh(`sp-jit${rv}`), now: cc.now, sleep: cc.sleep, random: () => rv });
      await x.gate("h.org"); const a = cc.t; await x.gate("h.org");
      check(`jitter bounds: random=${rv} -> gap ${want / 1000}s`, cc.t - a === want, cc.t - a);
    }
    check("host given as URL is normalised", pacing.normalizeHost("https://Jobs.Example.org:8443/a?b#c") === "jobs.example.org" && pacing.normalizeHost("JOBS.example.org") === "jobs.example.org");
  }

  console.log("# serialization: concurrent gate() calls on one host queue, never overlap");
  {
    const c = clock(), p = mk(c, fresh("serial"));
    const stamps = [];
    await Promise.all([1, 2, 3].map(() => p.gate("h.org").then(() => stamps.push(c.t))));
    check("three concurrent gates are >=105s apart", stamps.length === 3 && stamps[1] - stamps[0] === 105e3 && stamps[2] - stamps[1] === 105e3, stamps);
  }

  console.log("# daily host cap: 6 submissions / host");
  {
    const c = clock(), p = mk(c, fresh("hostcap"), { maxSubmissionsPerHostPerDay: 6, maxSubmissionsPerDay: null });
    for (let i = 0; i < 5; i++) { p.recordSubmission("h.org"); c.t += HOUR; }
    check("5 submissions: gate still open", (await rejects(p.gate("h.org"))) === null);
    p.recordSubmission("h.org");
    const first = c.t - 5 * HOUR; // time of the first submission
    const e = await rejects(p.gate("h.org"));
    check("6th submission closes the host (RateLimited, retryAt = first+24h)", e instanceof RateLimited && /daily submission cap/.test(e.message) && e.retryAt === first + DAY, e && e.message);
    check("other host unaffected", (await rejects(p.gate("other.org"))) === null);
    c.t = first + DAY + 1;
    check("cap rolls off after 24h", (await rejects(p.gate("h.org"))) === null);
    check("status shows 5 in 24h after roll-off", p.status().hosts["h.org"].submissions_24h === 5, p.status().hosts["h.org"]);
  }

  console.log("# daily global cap: 25 employer submissions");
  {
    const c = clock(), p = mk(c, fresh("globalcap"), { maxSubmissionsPerHostPerDay: null, maxSubmissionsPerDay: 25 });
    for (let i = 0; i < 24; i++) p.recordSubmission(`h${i % 5}.org`);
    check("24 submissions: new host still open", (await rejects(p.gate("fresh-host.org"))) === null);
    p.recordSubmission("h0.org");
    const e = await rejects(p.gate("another-new-host.org"));
    check("25th submission closes every host", e instanceof RateLimited && /global daily/.test(e.message), e && e.message);
    check("status total 25", p.status().submissions_24h === 25, p.status());
    c.t += DAY + 1;
    check("global cap rolls off after 24h", (await rejects(p.gate("another-new-host.org"))) === null);
  }

  console.log("# null caps allow unlimited counts without bypassing spacing or locks");
  {
    const c = clock(), file = fresh("unlimited");
    const cfg = { maxSubmissionsPerHostPerDay: null, maxSubmissionsPerDay: null };
    const p = mk(c, file, cfg);
    for (let i = 0; i < 30; i++) p.recordSubmission("h.org");
    check("null caps allow counts beyond former host/global defaults", (await rejects(p.gate("h.org"))) === null);
    const restarted = mk(c, file, cfg), before = c.t;
    check("unlimited caps survive restart", (await rejects(restarted.gate("h.org"))) === null);
    check("unlimited counts still enforce the temporal gap", c.t - before === 105e3, c.t - before);
    check("unlimited counts retain submission history", restarted.status().submissions_24h === 30);
    const lock = restarted.tripHost("h.org", "captcha_required");
    const e = await rejects(restarted.gate("h.org"));
    check("unlimited counts do not bypass a challenge lockout", e instanceof RateLimited && e.retryAt === lock.lockedUntil);
    check("a locked host does not block another host under null caps", (await rejects(restarted.gate("other.org"))) === null);
  }

  console.log("# backoff escalation + restart persistence of a lock");
  {
    const c = clock(), file = fresh("lock");
    let p = mk(c, file);
    const seq = [];
    const expect = [30 * MIN, 2 * HOUR, 12 * HOUR, 24 * HOUR, 24 * HOUR];
    for (let i = 0; i < expect.length; i++) {
      const lock = p.tripHost("h.org", "captcha_required");
      seq.push(lock.durationMs);
      check(`trip #${i + 1} locks ${expect[i] / MIN} min`, lock.durationMs === expect[i] && lock.lockedUntil === c.t + expect[i] && lock.strikes === i + 1, lock);
      // restart: brand-new pacer on the same file must still refuse
      p = mk(c, file);
      const e = await rejects(p.gate("h.org"));
      check(`  lock #${i + 1} survives restart`, e instanceof RateLimited && e.retryAt === lock.lockedUntil && /captcha_required/.test(e.message), e && e.message);
      c.t = lock.lockedUntil - 1;
      check("  still locked 1ms before expiry", (await rejects(p.gate("h.org"))) instanceof RateLimited);
      c.t = lock.lockedUntil;
      check("  open at expiry", (await rejects(p.gate("h.org"))) === null);
    }
    check("escalation sequence 30m,2h,12h,24h,24h", seq.join() === expect.join(), seq);

    // a lock is never shortened by a later, lower-strike trip and never lost on an untouched host
    const p2 = mk(c, fresh("noshorten")), l1 = p2.tripHost("h.org", "blocked");
    p2.unlock("h.org");
    const l2 = p2.tripHost("h.org", "blocked");
    check("unlock keeps strikes -> next trip escalates to 2h", l1.durationMs === 30 * MIN && l2.durationMs === 2 * HOUR, [l1, l2]);
    p2.unlock("h.org", { resetStrikes: true });
    check("unlock clears the lock", (await rejects(p2.gate("h.org"))) === null);
    check("unlock {resetStrikes} restarts the backoff at 30m", p2.tripHost("h.org", "blocked").durationMs === 30 * MIN);
    const long = p2.tripHost("h.org", "x"); // strikes=2 -> 2h
    const kept = JSON.parse(fs.readFileSync(fresh("noshorten"), "utf8")).hosts["h.org"].lockedUntil;
    check("lockedUntil persisted on disk", kept === long.lockedUntil, [kept, long]);
    check("trip on host A does not lock host B", (await rejects(mk(c, fresh("noshorten")).gate("b.org"))) === null);

    // module-level API honours ATS_PACING_FILE
    pacing.tripHost("env.example.org", "account_lockout");
    check("default instance writes to ATS_PACING_FILE and status() reads it", fs.existsSync(FILE) && pacing.status().hosts["env.example.org"].locked.reason === "account_lockout", pacing.status());
    check("default gate() refuses locked host", (await rejects(pacing.gate("env.example.org"))) instanceof RateLimited);
    check("default unlock()", pacing.unlock("env.example.org") === true && (await rejects(pacing.gate("env.example.org"))) === null);
  }

  console.log("# fail closed on corrupt state");
  {
    const file = fresh("corrupt"); fs.writeFileSync(file, "{not json");
    const p = mk(clock(), file);
    const e = await rejects(p.gate("h.org"));
    check("corrupt file -> error, not an empty (lock-forgetting) state", e instanceof Error && !(e instanceof RateLimited) && /corrupt/.test(e.message), e && e.message);
    check("corrupt file left untouched", fs.readFileSync(file, "utf8") === "{not json");
  }

  console.log("# pacedApply");
  {
    const c = clock(), p = mk(c, fresh("paced"));
    const job = (u) => ({ id: "j1", apply_url: u });
    const calls = [];
    const mkApply = (r, delay = 0) => async (_b, j, _p, o) => { calls.push(j.apply_url); await new Promise((res) => setTimeout(res, delay)); return typeof r === "function" ? r(o) : r; };

    let r = await p.pacedApply(mkApply({ status: "skipped", reason: "dry_run_complete", wouldSubmit: {} }), {}, job("https://jobs.hospital.org/a"), {}, {});
    check("normal result passes through untouched", r.status === "skipped" && r.reason === "dry_run_complete" && !r.pacing && calls.length === 1, r);

    r = await p.pacedApply(mkApply({ status: "manual", reason: "captcha_required", evidence: "recaptcha" }), {}, job("https://jobs.hospital.org/b"), {}, {});
    check("captcha_required result is returned with pacing.tripped", r.status === "manual" && r.reason === "captcha_required" && r.pacing && r.pacing.tripped && r.pacing.strikes === 1, r);
    check("host is now locked for 30m", p.status().hosts["jobs.hospital.org"].locked.reason === "captcha_required" && p.status().hosts["jobs.hospital.org"].locked.until === new Date(c.t + 30 * MIN).toISOString(), p.status());

    const n = calls.length;
    r = await p.pacedApply(mkApply({ status: "applied", reason: "confirmation_page" }), {}, job("https://jobs.hospital.org/c"), {}, { dryRun: false });
    check("locked host: applyOnSite NOT called, manual/rate_limited with retryAt", calls.length === n && r.status === "manual" && r.reason === "rate_limited" && r.retryAt === c.t + 30 * MIN, r);
    r = await p.pacedApply(mkApply({ status: "skipped", reason: "dry_run_complete" }), {}, job("https://other.hospital.org/x"), {}, {});
    check("other host still runs", r.reason === "dry_run_complete" && calls.length === n + 1, r);

    for (const reason of ["account_lockout", "ip_blocked", "access_blocked"]) {
      const h = `${reason}.org`;
      r = await p.pacedApply(mkApply({ status: "manual", reason }), {}, job(`https://${h}/a`), {}, {});
      check(`manual/${reason} trips the host`, r.pacing && r.pacing.tripped && (await rejects(p.gate(h))) instanceof RateLimited, r);
    }
    for (const [status, reason] of [["manual", "otp_required"], ["manual", "unanswered_questions"], ["failed", "adapter_error"], ["skipped", "posting_closed"]]) {
      const h = `${reason}.org`;
      r = await p.pacedApply(mkApply({ status, reason }), {}, job(`https://${h}/a`), {}, {});
      check(`${status}/${reason} does NOT trip`, !r.pacing && (await rejects(p.gate(h))) === null, r);
    }
    r = await p.pacedApply(mkApply({ status: "failed", reason: "captcha_required" }), {}, job("https://failed-captcha.org/a"), {}, {});
    check("only manual results trip (failed/captcha_required does not)", !r.pacing);

    // submissions counted only for real applied
    const h = "counts.org";
    await p.pacedApply(mkApply({ status: "applied", reason: "confirmation_page" }), {}, job(`https://${h}/a`), {}, {}); // dry-run default
    check("dry run (default) is never counted", !p.status().hosts[h] || p.status().hosts[h].submissions_24h === 0);
    await p.pacedApply(mkApply({ status: "applied", reason: "confirmation_page" }), {}, job(`https://${h}/a`), {}, { dryRun: false });
    check("real applied is counted", p.status().hosts[h].submissions_24h === 1, p.status().hosts[h]);
    await p.pacedApply(mkApply({ status: "failed", reason: "adapter_error" }), {}, job(`https://${h}/a`), {}, { dryRun: false });
    check("failed is not counted", p.status().hosts[h].submissions_24h === 1);
    const sameHost = clock(), q = mk(sameHost, fresh("paced-spacing")), okApply = async () => ({ status: "skipped", reason: "dry_run_complete" });
    await q.pacedApply(okApply, {}, job("https://spaced.org/1"), {}, {});
    const t0 = sameHost.t;
    await q.pacedApply(okApply, {}, job("https://spaced.org/2"), {}, {});
    check("pacedApply spaces same-host runs by the gap (105s)", sameHost.t - t0 === 105e3, sameHost.t - t0);

    // one in flight at a time, globally, across hosts; errors don't poison the queue
    let live = 0, peak = 0;
    const slow = async () => { live++; peak = Math.max(peak, live); await new Promise((res) => setTimeout(res, 15)); live--; return { status: "skipped", reason: "dry_run_complete" }; };
    await Promise.all(["a", "b", "c", "d"].map((x) => p.pacedApply(slow, {}, job(`https://conc-${x}.org/a`), {}, {})));
    check("max one application in flight globally", peak === 1, peak);
    const boom = await rejects(p.pacedApply(async () => { throw new Error("boom"); }, {}, job("https://boom.org/a"), {}, {}));
    r = await p.pacedApply(mkApply({ status: "skipped", reason: "dry_run_complete" }), {}, job("https://after-boom.org/a"), {}, {});
    check("a throwing applyOnSite propagates but does not wedge the queue", boom && boom.message === "boom" && r.reason === "dry_run_complete");

    // delegates when there is no usable URL (applyOnSite owns the error)
    r = await p.pacedApply(async () => ({ status: "failed", reason: "job_without_url" }), {}, {}, {}, {});
    check("no url -> delegated unchanged", r.reason === "job_without_url");
    // cap reached -> rate_limited, not applied
    const capP = mk(clock(), fresh("papcap"), { maxSubmissionsPerHostPerDay: 6, maxSubmissionsPerDay: null });
    for (let i = 0; i < 6; i++) capP.recordSubmission("cap.org");
    r = await capP.pacedApply(mkApply({ status: "applied" }), {}, job("https://cap.org/a"), {}, { dryRun: false });
    check("host cap reached -> manual/rate_limited", r.status === "manual" && r.reason === "rate_limited" && /daily submission cap/.test(r.evidence), r);
  }

  console.log(`\n${pass} passed, ${fail} failed`);
  fs.rmSync(tmp, { recursive: true, force: true });
  process.exit(fail ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(2); });
