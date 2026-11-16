// MailboxOtpProvider: reads ONE-TIME VERIFICATION CODES from the candidate's Gmail, through their already
// signed-in Chrome profile (the omp browser relay). Authorized by the candidate for verification codes ONLY.
//
// What this module is allowed to do, enforced in code (not just by convention):
//   1. Search Gmail for a narrow query: messages that arrived after the code was requested, from a
//      sender whose domain matches the site that asked for the code, with a verification-style subject.
//   2. Read only the subject line + sender domain + received time shown in the result LIST. It opens a
//      message only when the list row already passes the sender-domain AND subject AND freshness gates.
//   3. Extract a single code with a strict pattern and DISCARD everything else. No body, subject,
//      address or attachment is returned, stored or logged. The audit line holds sender domain and time.
//   4. Never open, search, label, archive, delete, star, reply to, send or mark any message.
//      It never navigates to any Gmail URL other than a search results list and a message it matched.
// Anything ambiguous (two different candidate codes, no match, sender mismatch) returns null. A human
// then supplies the code the old way. It never guesses.
"use strict";
const fs = require("fs");
const path = require("path");
const { OtpProvider } = require("./otp");

const ROOT = path.resolve(__dirname, "..", "..");
const AUDIT = path.join(ROOT, "logs", "mailbox_otp.log");

/** Sender-domain families: the code email may come from the ATS vendor, not the employer's own domain. */
const VENDOR = {
  "adp.com": ["adp.com", "adpselect.com"],
  "myworkdayjobs.com": ["myworkday.com", "workday.com", "myworkdayjobs.com"],
  "workday.com": ["myworkday.com", "workday.com"],
  "icims.com": ["icims.com"],
  "greenhouse.io": ["greenhouse.io", "greenhouse-mail.io"],
  "lever.co": ["lever.co", "hire.lever.co"],
  "smartrecruiters.com": ["smartrecruiters.com"],
  "ashbyhq.com": ["ashbyhq.com"],
  "linkedin.com": ["linkedin.com"],
};

const SUBJECT = /(verification|verify|security|one[- ]time|passcode|pass code|login|log in|sign[- ]?in|confirm|authenticat|access)[^\n]{0,40}(code|pin|passcode)|\b(code|pin)\b[^\n]{0,30}(verification|verify|login|sign[- ]?in)|your .{0,30}\bcode\b/i;
/** A code is 4-10 chars, digits, or mixed with at least one digit (avoid matching ordinary words). */
const CODE = /(?<![A-Za-z0-9])([0-9]{4,8}|(?=[A-Za-z0-9]*[0-9])[A-Za-z0-9]{6,10})(?![A-Za-z0-9])/g;

function domainFamily(site) {
  const host = String(site || "").toLowerCase().replace(/^www\./, "");
  const keys = Object.keys(VENDOR).filter((k) => host === k || host.endsWith("." + k));
  const base = host.split(".").slice(-2).join(".");
  const set = new Set([base, ...keys.flatMap((k) => VENDOR[k])]);
  return [...set];
}

function senderOk(fromDomain, family) {
  const d = String(fromDomain || "").toLowerCase();
  return family.some((f) => d === f || d.endsWith("." + f));
}

/** Pull a code out of text near a cue word. Returns the unique code, or null if none/ambiguous. */
function extractCode(text) {
  const t = String(text || "").replace(/\s+/g, " ");
  // Whole-word cue only ("pin" must not match "shopping"); the code must follow it closely.
  const cue = /(?<![A-Za-z])(verification code|security code|one[- ]time (?:code|passcode|password)|passcode|code|pin)(?![A-Za-z])[^A-Za-z0-9]{0,6}(?:is|:|-)?[^A-Za-z0-9]{0,6}/gi;
  const found = new Set();
  let m;
  while ((m = cue.exec(t))) {
    const after = t.slice(m.index + m[0].length, m.index + m[0].length + 14);
    const c = /^([0-9]{4,8}|(?=[A-Za-z0-9]*[0-9])[A-Za-z0-9]{6,10})(?![A-Za-z0-9])/.exec(after);
    if (!c || /^(19|20)\d{2}$/.test(c[1])) continue; // not a code, or a year
    found.add(c[1]);
  }
  return found.size === 1 ? [...found][0] : null;
}

function audit(entry) {
  try {
    fs.mkdirSync(path.dirname(AUDIT), { recursive: true });
    fs.appendFileSync(AUDIT, JSON.stringify({ t: new Date().toISOString(), ...entry }) + "\n");
  } catch { /* auditing must never break the application */ }
}

