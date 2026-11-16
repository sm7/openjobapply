// Authorized employer-site account flow for the candidate (profile.candidate.email). Adapters call ONE function:
//
//   const a = await ensureAccount(ctx, { adapterName, detect?, signInFlow? });
//   if (!a.ready) return a.result;                // manual result, already redacted (return it as-is)
//   ... a.action 'login'|'created'|'reset', a.account {site,email,action}, a.extras {account}; tab is signed in
//
// Policy (the user explicitly authorized exactly this, and ONLY this, relaxation of "no account creation /
// no password reset"; everything else in lib.js/answers.js is unchanged):
//   * Nothing is ever done unless ctx.dryRun === false AND ctx.opts.allowAccountCreation === true. Otherwise
//     the result is manual/account_required plus wouldCreateAccount / wouldResetPassword / wouldLogin and
//     NOTHING is typed, clicked, sent or read (mailbox untouched).
//   * Account Create/Register/Forgot/Sign-in controls are clicked only through gatedClick() below.
//   * NEW account: the password is credentials.reserveNew(site,email,'created') (PERSISTED BEFORE it is
//     typed anywhere); other fields only via the answers.js allowlist; unknown required field => manual.
//   * EXISTING account: saved password first; no saved password or it fails => "forgot password" for the same
//     email, reset link from the mailbox helper (https + same site family, opened in the SAME tab), new
//     password = credentials.reserveNew(site,email,'reset') persisted BEFORE it is submitted, then login.
//   * Caps per site per ctx: 1 creation, 1 reset request, 2 login attempts, 1 verification wait. Any captcha,
//     SMS/phone MFA, lockout, ambiguous page, or untrusted link => manual with a precise reason. Never loops,
//     never guesses a password, never solves a captcha. Optional Google sign-in uses only google_signin.js's gated flow.
//   * The password is only ever typed through fillCredential() (real key events, read-back that only compares)
//     and is never logged, returned, put in evidence or screenshots; every result passes redactResult().
//
// Reasons (all status 'manual'): account_required, captcha_required, mfa_required, verification_timeout,
// account_flow_failed. `account_created` / `password_reset_done` are INFO via extras.account.action.
//
// ctx.opts.mailbox must be a MailboxLinkProvider (src/ats/mailbox_account.js) for create/reset/verification.
// signInFlow (all optional, each called as hook(ctx[, arg]); missing => generic role/label implementation):
//   fillEmail(email)  fillPassword(pw, also confirm)  fillNames()  submit(kind)  clickCreate()  clickForgot()  clickSignIn()
//   chooseEmail() returns true only when a native first-party email/password method was pressed.
// detect(ctx) may return any detectAccountState value; 'none' falls back to the generic detector.
"use strict";
const {
  Blocked, result, sleep, evalIn, waitUntil, detectChallenge, observe, clickEl, snippet, jobKey, FORBIDDEN_CLICK,
} = require("./lib");
const credentials = require("./credentials");
const { scanForm, fillForm, enterCode } = require("./form");
const { classifyConsent } = require("./answers");
const { validateLink, hostOf } = require("./mailbox_account");
const googleSignIn = require("./google_signin");

