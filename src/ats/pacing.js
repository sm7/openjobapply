// Persistent, serialized, per-host pacing gate for employer-ATS applications (non-LinkedIn).
// LinkedIn has its own limiter (src/ratelimit.js); this one covers third-party hosts.
// Not wired into index.js: wrap applyOnSite with `pacedApply` (below) to use it.
//
// Policy (deliberately conservative):
//   - one employer application in flight at a time, globally (pacedApply queues behind a chain)
//   - >= 90s (+ 0-30s jitter) between page loads on the same host
//   - numeric submission caps come from data/campaign.json; null means unlimited per-host/global counts
//   - captcha/lockout/blocked signals lock the host with exponential backoff: 30m, 2h, 12h, 24h (then 24h)
//   - state lives in data/ats-pacing.json (env ATS_PACING_FILE overrides) and is written atomically under a
//     lock file, so restarts and concurrent processes can neither reset a budget nor lose a lock.
//     A locked host's lockedUntil only ever moves forward, except through an explicit unlock().
//   - unreadable/corrupt state FAILS CLOSED (throws) instead of starting from an empty state.
"use strict";
const fs = require("fs");
const path = require("path");

const MIN = 60e3, HOUR = 3600e3, DAY = 24 * HOUR;
const campaign = JSON.parse(fs.readFileSync(process.env.CAMPAIGN_CONFIG || path.resolve(__dirname, "..", "..", "data", "campaign.json"), "utf8"));
const CFG = Object.freeze({
  minGapMs: 90e3,
  jitterMs: 30e3,
  maxSubmissionsPerHostPerDay: campaign.max_submissions_per_host_per_day,
  maxSubmissionsPerDay: campaign.max_submissions_per_day,
  backoffMs: Object.freeze([30 * MIN, 2 * HOUR, 12 * HOUR, 24 * HOUR]),
});
const LOCK_STALE_MS = 15e3;
const LOCK_WAIT_MS = 5e3;

class RateLimited extends Error {
  constructor(msg, retryAt, host) { super(msg); this.name = "RateLimited"; this.retryAt = retryAt; this.host = host; }
}

const realSleep = (ms) => new Promise((r) => setTimeout(r, ms));
const isoOrNull = (t) => (t ? new Date(t).toISOString() : null);

