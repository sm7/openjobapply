// Workday candidate experience (*.myworkdayjobs.com, *.wdN.myworkdayjobs.com, myworkdaysite.com, workday.com careers).
//
// Observed flow: job page -> Apply (data-automation-id=adventureButton) -> "Start Your Application"
// (applyManually / useMyLastApplication / autofillWithResume) -> [sign in / create account] ->
// My Information -> My Experience -> Application Questions -> Voluntary Disclosures -> Self Identify -> Review -> Submit.
//
// The adapter is a STATE MACHINE keyed off stable data-automation-id hooks, so it can start on any step
// of an already-authenticated session:
//   * Sign-in / create-account / password prompts: by default (dry run, or live without opts.allowAccountCreation)
//     NOTHING is typed or clicked -> manual account_required | password_required, with wouldCreateAccount /
//     wouldResetPassword / wouldLoginWithSavedCredentials extras saying what the authorized live path would do.
//     ONLY when ctx.dryRun === false AND ctx.opts.allowAccountCreation === true is account.js#ensureAccount called
//     (authorized creation / saved-credential login / mailbox-link password reset for the candidate's own email;
//     passwords come from credentials.js and are typed through account.fillCredential, never logged or returned).
//     Captcha / MFA / lockout / ambiguity still stop (captcha_required, mfa_required, account_flow_failed, ...).
//     An already-authenticated session (Chrome relay tab) is simply used.
//   * Apply Manually is preferred (every field is then resolved and verified through answers.js);
//     Use My Last Application is only taken when Apply Manually is not offered. Autofill-with-resume is never used.
//   * My Experience: the resume is uploaded (or the site's attached file is reported); work-history / education /
//     language / certification / skills entries are NEVER retyped, only verified (reported as prefilledBySite; an
//     incomplete required entry stops the run with unanswered_questions).
//   * Voluntary Disclosures / Self Identify: decline through answers.js (EEO -> "I do not wish to answer").
//   * Workday widgets are not plain inputs: dropdowns are <button aria-haspopup=listbox>, "how did you hear" is a
//     multi-select with a search box, dates are spinbutton groups (never filled -> manual when required).
//     Text goes in through tab.fill and is read back; if a React-controlled input rejects it the field is
//     cleared and typed with real key events, then read back again.
//   * Dry run stops on Review BEFORE Submit; live mode presses Submit once and waits for "Application Submitted".
"use strict";
const path = require("path");
const {
  sleep, evalIn, visibleText, observe, FORBIDDEN_CLICK, Blocked, logConsent, result, detectChallenge, waitUntil, snippet, CONFIRMATION,
} = require("../lib");
const { NeedsHuman, resolveAnswer, deriveProfile } = require("../answers");
const { readValue, applyAction } = require("../form");
const F = require("../flow");

const HOST = /(^|\.)(myworkdayjobs|myworkdaysite|myworkdayjobs-impl)\.com$|(^|\.)workday\.com$/i;

