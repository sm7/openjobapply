// Persistent, serialized rate limiter for LinkedIn. Every LinkedIn page load or apply action
// MUST go through `gate(kind)`. State persists counters and real platform lockouts across restarts.
//
// Policy (deliberately conservative, human-like):
//   - strictly one request at a time (serialized via a promise chain)
//   - 8-20s jittered gap between any two LinkedIn page loads; 45-120s "reading break" every 4-7 loads
//   - apply actions: 60-180s apart
//   - numeric caps come from data/campaign.json; null means unlimited, including per-run submissions
//   - on challenge (429, authwall, checkpoint, captcha): lock out for 6h and refuse everything
const fs = require("fs");
const path = require("path");

const STATE = path.resolve(__dirname, "..", "data", "ratelimit.json");
const HOUR = 3600e3, DAY = 24 * HOUR;
const campaign = JSON.parse(fs.readFileSync(process.env.CAMPAIGN_CONFIG || path.resolve(__dirname, "..", "data", "campaign.json"), "utf8"));
const CFG = {
  gapMs: [8000, 20000],
  breakEvery: [4, 7],
  breakMs: [45000, 120000],
  applyGapMs: [60000, 180000],
  maxDetailPerHour: campaign.max_details_per_hour,
  maxLoadsPerDay: campaign.max_loads_per_day,
  maxAppliesPerDay: campaign.max_submissions_per_day,
  maxAppliesPerRun: campaign.max_submissions_per_run,
  lockoutMs: 6 * HOUR,
};

const sleep = ms => new Promise(r => setTimeout(r, ms));
const rnd = ([a, b]) => a + Math.floor(Math.random() * (b - a + 1));

function load() {
  try { return JSON.parse(fs.readFileSync(STATE, "utf8")); }
  catch { return { events: [], lockedUntil: 0, sinceBreak: 0, nextBreakAt: rnd(CFG.breakEvery), last: 0, lockReason: null }; }
}
function save(s) {
  fs.mkdirSync(path.dirname(STATE), { recursive: true });
  fs.writeFileSync(STATE, JSON.stringify(s));
}

let chain = Promise.resolve();   // serialization: callers queue behind each other

class RateLimited extends Error {
  constructor(msg, retryAt) { super(msg); this.name = "RateLimited"; this.retryAt = retryAt; }
}

/** kind: "load" (search/listing page), "detail" (job view page), "apply" (an application submit) */
function gate(kind) {
  const run = chain.then(async () => {
    const s = load(), now = Date.now();
    if (s.lockedUntil > now)
      throw new RateLimited(`locked out until ${new Date(s.lockedUntil).toISOString()} (${s.lockReason})`, s.lockedUntil);

    s.events = s.events.filter(e => now - e.t < DAY);
    const inLast = (k, ms) => s.events.filter(e => (!k || e.k === k) && now - e.t < ms).length;

    if (CFG.maxLoadsPerDay !== null && kind !== "apply" && s.events.filter(e => e.k !== "apply").length >= CFG.maxLoadsPerDay)
      throw new RateLimited("daily page-load cap reached", now + DAY);
    if (CFG.maxDetailPerHour !== null && kind === "detail" && inLast("detail", HOUR) >= CFG.maxDetailPerHour)
      throw new RateLimited("hourly detail-view cap reached", now + HOUR);
    if (CFG.maxAppliesPerDay !== null && kind === "apply" && inLast("apply", DAY) >= CFG.maxAppliesPerDay)
      throw new RateLimited("daily application cap reached", now + DAY);

    // wait out the required gap since the last request
    const gap = kind === "apply" ? rnd(CFG.applyGapMs) : rnd(CFG.gapMs);
    let wait = Math.max(0, s.last + gap - now);
    if (kind !== "apply" && s.sinceBreak >= s.nextBreakAt) {
      wait = Math.max(wait, rnd(CFG.breakMs));
      s.sinceBreak = 0; s.nextBreakAt = rnd(CFG.breakEvery);
    }
    if (wait) await sleep(wait);

    s.events.push({ t: Date.now(), k: kind });
    if (kind !== "apply") s.sinceBreak++;
    s.last = Date.now();
    save(s);
  });
  chain = run.catch(() => {});   // a rejection must not poison the queue
  return run;
}

/** Call on any sign of throttling/challenge. Locks everything out. */
function trip(reason) {
  const s = load();
  s.lockedUntil = Date.now() + CFG.lockoutMs;
  s.lockReason = reason;
  save(s);
  return new RateLimited(`tripped: ${reason}`, s.lockedUntil);
}

/** Inspect a loaded page; trips the limiter if LinkedIn is pushing back. Returns true if blocked. */
function checkPage({ url = "", title = "", text = "", status = 200 }) {
  const t = `${title}\n${text.slice(0, 800)}`;
  const bad =
    status === 429 || status === 999 ||
    /authwall|\/checkpoint\/|\/uas\/login|\/login\?/i.test(url) ||
    /security verification|let'?s do a quick security check|unusual activity|are you a human|verify you'?re not a robot|captcha|too many requests|temporarily restricted/i.test(t);
  if (bad) { trip(`challenge at ${url.slice(0, 80)} (${title.slice(0, 40)})`); return true; }
  return false;
}

function status() {
  const s = load(), now = Date.now();
  const n = (k, ms) => s.events.filter(e => e.k === k && now - e.t < ms).length;
  return {
    locked: s.lockedUntil > now ? { until: new Date(s.lockedUntil).toISOString(), reason: s.lockReason } : null,
    loads_24h: s.events.filter(e => now - e.t < DAY && e.k !== "apply").length,
    detail_1h: n("detail", HOUR), applies_24h: n("apply", DAY), limits: CFG,
  };
}

function unlock() { const s = load(); s.lockedUntil = 0; s.lockReason = null; save(s); }

module.exports = { gate, trip, checkPage, status, unlock, RateLimited, CFG };
