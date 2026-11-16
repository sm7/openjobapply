// Shared helpers for ATS adapters: result builders, consent log, guarded clicking, page probes.
// Hard rules enforced here (not left to each adapter):
//   * never click social sign-in, "talent community", alert/subscribe controls  (clickEl refuses)
//   * never solve / bypass captcha: detectChallenge() only DETECTS so the adapter can hand to a human
//   * every accepted consent is appended to logs/consents.log
"use strict";
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

const ROOT = path.resolve(__dirname, "..", "..");
const CONSENT_LOG = process.env.ATS_CONSENT_LOG || path.join(ROOT, "logs", "consents.log");
const EVIDENCE_DIR = process.env.ATS_EVIDENCE_DIR || path.join(ROOT, "logs", "ats");

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const jitter = (a, b) => sleep(a + Math.floor(Math.random() * (b - a)));

/**
 * Controls this plugin must never press, whatever an adapter asks for.
 * "Sign in / log in / sign up / continue with X" is blocked for every X EXCEPT:
 *   - the first-party choices "email" and "password";
 *   - Google, ONLY through isGoogleSignIn() below (the user explicitly allowed her own Google account,
 *     which Chrome is already signed into). Every other provider (LinkedIn, Apple, Microsoft, Facebook,
 *     SSO, unknown) stays blocked, and Google itself stays blocked here so nothing can click it by accident.
 */
const FORBIDDEN_CLICK = /(?:sign ?(?:in|up)|log ?in|continue|register|connect) with (?!(?:my |your )?(?:e-?mail|password)\b)|talent (community|network)|job alert|create (an )?alert|subscribe|newsletter|e-?sign/i;

/** The exact label of the one social button we may press: "Sign in with Google" / "Continue with Google" / "Sign up with Google". */
const GOOGLE_SIGNIN = /^(?:sign ?(?:in|up)|log ?in|continue|register)\s+with\s+google\s*$/i;
const isGoogleSignIn = (name) => GOOGLE_SIGNIN.test(String(name || "").trim());

class Blocked extends Error {
  constructor(reason, evidence, extra) { super(reason); this.name = "Blocked"; this.reason = reason; this.evidence = evidence; this.extra = extra || {}; }
}

function result(status, reason, extra) { return { status, reason, ...(extra || {}) }; }

function jobKey(job) {
  return (job && (job.id || job.source_id)) || crypto.createHash("sha1").update(String((job && (job.apply_url || job.url)) || "")).digest("hex").slice(0, 16);
}

function logConsent(ctx, text, rule) {
  const entry = { t: new Date().toISOString(), job: jobKey(ctx.job), site: ctx.site, accepted: String(text).replace(/\s+/g, " ").trim().slice(0, 300), rule, dryRun: !!ctx.dryRun };
  fs.mkdirSync(path.dirname(CONSENT_LOG), { recursive: true });
  fs.appendFileSync(CONSENT_LOG, JSON.stringify(entry) + "\n");
  ctx.consents.push(entry.accepted);
  ctx.log && ctx.log("consent", entry);
}

/** page-global IIFE with a JSON argument (tab.evaluate takes a string expression). */
function evalIn(tab, fn, arg) {
  return tab.evaluate(`(${fn.toString()})(${JSON.stringify(arg === undefined ? null : arg)})`);
}

async function visibleText(tab) {
  return (await tab.evaluate(`document.body ? document.body.innerText : ""`)) || "";
}

/** Interactive elements without the (huge) option lists of country dropdowns. */
async function observe(tab) {
  const o = await tab.observe();
  return o.elements.filter((e) => e.role !== "option");
}

function matchEl(els, { roles, name }) {
  return els.find((e) => (!roles || roles.includes(e.role)) && name.test(e.name || ""));
}

async function clickEl(tab, el) {
  if (FORBIDDEN_CLICK.test(el.name || "")) throw new Blocked("forbidden_control", `refused to click "${el.name}"`);
  await tab.id(el.id).click();
}

/** observe -> find by role/name -> click. Returns the clicked element name or null. */
async function clickRole(tab, roles, name) {
  const els = await observe(tab);
  const el = matchEl(els, { roles, name });
  if (!el) return null;
  await clickEl(tab, el);
  return el.name;
}

async function waitUntil(fn, { timeout = 15000, every = 400 } = {}) {
  const end = Date.now() + timeout;
  for (;;) {
    const v = await fn();
    if (v) return v;
    if (Date.now() > end) return null;
    await sleep(every);
  }
}

/** Visible captcha / bot-check challenge. We only detect; a human must handle it. */
async function detectChallenge(tab) {
  return evalIn(tab, () => {
    const vis = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return r.width > 80 && r.height > 80 && s.visibility !== "hidden" && s.display !== "none"; };
    for (const f of document.querySelectorAll("iframe")) {
      const src = (f.src || "") + " " + (f.title || "");
      if (/hcaptcha|recaptcha|challenge|turnstile|arkose|funcaptcha|geetest/i.test(src) && vis(f)) {
        // reCAPTCHA v3/invisible badge is a tiny anchor frame; a challenge frame is large
        return src.slice(0, 120);
      }
    }
    const t = document.body ? document.body.innerText : "";
    if (/verify (that )?you are (a )?human|i'?m not a robot|unusual traffic|are you a robot|press (&|and) hold/i.test(t)) return "text:bot-check";
    return null;
  });
}

async function shot(ctx, name) {
  try {
    const p = await ctx.tab.screenshot({ silent: true });
    if (typeof p !== "string") return null;
    fs.mkdirSync(EVIDENCE_DIR, { recursive: true });
    const dest = path.join(EVIDENCE_DIR, `${jobKey(ctx.job)}-${name}-${Date.now()}.webp`);
    fs.copyFileSync(p, dest);
    return dest;
  } catch { return null; }
}

/** One-line page summary for evidence strings. */
async function snippet(tab, n = 400) {
  const t = await visibleText(tab);
  return t.replace(/\s+/g, " ").slice(0, n);
}

const CONFIRMATION = /thank you for (applying|your (application|interest))|application (has been |was )?(successfully )?(submitted|received|sent)|successfully (submitted|applied)|we('| ha)ve received your application|your application is complete/i;

module.exports = {
  ROOT, CONSENT_LOG, EVIDENCE_DIR, sleep, jitter, FORBIDDEN_CLICK, isGoogleSignIn, Blocked, result, jobKey, logConsent, evalIn,
  visibleText, observe, matchEl, clickEl, clickRole, waitUntil, detectChallenge, shot, snippet, CONFIRMATION,
};
