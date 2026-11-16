// iCIMS candidate experience (*.icims.com: careers-*, jobs-*, <tenant>.icims.com).
// Observed layout: the career site wraps the whole application in iframe#icims_content_iframe
// (inside div#iCIMS_MainWrapper); the framed pages are ordinary navigations (job -> login -> multi-page
// application -> review -> submit -> "Thank you for applying").
//
// Flow (a STATE MACHINE keyed off what is on screen, so it can start on any step):
//   job page -> "Apply for this job online" -> login/register screen
//     * authenticated session already present (Sign Out link)      -> straight to the application pages
//     * email-only screen: type the profile email, press Next/Continue (never Create/Register) and re-read
//     * password page (existing account)                           -> manual / password_required
//     * create-profile screen / choice wall / password+confirm     -> manual / account_required
//   application pages -> fillForm (answers.js allowlist) -> Next / Save & Continue ... -> review -> Submit.
//
// Account walls (password page, create-profile page): by default (dry run, or live without
// opts.allowAccountCreation) NOTHING is typed or clicked past the email step -> manual password_required |
// account_required with wouldResetPassword / wouldCreateAccount / wouldLoginWithSavedCredentials extras.
// ONLY when ctx.dryRun === false AND ctx.opts.allowAccountCreation === true is account.js#ensureAccount called
// (authorized creation / saved-credential login / mailbox-link password reset for the candidate's own email);
// passwords come from credentials.js and are typed through account.fillCredential, never logged or returned.
// The choice wall (Log In / Create a Profile buttons, no email step) stays manual. Never solves a captcha or
// clicks social sign-in / talent-network controls (lib.js clickEl refuses). An emailed code requested by the
// site's own email step is reported as manual/otp_required and never read or entered.
"use strict";
const { sleep, observe, matchEl, clickEl, visibleText, detectChallenge, evalIn, result, snippet, CONFIRMATION, waitUntil, Blocked } = require("../lib");
const { deriveProfile, NeedsHuman, STATE_NAMES } = require("../answers");
const { scanForm, fillForm } = require("../form");
const F = require("../flow");

const HOST = /(^|\.)icims\.com$/i;