// ---- page inspection ---------------------------------------------------------------------------------
/** Self-contained (serialised into the page). Returns facts only; no field VALUES leave the page. */
function inspectInPage() {
  const clean = (s) => (s || "").replace(/[\u200b\u00a0]/g, " ").replace(/\s+/g, " ").trim();
  const vis = (el) => {
    if (!el) return false;
    if (el.checkVisibility && !el.checkVisibility({ visibilityProperty: true })) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    return !el.closest("[aria-hidden=true]");
  };
  const labelOf = (el) => {
    const out = [];
    const lb = el.getAttribute("aria-labelledby");
    if (lb) lb.split(/\s+/).forEach((id) => { const n = document.getElementById(id); if (n) out.push(n.textContent); });
    if (!clean(out.join(" ")) && el.labels) [...el.labels].forEach((l) => out.push(l.textContent));
    let s = clean(out.join(" "));
    if (!s) s = clean(el.getAttribute("aria-label")) || clean(el.getAttribute("placeholder")) || clean(el.getAttribute("title"));
    if (!s) { const l = el.closest("label"); if (l) s = clean(l.textContent); }
    return s;
  };
  const inputs = [];
  for (const el of document.querySelectorAll("input, select, textarea")) {
    const type = (el.getAttribute("type") || (el.tagName === "SELECT" ? "select" : el.tagName === "TEXTAREA" ? "textarea" : "text")).toLowerCase();
    if (["hidden", "submit", "button", "image", "reset", "checkbox", "radio", "file"].includes(type)) continue;
    if (el.disabled || !vis(el)) continue;
    if (/captcha|honey|bot-?field|g-recaptcha|hcaptcha/i.test((el.name || "") + " " + el.id + " " + el.className)) continue;
    inputs.push({ type, label: labelOf(el), name: el.getAttribute("name") || el.id || "", ac: el.getAttribute("autocomplete") || "", max: el.maxLength > 0 ? el.maxLength : 0 });
  }
  const controls = [];
  for (const el of document.querySelectorAll("button, input[type=submit], input[type=button], a[href], [role=button], [role=link]")) {
    if (!vis(el) || el.disabled) continue;
    const tag = el.tagName.toLowerCase();
    const role = el.getAttribute("role") || (tag === "a" ? "link" : "button");
    const name = clean(el.getAttribute("aria-label")) || clean(tag === "input" ? el.value : el.innerText || el.textContent);
    if (name) controls.push({ name: name.slice(0, 120), role, tag });
  }
  const heads = [...document.querySelectorAll("h1, h2, h3, legend, [role=heading]")].filter(vis).map((h) => clean(h.textContent).slice(0, 120));
  const text = clean(document.body ? document.body.innerText : "").slice(0, 3500);
  return { inputs, controls, heads, text, title: document.title || "" };
}

async function inspectPage(tab) {
  const info = await evalIn(tab, inspectInPage);
  info.captcha = await detectChallenge(tab).catch(() => null);
  return info;
}

