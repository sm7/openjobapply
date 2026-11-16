// MailboxLinkProvider: extends the Gmail reader of mailbox_otp.js to exactly TWO more message kinds that
// the candidate authorized for employer-site accounts they asked us to create / reset:
//   'verify'  the account-verification / email-confirmation message of the site we JUST registered on
//   'reset'   the password-reset message of the site we JUST requested a reset from
// (verification CODES keep going through MailboxOtpProvider.getCode, inherited unchanged.)
//
// Same gates as codes, enforced in code:
//   1. nothing is read unless markTriggered(req, kind) recorded that WE triggered that email, and the
//      trigger is consumed by the one read attempt (a second call without a new trigger returns null);
//   2. only list rows whose sender domain is in the site's family AND whose subject matches the kind
//      AND whose received time is parseable and after the trigger (minus clock slack) are candidates;
//   3. only the single NEWEST candidate is opened; from it only anchor hrefs are read, everything else
//      (body text, subject, addresses) is discarded inside the page and never reaches this process;
//   4. exactly one link must survive validateLink(): https, same site family as the sender gate, no
//      credentials/odd port, purpose-looking path, not an unsubscribe/privacy/help link. Two different
//      survivors, or none, means null (a human then does it the old way);
//   5. the audit line holds event + site + kind + time, never a link, subject, sender or body;
//   6. the Gmail tab is closed in `finally`; it never labels, archives, deletes, replies or sends.
// The returned link is navigated by the caller in the tab IT holds, never in the mailbox tab.
"use strict";
const fs = require("fs");
const path = require("path");
const { MailboxOtpProvider, domainFamily, senderOk } = require("./mailbox_otp");

const ROOT = path.resolve(__dirname, "..", "..");
const AUDIT = process.env.ATS_MAILBOX_ACCOUNT_LOG || path.join(ROOT, "logs", "mailbox_account.log");

const VERIFY_SUBJECT = /verify (your )?(e-?mail|account|address)|confirm (your )?(e-?mail|account|address|registration)|activate (your )?(account|profile|registration)|(e-?mail|account) (verification|confirmation|activation)|\bactivate\b/i;
const RESET_SUBJECT = /reset (your |the )?(account )?password|password reset|reset (your )?(account )?credentials|forgot(ten)? (your )?password/i;
const SUBJECTS = { verify: VERIFY_SUBJECT, reset: RESET_SUBJECT };
// What the link itself must look like (path + query), and what it must never look like.
const PURPOSE = {
  verify: /verif|confirm|activat|token|key|code|otp|ticket|registration|validate/i,
  reset: /reset|password|token|key|code|recover|forgot|ticket/i,
};
const NOT_ACTION = /unsubscribe|opt-?out|privacy|terms|preferences|notification|help|support|faq|contact|logo|\.(png|jpe?g|gif|svg)(\?|$)/i;