const RE = {
  already: /you('ve| have) (already|previously) (applied|submitted)|already (applied|submitted) (to|for) this/i,
  closed: /no longer (available|accepting)|(job|position|posting|requisition) (is |has been )?(no longer|not) (available|found|open)|(job|position|posting) (has been )?(closed|filled|expired)|posting has expired|this job is not available/i,
  apply: /^apply( now| for this (job|position|role))?( online)?$|^apply for (this )?(job|position|role) online$/i,
  loginText: /(log ?in|sign ?in) (to|or create|or register)|sign in or|returning (candidate|user)|candidate login|create (a |your |an )?(new )?(profile|account)|new (user|candidate)|\bregister\b|already have an account|forgot (your )?password|log in or/i,
  emailStepText: /log ?in|sign ?in|account|profile|returning|new candidate|continue|enter your (information|e-?mail)|e-?mail \(primary\)/i,
  registerHead: /create (a |your |an )?(new )?(profile|account|password)|\bregister\b|sign up|new (user|candidate)/i,
  loginHead: /log ?in|sign ?in|welcome back/i,
  confirmPw: /confirm|re-?enter|retype|repeat|verify/i,
  signOut: /^(sign out|log ?out|logout)$/i,
  emailNext: /^(next|continue|proceed)$/i,
};

// ---- the frame ---------------------------------------------------------------------------------------
const TRANSIENT = /execution context|context was destroyed|detached|navigating|target closed|frame was|no frame/i;

/**
 * A tab-shaped scope that follows the application wherever it currently lives: iframe#icims_content_iframe
 * when the page has one, else the page itself. It re-resolves on EVERY call, so in-frame navigations and
 * a wrapper that drops the iframe never leave a stale handle. observe()/id() go through the main tab
 * (it reports framed controls too); everything addressed by CSS selector runs inside the frame.
 */
function scopeOf(tab) {
  const locate = async () => {
    if (typeof tab.frames !== "function") return null;
    const kids = ((await tab.frames()) || []).filter((f) => f.parentId);
    return kids.find((f) => /icims_content_iframe/i.test(`${f.name || ""} ${f.selector || ""}`))
      || kids.find((f) => /\/\/[^/]*\bicims\.com\b/i.test(f.url || "") || /[?&]in_iframe=1/.test(f.url || ""))
      || null;
  };
  const target = async () => {
    const e = await locate();
    if (!e) return { t: tab, e: null };
    return { t: await tab.frame(e.selector || e.name || e.url), e };
  };
  const call = async (fn) => {
    let err;
    for (let i = 0; i < 4; i++) {
      try { const { t, e } = await target(); return await fn(t, e); } catch (x) {
        err = x;
        if (!TRANSIENT.test(String(x && x.message))) throw x;
        await sleep(400);
      }
    }
    throw err;
  };
  // select / uploadFile are not on the frame handle: use the frame's own puppeteer object
  const viaFrame = (e, op, sel, args) => tab.run(async ({ page }, a) => {
    const f = page.frames().find((x) => (a.name && x.name() === a.name) || x.url() === a.url);
    if (!f) throw new Error("frame not found");
    if (a.op === "select") return await f.select(a.sel, ...a.args);
    const h = await f.$(a.sel);
    if (!h) throw new Error(`element not found: ${a.sel}`);
    await h.uploadFile(...a.args);
    return true;
  }, { args: [{ name: e.name || "", url: e.url, op, sel, args }] });

  const scope = {
    evaluate: (s) => call((t) => t.evaluate(s)),
    click: (...a) => call((t) => t.click(...a)),
    fill: (...a) => call((t) => t.fill(...a)),
    type: (...a) => call((t) => t.type(...a)),
    press: (...a) => call((t) => t.press(...a)),
    select: (sel, ...vals) => call((t, e) => (e ? viaFrame(e, "select", sel, vals) : t.select(sel, ...vals))),
    uploadFile: (sel, ...paths) => call((t, e) => (e ? viaFrame(e, "upload", sel, paths) : t.uploadFile(sel, ...paths))),
    isChecked: (sel) => call((t) => t.evaluate(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); return e ? !!e.checked : null; })()`)),
    observe: (...a) => tab.observe(...a),
    id: (n) => tab.id(n),
    screenshot: (...a) => tab.screenshot(...a),
    url: () => tab.url(),
    /** {framed, drivable}: is there an iframe, and can we execute inside it? */
    async probe() {
      let e;
      try { e = await locate(); } catch { return { framed: false, drivable: true }; }
      if (!e) return { framed: false, drivable: true };
      try { return { framed: true, drivable: (await (await tab.frame(e.selector || e.name || e.url)).evaluate("1")) === 1, src: e.url }; } catch (x) { return { framed: true, drivable: false, src: e.url, error: x.message }; }
    },
  };
  // checkbox / radio: a real click on the control (form.js falls back to the label / DOM click if hidden)
  scope.check = async (sel) => { if ((await scope.isChecked(sel)) !== true) await scope.click(sel); };
  scope.uncheck = async (sel) => { if ((await scope.isChecked(sel)) === true) await scope.click(sel); };
  return scope;
}

// ---- state detection --------------------------------------------------------------------------------
async function headingOf(tab) {
  return (await evalIn(tab, () => {
    const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(e).visibility !== "hidden"; };
    return [...document.querySelectorAll("h1, h2, h3, legend, [role=heading]")].filter(vis).map((e) => (e.innerText || "").replace(/\s+/g, " ").trim()).filter(Boolean).slice(0, 4).join(" | ").slice(0, 240);
  })) || "";
}

// Only a real text-entry box can be "the email field". A checkbox / radio / select whose LABEL mentions e-mail (e.g. "Email me job alerts
// and marketing") must never match: the account hook would otherwise click/clear/type into a consent control.
const TEXT_KINDS = new Set(["email", "text", "textarea", "tel", "url", ""]);
const isEmailField = (f) => TEXT_KINDS.has(String(f.kind || "")) && (f.kind === "email" || /e-?mail/i.test(`${f.label} ${f.placeholder} ${f.name}`));

async function detectState(ctx) {
  const tab = ctx.tab;
  const body = await visibleText(tab);
  const els = await observe(tab);
  const btn = (name) => matchEl(els, { roles: ["button", "link"], name });
  const authed = els.some((e) => (e.role === "link" || e.role === "button") && RE.signOut.test((e.name || "").trim()));
  const base = { text: body, els, authed };

  if (RE.already.test(body)) return { s: "already_applied", ...base };
  const apply = !ctx.submitted && btn(RE.apply);
  if (apply) return { s: "job", apply, ...base };
  if (RE.closed.test(body)) return { s: "closed", ...base };

  const fields = await scanForm(tab);
  const final = matchEl(els, { roles: ["button", "link"], name: F.FINAL_BTN });
  const next = matchEl(els, { roles: ["button", "link"], name: F.NEXT_BTN });
  // A thank-you banner on an unfinished Source/application form is not a receipt.
  if (CONFIRMATION.test(body) && !fields.length && !final && !next) return { s: "done", ...base };
  const pw = fields.filter((f) => f.kind === "password");
  if (pw.length) {
    const heading = await headingOf(tab);
    // a signed-in candidate's profile page shows the stored login + (pre-filled) password pair: that is an application form, not registration
    const register = !authed && (pw.length >= 2
      || pw.some((f) => RE.confirmPw.test(f.label || ""))
      || (fields.some((f) => /^first( name)?$/i.test(f.label)) && fields.some((f) => /^last( name)?$/i.test(f.label)))
      || (RE.registerHead.test(heading) && !RE.loginHead.test(heading)));
    if (authed) return { s: "form", fields, ...base };
    return { s: register ? "register" : "password", heading, ...base };
  }
  const heading = await headingOf(tab);
  const progress = heading.match(/\bstep\s+(\d+)\s+of\s+(\d+)\b/i);
  const intermediate = !!progress && Number(progress[1]) > 0 && Number(progress[1]) < Number(progress[2]);
  const source = /(?:^|\|\s*)source(?: attribution)?(?:\s*\||$)/i.test(heading)
    || (fields.some((f) => /^source\s*\*?$/i.test(f.label || ""))
      && fields.some((f) => /^source (channel|name|email|phone)\b/i.test(f.label || "")));
  if (!authed && fields.length === 1 && isEmailField(fields[0]) && RE.emailStepText.test(body)) return { s: "email_step", field: fields[0], ...base };
  if (!authed && !final && !next && !fields.length && RE.loginText.test(body)) return { s: "wall", heading: await headingOf(tab), ...base };
  if (fields.length || final || next) return { s: "form", fields, heading, intermediate, source, ...base };
  return { s: "unknown", ...base };
}

async function waitChange(ctx, before, timeout) {
  // changed AND rendered: a navigation passes through a blank document first
  return waitUntil(async () => { try { const fp = await F.fingerprint(ctx.tab); return fp !== before && !/:0$/.test(fp); } catch { return false; } }, { timeout, every: 500 });
}

// ---- authorized account flow (account.js#ensureAccount) ---------------------------------------------------
// Only a live run with opts.allowAccountCreation reaches this. Hooks drive the framed login pages; passwords are
// only typed through account.fillCredential and never enter a log, result or evidence string.
const account = () => require("../account");
const accountAllowed = (ctx) => ctx.dryRun === false && !!ctx.opts && ctx.opts.allowAccountCreation === true;
function gate(ctx) {
  if (!accountAllowed(ctx)) throw new Blocked("account_flow_failed", "account control refused: this run is not authorized to create or reset accounts");
}
const RESET_TEXT = /reset (your )?password|set (a )?new password|choose (a )?new password|create (a )?new password/i;

/** account.js state vocabulary for the page in front of us. */
async function detectAccount(ctx) {
  if ((await detectChallenge(ctx.tab)) || (ctx.rootTab && (await detectChallenge(ctx.rootTab)))) return "captcha";
  const st = await detectState(ctx);
  const text = st.text || "";
  if (/(enter|type) (the |your )?(6-digit |verification |security |one-time )?code|authenticator app|(sent|texted) (you )?(a )?(code|text message)/i.test(text)) return "mfa";
  if (/verify (your )?(email|account)|check your (email|inbox)|confirmation email|email (has been|was) sent|we('ve| have) sent/i.test(text)) return RESET_TEXT.test(text) && !/set (a )?new password|choose (a )?new password/i.test(text) ? "reset_sent" : "verify_email";
  if (st.s === "password" || st.s === "register") {
    if (RESET_TEXT.test(`${st.heading || ""} ${text}`) && st.s === "register" && /new password/i.test(text)) return "reset_form"; // reached through the emailed link
    return st.s === "password" ? "signin_password" : "create";
  }
  if (st.s === "email_step") return "signin_email";
  // application pages (and the job/closing pages): signed in, so account.js must not run its generic classifier on them
  // (a contact-information step looks like a create-account form to it)
  if (["form", "job", "done", "already_applied", "closed"].includes(st.s)) return "authenticated";
  return "none";
}

async function acctFields(ctx) { return scanForm(ctx.tab); }

/** Fill the non-password fields of the current account page from the answers.js allowlist; unknown required fields stop the run. */
async function fillAllowlisted(ctx, only) {
  const rep = await fillForm(ctx, { only });
  F.merge(ctx, rep);
  const blocked = F.submittable(rep);
  if (blocked) throw new NeedsHuman((rep.unanswered[0] || {}).question || "account form field", blocked);
}

const ACCOUNT_HOOKS = {
  async fillEmail(ctx) {
    gate(ctx);
    for (const f of (await acctFields(ctx)).filter((x) => x.kind !== "password" && isEmailField(x))) {
      if (!(await typeEmail(ctx, f))) throw new Blocked("account_flow_failed", "the email box did not hold the typed address");
    }
  },
  /** Everything except passwords through the answers.js allowlist; pre-ticked marketing / alert boxes are unticked by account.js. */
  async fillNames(ctx) {
    gate(ctx);
    return account().genericFillNonPassword(ctx);
  },
  /** Every visible password field (password + confirm, or the new-password pair of a reset form). */
  async fillPassword(ctx, pw) {
    gate(ctx);
    const fields = (await acctFields(ctx)).filter((f) => f.kind === "password");
    if (!fields.length) throw new Blocked("account_flow_failed", "no password field on the page");
    for (const f of fields) await account().fillCredential(ctx, f, pw);
  },
  async submit(ctx) {
    gate(ctx);
    const btn = matchEl(await observe(ctx.tab), { roles: ["button", "link"], name: /^(log ?in|sign ?in|create( my)?( a)?( new)? (profile|account)|register|submit|continue|next|reset( my)?( password)?|send( reset)?( password)?( link| email)?|save( password)?|set( new)? password)$/i });
    if (!btn) throw new Blocked("account_flow_failed", "no submit control on the account page");
    const before = await F.fingerprint(ctx.tab);
    await clickEl(ctx.tab, btn);
    await waitChange(ctx, before, 15000);
  },
  async clickCreate(ctx) {
    gate(ctx);
    const el = matchEl(await observe(ctx.tab), { roles: ["button", "link"], name: /^(create( a| my)?( new)? (profile|account)|register)$/i });
    if (!el) throw new Blocked("account_flow_failed", "no Create Profile control");
    const before = await F.fingerprint(ctx.tab);
    await clickEl(ctx.tab, el);
    await waitChange(ctx, before, 15000);
  },
  async clickForgot(ctx) {
    gate(ctx);
    const el = matchEl(await observe(ctx.tab), { roles: ["button", "link"], name: /^(forgot( your)? password\??|reset your password)$/i });
    if (!el) throw new Blocked("account_flow_failed", "no Forgot / Reset your password control");
    const before = await F.fingerprint(ctx.tab);
    await clickEl(ctx.tab, el);
    await waitChange(ctx, before, 15000);
  },
  async submitNewPassword(ctx, pw) {
    await ACCOUNT_HOOKS.fillPassword(ctx, pw);
    await ACCOUNT_HOOKS.submit(ctx);
  },
};

/** What the authorized live path WOULD do for this wall; reported in dry run / un-authorized runs. */
function wouldExtras(ctx, st) {
  if (st.s === "register") return { wouldCreateAccount: true };
  if (st.s !== "password") return {};
  let saved = null;
  try { saved = require("../credentials").get(ctx.site, ctx.P && ctx.P.email); } catch { /* unreadable vault: report as not saved */ }
  return saved ? { wouldLoginWithSavedCredentials: true } : { wouldResetPassword: true };
}

async function stepAccount(ctx, root, st, extras) {
  if (!accountAllowed(ctx)) {
    const why = ctx.dryRun === false ? "opts.allowAccountCreation is not set" : "dry run";
    const reason = st.s === "password" ? "password_required" : "account_required";
    const what = st.s === "password" ? `existing-account login asks for a password (${st.heading || "login page"})` : `profile registration (password creation) (${st.heading || "login page"})`;
    return F.manual(ctx, reason, { ...extras, evidence: `${what}; no account action is taken (${why}). Log in once in the browser session, then re-run`, ...wouldExtras(ctx, st) });
  }
  root.accountRuns = (root.accountRuns || 0) + 1;
  if (root.accountRuns > 2) return F.manual(ctx, "account_flow_failed", { ...extras, evidence: "still on a login / create-profile page after the account flow ran" });
  const a = await account().ensureAccount(ctx, { adapterName: "icims", detect: () => detectAccount(ctx), signInFlow: ACCOUNT_HOOKS });
  if (!a.ready) return a.result;
  root.accountExtras = a.extras;
  return null;
}

// ---- steps ---------------------------------------------------------------------------------------------
/** Type the profile email into one email box: real click, clear, type, read back (fill() alone can leave only the first character on iCIMS pages). */
async function typeEmail(ctx, field) {
  const want = String((ctx.P && ctx.P.email) || "");
  if (!want) return false;
  const read = () => evalIn(ctx.tab, (s) => { const e = document.querySelector(s); return e ? e.value : null; }, field.selector);
  if (String((await read()) || "").toLowerCase() === want.toLowerCase()) return true;
  await ctx.tab.click(field.selector);
  await ctx.tab.fill(field.selector, "");
  await ctx.tab.type(field.selector, want);
  await sleep(300);
  return String((await read()) || "").toLowerCase() === want.toLowerCase();
}

/** Email-only login screen: the profile email is an allowlisted answer; we only press Next/Continue. */
async function stepEmail(ctx, st) {
  // iCIMS labels the box "Email (Primary)" (not required, not matched by the answers.js email rule), so fillForm would
  // skip it and Next would be pressed on an empty field. The profile email is an allowlisted answer: type and verify it.
  if (st.field && !(await typeEmail(ctx, st.field))) return F.manual(ctx, "validation_error", { evidence: "email field did not hold the typed address before Next", unanswered: [] });
  const rep = await fillForm(ctx);
  F.merge(ctx, rep);
  const blocked = F.submittable(rep);
  if (blocked) return F.manual(ctx, "unanswered_questions", { unanswered: rep.unanswered.map((u) => u.question), evidence: blocked });
  const next = matchEl(await observe(ctx.tab), { roles: ["button", "link"], name: RE.emailNext });
  if (!next) return F.manual(ctx, "account_required", { evidence: `login screen without a Next/Continue control; sign in or create an account manually. ${(st.text || "").replace(/\s+/g, " ").slice(0, 200)}` });
  const before = await F.fingerprint(ctx.tab);
  await clickEl(ctx.tab, next);
  const changed = await waitChange(ctx, before, 15000);
  if (!changed) {
    const errs = await F.validationErrors(ctx.tab);
    return F.manual(ctx, "login_not_advanced", { evidence: errs.join(" | ") || "email step did not advance", unanswered: errs });
  }
  ctx.emailProbed = true;
  return null;
}

/**
 * iCIMS candidate-profile boxes (PersonProfileFields.*) whose saved value already equals the confirmed profile value.
 * `sel` maps the underlying <select> name -> selected text (the visible "_icimsDropdown" comboboxes report an empty value).
 */
function savedProfileFieldOk(ctx, f, sel) {
  const n = String(f.name || "").replace(/_icimsDropdown$/, "");
  if (!/PersonProfileFields\./.test(n)) return false;
  const v = String((n in sel ? sel[n] : f.value) || "").trim().toLowerCase();
  const email = String((ctx.P && ctx.P.email) || "").toLowerCase();
  if (/\.(Login|Email)$/.test(n)) return !!email && v === email;
  if (/PhoneType$/.test(n)) return v === "mobile";
  if (/AddressType$/.test(n)) return v === "home";
  if (/AddressCountry$/.test(n)) return v === "united states";
  if (/AddressState$/.test(n)) {
    const state = ctx.P && ctx.P.state;
    return !!state && (v === state.toLowerCase() || v === String(STATE_NAMES[state] || "").toLowerCase());
  }
  return false;
}

async function savedSelects(ctx) {
  const raw = await ctx.tab.evaluate(`JSON.stringify(Object.fromEntries([...document.querySelectorAll("select[name*='PersonProfileFields.']")].map((e) => [e.name, (e.selectedOptions[0] || {}).textContent || ""])))`);
  try { return JSON.parse(raw) || {}; } catch { return {}; }
}

/**
 * The application is a tall iframe inside a scrolling page: a control near the bottom of the frame can sit outside the viewport,
 * where the real click is not delivered. Scroll the outer page (no page state changes) so the named control is mid-screen.
 */
async function centerControl(ctx, name) {
  if (!name || !ctx.rootTab || typeof ctx.rootTab.evaluate !== "function") return;
  const y = await ctx.tab.evaluate(`(() => { const n = ${JSON.stringify(String(name).toLowerCase())}; const c = [...document.querySelectorAll("input[type=submit],input[type=button],button,a")].find((e) => (e.innerText || e.value || "").trim().toLowerCase() === n); if (!c) return null; const r = c.getBoundingClientRect(); return r.top + r.height / 2; })()`);
  if (typeof y !== "number") return;
  await ctx.rootTab.evaluate(`(() => { const f = document.getElementById("icims_content_iframe"); if (!f) return; const t = f.getBoundingClientRect().top + ${y}; window.scrollBy(0, t - innerHeight / 2); })()`);
  await sleep(300);
}

// Values and validation text may change without leaving a form; neither permits a second Submit.
function formKey(st) {
  return JSON.stringify([st.heading || "", (st.fields || []).map((f) => [f.name || f.selector, f.label, f.kind])]);
}

/**
 * Submit can save an iCIMS step rather than send the application. Continue only to an observed,
 * changed Source form or explicitly numbered intermediate step; never repeat an unchanged Submit.
 */
async function submitStep(ctx, root, finalEl, before, previous) {
  await clickEl(ctx.tab, finalEl);
  root.submitted = ctx.submitted = true;
  const end = Date.now() + 30000;
  while (Date.now() < end) {
    await sleep(500);
    const ch = (await detectChallenge(ctx.tab)) || (await detectChallenge(root.tab));
    if (ch) return F.manual(ctx, "captcha_required", { evidence: `challenge after Submit (not solved): ${ch}` });
    const st = await detectState(ctx);
    if (st.s === "done") return result("applied", "confirmation_page", { evidence: st.text.replace(/\s+/g, " ").slice(0, 300), consents: ctx.consents.slice(), tabName: ctx.tabName });
    if (await F.codePromptVisible(ctx.tab)) return F.manual(ctx, "otp_required", { evidence: "the site asked for a verification code after Submit; the adapter never reads or enters it" });
    const changed = (await F.fingerprint(ctx.tab)) !== before;
    if (changed && st.s === "form" && formKey(st) !== formKey(previous) && (st.source || st.intermediate)) {
      if (++ctx.submitContinuations > 4) return F.manual(ctx, "too_many_steps", { evidence: "more than four intermediate forms followed Submit without a receipt" });
      return null;
    }
    const errs = await F.validationErrors(ctx.tab);
    if (errs.length) return result("failed", "validation_error_after_submit", { evidence: errs.join(" | "), consents: ctx.consents.slice(), tabName: ctx.tabName });
  }
  const errs = await F.validationErrors(ctx.tab);
  return result("failed", errs.length ? "validation_error_after_submit" : "no_confirmation", { evidence: errs.join(" | ") || (await snippet(ctx.tab, 300)), consents: ctx.consents.slice(), tabName: ctx.tabName });
}

async function stepForm(ctx, root, st) {
  // signed in: the stored login/password boxes are never touched; saved contact fields are left as they are ONLY while each equals her confirmed profile value
  const sel = st && st.authed ? await savedSelects(ctx) : null;
  const rep = await fillForm(ctx, sel ? { only: (f) => f.kind !== "password" && !savedProfileFieldOk(ctx, f, sel) } : undefined);
  F.merge(ctx, rep);
  const blocked = F.submittable(rep);
  if (blocked) return F.manual(ctx, "unanswered_questions", { unanswered: rep.unanswered.map((u) => u.question), evidence: blocked });
  const ch = (await detectChallenge(ctx.tab)) || (await detectChallenge(root.tab));
  if (ch) return F.manual(ctx, "captcha_required", { evidence: `bot check present (not solved): ${ch}` });

  // iCIMS's custom dropdowns stay open after a choice and cover the buttons below them: toggle every open one shut with a real click
  for (let i = 0; i < 4; i++) {
    const open = await ctx.tab.evaluate(`(() => { const a = document.querySelector("a[id$='_icimsDropdown'][aria-expanded='true']"); return a ? a.id : null; })()`);
    if (!open) break;
    await ctx.tab.click(`[id="${open}"]`);
  }
  const nav = await F.findNav(ctx.tab);
  if (nav.final) {
    // Only explicit step progress makes a plain Submit safe navigation in a dry run.
    const intermediate = st.intermediate && /^submit$/i.test(nav.final.name);
    if (ctx.dryRun && !intermediate) return result("skipped", "dry_run_complete", { dryRun: true, wouldSubmit: F.dryRunSummary(ctx, nav.final.name), evidence: `stopped before "${nav.final.name}"`, consents: ctx.consents.slice(), tabName: ctx.tabName });
    const before = await F.fingerprint(ctx.tab);
    const key = formKey(st);
    if (ctx.submitPages.has(key)) return F.manual(ctx, "no_confirmation", { evidence: "this form was already submitted without a receipt; Submit is not retried" });
    ctx.submitPages.add(key);
    await centerControl(ctx, nav.final.name);
    if (!intermediate) return submitStep(ctx, root, nav.final, before, st);
    await clickEl(ctx.tab, nav.final);
    if (!ctx.dryRun) root.submitted = ctx.submitted = true;
    if (!(await waitChange(ctx, before, 15000))) {
      const errs = await F.validationErrors(ctx.tab);
      return F.manual(ctx, "validation_error", { evidence: errs.join(" | ") || "intermediate Submit did not advance; it is not retried", unanswered: errs });
    }
    return null;
  }
  // iCIMS's signed-in candidate-profile page advances with "Update profile" (it saves the profile and opens the next application step)
  if (!nav.next) nav.next = matchEl(nav.els, { roles: ["button", "link"], name: /^update profile$/i });
  if (!nav.next) return F.manual(ctx, "no_navigation_button", { evidence: await snippet(ctx.tab, 300) });
  await centerControl(ctx, nav.next.name);
  const before = await F.fingerprint(ctx.tab);
  await clickEl(ctx.tab, nav.next);
  if (!(await waitChange(ctx, before, 15000))) {
    const errs = await F.validationErrors(ctx.tab);
    return F.manual(ctx, "validation_error", { evidence: errs.join(" | ") || "page did not advance", unanswered: errs });
  }
  return null;
}

async function applyFlow(root) {
  root.P = root.P || deriveProfile(root.profile);
  const scope = scopeOf(root.tab);
  const probe = await scope.probe();
  if (probe.framed && !probe.drivable) {
    return F.manual(root, "embedded_ats:icims", { evidence: `application is embedded in an iframe (${probe.src}) that this browser cannot drive${probe.error ? `: ${probe.error}` : ""}; apply manually` });
  }
  // every helper sees the scope as "the tab"; arrays/flags below are shared with the real ctx
  const ctx = { ...root, tab: scope, rootTab: root.tab, emailProbed: false, submitPages: new Set(), submitContinuations: 0 };
  const trail = [];
  let lastFp = null, same = 0, unknown = 0, authed = false;

  for (let i = 0; i < 40; i++) {
    const st = await detectState(ctx);
    if (st.s !== "unknown") unknown = 0;
    trail.push(st.s);
    if (st.authed) authed = true;
    root.log && root.log("icims_state", { s: st.s, i, framed: probe.framed });
    const extras = { trail, framed: probe.framed, authenticated: authed };

    const fp = await F.fingerprint(scope);
    same = fp === lastFp ? same + 1 : 0;
    lastFp = fp;
    if (same >= 3 && st.s !== "unknown") return F.manual(ctx, `stuck_${st.s}`, extras);

    if (ctx.emailProbed && st.s !== "email_step" && (await F.codePromptVisible(scope))) {
      return F.manual(ctx, "otp_required", { ...extras, evidence: "the site asked for an emailed verification code after the email step; the code is never requested, read or entered by the adapter" });
    }
    const ch = st.s !== "done" ? ((await detectChallenge(scope)) || (await detectChallenge(root.tab))) : null;
    if (ch) return F.manual(ctx, "captcha_required", { ...extras, evidence: `bot check present (not solved): ${ch}` });

    let out = null;
    switch (st.s) {
      case "done": {
        const evidence = st.text.replace(/\s+/g, " ").slice(0, 300);
        return root.submitted
          ? result("applied", "confirmation_page", { ...extras, evidence, consents: root.consents.slice() })
          : result("skipped", "confirmation_page_already_shown", { ...extras, evidence });
      }
      case "already_applied": return result("skipped", "already_applied", { ...extras, evidence: (st.text.match(RE.already) || [""])[0] });
      case "closed": return result("skipped", "posting_closed", { ...extras, evidence: (st.text.match(RE.closed) || [""])[0] });
      case "job": {
        const before = await F.fingerprint(scope);
        await clickEl(scope, st.apply);
        await waitChange(ctx, before, 15000);
        break;
      }
      case "email_step": out = await stepEmail(ctx, st); break;
      case "password":
      case "register":
        out = await stepAccount(ctx, root, st, extras);
        break;
      case "wall":
        return F.manual(ctx, "account_required", { ...extras, evidence: `login / create-profile choice wall (${st.heading || "login page"}); no email step to continue from. Log in once in the browser session, then re-run` });
      case "form": out = await stepForm(ctx, root, st); break;
      default:
        await sleep(1500);
        if (++unknown >= 4) return F.manual(ctx, "unrecognized_page", { ...extras, evidence: st.text.replace(/\s+/g, " ").slice(0, 300) });
    }
    if (out) { out.trail = trail; out.framed = probe.framed; out.authenticated = authed; return out; }
  }
  return F.manual(ctx, "too_many_steps", { trail });
}

/** The flow plus the account extras (who/what was created or reset; never a password). */
async function apply(root) {
  const r = await applyFlow(root);
  if (!root.accountRuns) return r;
  return require("../credentials").redact({ ...r, ...(root.accountExtras || {}) });
}

module.exports = {
  name: "icims",
  matches(url) { try { return HOST.test(new URL(url).hostname); } catch { return false; } },
  apply,
  _detectState: detectState,
};