/** Lower-cased hostname from a hostname or URL. */
function normalizeHost(h) {
  let s = String(h || "").trim().toLowerCase();
  if (!s) throw new TypeError("pacing: host required");
  if (/^[a-z][a-z0-9+.-]*:\/\//.test(s)) { try { s = new URL(s).hostname; } catch { throw new TypeError(`pacing: bad url ${h}`); } }
  else s = s.split(/[/?#]/)[0].replace(/:\d+$/, "");
  if (!s) throw new TypeError("pacing: host required");
  return s;
}

/** Reasons that mean "the employer site is pushing back": trip the host. */
function isTripReason(reason) {
  return reason === "captcha_required" || /lockout|blocked/i.test(String(reason || ""));
}

/**
 * createPacer({file, now, sleep, random}) -> {gate, tripHost, unlock, status, recordSubmission, pacedApply}
 * `file` may be a string or a function (so the default instance honours ATS_PACING_FILE at call time).
 * `now`/`sleep`/`random` are injectable for deterministic tests.
 */
function createPacer(opts = {}) {
  const now = opts.now || Date.now;
  const sleep = opts.sleep || realSleep;
  const random = opts.random || Math.random;
  const cfg = { ...CFG, ...(opts.cfg || {}) };
  const fileOf = () => (typeof opts.file === "function" ? opts.file() : opts.file) ||
    process.env.ATS_PACING_FILE || path.resolve(__dirname, "..", "..", "data", "ats-pacing.json");

  const emptyState = () => ({ version: 1, hosts: {} });

  function readState(file) {
    let txt;
    try { txt = fs.readFileSync(file, "utf8"); }
    catch (e) { if (e.code === "ENOENT") return emptyState(); throw new Error(`pacing: cannot read ${file}: ${e.message}`); }
    let s;
    try { s = JSON.parse(txt); } catch (e) { throw new Error(`pacing: ${file} is corrupt (${e.message}); refusing to start from empty state — fix or delete it by hand`); }
    if (!s || typeof s !== "object" || typeof s.hosts !== "object" || !s.hosts) throw new Error(`pacing: ${file} has unexpected shape; refusing to continue`);
    return s;
  }

  function writeState(file, s) {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    const tmp = `${file}.${process.pid}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(s, null, 1));
    fs.renameSync(tmp, file);
  }

  // Cross-process mutex around read-modify-write (O_EXCL lock file, stale-lock takeover). Synchronous.
  function withState(fn) {
    const file = fileOf(), lock = `${file}.lock`;
    fs.mkdirSync(path.dirname(file), { recursive: true });
    const start = Date.now();
    for (;;) {
      try { fs.closeSync(fs.openSync(lock, "wx")); break; }
      catch (e) {
        if (e.code !== "EEXIST") throw e;
        let age = 0;
        try { age = Date.now() - fs.statSync(lock).mtimeMs; } catch { continue; }
        if (age > LOCK_STALE_MS) { try { fs.unlinkSync(lock); } catch { /* raced */ } continue; }
        if (Date.now() - start > LOCK_WAIT_MS) throw new Error(`pacing: timed out waiting for ${lock}`);
        Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 25);
      }
    }
    try {
      const s = readState(file);
      const out = fn(s);
      if (out && out.save) writeState(file, s);
      return out ? out.value : undefined;
    } finally { try { fs.unlinkSync(lock); } catch { /* already gone */ } }
  }

  function host(s, h) { return (s.hosts[h] ||= { last: 0, subs: [], lockedUntil: 0, lockReason: null, strikes: 0 }); }

  function prune(s, t) {
    for (const [h, e] of Object.entries(s.hosts)) {
      e.subs = (e.subs || []).filter((x) => t - x < DAY);
      if (!e.subs.length && !(e.lockedUntil > t) && !e.strikes && t - (e.last || 0) > DAY) delete s.hosts[h];
    }
  }

  const globalCount = (s) => Object.values(s.hosts).reduce((n, e) => n + e.subs.length, 0);

  /** Throws RateLimited if the host is locked or a cap is reached. */
  function checkAllowed(s, h, t) {
    const e = s.hosts[h];
    if (e && e.lockedUntil > t)
      throw new RateLimited(`${h} locked until ${isoOrNull(e.lockedUntil)} (${e.lockReason})`, e.lockedUntil, h);
    if (cfg.maxSubmissionsPerHostPerDay !== null && e && e.subs.length >= cfg.maxSubmissionsPerHostPerDay)
      throw new RateLimited(`${h}: daily submission cap (${cfg.maxSubmissionsPerHostPerDay}) reached`, Math.min(...e.subs) + DAY, h);
    if (cfg.maxSubmissionsPerDay !== null && globalCount(s) >= cfg.maxSubmissionsPerDay) {
      const oldest = Math.min(...Object.values(s.hosts).flatMap((x) => x.subs));
      throw new RateLimited(`global daily employer submission cap (${cfg.maxSubmissionsPerDay}) reached`, oldest + DAY, h);
    }
  }

  let gateChain = Promise.resolve();   // serializes gate() callers
  let flightChain = Promise.resolve(); // serializes whole applications

  /** Wait for/consume a page-load slot on `h`. Resolves when the caller may load; throws RateLimited otherwise. */
  function gate(hostArg) {
    const h = normalizeHost(hostArg);
    const run = gateChain.then(async () => {
      const gap = cfg.minGapMs + Math.round(random() * cfg.jitterMs); // fixed per call, re-evaluated against fresh state
      for (;;) {
        const wait = withState((s) => {
          const t = now();
          prune(s, t);
          checkAllowed(s, h, t);
          const e = host(s, h);
          const w = Math.max(0, (e.last || 0) + gap - t);
          if (w > 0) return { value: w, save: false };
          e.last = t;
          return { value: 0, save: true };
        });
        if (!wait) return;
        await sleep(wait); // state is re-read afterwards: a lock tripped meanwhile (by any process) is honoured
      }
    });
    gateChain = run.catch(() => {});
    return run;
  }

  /** Lock `h` after a captcha/lockout/blocked signal. Escalates 30m -> 2h -> 12h -> 24h; never shortens an existing lock. */
  function tripHost(hostArg, reason) {
    const h = normalizeHost(hostArg);
    return withState((s) => {
      const t = now(), e = host(s, h);
      e.strikes = (e.strikes || 0) + 1;
      const dur = cfg.backoffMs[Math.min(e.strikes, cfg.backoffMs.length) - 1];
      e.lockedUntil = Math.max(e.lockedUntil || 0, t + dur);
      e.lockReason = String(reason || "tripped");
      e.lastTrip = t;
      return { save: true, value: { host: h, strikes: e.strikes, lockedUntil: e.lockedUntil, until: isoOrNull(e.lockedUntil), reason: e.lockReason, durationMs: dur } };
    });
  }

  /** Count a real employer submission toward the daily caps. */
  function recordSubmission(hostArg) {
    const h = normalizeHost(hostArg);
    return withState((s) => {
      const t = now();
      prune(s, t);
      host(s, h).subs.push(t);
      return { save: true, value: undefined };
    });
  }

  /** Human override: clear the lock on `h`. Backoff strikes are kept unless {resetStrikes:true}. */
  function unlock(hostArg, o = {}) {
    const h = normalizeHost(hostArg);
    return withState((s) => {
      const e = s.hosts[h];
      if (!e) return { save: false, value: false };
      e.lockedUntil = 0; e.lockReason = null;
      if (o.resetStrikes) e.strikes = 0;
      return { save: true, value: true };
    });
  }

  function status() {
    return withState((s) => {
      const t = now();
      const hosts = {};
      for (const [h, e] of Object.entries(s.hosts)) {
        const subs = e.subs.filter((x) => t - x < DAY);
        hosts[h] = {
          locked: e.lockedUntil > t ? { until: isoOrNull(e.lockedUntil), reason: e.lockReason } : null,
          strikes: e.strikes || 0, submissions_24h: subs.length, last_load: isoOrNull(e.last),
        };
      }
      const total = Object.values(hosts).reduce((n, x) => n + x.submissions_24h, 0);
      return { value: { file: fileOf(), submissions_24h: total, hosts, limits: cfg } };
    });
  }

  /**
   * applyOnSite-compatible wrapper. One application at a time (queued); gates the job's host; calls applyOnSite;
   * trips the host on manual results with reason captcha_required or any reason matching /lockout|blocked/;
   * counts real (non-dry-run) `applied` results toward the daily caps.
   * Not allowed -> manual/rate_limited with retryAt (nothing is loaded, nothing is guessed).
   */
  function pacedApply(applyOnSite, browser, job, profile, o = {}) {
    const run = flightChain.then(async () => {
      const url = job && (job.apply_url || job.url);
      let h;
      try { h = normalizeHost(url); } catch { return applyOnSite(browser, job, profile, o); } // no usable URL: applyOnSite reports job_without_url
      try { await gate(h); }
      catch (e) {
        if (e instanceof RateLimited) return { status: "manual", reason: "rate_limited", evidence: e.message, retryAt: e.retryAt, host: h, dryRun: o.dryRun !== false };
        throw e;
      }
      const r = await applyOnSite(browser, job, profile, o);
      if (r && r.status === "manual" && isTripReason(r.reason)) {
        const lock = tripHost(h, r.reason);
        return { ...r, pacing: { tripped: true, host: h, strikes: lock.strikes, lockedUntil: lock.until } };
      }
      if (r && r.status === "applied" && o.dryRun === false) recordSubmission(h);
      return r;
    });
    flightChain = run.catch(() => {});
    return run;
  }

  return { gate, tripHost, unlock, status, recordSubmission, pacedApply };
}

const def = createPacer();

module.exports = {
  gate: def.gate, tripHost: def.tripHost, unlock: def.unlock, status: def.status,
  recordSubmission: def.recordSubmission, pacedApply: def.pacedApply,
  createPacer, normalizeHost, isTripReason, RateLimited, CFG,
};