const RX = {
  mfa: /text message|\bsms\b|phone (number )?ending|mobile (number|phone)|sent (a |the )?(verification |security |login )?code to (your )?(phone|mobile|number)|authenticator app|two[- ]?factor|2-?step|multi-?factor|\bmfa\b|\b2fa\b/i,
  codePrompt: /enter (the |your |a )?(\d[- ]?digit )?(verification |security |login )?code|verification code|security code/i,
  resetSent: /(we('ve| have)? sent|has been sent|have been sent|sent you|if an account exists|check your (e-?mail|inbox)).{0,120}(reset|password)|(reset|password).{0,60}(link|instructions|e-?mail).{0,60}(has been|have been|was|were|is on its way|sent)/i,
  verifyEmail: /(we('ve| have)? sent|has been sent|sent you|sent an?|check your (e-?mail|inbox)).{0,140}(verif|confirm|activat)|(please |you must |must )?(verify|confirm|activate) your (e-?mail|account)|verification (link|e-?mail)|confirm your e-?mail/i,
  resetHeading: /reset (your )?password|(set|choose|create|enter) (a |your )?new password|^new password/i,
  signInBtn: /^(sign|log) ?in\b(?!.*\bwith\b)/i,
  createBtn: /^(create( an?| your)? (account|profile)|register|sign ?up(?!.*\bwith\b)|join( now)?)\b/i,
  forgot: /forgot|reset (your )?password|can'?t (sign|log) ?in|trouble (signing|logging)/i,
  cont: /^(continue|next|sign ?in|log ?in|submit|get started|proceed)\b(?!.*\bwith\b)/i,
  emailField: /e-?mail|user ?name|login id/i,
  nameField: /first name|last name|given name|family name|full name|surname|legal name/i,
  exists: /(e-?mail|account|user(name)?|address).{0,60}(already (exists?|registered|in use|taken|associated)|is already)|already (registered|exists|in use)|(duplicate|existing) (account|user)/i,
  bad: /(incorrect|invalid|wrong|didn'?t match|did not match|not recognized|doesn'?t match|unable to (sign|log) ?in).{0,60}(password|e-?mail|credentials|username|sign|log)|(password|e-?mail|credentials).{0,40}(incorrect|invalid|wrong|not recognized)/i,
  locked: /(account|sign.?in).{0,40}(locked|disabled|suspended)|too many (failed |sign.?in |login )?(attempts|tries)|temporarily (blocked|locked|disabled)/i,
  notFound: /(couldn'?t|could not|can'?t|unable to) find (an |your )?account|no account (found|exists|with)|account (not found|does not exist|doesn'?t exist)|we don'?t recognize/i,
  // first-party "Sign in / Log in / Sign up / Continue with email|password" chooser button
  emailMethod: /^(?:sign ?(?:in|up)|log ?in|continue|register)\s+with\s+(?:my\s+|your\s+)?(?:e-?mail|password)\b/i,
};

/** Pure: page facts -> state. Uses roles/labels/text, never ids. */
function classifyAccountPage(info) {
  if (info.captcha) return "captcha";
  const pw = info.inputs.filter((i) => i.type === "password");
  const others = info.inputs.filter((i) => i.type !== "password");
  const isCode = (i) => i.ac === "one-time-code" || /code|otp|pin/i.test(`${i.label} ${i.name}`) || i.max === 1;
  const codeInputs = others.filter(isCode);
  const emailInputs = others.filter((i) => i.type === "email" || RX.emailField.test(`${i.label} ${i.name} ${i.ac}`));
  const textInputs = others.filter((i) => !isCode(i) && !emailInputs.includes(i));
  const nameFields = others.filter((i) => RX.nameField.test(i.label));
  const primary = info.controls.filter((c) => c.tag !== "a" || c.role === "button");
  const hasSignInBtn = primary.some((c) => RX.signInBtn.test(c.name));
  const hasCreateBtn = primary.some((c) => RX.createBtn.test(c.name));
  const hasForgot = info.controls.some((c) => RX.forgot.test(c.name));
  const hasCont = primary.some((c) => RX.cont.test(c.name));
  const text = info.text || "";
  if (!pw.length) {
    if (RX.mfa.test(text) && (codeInputs.length || RX.codePrompt.test(text))) return "mfa";
    if (RX.resetSent.test(text)) return "reset_sent";
    if (RX.verifyEmail.test(text) && !textInputs.length && !emailInputs.length) return "verify_email";
  }
  const resetHead = info.heads.some((h) => RX.resetHeading.test(h)) || pw.some((p) => /^(new|confirm new)\b/i.test(p.label));
  if (pw.length >= 1 && resetHead && !hasSignInBtn && !emailInputs.length) return "reset_form";
  if (pw.length >= 2) return "create";
  if (pw.length === 1) {
    if (nameFields.length) return "create";
    if (hasForgot || hasSignInBtn) return "signin_password";
    if (hasCreateBtn) return "create";
    return "signin_password";
  }
  if (emailInputs.length && nameFields.length) return "create";
  if (emailInputs.length && !textInputs.length && hasCont) return "signin_email";
  // Provider chooser (e.g. Workday "Sign in with Google" + "Sign in with email", no inputs yet): the only
  // control we may press is the first-party email/password one. Social providers stay refused by FORBIDDEN_CLICK.
  if (!pw.length && !emailInputs.length && primary.some((c) => RX.emailMethod.test(c.name))) return "choose_method";
  return "none";
}

/** Error/notice flags from visible text. Booleans only. */
function noticesOf(text) {
  const t = String(text || "");
  return { exists: RX.exists.test(t), bad: RX.bad.test(t), locked: RX.locked.test(t), notFound: RX.notFound.test(t) };
}

/** 'signin_email'|'signin_password'|'create'|'verify_email'|'reset_sent'|'reset_form'|'mfa'|'captcha'|'none' */
async function detectAccountState(tab) { return classifyAccountPage(await inspectPage(tab)); }

// ---- secrets -----------------------------------------------------------------------------------------
function redactResult(r) {
  if (r == null) return r;
  const strip = (v) => Array.isArray(v) ? v.map(strip)
    : v && typeof v === "object" ? Object.fromEntries(Object.entries(v).filter(([k]) => !/^(password|passwd|pwd|secret|new_?password)$/i.test(k)).map(([k, x]) => [k, strip(x)]))
      : v;
  return credentials.redact(strip(r));
}

const scrub = (msg, secret) => credentials.redact(String(msg == null ? "" : msg).split(secret).join("[REDACTED]")).slice(0, 200);

/**
 * Type `secret` into one password field with real key events and verify the field took it.
 * target: CSS selector string | {selector} | {id} (an observe() element). The read-back only COMPARES; the value is
 * never returned, logged or put in an error. Throws Blocked('account_flow_failed') if the field did not accept it.
 */
async function fillCredential(ctx, target, secret) {
  if (!secret) throw new Error("fillCredential: empty secret");
  const tab = ctx.tab;
  const selector = typeof target === "string" ? target : target && target.selector;
  const h = selector ? null : tab.id(target.id);
  const act = selector
    ? { click: () => tab.click(selector), clear: () => tab.fill(selector, ""), type: (s) => tab.type(selector, s), fill: (s) => tab.fill(selector, s), read: () => evalIn(tab, (q) => { const e = document.querySelector(q); return e ? e.value : null; }, selector) }
    : { click: () => h.click(), clear: () => h.fill(""), type: (s) => h.type(s), fill: (s) => h.fill(s), read: () => h.value() };
  try {
    await act.click();
    await act.clear();
    await act.type(secret);
    let got = await act.read();
    if (!(typeof got === "string" && got.length > 0 && got === secret)) {
      await act.clear();
      await act.fill(secret);
      got = await act.read();
    }
    const ok = typeof got === "string" && got.length > 0 && got === secret;
    got = null;
    if (!ok) throw new Blocked("account_flow_failed", "a password field did not accept the typed value");
    return true;
  } catch (e) {
    if (e && e.name === "Blocked") throw e;
    throw new Error(`credential field error: ${scrub(e && e.message, secret)}`);
  }
}

// ---- gate + controls ---------------------------------------------------------------------------------
class Stop extends Error {
  constructor(reason, evidence, extra) { super(reason); this.name = "Stop"; this.reason = reason; this.evidence = evidence; this.extra = extra || {}; }
}

const authorised = (ctx) => ctx.dryRun === false && !!ctx.opts && ctx.opts.allowAccountCreation === true;
function assertAuthorised(ctx) {
  if (!authorised(ctx)) throw new Stop("account_required", "account actions require dryRun:false and opts.allowAccountCreation:true");
}

/** The only way account.js presses Create/Register/Forgot/Sign-in/submit controls. */
async function gatedClick(ctx, el) {
  assertAuthorised(ctx);
  if (FORBIDDEN_CLICK.test(el.name || "")) throw new Blocked("forbidden_control", `refused to click "${el.name}"`);
  await clickEl(ctx.tab, el);
}

const CONTROL = {
  identify: [/^(continue|next|sign ?in|log ?in|submit|get started)\b(?!.*\bwith\b)/i],
  signin: [/^(sign|log) ?in\b(?!.*\bwith\b)/i, /^(continue|next|submit)\b/i],
  create: [/^(create( an?| your)? (account|profile)|register|sign ?up(?!.*\bwith\b)|join( now)?)\b/i, /^(submit|continue|next|get started)\b/i],
  reset: [/(send|e-?mail|request|get).{0,25}(reset|link|instructions)|^reset( password)?$/i, /^(submit|continue|next)\b/i],
  newpw: [/^(reset|set|save|update|change|confirm|submit|continue)\b/i],
  verify: [/^(verify|confirm|continue|submit|next)\b/i],
};
const LINK = {
  forgot: /forgot|reset (your )?password|can'?t (sign|log) ?in|trouble (signing|logging)/i,
  create: /^(create( an?)? (account|profile)|register|sign ?up(?!.*\bwith\b)|new (user|here))/i,
  signin: /^(sign|log) ?in\b(?!.*\bwith\b)/i,
};

async function pressControl(ctx, kind) {
  const els = await observe(ctx.tab);
  for (const rx of CONTROL[kind]) {
    const el = els.find((e) => e.role === "button" && rx.test(e.name || "") && !FORBIDDEN_CLICK.test(e.name || ""));
    if (el) { await gatedClick(ctx, el); return el.name; }
  }
  assertAuthorised(ctx);
  await ctx.tab.press("input:focus", "Enter").catch(() => {});
  return "Enter";
}

async function clickLink(ctx, rx) {
  const els = await observe(ctx.tab);
  const el = els.find((e) => (e.role === "link" || e.role === "button") && rx.test(e.name || "") && !FORBIDDEN_CLICK.test(e.name || ""));
  if (!el) return null;
  await gatedClick(ctx, el);
  return el.name;
}

const fingerprint = (tab) => evalIn(tab, () => `${location.href}|${(document.body ? document.body.innerText : "").length}|${(document.body ? document.body.innerText : "").slice(0, 300)}`).catch(() => "");
async function settle(ctx, before) {
  await waitUntil(async () => (await fingerprint(ctx.tab)) !== before, { timeout: 7000, every: 250 });
  await sleep(300);
}

const flat = (s) => String(s || "").toLowerCase().replace(/\s+/g, " ").trim();

// ---- generic field helpers ---------------------------------------------------------------------------
async function genericFillEmail(ctx, email) {
  const fields = (await scanForm(ctx.tab)).filter((f) => ["email", "text"].includes(f.kind) && (f.type === "email" || RX.emailField.test(`${f.label} ${f.name} ${f.autocomplete}`)));
  for (const f of fields) {
    if (flat(f.value) === flat(email)) continue;
    await ctx.tab.fill(f.selector, email);
  }
  return fields.length;
}

async function hasEmailField(ctx) {
  const fields = await scanForm(ctx.tab);
  return fields.some((f) => ["email", "text"].includes(f.kind) && (f.type === "email" || RX.emailField.test(`${f.label} ${f.name} ${f.autocomplete}`)));
}

async function genericFillPassword(ctx, pw) {
  const fields = (await scanForm(ctx.tab)).filter((f) => f.kind === "password");
  if (!fields.length) throw new Stop("account_flow_failed", "no password field on the page");
  for (const f of fields) await fillCredential(ctx, f.selector, pw);
  return fields.length;
}

/** Everything except passwords, through the answers.js allowlist. Pre-checked marketing/alert boxes are unticked. */
async function genericFillNonPassword(ctx) {
  const fields = await scanForm(ctx.tab);
  for (const f of fields) {
    if (f.kind !== "checkbox" || !f.checked) continue;
    if (classifyConsent(`${f.group || ""} ${f.label || ""}`, ctx.profile).verdict !== "allow") await ctx.tab.uncheck(f.selector).catch(() => {});
  }
  return fillForm(ctx, { only: (f) => f.kind !== "password" });
}

// ---- main --------------------------------------------------------------------------------------------
/**
 * @returns {Promise<{ready:true,status:'continue',action,account,extras}|{ready:false,status:'manual',result}>}
 */
async function ensureAccount(ctx, o = {}) {
  const hooks = o.signInFlow || {};
  const adapter = o.adapterName || "adapter";
  const email = (ctx.P && ctx.P.email) || null;
  const siteHost = ctx.site || hostOf(ctx.url) || "";
  const site = credentials.siteKey(siteHost);
  ctx.consents = ctx.consents || [];
  const caps = ((ctx.accountCaps = ctx.accountCaps || {})[site] = ctx.accountCaps[site] || { create: 0, reset: 0, login: 0, verify: 0 });
  const root = ctx.rootTab || ctx.tab;
  const log = (event, extra) => { try { ctx.log && ctx.log(event, extra); } catch { /* ignore */ } };
  const manual = (reason, evidence, extra) => ({
    ready: false, status: "manual",
    result: redactResult(result("manual", reason, { evidence: String(evidence == null ? "" : evidence).slice(0, 400), account: { site, email, action: "none" }, ...(extra || {}) })),
  });
  const H = async (name, ...args) => { if (typeof hooks[name] === "function") { await hooks[name](ctx, ...args); return true; } return false; };

  try {
    if (!email) return manual("account_flow_failed", "profile has no candidate email for the account");
    const stateNow = async () => {
      const ch = await detectChallenge(ctx.tab).catch(() => null);
      if (ch) return "captcha";
      let s = o.detect ? await o.detect(ctx) : "none";
      if (!s || s === "none") s = await detectAccountState(ctx.tab);
      return s;
    };
    const textNow = async () => (await inspectPage(ctx.tab)).text;
    const guard = (s) => {
      if (s === "captcha") throw new Stop("captcha_required", "captcha / bot check on the account page (not solved; human required)");
      if (s === "mfa") throw new Stop("mfa_required", "SMS / phone / app multi-factor verification is required (human required)");
    };
    const mailboxReq = () => ({ token: ctx.token || site, site: siteHost, jobId: jobKey(ctx.job) });
    const mailbox = (what) => {
      const mb = ctx.opts && ctx.opts.mailbox;
      if (!mb || typeof mb.markTriggered !== "function") throw new Stop("account_required", `${what} needs the authorized mailbox helper (opts.mailbox: MailboxLinkProvider)`);
      return mb;
    };
    const trusted = (link, what) => {
      const v = link && validateLink(link, siteHost, { allowLoopbackHttp: !!(ctx.opts && ctx.opts.allowLoopbackHttp) });
      if (!v) throw new Stop("account_flow_failed", `untrusted_link: the ${what} link failed the https/same-site check and was not opened`);
      return v;
    };
    const success = (action) => {
      const account = { site, email, action };
      log("account", account);
      ctx.account = account;
      return { ready: true, status: "continue", action, account, extras: { account } };
    };

    let s = await stateNow();
    guard(s);
    const saved0 = credentials.get(site, email);

    // ---- gate: dry run / not authorized does nothing at all
    if (!authorised(ctx)) {
      const extra = {};
      if (["signin_email", "signin_password", "create", "choose_method"].includes(s)) {
        if (saved0) extra.wouldLogin = true;
        else if (s === "create") extra.wouldCreateAccount = true;
        else extra.wouldResetPassword = true;
      }
      const why = extra.wouldCreateAccount ? "a new employer-site account would be created for the candidate email"
        : extra.wouldResetPassword ? "the site's password would be reset for the candidate email (no saved password)"
          : extra.wouldLogin ? "the saved credential would be used to sign in"
            : "account step not recognized";
      return manual("account_required", `${adapter}: ${why}; not performed (${ctx.dryRun === false ? "opts.allowAccountCreation is not true" : "dry run"})`, extra);
    }

    let emailSubmitted = false;
    let unusedPassword = false; // a freshly reserved password the site never accepted (account already existed)
    const login = async (pw) => {
      if (caps.login >= 2) throw new Stop("account_flow_failed", "login attempt cap (2) reached for this site");
      caps.login++;
      let st = await stateNow();
      guard(st);
      if (st === "signin_email") {
        const before = await fingerprint(ctx.tab);
        if (!(await H("fillEmail", email))) await genericFillEmail(ctx, email);
        if (!(await H("submit", "identify"))) await pressControl(ctx, "identify");
        await settle(ctx, before);
        st = await stateNow();
        guard(st);
      }
      if (st !== "signin_password") throw new Stop("account_flow_failed", `expected a password prompt but the page is "${st}"`);
      const before = await fingerprint(ctx.tab);
      if (!(await H("fillEmail", email))) await genericFillEmail(ctx, email);
      if (!(await H("fillPassword", pw))) await genericFillPassword(ctx, pw);
      if (!(await H("submit", "signin"))) await pressControl(ctx, "signin");
      await settle(ctx, before);
      st = await stateNow();
      guard(st);
      const n = noticesOf(await textNow());
      if (n.locked) throw new Stop("account_flow_failed", "lockout: the site reports the account is locked or rate limited");
      if (["signin_password", "signin_email", "create"].includes(st) || n.bad) return false;
      return true;
    };

    const resetFlow = async () => {
      if (caps.reset >= 1) throw new Stop("account_flow_failed", "login failed and the one password reset request for this run was already used");
      const mb = mailbox("password reset");
      caps.reset++;
      mb.markTriggered(mailboxReq(), "reset");
      let before = await fingerprint(ctx.tab);
      if (!(await H("clickForgot"))) {
        if (!(await clickLink(ctx, LINK.forgot))) throw new Stop("account_flow_failed", "no Forgot password control found");
      }
      await settle(ctx, before);
      guard(await stateNow());
      if (await hasEmailField(ctx)) {
        before = await fingerprint(ctx.tab);
        if (!(await H("fillEmail", email))) await genericFillEmail(ctx, email);
        if (!(await H("submit", "reset"))) await pressControl(ctx, "reset");
        await settle(ctx, before);
      }
      guard(await stateNow());
      const n = noticesOf(await textNow());
      if (n.locked) throw new Stop("account_flow_failed", "lockout: the site reports the account is locked or rate limited");
      if (n.notFound) return "no_account";
      if (n.bad) throw new Stop("account_flow_failed", `password reset request rejected: ${await snippet(ctx.tab, 160)}`);
      const link = await mb.getResetLink(mailboxReq());
      if (!link) throw new Stop("verification_timeout", "no password-reset email arrived (90s wait) or it failed the sender/subject/freshness gates");
      const url = trusted(link, "password-reset");
      await root.goto(url);
      await sleep(300);
      let st = await stateNow();
      guard(st);
      if (st !== "reset_form") {
        const fields = await scanForm(ctx.tab);
        if (!fields.some((f) => f.kind === "password")) throw new Stop("account_flow_failed", "the reset link did not open a new-password form");
      }
      const newPw = credentials.reserveNew(site, email, "reset"); // persisted BEFORE it is submitted
      before = await fingerprint(ctx.tab);
      if (!(await H("submitNewPassword", newPw))) {
        await genericFillPassword(ctx, newPw);
        await pressControl(ctx, "newpw");
      }
      await settle(ctx, before);
      st = await stateNow();
      guard(st);
      const n2 = noticesOf(await textNow());
      if (st === "reset_form" || n2.bad) throw new Stop("account_flow_failed", "the site did not accept the new password (reset form still open)");
      if (!["signin_email", "signin_password"].includes(st)) {
        const b2 = await fingerprint(ctx.tab);
        if (await clickLink(ctx, LINK.signin)) { await settle(ctx, b2); st = await stateNow(); guard(st); }
      }
      if (["signin_email", "signin_password"].includes(st)) {
        if (!(await login(newPw))) throw new Stop("account_flow_failed", "signing in with the reset password did not succeed");
      }
      return "reset";
    };

    const verification = async (pw, info) => {
      if (caps.verify >= 1) throw new Stop("account_flow_failed", "verification wait cap (1) reached for this site");
      caps.verify++;
      const mb = mailbox("email verification");
      const hasCode = info.inputs.some((i) => i.type !== "password" && (i.ac === "one-time-code" || /code|otp/i.test(`${i.label} ${i.name}`)));
      let before = await fingerprint(ctx.tab);
      if (hasCode) {
        const code = await mb.getCode(mailboxReq());
        if (!code) throw new Stop("verification_timeout", "no verification-code email arrived (90s wait) or it failed the gates");
        if (!(await enterCode(ctx.tab, code))) throw new Stop("account_flow_failed", "could not find the verification code input");
        if (!(await H("submit", "verify"))) await pressControl(ctx, "verify");
      } else {
        const link = await mb.getVerificationLink(mailboxReq());
        if (!link) throw new Stop("verification_timeout", "no verification email arrived (90s wait) or it failed the sender/subject/freshness gates");
        await root.goto(trusted(link, "verification"));
      }
      await settle(ctx, before);
      let st = await stateNow();
      guard(st);
      if (["signin_email", "signin_password"].includes(st)) {
        if (!(await login(pw))) throw new Stop("account_flow_failed", "signing in after email verification did not succeed");
      } else if (st === "create" || st === "verify_email") {
        throw new Stop("account_flow_failed", `verification did not complete; page is "${st}"`);
      }
    };

    const createFlow = async () => {
      if (caps.create >= 1) throw new Stop("account_flow_failed", "account creation attempt cap (1) reached for this site");
      const mb = mailbox("account creation");
      if (credentials.get(site, email) && !unusedPassword) {
        // an account we already hold credentials for: never overwrite its saved password; sign in instead
        const b = await fingerprint(ctx.tab);
        if (!(await clickLink(ctx, LINK.signin))) throw new Stop("account_flow_failed", "saved credential exists for this site but no Sign in link was found on the create page");
        await settle(ctx, b);
        return "again";
      }
      if (await hasEmailField(ctx)) { await H("fillEmail", email); }
      let rep = null;
      if (typeof hooks.fillNames === "function") rep = await hooks.fillNames(ctx);
      else rep = await genericFillNonPassword(ctx);
      if (rep && ((rep.unanswered && rep.unanswered.length) || (rep.unverified && rep.unverified.length))) {
        const q = (rep.unanswered || [])[0];
        const v = (rep.unverified || [])[0];
        throw new Stop("account_flow_failed", `unknown required field: ${q ? q.question : `could not verify "${v.label}"`}`, { unanswered: (rep.unanswered || []).map((x) => x.question) });
      }
      caps.create++;
      const pw = credentials.reserveNew(site, email, "created"); // persisted BEFORE it is typed anywhere
      const before = await fingerprint(ctx.tab);
      if (!(await H("fillPassword", pw))) await genericFillPassword(ctx, pw);
      mb.markTriggered(mailboxReq(), "verify");
      mb.markTriggered(mailboxReq(), "code");
      if (!(await H("submit", "create"))) await pressControl(ctx, "create");
      await settle(ctx, before);
      const st = await stateNow();
      guard(st);
      const info = await inspectPage(ctx.tab);
      const n = noticesOf(info.text);
      if (n.exists) { unusedPassword = true; return "exists"; }
      if (n.locked || n.bad) throw new Stop("account_flow_failed", `account creation was rejected: ${await snippet(ctx.tab, 160)}`);
      if (st === "verify_email") { await verification(pw, info); return "created"; }
      if (st === "create") throw new Stop("account_flow_failed", `the create-account form did not accept the submission: ${await snippet(ctx.tab, 160)}`);
      if (["signin_email", "signin_password"].includes(st)) {
        if (!(await login(pw))) throw new Stop("account_flow_failed", "signing in with the new account did not succeed");
      }
      return "created";
    };

    // ---- state machine (bounded: every branch that acts is capped, so it cannot loop)
    let methodChosen = false;
    for (let hop = 0; hop < 7; hop++) {
      s = hop === 0 ? s : await stateNow();
      guard(s);
      if (s === "choose_method") {
        if (methodChosen) throw new Stop("account_flow_failed", "the sign-in method chooser did not advance after choosing a method");
        methodChosen = true; // one press per run: cannot loop
        // Preferred when enabled: her own Google account (Chrome is already signed in). No password is created or stored.
        if (ctx.opts && ctx.opts.allowGoogleSignIn === true) {
          const employerOrigin = new URL(await ctx.tab.url()).origin;
          const g = await googleSignIn.signInWithGoogle(ctx, { email });
          if (g.ok) {
            const after = await stateNow();
            guard(after);
            if (["choose_method", "signin_email", "signin_password", "create"].includes(after)) {
              log("google_not_signed_in", { site });
            } else {
              return success("google");
            }
          } else if (["captcha_required", "mfa_required"].includes(g.reason)) {
            throw new Stop(g.reason, g.evidence);
          } else {
            log("google_failed", { site, reason: g.reason });
            // A failed Google hop may still be on accounts.google.com. Never search it for employer controls
            // (or replace the actual Google stop with a misleading "no first-party email option" error).
            if (new URL(await ctx.tab.url()).origin !== employerOrigin) throw new Stop(g.reason || "account_flow_failed", g.evidence || "Google sign-in did not return to the employer site");
          }
        }
        const before = await fingerprint(ctx.tab);
        const pressed = typeof hooks.chooseEmail === "function" ? await hooks.chooseEmail(ctx) : await clickLink(ctx, RX.emailMethod);
        if (!pressed) throw new Stop("account_flow_failed", "sign-in method chooser has no first-party email option");
        await settle(ctx, before);
        continue;
      }
      if (s === "signin_email") {
        if (!emailSubmitted) {
          emailSubmitted = true;
          const before = await fingerprint(ctx.tab);
          if (!(await H("fillEmail", email))) await genericFillEmail(ctx, email);
          if (!(await H("submit", "identify"))) await pressControl(ctx, "identify");
          await settle(ctx, before);
          continue;
        }
        if (noticesOf(await textNow()).notFound) {
          const before = await fingerprint(ctx.tab);
          if (!(await H("clickCreate")) && !(await clickLink(ctx, LINK.create))) throw new Stop("account_flow_failed", "site reports no account and no Create account control was found");
          await settle(ctx, before);
          continue;
        }
        throw new Stop("account_flow_failed", "the email step did not advance to a password or create-account page");
      }
      if (s === "signin_password") {
        const cur = credentials.get(site, email);
        if (cur && !unusedPassword) {
          if (await login(cur.password)) return success("login");
          // A password the USER stored (origin "user") that the site rejects is never auto-reset: a reset would overwrite it
          // and send another email (and can trigger a captcha). Stop so she can correct it.
          if (cur.origin === "user") throw new Stop("account_flow_failed", "the password she stored for this site was rejected; not requesting a reset (it would overwrite her saved password). Ask her to re-check it and run scripts/store_credential.py again");
        }
        const r = await resetFlow();
        if (r === "no_account") {
          const before = await fingerprint(ctx.tab);
          if (!(await H("clickCreate")) && !(await clickLink(ctx, LINK.create))) throw new Stop("account_flow_failed", "site reports no account for this email and no Create account control was found");
          await settle(ctx, before);
          continue;
        }
        return success("reset");
      }
      if (s === "create") {
        const r = await createFlow();
        if (r === "created") return success("created");
        if (r === "exists") {
          const before = await fingerprint(ctx.tab);
          if (!(await H("clickSignIn")) && !(await clickLink(ctx, LINK.signin))) throw new Stop("account_flow_failed", "the site says the account exists but no Sign in link was found");
          await settle(ctx, before);
        }
        continue;
      }
      throw new Stop("account_flow_failed", `unexpected page state "${s}" for the account step`);
    }
    throw new Stop("account_flow_failed", "account step did not settle within the allowed number of page transitions");
  } catch (e) {
    if (e && e.name === "Stop") return manual(e.reason, e.evidence, e.extra);
    if (e && e.name === "Blocked") return manual(e.reason, e.evidence, e.extra);
    if (e && e.name === "NeedsHuman") return manual("account_flow_failed", `unknown required field: ${e.question}`, { unanswered: [e.question] });
    if (e && typeof e.message === "string") e.message = credentials.redact(e.message);
    throw e;
  }
}

module.exports = {
  ensureAccount, detectAccountState, classifyAccountPage, inspectPage, noticesOf, fillCredential, redactResult, gatedClick,
  genericFillEmail, genericFillPassword, genericFillNonPassword,
};