const CLOSED = /no longer (available|accepting)|(job|position|posting|requisition) (is )?(no longer|not) (available|found|open)|this job has (been )?(closed|filled)|posting has expired|page you('re| are) looking for (doesn'?t|does not) exist|couldn'?t find the job/i;
const ALREADY = /you('ve| have) already applied|already (applied|submitted)( for| to)? (this|the) (job|position)/i;
const DONE = /application submitted|thanks? (you )?for applying|your application (has been |was )?(successfully )?(submitted|received)/i;
const FINAL = /^(submit( application)?|submit & apply)$/i;

/** Sections that hold history entries. These are verified, never typed. */
const HISTORY = [
  '[data-automation-id^="workExperience"]', '[data-automation-id^="education"]', '[data-automation-id^="language"]',
  '[data-automation-id^="certification"]', '[data-automation-id^="skills"]',
];
const HISTORY_NAMES = { workExperience: "Work Experience", education: "Education", language: "Languages", certification: "Certifications", skills: "Skills" };

// ---- in-page probes (serialised into the page: must be self-contained) ----------------------------------------
function probeInPage() {
  const clean = (s) => (s || "").replace(/[\u200b\u00a0]/g, " ").replace(/\s+/g, " ").trim();
  const vis = (el) => { const r = el.getBoundingClientRect(); if (r.width < 1 || r.height < 1) return false; const s = getComputedStyle(el); return s.visibility !== "hidden" && s.display !== "none"; };
  const all = (id) => [...document.querySelectorAll(`[data-automation-id="${id}"]`)].filter(vis);
  const any = (ids) => ids.filter((id) => all(id).length);
  const nav = all("bottom-navigation-next-button")[0] || null;
  const stepEl = all("progressBarActiveStep")[0];
  const heading = [...document.querySelectorAll("h1, h2")].filter(vis)[0];
  const errors = new Set();
  for (const id of ["errorBanner", "errorHeading", "errorMessage"]) for (const e of all(id)) { const t = clean(e.innerText || e.textContent); if (t && t.length < 400) errors.add(t); }
  for (const e of document.querySelectorAll("[role=alert]")) { const t = clean(e.innerText || e.textContent); if (t && t.length < 400 && vis(e) && !/loading/i.test(t)) errors.add(t); }
  const step = stepEl ? clean(stepEl.innerText || stepEl.textContent).replace(/^current step \d+ of \d+\s*/i, "") : (heading ? clean(heading.innerText) : "");
  return {
    nav: nav ? { text: clean(nav.innerText || nav.textContent), disabled: !!nav.disabled || nav.getAttribute("aria-disabled") === "true" } : null,
    job: all("adventureButton").length > 0,
    choice: { manual: all("applyManually").length > 0, last: all("useMyLastApplication").length > 0, autofill: all("autofillWithResume").length > 0 },
    account: any(["signInContent", "signInSubmitButton", "createAccountSubmitButton", "createAccountLink", "verifyPassword"]),
    password: [...document.querySelectorAll("input[type=password]")].filter(vis).length,
    step,
    errors: [...errors].slice(0, 8),
    text: clean(document.body ? document.body.innerText : "").slice(0, 2000),
  };
}

function locateInPage(id) {
  const clean = (s) => (s || "").replace(/[\u200b\u00a0]/g, " ").replace(/\s+/g, " ").trim();
  const vis = (el) => { const r = el.getBoundingClientRect(); if (r.width < 1 || r.height < 1) return false; const s = getComputedStyle(el); return s.visibility !== "hidden" && s.display !== "none"; };
  const el = typeof id === "string"
    ? [...document.querySelectorAll(`[data-automation-id="${id}"]`)].find(vis)
    : [...document.querySelectorAll("button, input[type=button], input[type=submit], a, [role=button], [role=link]")].find((e) =>
      vis(e) && !e.disabled && e.getAttribute("aria-disabled") !== "true" &&
      new RegExp(id.pattern, id.flags).test(clean(e.innerText || e.value || e.getAttribute("aria-label") || e.textContent)));
  if (!el) return null;
  const css = (e) => {
    if (e.id && document.querySelectorAll("#" + CSS.escape(e.id)).length === 1) return "#" + CSS.escape(e.id);
    const parts = [];
    for (let n = e; n && n.nodeType === 1 && n !== document.documentElement; n = n.parentElement) {
      if (n.id && document.querySelectorAll("#" + CSS.escape(n.id)).length === 1) { parts.unshift("#" + CSS.escape(n.id)); break; }
      const sib = n.parentElement ? [...n.parentElement.children].filter((c) => c.tagName === n.tagName) : [n];
      parts.unshift(n.tagName.toLowerCase() + (sib.length > 1 ? `:nth-of-type(${sib.indexOf(n) + 1})` : ""));
    }
    return parts.join(" > ");
  };
  return { selector: css(el), text: clean(el.innerText || el.value || el.getAttribute("aria-label") || el.textContent), disabled: !!el.disabled || el.getAttribute("aria-disabled") === "true" };
}

/**
 * Scan the current Workday step. Fields come back in form.js's field shape so answers.js/form.js actions work on them;
 * widgets form.js cannot see (dropdown buttons, multi-select, date groups) are reported as kind "combobox"/"date".
 * Anything inside a HISTORY section is returned separately (verify only, never typed).
 */
function scanInPage(arg) {
  const clean = (s) => (s || "").replace(/[\u200b\u00a0]/g, " ").replace(/\s+/g, " ").trim();
  const txt = (n) => clean(n && (n.innerText || n.textContent));
  const visible = (el) => {
    if (!el) return false;
    if (el.checkVisibility && !el.checkVisibility({ visibilityProperty: true })) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    if (el.closest("[aria-hidden=true]")) return false;
    return true;
  };
  const css = (el) => {
    if (el.id && document.querySelectorAll("#" + CSS.escape(el.id)).length === 1) return "#" + CSS.escape(el.id);
    const parts = [];
    for (let n = el; n && n.nodeType === 1 && n !== document.documentElement; n = n.parentElement) {
      if (n.id && document.querySelectorAll("#" + CSS.escape(n.id)).length === 1) { parts.unshift("#" + CSS.escape(n.id)); break; }
      const sib = n.parentElement ? [...n.parentElement.children].filter((c) => c.tagName === n.tagName) : [n];
      parts.unshift(n.tagName.toLowerCase() + (sib.length > 1 ? `:nth-of-type(${sib.indexOf(n) + 1})` : ""));
    }
    return parts.join(" > ");
  };
  const labelText = (l) => { const c = l.cloneNode(true); c.querySelectorAll("input,select,textarea,button").forEach((x) => x.remove()); return clean(c.textContent); };
  const own = (el) => {
    const out = [];
    const lb = el.getAttribute("aria-labelledby");
    if (lb) lb.split(/\s+/).forEach((id) => { const n = document.getElementById(id); if (n) out.push(txt(n)); });
    if (!out.join("").trim() && el.labels) [...el.labels].forEach((l) => out.push(labelText(l)));
    return clean(out.join(" "));
  };
  const ffOf = (el) => el.closest('[data-automation-id^="formField"]');
  // the field's visible caption (with its * markers) taken from the Workday formField wrapper
  const ffRaw = (el, skipFor) => {
    const ff = ffOf(el);
    if (!ff) return "";
    const lg = ff.querySelector("legend");
    if (lg) return labelText(lg);
    const labels = [...ff.querySelectorAll("label, [data-automation-id='fieldLabel']")].filter((l) => !l.querySelector("input,button,select,textarea") && !(skipFor && l.htmlFor && skipFor.includes(l.htmlFor)));
    const mine = labels.find((l) => l.htmlFor && l.htmlFor === el.id) || labels[0];
    return mine ? labelText(mine) : "";
  };
  const finish = (s) => clean(s.replace(/\(s\)/gi, "").replace(/[*\u2731]+/g, " ").replace(/\s*\(required\)\s*$/i, ""));
  const star = (s) => /[*\u2731]/.test(s) || /\(required\)\s*$/i.test(s);

  const skipSel = (arg.skip || []).join(",");
  const out = [];
  const history = [];
  const roots = new Map();
  const seenGroups = new Set();
  const nameOfRoot = (r) => (r.getAttribute("data-automation-id") || "").replace(/(Section|-\d+)$/g, "");

  const els = [...document.querySelectorAll("input, textarea, select, button[aria-haspopup=listbox], [data-automation-id=multiSelectContainer], [data-automation-id=dateInputWrapper]")];
  for (const el of els) {
    if (el.closest("header, footer, [data-automation-id=utilityButtonSignIn]")) continue;
    const tag = el.tagName.toLowerCase();
    const aid = el.getAttribute("data-automation-id") || "";
    const type = (el.getAttribute("type") || "").toLowerCase();
    if (tag === "input" && ["hidden", "submit", "button", "image", "reset"].includes(type)) continue;
    if (el.disabled || el.getAttribute("aria-disabled") === "true") continue;
    if (/captcha|honey|bot-?field/i.test((el.getAttribute("name") || "") + " " + el.id + " " + el.className)) continue;
    if (el.closest("[role=listbox]")) continue;
    const isMulti = aid === "multiSelectContainer";
    const isDate = aid === "dateInputWrapper";
    if (!isMulti && !isDate) {
      if (el.closest("[data-automation-id=multiSelectContainer]")) continue;
      if (el.closest("[data-automation-id=dateInputWrapper]") || el.getAttribute("role") === "spinbutton") continue;
    }
    const isCheck = type === "radio" || type === "checkbox";
    const isFile = type === "file";
    if (isCheck) { const l = el.labels && el.labels[0]; if (!(visible(el) || (l && visible(l)))) continue; }
    else if (!isFile && !visible(el)) continue;
    if (el.readOnly && tag !== "select" && !isFile) continue;

    // ---- history sections: verify only
    let hroot = null;
    if (skipSel) { let n = el.closest(skipSel); while (n) { hroot = n; n = n.parentElement && n.parentElement.closest(skipSel); } }
    if (hroot) {
      if (isCheck || isFile) continue;
      const raw = (tag === "button" ? ffRaw(el) : (own(el) || ffRaw(el))) || clean(el.getAttribute("aria-label")) || clean(el.placeholder);
      const value = tag === "button" ? (/^select one$/i.test(txt(el)) ? "" : txt(el)) : (el.value || "");
      const sec = nameOfRoot(hroot);
      history.push({ section: sec, label: finish(raw) || sec, required: star(raw) || el.required || el.getAttribute("aria-required") === "true", value });
      if (!roots.has(hroot)) roots.set(hroot, { section: sec, text: txt(hroot).slice(0, 240) });
      continue;
    }

    let kind, label = "", group = "", required = false, options = [], value = "", selector = css(el), widget = "";
    if (isMulti) {
      const box = el.querySelector("input[data-automation-id=searchBox], input");
      if (!box) continue;
      kind = "combobox"; widget = "multiselect"; selector = css(box);
      const raw = ffRaw(el) || own(box) || clean(box.getAttribute("aria-label"));
      label = raw; required = star(raw) || box.getAttribute("aria-required") === "true";
      value = [...el.querySelectorAll("[data-automation-id=selectedItem]")].map(txt).join(", ");
    } else if (isDate) {
      kind = "date"; widget = "date";
      const raw = ffRaw(el);
      label = raw; required = star(raw);
      const parts = [...el.querySelectorAll("input")].map((i) => i.value || "");
      value = parts.every((p) => p) ? parts.join("/") : "";
    } else if (tag === "button") {
      kind = "combobox"; widget = "dropdown";
      const al = clean(el.getAttribute("aria-label"));
      const raw = ffRaw(el) || own(el) || al;
      label = raw; required = star(raw) || /\srequired$/i.test(al) || el.getAttribute("aria-required") === "true";
      value = /^select one$/i.test(txt(el)) ? "" : txt(el);
    } else if (tag === "select") {
      kind = "select";
      const raw = own(el) || ffRaw(el) || clean(el.getAttribute("aria-label"));
      label = raw; required = star(raw) || el.required || el.getAttribute("aria-required") === "true";
      options = [...el.options].map((o) => ({ label: clean(o.textContent), value: o.value, selected: o.selected, placeholder: o.value === "" || /^(select|choose|please select|--)/i.test(clean(o.textContent)) }));
      value = (options.find((o) => o.selected && !o.placeholder) || {}).label || "";
    } else if (type === "radio") {
      kind = "radio";
      const nm = el.name;
      const radios = nm ? [...document.querySelectorAll(`input[type=radio][name="${CSS.escape(nm)}"]`)] : [el];
      const key = nm ? "r:" + nm : null;
      if (key && seenGroups.has(key)) continue;
      if (key) seenGroups.add(key);
      options = radios.map((r) => ({ label: own(r) || clean(r.value), value: r.value, selector: css(r), checked: r.checked, labelSelector: r.id ? `label[for="${CSS.escape(r.id)}"]` : null }));
      const fs = el.closest("fieldset, [role=radiogroup], [role=group]");
      let raw = "";
      if (fs) {
        const lg = fs.querySelector("legend");
        raw = lg ? labelText(lg) : (fs.getAttribute("aria-labelledby") ? fs.getAttribute("aria-labelledby").split(/\s+/).map((id) => txt(document.getElementById(id))).join(" ") : clean(fs.getAttribute("aria-label")));
      }
      if (!clean(raw)) raw = ffRaw(el, radios.map((r) => r.id).filter(Boolean));
      group = raw; label = raw;
      required = star(raw) || radios.some((r) => r.required || r.getAttribute("aria-required") === "true") || (fs && fs.getAttribute("aria-required") === "true");
      value = (options.find((o) => o.checked) || {}).label || "";
    } else if (type === "checkbox") {
      kind = "checkbox";
      const fs = el.closest("fieldset, [role=group]");
      if (fs) { const lg = fs.querySelector("legend"); group = lg ? labelText(lg) : clean(fs.getAttribute("aria-label")); }
      const raw = own(el) || ffRaw(el) || clean(el.getAttribute("aria-label"));
      label = raw; required = star(raw) || el.required || el.getAttribute("aria-required") === "true" || star(group);
      value = el.checked ? "checked" : "";
    } else if (isFile) {
      kind = "file";
      const hold = el.closest("[data-automation-id^=formField], [data-automation-id*=esume i], [data-automation-id*=ileUpload i]") || el.parentElement;
      const sec = el.closest("[data-automation-id*=esume i], [data-automation-id*=cv i]");
      let raw = ffRaw(el) || clean(el.getAttribute("aria-label"));
      if (!raw && sec) raw = "Resume/CV";
      if (!raw && hold) { const h = hold.querySelector("h2, h3, h4, legend, label"); raw = h ? txt(h) : ""; }
      label = raw; required = star(raw) || el.required || el.getAttribute("aria-required") === "true";
      const item = (hold || document).querySelector("[data-automation-id=file-upload-item], [data-automation-id=file-upload-successful]");
      value = item ? txt(item) : "";
    } else {
      kind = tag === "textarea" ? "textarea" : ["email", "tel", "url", "number"].includes(type) ? type : type === "password" ? "password" : (type === "date" || type === "month" || type === "time" || type === "datetime-local") ? "date" : "text";
      const raw = own(el) || ffRaw(el) || clean(el.getAttribute("aria-label")) || clean(el.placeholder);
      label = raw; required = star(raw) || el.required || el.getAttribute("aria-required") === "true";
      value = el.value || "";
    }
    out.push({
      tag, kind, type, widget, label: finish(label), group: finish(group), required, options, placeholder: el.getAttribute("placeholder") || "",
      name: el.getAttribute("name") || el.id || aid || "", selector, value, checked: !!el.checked, autocomplete: el.getAttribute("autocomplete") || "",
    });
  }
  return { fields: out, history, roots: [...roots.values()] };
}

function readMultiInPage(sel) {
  const box = document.querySelector(sel);
  const c = box && box.closest("[data-automation-id=multiSelectContainer]");
  if (!c) return "";
  const chips = [...c.querySelectorAll("[data-automation-id=selectedItem]")].map((e) => (e.innerText || e.textContent || "").replace(/\s+/g, " ").trim());
  return chips.join(", ");
}

// ---- helpers ------------------------------------------------------------------------------------------------
const flat = (s) => String(s || "").toLowerCase().replace(/\s+/g, " ").trim();
const digits = (s) => String(s || "").replace(/\D/g, "");
const probe = (ctx) => evalIn(ctx.tab, probeInPage);

async function press(ctx, id) {
  const el = await evalIn(ctx.tab, locateInPage, id);
  if (!el) return null;
  if (FORBIDDEN_CLICK.test(el.text)) throw new Blocked("forbidden_control", `refused to click "${el.text}"`);
  await ctx.tab.click(el.selector);
  return el;
}

/** Wait until the visible text stops changing (React steps render in stages). */
async function settle(tab, max = 6000) {
  const end = Date.now() + max;
  let prev = await F.fingerprint(tab);
  while (Date.now() < end) {
    await sleep(450);
    const now = await F.fingerprint(tab);
    if (now === prev) return true;
    prev = now;
  }
  return false;
}

async function detectState(ctx) {
  const p = await probe(ctx);
  const base = { p, step: p.step, text: p.text, errors: p.errors };
  if (p.nav) return { ...base, s: "form" };
  if (p.choice.manual || p.choice.last || p.choice.autofill) return { ...base, s: "choice" };
  if (p.account.length) return { ...base, s: "account", reason: "account_required", evidence: `Workday asks for sign-in / account creation (${p.account.join(", ")}); no account action is taken unless the authorized account flow runs` };
  if (p.password) return { ...base, s: "account", reason: "password_required", evidence: "Workday shows a password field; no password is entered unless the authorized account flow runs" };
  if (/verify (your )?(email|account)|check your (email|inbox)|email (has been|was) sent/i.test(p.text) && !p.job) return { ...base, s: "account", reason: "account_required", evidence: "Workday wants email verification of an account; the inbox is only read by the authorized account flow" };
  if (ALREADY.test(p.text)) return { ...base, s: "already_applied" };
  if (DONE.test(p.text) || CONFIRMATION.test(p.text)) return { ...base, s: "done" };
  if (p.job) return { ...base, s: "job" };
  if (CLOSED.test(p.text)) return { ...base, s: "closed" };
  return { ...base, s: "unknown" };
}

// ---- authorized account flow (account.js#ensureAccount) ---------------------------------------------------------
// Sign-in / create-account walls are handed to account.js ONLY for a live run that opted in. Everything the helper
// needs from this site (state detection, field/button hooks) is below; passwords are only ever typed through
// account.fillCredential and never enter a log, result, or evidence string.
const account = () => require("../account");
const accountAllowed = (ctx) => ctx.dryRun === false && !!ctx.opts && ctx.opts.allowAccountCreation === true;
const ACCT = {
  email: ["email", "emailAddress", "userName"],
  pw: ["password", "newPassword"],
  verify: ["verifyPassword", "confirmPassword"],
  terms: ["createAccountCheckbox"],
  create: ["createAccountLink"],
  forgot: ["forgotPasswordLink"],
  submit: ["createAccountSubmitButton", "signInSubmitButton", "resetPasswordSubmitButton", "forgotPasswordSubmitButton", "passwordResetSubmitButton"],
};

function acctProbeInPage() {
  const clean = (s) => (s || "").replace(/[\u200b\u00a0]/g, " ").replace(/\s+/g, " ").trim();
  const vis = (el) => { const r = el.getBoundingClientRect(); if (r.width < 1 || r.height < 1) return false; const s = getComputedStyle(el); return s.visibility !== "hidden" && s.display !== "none"; };
  const has = (id) => [...document.querySelectorAll(`[data-automation-id="${id}"]`)].some(vis);
  const cb = [...document.querySelectorAll('[data-automation-id="createAccountCheckbox"]')].find(vis);
  let cbText = "";
  if (cb) {
    const lab = (cb.id && document.querySelector(`label[for="${CSS.escape(cb.id)}"]`)) || cb.closest("label") || (cb.parentElement && cb.parentElement.querySelector("label"));
    cbText = clean((lab && (lab.innerText || lab.textContent)) || cb.getAttribute("aria-label") || (cb.parentElement && cb.parentElement.innerText));
  }
  return {
    signin: has("signInContent") || has("signInSubmitButton"), createBtn: has("createAccountSubmitButton"), verifyPw: ["verifyPassword", "confirmPassword"].some(has),
    resetHeading: [...document.querySelectorAll("h1, h2, h3, [role=heading], legend")].filter(vis).some((h) => /^(?:reset (?:your )?password|set (?:a )?new password|choose (?:a )?new password|new password)\b/i.test(clean(h.innerText || h.textContent))),
    resetSubmit: ["resetPasswordSubmitButton", "passwordResetSubmitButton"].some(has),
    emailField: ["email", "emailAddress", "userName"].some(has) || [...document.querySelectorAll("input[type=email], input[autocomplete=username], input[autocomplete=email]")].some(vis),
    emailMethod: [...document.querySelectorAll("button, input[type=button], input[type=submit], [role=button], a, [role=link]")].filter(vis).some((b) => /^(?:sign ?(?:in|up)|log ?in|continue|register)\s+with\s+(?:my\s+|your\s+)?(?:e-?mail|password)\b/i.test(clean(b.innerText || b.value || b.getAttribute("aria-label") || b.textContent))),
    pw: [...document.querySelectorAll("input[type=password]")].filter(vis).length, job: has("adventureButton"), nav: has("bottom-navigation-next-button"),
    termsBox: !!cb, termsChecked: !!(cb && cb.checked), cbText, text: clean(document.body ? document.body.innerText : "").slice(0, 1500),
  };
}

/** account.js state vocabulary for the page in front of us. */
async function detectAccount(ctx) {
  if (await detectChallenge(ctx.tab)) return "captcha";
  const a = await evalIn(ctx.tab, acctProbeInPage);
  if (a.job || a.nav) return "authenticated"; // application steps are not account pages (their contact fields would look like a create form to the generic classifier)
  if (/(enter|type|submit) (the |your )?(6-digit |verification |security |one-time )?code|authenticator app|(sent|texted) (you )?(a )?(code|text message)/i.test(a.text)) return "mfa";
  const resetText = /reset (your )?password|set (a )?new password|forgot (your )?password/i.test(a.text);
  if (/verify (your )?(email|account)|check your (email|inbox)|email (has been|was) sent|we('ve| have) sent/i.test(a.text)) return resetText ? "reset_sent" : "verify_email";
  // Create pages also have a "Forgot password" link. That navigation text is not evidence of a reset form.
  if (a.createBtn) return "create";
  if (a.verifyPw && !a.emailField && (a.resetHeading || a.resetSubmit)) return "reset_form";
  if (a.verifyPw) return "create";
  if (a.signin && !a.pw && !a.emailField && a.emailMethod) return "choose_method"; // provider chooser: Google + email buttons, no inputs yet
  if (a.signin) return a.pw ? "signin_password" : "signin_email";
  return account().detectAccountState(ctx.tab);
}

function gate(ctx) {
  if (!accountAllowed(ctx)) throw new Blocked("account_flow_failed", "account control refused: this run is not authorized to create or reset accounts");
}

async function acctFind(ctx, ids) {
  for (const id of ids) {
    const el = await evalIn(ctx.tab, locateInPage, id);
    if (el) return { ...el, id };
  }
  return null;
}

async function acctPress(ctx, ids, textRe) {
  const el = await acctFind(ctx, ids);
  if (el) {
    if (FORBIDDEN_CLICK.test(el.text)) throw new Blocked("forbidden_control", `refused to click "${el.text}"`);
    await ctx.tab.click(el.selector);
    return true;
  }
  // Use the same native controls / normalized visible labels as the probe, not a second accessibility snapshot.
  const hit = await evalIn(ctx.tab, locateInPage, { pattern: textRe.source, flags: textRe.flags });
  if (!hit || FORBIDDEN_CLICK.test(hit.text)) return false;
  await ctx.tab.click(hit.selector);
  return true;
}

async function acctSettle(ctx, before) {
  await F.waitChange(ctx.tab, before, 8000);
  await settle(ctx.tab, 4000);
}

/** The account-terms tickbox: only a recognised terms/privacy statement (answers.js allowlist) is ticked; anything else stops. */
async function acceptAccountTerms(ctx) {
  const a = await evalIn(ctx.tab, acctProbeInPage);
  if (!a.termsBox || a.termsChecked) return;
  const { classifyConsent } = require("../answers");
  const c = classifyConsent(a.cbText, ctx.profile);
  if (c.verdict !== "allow") throw new NeedsHuman(a.cbText || "account terms checkbox", `account checkbox is not an allowlisted consent (${c.rule})`);
  const el = await acctFind(ctx, ACCT.terms);
  await ctx.tab.click(el.selector);
  logConsent(ctx, a.cbText, c.rule);
}

const ACCOUNT_HOOKS = {
  async chooseEmail(ctx) {
    gate(ctx);
    return acctPress(ctx, [], /^(?:sign ?(?:in|up)|log ?in|continue|register)\s+with\s+(?:my\s+|your\s+)?(?:e-?mail|password)\b/i);
  },
  async fillEmail(ctx, email) {
    gate(ctx);
    const el = await acctFind(ctx, ACCT.email);
    if (!el) throw new Blocked("account_flow_failed", "no email field on the sign-in / create-account page");
    await ctx.tab.fill(el.selector, email);
    const got = await ctx.tab.evaluate(`(document.querySelector(${JSON.stringify(el.selector)}) || {}).value || ""`);
    if (flat(got) !== flat(email)) { await ctx.tab.fill(el.selector, ""); await ctx.tab.type(el.selector, email); }
  },
  /** New-password + confirm fields (create form / reset form) or the single sign-in password. */
  async fillPassword(ctx, pw) {
    gate(ctx);
    const el = await acctFind(ctx, ACCT.pw);
    if (!el) throw new Blocked("account_flow_failed", "no password field on the page");
    await account().fillCredential(ctx, el, pw);
    const again = await acctFind(ctx, ACCT.verify);
    if (again) await account().fillCredential(ctx, again, pw);
  },
  async submit(ctx) {
    gate(ctx);
    await acceptAccountTerms(ctx);
    const before = await F.fingerprint(ctx.tab);
    if (!(await acctPress(ctx, ACCT.submit, /^(sign in|create account|submit|reset password|send( reset)?( password)?( reset)?( email| link)?|set (new )?password|continue)$/i))) throw new Blocked("account_flow_failed", "no submit control on the account page");
    await acctSettle(ctx, before);
  },
  async clickCreate(ctx) {
    gate(ctx);
    const before = await F.fingerprint(ctx.tab);
    if (!(await acctPress(ctx, ACCT.create, /^create account$/i))) throw new Blocked("account_flow_failed", "no Create Account control");
    await acctSettle(ctx, before);
  },
  async clickForgot(ctx) {
    gate(ctx);
    const before = await F.fingerprint(ctx.tab);
    if (!(await acctPress(ctx, ACCT.forgot, /^forgot (your )?password\??$/i))) throw new Blocked("account_flow_failed", "no Forgot your password control");
    await acctSettle(ctx, before);
  },
  async submitNewPassword(ctx, pw) {
    await ACCOUNT_HOOKS.fillPassword(ctx, pw);
    await ACCOUNT_HOOKS.submit(ctx);
  },
};

/** What the authorized live path WOULD do for this wall; reported in dry run / un-authorized runs. */
function wouldExtras(ctx, st) {
  const ids = st.p.account || [];
  let saved = null;
  try { saved = require("../credentials").get(ctx.site, ctx.P && ctx.P.email); } catch { /* unreadable vault: report as not saved */ }
  const createForm = ids.includes("createAccountSubmitButton") || ids.includes("verifyPassword");
  if (createForm) return { wouldCreateAccount: true };
  if (saved) return { wouldLoginWithSavedCredentials: true };
  if (st.reason === "password_required") return { wouldResetPassword: true };
  if (st.reason === "account_required" && ids.length) return { wouldCreateAccount: true, wouldResetPassword: true }; // sign-in wall: which one depends on whether the account exists
  return {};
}

async function stepAccount(ctx, st, trail) {
  if (!accountAllowed(ctx)) {
    const why = ctx.dryRun === false ? "opts.allowAccountCreation is not set" : "dry run";
    return F.manual(ctx, st.reason, { evidence: `${st.evidence} (${why})`, trail, ...wouldExtras(ctx, st) });
  }
  ctx.accountRuns = (ctx.accountRuns || 0) + 1;
  if (ctx.accountRuns > 2) return F.manual(ctx, "account_flow_failed", { evidence: "still on a sign-in / create-account page after the account flow ran", trail });
  const a = await account().ensureAccount(ctx, { adapterName: "workday", detect: () => detectAccount(ctx), signInFlow: ACCOUNT_HOOKS });
  if (!a.ready) return a.result;
  ctx.accountExtras = a.extras;
  await settle(ctx.tab);
  return null;
}

// ---- field actions ------------------------------------------------------------------------------------------
function sameText(want, got, f) {
  if (f.kind === "tel" || /phone|mobile|cell/i.test(f.label)) return digits(got).endsWith(digits(want)) && digits(want).length >= 7;
  return flat(got) === flat(want) || flat(got).includes(flat(want));
}

/** tab.fill, read back; a React-controlled input that dropped it gets cleared and typed with real key events. */
async function fillText(ctx, f, value) {
  const tab = ctx.tab;
  await tab.fill(f.selector, value);
  await sleep(150);
  let got = await readValue(tab, f);
  if (!sameText(value, got, f)) {
    await tab.click(f.selector);
    await tab.fill(f.selector, "");
    await tab.type(f.selector, String(value));
    await sleep(250);
    got = await readValue(tab, f);
  }
  return { ok: sameText(value, got, f), got, shown: value };
}

async function listOptions(tab) {
  return waitUntil(async () => { const e = (await tab.observe()).elements.filter((x) => x.role === "option"); return e.length ? e : null; }, { timeout: 3500, every: 300 });
}

function narrow(act, match) {
  if (match.length > 1 && act.rule === "phone_country_code") {
    const exact = match.filter((e) => /^united states( of america)?\s*(\(\+?1\))?$/i.test((e.name || "").trim()));
    if (exact.length) return exact;
  }
  return match.length > 1 && act.pickFirst ? [match[0]] : match;
}

async function pickDropdown(ctx, f, act) {
  const tab = ctx.tab;
  await tab.click(f.selector);
  const els = (await listOptions(tab)) || [];
  const match = narrow(act, els.filter((e) => act.test(e.name || "")));
  if (match.length !== 1) { try { await tab.press("Escape", { selector: f.selector }); } catch { /* ignore */ } return { ok: false, got: `options=${els.length} matches=${match.length}` }; }
  await tab.id(match[0].id).click();
  await sleep(300);
  const got = await readValue(tab, f);
  return { ok: !!got && !/^select one$/i.test(got.trim()) && flat(got).includes(flat(match[0].name)), got, shown: match[0].name };
}

async function pickMultiselect(ctx, f, act) {
  const tab = ctx.tab;
  const wanted = String(act.describe || act.value || "");
  await tab.click(f.selector);
  await tab.fill(f.selector, "");
  await tab.type(f.selector, wanted);
  await tab.press("Enter", { selector: f.selector });
  await sleep(600);
  const els = (await listOptions(tab)) || [];
  const match = narrow(act, els.filter((e) => act.test(e.name || "")));
  if (match.length !== 1) { try { await tab.press("Escape", { selector: f.selector }); } catch { /* ignore */ } return { ok: false, got: `options=${els.length} matches=${match.length}` }; }
  await tab.id(match[0].id).click();
  await sleep(350);
  const got = await evalIn(tab, readMultiInPage, f.selector);
  return { ok: flat(got).includes(flat(match[0].name)), got, shown: match[0].name };
}

async function uploadResume(ctx, f, act) {
  const tab = ctx.tab;
  const name = path.basename(act.path);
  await tab.uploadFile(f.selector, act.path);
  const stem = flat(name.replace(/\.[^.]+$/, ""));
  const ok = await waitUntil(async () => {
    const t = flat(await visibleText(tab));
    if (t.includes(stem)) return true;
    return !!(await readValue(tab, f).catch(() => ""));
  }, { timeout: 9000, every: 400 });
  return { ok: !!ok, got: ok ? name : "", shown: name };
}

async function apply1(ctx, f, act) {
  if (act.op === "text") return fillText(ctx, f, act.value);
  if (act.op === "upload") return uploadResume(ctx, f, act);
  if (act.op === "choose" && f.kind === "combobox") return f.widget === "multiselect" ? pickMultiselect(ctx, f, act) : (f.tag === "button" ? pickDropdown(ctx, f, act) : applyAction(ctx, f, act));
  return applyAction(ctx, f, act);
}

/** Fill every resolvable field of the current step; mirrors form.js#fillForm but understands Workday widgets. */
async function fillStep(ctx) {
  ctx.P = ctx.P || deriveProfile(ctx.profile);
  const scan = await evalIn(ctx.tab, scanInPage, { skip: HISTORY });
  const rep = { filled: [], skipped: [], unanswered: [], unverified: [], consents: [], prefilled: [], fields: scan.fields.length };

  // history sections are verified, never retyped
  for (const r of scan.roots) {
    const name = HISTORY_NAMES[r.section] || r.section;
    rep.prefilled.push({ label: name, value: r.text.slice(0, 160) });
    rep.skipped.push({ label: name, reason: "history entries left as saved by the site (verified only, not retyped)" });
  }
  for (const h of scan.history) {
    if (h.required && !String(h.value).trim()) rep.unanswered.push({ question: `${HISTORY_NAMES[h.section] || h.section}: ${h.label}`, required: true, detail: "required history entry is empty; history is never typed by the bot" });
  }

  for (const f of scan.fields) {
    const q = f.group || f.label || f.placeholder || f.name || "(unlabeled)";
    let act;
    try {
      act = resolveAnswer(f, ctx);
    } catch (e) {
      if (!(e instanceof NeedsHuman)) throw e;
      const TEXTISH = ["text", "textarea", "email", "tel", "url", "number"];
      if (f.required && f.value && TEXTISH.includes(f.kind) && !(e.detail || "").includes("forbidden")) rep.prefilled.push({ label: q, value: String(f.value).slice(0, 80) });
      if (f.required) rep.unanswered.push({ question: e.question, required: true, detail: e.detail });
      else rep.skipped.push({ label: q, reason: `optional, not answerable: ${e.detail || "not in allowlist"}` });
      continue;
    }
    if (act.op === "leave") { rep.skipped.push({ label: q, reason: `left as-is (${act.rule})` }); continue; }
    if (f.kind === "date") { // spinbutton groups are never typed
      if (f.required) rep.unanswered.push({ question: q, required: true, detail: "date widgets are not filled by the bot" });
      else rep.skipped.push({ label: q, reason: "date widget not filled" });
      continue;
    }
    if (act.op === "text" && f.value && sameText(act.value, f.value, f)) { rep.filled.push({ label: q, value: act.value, rule: act.rule, preset: true }); continue; }
    if (act.op === "choose" && f.kind === "combobox" && f.value && act.test && act.test(f.value)) { rep.filled.push({ label: q, value: f.value, rule: act.rule, preset: true }); continue; }
    if (act.op === "upload" && f.value) {
      const stem = flat(path.basename(act.path).replace(/\.[^.]+$/, ""));
      if (flat(f.value).includes(stem)) rep.filled.push({ label: q, value: f.value, rule: act.rule, preset: true });
      else { rep.prefilled.push({ label: q, value: String(f.value).slice(0, 80) }); rep.skipped.push({ label: q, reason: "a file saved by the site is already attached; not replaced" }); }
      continue;
    }
    try {
      const r = await apply1(ctx, f, act);
      if (!r.ok) {
        rep.unverified.push({ label: q, want: act.value || act.describe || act.op, got: r.got });
        if (f.required) rep.unanswered.push({ question: q, required: true, detail: "could not verify the field after filling" });
        continue;
      }
      rep.filled.push({ label: q, value: r.shown, rule: act.rule });
      if (act.consent) { logConsent(ctx, act.consent.text, act.consent.rule); rep.consents.push(act.consent.text); }
    } catch (e) {
      if (e instanceof NeedsHuman) {
        if (f.required) rep.unanswered.push({ question: e.question, required: true, detail: e.detail });
        else rep.skipped.push({ label: q, reason: e.detail });
      } else {
        rep.unverified.push({ label: q, want: act.value || act.describe || act.op, got: `error: ${e.message}` });
        if (f.required) rep.unanswered.push({ question: q, required: true, detail: `fill error: ${e.message}` });
      }
    }
  }
  return rep;
}

// ---- steps --------------------------------------------------------------------------------------------------
async function stepForm(ctx, st) {
  const tab = ctx.tab;
  await settle(tab);
  const rep = await fillStep(ctx);
  F.merge(ctx, rep);
  const blocked = F.submittable(rep);
  if (blocked) return F.manual(ctx, "unanswered_questions", { unanswered: rep.unanswered.map((u) => u.question), evidence: blocked });
  const ch = await detectChallenge(tab);
  if (ch) return F.manual(ctx, "captcha_required", { evidence: `bot check present (not solved): ${ch}` });

  const p = await probe(ctx);
  if (!p.nav) return null; // page moved on by itself; re-detect
  if (p.nav.disabled) return F.manual(ctx, "no_navigation_button", { evidence: `"${p.nav.text}" is disabled` });

  if (FINAL.test(p.nav.text)) {
    if (p.errors.length) return F.manual(ctx, "validation_error", { evidence: p.errors.join(" | "), unanswered: p.errors });
    const els = await observe(tab);
    const final = els.find((e) => e.role === "button" && FINAL.test(e.name || ""));
    if (!final) return F.manual(ctx, "submit_button_missing");
    if (ctx.dryRun) {
      return result("skipped", "dry_run_complete", { dryRun: true, wouldSubmit: F.dryRunSummary(ctx, final.name), evidence: `stopped before "${final.name}" on ${st.step || "Review"}`, consents: ctx.consents.slice(), tabName: ctx.tabName });
    }
    let r = await F.submitAndConfirm(ctx, final);
    ctx.submitted = true;
    if (r.status === "failed" && r.reason === "no_confirmation") {
      const text = await visibleText(tab);
      if (DONE.test(text)) r = result("applied", "confirmation_page", { evidence: text.replace(/\s+/g, " ").slice(0, 300), consents: ctx.consents.slice(), tabName: ctx.tabName });
    }
    return r;
  }

  if (FORBIDDEN_CLICK.test(p.nav.text)) throw new Blocked("forbidden_control", `refused to click "${p.nav.text}"`);
  const before = await F.fingerprint(tab);
  const beforeStep = p.step;
  await press(ctx, "bottom-navigation-next-button");
  let after = null;
  await waitUntil(async () => {
    after = await probe(ctx);
    return after.errors.length > 0 || !after.nav || after.step !== beforeStep || (await F.fingerprint(tab)) !== before;
  }, { timeout: 15000, every: 500 });
  if (after && after.errors.length) return F.manual(ctx, "validation_error", { evidence: after.errors.join(" | "), unanswered: after.errors });
  if (after && after.nav && after.step === beforeStep && (await F.fingerprint(tab)) === before) return F.manual(ctx, "validation_error", { evidence: "page did not advance" });
  return null;
}

async function stepChoice(ctx, st) {
  const c = st.p.choice;
  const before = await F.fingerprint(ctx.tab);
  if (c.manual) await press(ctx, "applyManually");
  else if (c.last) await press(ctx, "useMyLastApplication");
  else return F.manual(ctx, "apply_manually_unavailable", { evidence: "only 'Autofill with Resume' is offered; it is not used" });
  await F.waitChange(ctx.tab, before, 15000);
  return null;
}

async function applyFlow(ctx) {
  ctx.P = ctx.P || deriveProfile(ctx.profile);
  const trail = [];
  let same = 0, last = null;
  for (let i = 0; i < 30; i++) {
    const st = await detectState(ctx);
    const key = st.s === "form" ? `form:${st.step}` : st.s;
    trail.push(key);
    ctx.log && ctx.log("workday_state", { s: st.s, step: st.step, i });
    same = key === last ? same + 1 : 0;
    last = key;
    if (same >= 3 && st.s !== "unknown") return F.manual(ctx, `stuck_${st.s}`, { trail });
    const ch = st.s !== "done" ? await detectChallenge(ctx.tab) : null;
    if (ch) return F.manual(ctx, "captcha_required", { evidence: `bot check present (not solved): ${ch}`, trail });

    let out = null;
    switch (st.s) {
      case "done":
        return ctx.submitted
          ? result("applied", "confirmation_page", { evidence: st.text.slice(0, 300), consents: ctx.consents.slice(), trail })
          : result("skipped", "confirmation_page_already_shown", { evidence: st.text.slice(0, 300), trail });
      case "already_applied": return result("skipped", "already_applied", { evidence: (st.text.match(ALREADY) || [""])[0], trail });
      case "closed": return result("skipped", "posting_closed", { evidence: (st.text.match(CLOSED) || [""])[0], trail });
      case "account": out = await stepAccount(ctx, st, trail); break;
      case "job": {
        const before = await F.fingerprint(ctx.tab);
        await press(ctx, "adventureButton");
        await F.waitChange(ctx.tab, before, 15000);
        break;
      }
      case "choice": out = await stepChoice(ctx, st); break;
      case "form": out = await stepForm(ctx, st); break;
      default: {
        await sleep(1800);
        if (trail.filter((x) => x === "unknown").length >= 10) return F.manual(ctx, "unrecognized_page", { evidence: st.text.slice(0, 300), trail });
      }
    }
    if (out) { out.trail = trail; return out; }
  }
  return F.manual(ctx, "too_many_steps", { trail, evidence: await snippet(ctx.tab, 200) });
}

/** The flow plus the account extras (who/what was created or reset; never a password). */
async function apply(ctx) {
  const r = await applyFlow(ctx);
  if (!ctx.accountRuns) return r;
  return require("../credentials").redact({ ...r, ...(ctx.accountExtras || {}) });
}

module.exports = {
  name: "workday",
  matches(url) { try { return HOST.test(new URL(url).hostname); } catch { return false; } },
  apply,
  _detectState: detectState,
};
