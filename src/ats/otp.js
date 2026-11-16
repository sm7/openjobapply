// OTP / verification-code hand-off.
//
// Policy: by default this project NEVER reads the candidate's Gmail or phone. A human reads the code and gives it
// to us. The interface is pluggable so that a dedicated job-application mailbox that the candidate
// configures can be added as another provider (e.g. `MailboxOtpProvider`) with no adapter
// changes. HumanPromptOtpProvider is the default.
//
//   interface OtpProvider {
//     // May the adapter TRIGGER a code now (click "Send")? Codes expire and cost the employer an SMS,
//     // so by default a human must say "I'm ready" first.
//     authorizeSend(req) -> Promise<boolean>
//     // Return the code if the human has supplied one, else null. Must never block forever.
//     getCode(req) -> Promise<string|null>
//     // Called after the adapter actually clicked Send (lets a provider drop a one-shot authorization).
//     sent(req) -> Promise<void>
//   }
//   req = {token, site, jobId, url, channel:'sms'|'email', stage:'ready_to_send'|'awaiting_code'}
//
// Resume protocol (no process needs to stay alive between steps; the browser tab is the state):
//   1. applyOnSite() returns {status:'manual', reason:'otp_required', resumeToken, otpStage:'ready_to_send'}
//   2. human runs:   bun src/ats/otp.js send <token>        (authorizes the Send click)
//      and re-runs applyOnSite on the same tab -> it clicks Send -> returns otp_required/'awaiting_code'
//   3. human reads the code from her phone/email and runs: bun src/ats/otp.js code <token> 123456
//      re-run applyOnSite on the same tab -> it types the code and continues the application.
//   Files: data/otp/<token>.pending.json (what is needed), .send (authorization), .code (the code; deleted on use).
"use strict";
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

const ROOT = path.resolve(__dirname, "..", "..");
const DEFAULT_DIR = path.join(ROOT, "data", "otp");

/** Deterministic per (site, job): the same job always resumes with the same token. */
function otpToken(site, jobId) {
  return crypto.createHash("sha1").update(`${site}|${jobId}`).digest("hex").slice(0, 12);
}

class OtpProvider {
  // eslint-disable-next-line no-unused-vars
  async authorizeSend(req) { return false; }
  // eslint-disable-next-line no-unused-vars
  async getCode(req) { return null; }
  async sent(req) { void req; }
}

class HumanPromptOtpProvider extends OtpProvider {
  /**
   * @param {object} [o]
   * @param {string} [o.dir]          pending/send/code files (default data/otp)
   * @param {boolean} [o.interactive] prompt on a TTY (readline) instead of returning immediately
   * @param {number} [o.waitMs]       with interactive=false, poll the .code file this long (default 0)
   * @param {'sms'|'email'} [o.channel] preferred delivery channel (default 'sms')
   * @param {object} [o.env]          environment (ATS_OTP_SEND=1 authorizes sends; ATS_OTP_CODE supplies a code)
   */
  constructor(o = {}) {
    super();
    this.dir = o.dir || DEFAULT_DIR;
    this.interactive = !!o.interactive;
    this.waitMs = o.waitMs || 0;
    this.channel = o.channel || "sms";
    this.env = o.env || process.env;
    this._sendOk = new Set();
    this._codes = new Map();
  }

  _file(token, ext) { return path.join(this.dir, `${token}.${ext}`); }

  _pending(req) {
    fs.mkdirSync(this.dir, { recursive: true });
    const instr = req.stage === "ready_to_send"
      ? `Ready to request a ${req.channel} verification code on ${req.site}. When you can read it, run: bun src/ats/otp.js send ${req.token}  then re-run the application.`
      : `A ${req.channel} code was requested on ${req.site}. Read it and run: bun src/ats/otp.js code ${req.token} <code>  then re-run the application.`;
    fs.writeFileSync(this._file(req.token, "pending.json"), JSON.stringify({ ...req, instruction: instr, at: new Date().toISOString() }, null, 2));
    return instr;
  }

  /** programmatic equivalents of the CLI (useful in the same process). */
  allowSend(token) { this._sendOk.add(token); }
  supplyCode(token, code) { this._codes.set(token, String(code)); }

  async sent(req) { this._sendOk.delete(req.token); try { fs.unlinkSync(this._file(req.token, "send")); } catch { /* none */ } }

  async authorizeSend(req) {
    if (this._sendOk.has(req.token) || this.env.ATS_OTP_SEND === "1" || fs.existsSync(this._file(req.token, "send"))) return true;
    if (this.interactive && process.stdin.isTTY) {
      const a = await ask(`[ats] Send a ${req.channel} verification code now for ${req.site} (job ${req.jobId})? [y/N] `);
      if (/^y/i.test(a)) return true;
    }
    this._pending({ ...req, stage: "ready_to_send" });
    return false;
  }

  _take(token) {
    if (this._codes.has(token)) { const c = this._codes.get(token); this._codes.delete(token); return c; }
    const f = this._file(token, "code");
    if (fs.existsSync(f)) { const c = fs.readFileSync(f, "utf8"); fs.unlinkSync(f); return c; }
    if (this.env.ATS_OTP_CODE) return this.env.ATS_OTP_CODE;
    return null;
  }

  async getCode(req) {
    const sane = (c) => { const d = String(c || "").replace(/\s+/g, ""); return /^[A-Za-z0-9]{4,10}$/.test(d) ? d : null; };
    let code = sane(this._take(req.token));
    if (!code && this.waitMs) {
      const end = Date.now() + this.waitMs;
      while (!code && Date.now() < end) { await new Promise((r) => setTimeout(r, 1000)); code = sane(this._take(req.token)); }
    }
    if (!code && this.interactive && process.stdin.isTTY) code = sane(await ask(`[ats] Enter the ${req.channel} code for ${req.site}: `));
    if (!code) this._pending({ ...req, stage: "awaiting_code" });
    return code;
  }
}

function ask(q) {
  return new Promise((resolve) => {
    const rl = require("readline").createInterface({ input: process.stdin, output: process.stderr });
    rl.question(q, (a) => { rl.close(); resolve(a); });
  });
}

module.exports = { OtpProvider, HumanPromptOtpProvider, otpToken };

// CLI: bun src/ats/otp.js send <token> | code <token> <digits> | list
if (require.main === module) {
  const [cmd, token, code] = process.argv.slice(2);
  const p = new HumanPromptOtpProvider();
  fs.mkdirSync(p.dir, { recursive: true });
  if (cmd === "send" && token) { fs.writeFileSync(p._file(token, "send"), ""); console.log(`authorized send for ${token}`); }
  else if (cmd === "code" && token && code) { fs.writeFileSync(p._file(token, "code"), code); console.log(`stored code for ${token}`); }
  else if (cmd === "list") { for (const f of fs.readdirSync(p.dir).filter((x) => x.endsWith(".pending.json"))) console.log(fs.readFileSync(path.join(p.dir, f), "utf8")); }
  else { console.error("usage: bun src/ats/otp.js send <token> | code <token> <digits> | list"); process.exit(2); }
}