function hostOf(site) {
  const s = String(site || "").trim();
  if (!s) return "";
  try { return new URL(/^[a-z][a-z0-9+.-]*:\/\//i.test(s) ? s : `https://${s}`).hostname.toLowerCase(); } catch { return ""; }
}
const isIp = (h) => /^\d{1,3}(\.\d{1,3}){3}$/.test(h) || h.includes(":");
const isLoopback = (h) => h === "localhost" || h === "127.0.0.1" || h === "[::1]" || h === "::1";

/**
 * A link is acceptable only if it is https on the site's own family (or exactly the site host).
 * `allowLoopbackHttp` exists for local fixtures only: http is then tolerated for a loopback host that is
 * EXACTLY the site host. Returns the normalized URL string or null.
 */
function validateLink(raw, site, o = {}) {
  let u;
  try { u = new URL(String(raw)); } catch { return null; }
  if (u.username || u.password) return null;
  const host = u.hostname.toLowerCase();
  const siteHost = hostOf(site);
  if (!siteHost || !host) return null;
  const loopbackOk = !!o.allowLoopbackHttp && u.protocol === "http:" && isLoopback(host) && host === siteHost;
  if (u.protocol !== "https:" && !loopbackOk) return null;
  if (u.protocol === "https:" && u.port) return null; // employer mail links use the default port
  if (host === siteHost) return u.href;
  if (isIp(siteHost) || isIp(host)) return null;
  return senderOk(host, domainFamily(siteHost)) ? u.href : null;
}

/** From the hrefs of the newest message: the single acceptable action link, or null (none/ambiguous). */
function pickLink(hrefs, site, kind, o = {}) {
  const ok = new Set();
  for (const h of hrefs || []) {
    const v = validateLink(h, site, o);
    if (!v) continue;
    const u = new URL(v);
    const where = `${u.pathname}${u.search}`;
    if (NOT_ACTION.test(where) || !PURPOSE[kind].test(where)) continue;
    ok.add(v);
  }
  return ok.size === 1 ? [...ok][0] : null;
}

/** Candidate list rows -> the single newest row that passes sender + subject + freshness gates. */
function selectMessage(rows, family, kind, earliest) {
  const subject = SUBJECTS[kind];
  const cand = [];
  for (const r of rows || []) {
    if (!senderOk(r.domain, family)) continue;      // gate: sender family
    if (!subject.test(r.subject || "")) continue;    // gate: subject for this kind
    const ts = Date.parse(r.when);
    if (!Number.isFinite(ts) || ts < earliest) continue; // gate: provably after we triggered it (unknown time = no)
    cand.push({ ...r, ts });
  }
  if (!cand.length) return null;
  return cand.reduce((a, b) => (b.ts > a.ts ? b : a));
}

function audit(entry) {
  try {
    fs.mkdirSync(path.dirname(AUDIT), { recursive: true });
    fs.appendFileSync(AUDIT, JSON.stringify({ t: new Date().toISOString(), ...entry }) + "\n");
  } catch { /* auditing must never break the application */ }
}

class MailboxLinkProvider extends MailboxOtpProvider {
  /** Same options as MailboxOtpProvider, plus `settleMs`/`openMs` (page settle waits, default 2500/2000) and `allowLoopbackHttp` (fixtures only). */
  constructor(o = {}) {
    super(o);
    this.settleMs = o.settleMs ?? 2500;
    this.openMs = o.openMs ?? 2000;
    this.allowLoopbackHttp = !!o.allowLoopbackHttp;
    this._trig = new Map();
  }

  /** Record that WE just triggered a 'verify' | 'reset' | 'code' email for req. Nothing is read before this. */
  markTriggered(req, kind) {
    if (kind === "code") return this.sent(req);
    if (!SUBJECTS[kind]) throw new Error(`unknown mailbox kind "${kind}"`);
    this._trig.set(`${req.token}|${kind}`, Date.now());
    return undefined;
  }

  getVerificationLink(req) { return this._link(req, "verify"); }
  getResetLink(req) { return this._link(req, "reset"); }
  // getCode(req) is inherited from MailboxOtpProvider (same sender/subject/freshness gates, code only).

  _query(req, family, kind) {
    const from = family.map((d) => `from:${d}`).join(" OR ");
    const subj = kind === "reset" ? "subject:(reset OR password)" : "subject:(verify OR confirm OR activate OR activation)";
    return `in:anywhere newer_than:1d (${from}) ${subj}`;
  }

  async _link(req, kind) {
    const key = `${req.token}|${kind}`;
    const triggeredAt = this._trig.get(key);
    if (!triggeredAt) return null;       // never look for mail we did not ask for
    this._trig.delete(key);              // one read attempt per trigger
    const siteHost = hostOf(req.site);
    if (!siteHost || isIp(siteHost) || isLoopback(siteHost)) return null;
    const family = domainFamily(siteHost);
    if (!family.length) return null;
    const earliest = triggeredAt - this.slackMs;
    const end = Date.now() + this.waitMs;
    const name = `acct-mail-${req.token}`;
    let tab = null;
    try {
      tab = await this.browser.open({ name, url: "about:blank", app: this.app });
      const seen = new Set();
      do {
        const r = await this._scanLink(tab, req, kind, family, earliest, siteHost, seen);
        if (r.link) { audit({ event: "link_read", site: siteHost, kind }); return r.link; }
        if (r.stop) { audit({ event: r.stop, site: siteHost, kind }); return null; }
        if (Date.now() >= end) break;
        await new Promise((res) => setTimeout(res, this.pollMs));
      } while (Date.now() < end);
      audit({ event: "no_link", site: siteHost, kind });
      return null;
    } finally {
      if (tab) { try { await this.browser.close({ name }); } catch { /* best effort */ } }
    }
  }

  async _scanLink(tab, req, kind, family, earliest, siteHost, seen) {
    const q = encodeURIComponent(this._query(req, family, kind));
    await tab.goto(`https://mail.google.com/mail/u/0/#search/${q}`);
    await new Promise((r) => setTimeout(r, this.settleMs));
    const rows = await tab.evaluate(`/*rows*/(() => [...document.querySelectorAll('tr.zA')].slice(0, 6).map((r, i) => {
      const from = r.querySelector('.yW span[email], .yP, .zF');
      const email = (from && (from.getAttribute('email') || '')) || '';
      const subj = (r.querySelector('.bog, .bqe') || {}).innerText || '';
      const whenEl = r.querySelector('td.xW span, .xW span');
      const when = (whenEl && whenEl.getAttribute('title')) || '';
      return { i, domain: (email.split('@')[1] || '').toLowerCase(), subject: subj.slice(0, 160), when };
    }))()`);
    const newest = selectMessage(rows, family, kind, earliest);
    if (!newest) return {};
    const sig = `${newest.i}|${newest.when}|${newest.subject}`;
    if (seen.has(sig)) return {};        // already opened this one: do not reopen while polling
    seen.add(sig);
    await tab.evaluate(`/*open*/(() => { const r = document.querySelectorAll('tr.zA')[${Number(newest.i)}]; r && r.click(); })()`);
    await new Promise((r) => setTimeout(r, this.openMs));
    // Only anchor hrefs of the newest message body leave the page. Nothing else is read.
    const hrefs = await tab.evaluate(`/*links*/(() => { const bodies = [...document.querySelectorAll('.a3s.aiL, .a3s')]; const m = bodies[bodies.length - 1]; return m ? [...m.querySelectorAll('a[href]')].slice(0, 40).map((a) => a.getAttribute('href') || '') : []; })()`);
    const link = pickLink(hrefs, siteHost, kind, { allowLoopbackHttp: false });
    if (link) return { link };
    return { stop: "no_acceptable_link" };   // opened the right message but no single safe link: never guess
  }
}

module.exports = { MailboxLinkProvider, validateLink, pickLink, selectMessage, hostOf, VERIFY_SUBJECT, RESET_SUBJECT, AUDIT };