class MailboxOtpProvider extends OtpProvider {
  /**
   * @param {object} o
   * @param {object} o.browser                the omp `browser` global
   * @param {object} [o.app]                  browser app selector for the relay (default {relay:true})
   * @param {number} [o.waitMs]               how long to poll for the code (default 90000)
   * @param {number} [o.pollMs]               poll interval (default 6000)
   * @param {number} [o.slackMs]              clock-skew allowance before the Send click (default 60000)
   * @param {boolean} [o.allowSend]           auto-authorize the "Send code" click (default false)
   */
  constructor(o = {}) {
    super();
    if (!o.browser) throw new Error("MailboxOtpProvider requires the browser global");
    this.browser = o.browser;
    this.app = o.app || "relay"; // the worker's installed CLI takes the string; pass {relay:true} explicitly on older CLIs
    this.waitMs = o.waitMs ?? 90000;
    this.pollMs = o.pollMs ?? 6000;
    this.slackMs = o.slackMs ?? 60000;
    this.autoSend = !!o.allowSend;
    this._sentAt = new Map();
  }

  async authorizeSend(req) { return this.autoSend; }

  async sent(req) { this._sentAt.set(req.token, Date.now()); }

  /** Gmail search query. Narrow on purpose: sender family + freshness. Subject is verified again in code. */
  _query(req, family) {
    const from = family.map((d) => `from:${d}`).join(" OR ");
    return `in:anywhere newer_than:1d (${from})`;
  }

  async getCode(req) {
    const sentAt = this._sentAt.get(req.token);
    if (!sentAt) return null; // never look for a code we did not ask for
    const family = domainFamily(req.site);
    if (!family.length) return null;
    const earliest = sentAt - this.slackMs;
    const end = Date.now() + this.waitMs;
    let tab = null;
    try {
      tab = await this.browser.open({ name: `otp-mail-${req.token}`, url: "about:blank", app: this.app });
      while (Date.now() < end) {
        const code = await this._scan(tab, req, family, earliest);
        if (code) { audit({ event: "code_read", site: req.site, job: req.jobId, family: family[0] }); return code; }
        await new Promise((r) => setTimeout(r, this.pollMs));
      }
      audit({ event: "no_code", site: req.site, job: req.jobId });
      return null;
    } finally {
      if (tab) { try { await this.browser.close({ name: `otp-mail-${req.token}` }); } catch { /* best effort */ } }
    }
  }

  async _scan(tab, req, family, earliest) {
    const q = encodeURIComponent(this._query(req, family));
    await tab.goto(`https://mail.google.com/mail/u/0/#search/${q}`);
    await new Promise((r) => setTimeout(r, 2500));
    const rows = await tab.evaluate(`(() => [...document.querySelectorAll('tr.zA')].slice(0, 6).map((r, i) => {
      const from = r.querySelector('.yW span[email], .yP, .zF');
      const email = (from && (from.getAttribute('email') || '')) || '';
      const subj = (r.querySelector('.bog, .bqe') || {}).innerText || '';
      const when = (r.querySelector('td.xW span, .xW span') || {}).getAttribute && r.querySelector('td.xW span, .xW span').getAttribute('title') || '';
      return { i, domain: (email.split('@')[1] || '').toLowerCase(), subject: subj.slice(0, 160), when };
    }))()`);
    const candidates = [];
    for (const r of rows || []) {
      if (!senderOk(r.domain, family)) continue;       // gate 1: sender
      if (!SUBJECT.test(r.subject)) continue;           // gate 2: looks like a verification email
      const ts = Date.parse(r.when);
      if (Number.isFinite(ts) && ts < earliest) continue; // gate 3: arrived after we asked
      candidates.push(r);
    }
    if (!candidates.length) return null;
    // Subject alone often carries the code ("Your code is 123456"). Prefer it: no message needs opening.
    const codes = new Set();
    for (const c of candidates) { const k = extractCode(c.subject); if (k) codes.add(k); }
    if (codes.size === 1) return [...codes][0];
    if (codes.size > 1) return null; // ambiguous: refuse to guess
    // Otherwise open only the NEWEST candidate that passed all three gates, read its code, discard the rest.
    const newest = candidates[0];
    await tab.evaluate(`(() => { const r = document.querySelectorAll('tr.zA')[${newest.i}]; r && r.click(); })()`);
    await new Promise((r) => setTimeout(r, 2000));
    const body = await tab.evaluate(`(() => { const m = document.querySelector('.a3s.aiL, .a3s'); return m ? m.innerText.slice(0, 1200) : ''; })()`);
    return extractCode(body);
  }
}

module.exports = { MailboxOtpProvider, extractCode, domainFamily, senderOk, SUBJECT, AUDIT };
