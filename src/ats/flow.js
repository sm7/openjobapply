// Flow pieces shared by adapters: page fingerprints, validation errors, the submit gate, dry-run
// summary, OTP stage handling and final confirmation.
"use strict";
const { sleep, evalIn, visibleText, observe, matchEl, clickEl, waitUntil, detectChallenge, shot, snippet, result, CONFIRMATION, jobKey, Blocked } = require("./lib");
const { enterCode } = require("./form");
const { otpToken } = require("./otp");

const FINAL_BTN = /^(submit( my)?( application)?|submit & apply|finish|complete( application)?|send application|confirm and submit)$/i;
const NEXT_BTN = /^(continue|next|save (and|&) (continue|next)|save & continue|review|next step|proceed)$/i;

async function fingerprint(tab) {
  const t = await visibleText(tab);
  let h = 0;
  const s = t.slice(0, 3000);
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return `${h}:${t.length}`;
}

async function waitChange(tab, before, timeout = 12000) {
  return waitUntil(async () => (await fingerprint(tab)) !== before, { timeout, every: 500 });
}

/** Visible inline validation / alert messages (what the site complains about). */
async function validationErrors(tab) {
  return evalIn(tab, () => {
    const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(e).visibility !== "hidden"; };
    const out = new Set();
    for (const e of document.querySelectorAll("[role=alert], [aria-live=assertive], .error, .errors, .field-error, .invalid-feedback, [class*=error-message], [class*=ErrorMessage], [data-error]")) {
      const t = (e.innerText || "").replace(/\s+/g, " ").trim();
      if (t && t.length < 300 && vis(e)) out.add(t);
    }
    return [...out].slice(0, 8);
  });
}

/** Resolve the Submit / Next button of the current page. */
async function findNav(tab) {
  const els = await observe(tab);
  const btns = els.filter((e) => e.role === "button" || e.role === "link");
  const final = matchEl(btns, { roles: ["button", "link"], name: FINAL_BTN });
  const next = matchEl(btns, { roles: ["button", "link"], name: NEXT_BTN });
  return { final, next, els };
}

/** Every required field must have been filled from verified profile data; nothing forbidden ticked. */
function submittable(rep) {
  if (rep.unanswered.length) return `unanswered: ${rep.unanswered.map((u) => u.question).join(" | ")}`;
  return null;
}

function merge(ctx, rep) {
  ctx.filled.push(...rep.filled);
  ctx.skipped.push(...rep.skipped);
  ctx.prefilled.push(...(rep.prefilled || []));
}

function dryRunSummary(ctx, finalName) {
  return {
    url: ctx.url,
    submitButton: finalName,
    fields: ctx.filled.map((f) => ({ label: f.label, value: f.value, rule: f.rule })),
    prefilledBySite: ctx.prefilled,
    skipped: ctx.skipped.map((s) => `${s.label}: ${s.reason}`),
    consents: ctx.consents,
  };
}

async function manual(ctx, reason, extra = {}) {
  const text = extra.evidence || (await snippet(ctx.tab, 300));
  const s = await shot(ctx, reason);
  return result("manual", reason, { ...extra, evidence: s ? `${text} [screenshot: ${s}]` : text, consents: ctx.consents.slice(), tabName: ctx.tabName });
}

/** Is a "type the code we emailed/texted you" prompt on screen? */
async function codePromptVisible(tab) {
  const t = await visibleText(tab);
  if (!/(security|verification|confirmation|one[- ]time) code|verify your (email|identity)|enter the code/i.test(t)) return false;
  const boxes = await evalIn(tab, () => [...document.querySelectorAll("input")].filter((i) => { const r = i.getBoundingClientRect(); return r.width > 2 && r.height > 2 && ["text", "tel", "number", ""].includes((i.type || "").toLowerCase()); }).map((i) => ({ max: i.maxLength, label: (i.getAttribute("aria-label") || (i.labels && i.labels[0] && i.labels[0].textContent) || i.placeholder || i.name || "") })));
  return boxes.filter((b) => b.max === 1).length >= 4 || boxes.some((b) => /code|otp|verif|security|pin/i.test(b.label));
}

/** Wait for a confirmation page; hand captcha/OTP to a human; else report failure with the site's own errors. */
async function awaitConfirmation(ctx, before, timeout, allowOtp) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    await sleep(1000);
    const text = await visibleText(ctx.tab);
    if (CONFIRMATION.test(text)) return result("applied", "confirmation_page", { evidence: text.replace(/\s+/g, " ").slice(0, 300), consents: ctx.consents.slice(), tabName: ctx.tabName });
    const ch = await detectChallenge(ctx.tab);
    if (ch) return result("manual", "captcha_required", { evidence: `challenge after submit (not solved): ${ch}`, consents: ctx.consents.slice(), tabName: ctx.tabName });
    if (allowOtp && (await fingerprint(ctx.tab)) !== before && (await codePromptVisible(ctx.tab))) return afterSubmitOtp(ctx);
  }
  const errs = await validationErrors(ctx.tab);
  return result("failed", errs.length ? "validation_error_after_submit" : "no_confirmation", { evidence: errs.join(" | ") || (await snippet(ctx.tab, 300)), consents: ctx.consents.slice(), tabName: ctx.tabName });
}

/** The site asked for an emailed/texted code AFTER Submit (e.g. Greenhouse security code). The code was sent by the site itself, so no send authorization is needed. */
async function afterSubmitOtp(ctx) {
  const code = await ctx.otp.getCode(otpReq(ctx, "awaiting_code"));
  if (!code) return otpRequired(ctx, "awaiting_code", `bun src/ats/otp.js code ${ctx.token} <code>; application submitted but NOT yet confirmed`);
  const before = await fingerprint(ctx.tab);
  if ((await submitCode(ctx, code, /^(submit|verify|confirm|continue)( application)?$/i)) === "no_input") return manual(ctx, "code_input_not_found");
  return awaitConfirmation(ctx, before, 30000, false);
}

/** After pressing the final button: confirmation text -> applied; challenge -> manual; else failed. */
async function submitAndConfirm(ctx, finalEl, timeout = 30000) {
  const before = await fingerprint(ctx.tab);
  await clickEl(ctx.tab, finalEl);
  return awaitConfirmation(ctx, before, timeout, true);
}

// ---- OTP stage handling (shared) ------------------------------------------------------------------
function otpReq(ctx, stage) {
  return { token: ctx.token, site: ctx.site, jobId: jobKey(ctx.job), url: ctx.url, channel: ctx.channel, stage };
}
function otpRequired(ctx, stage, instruction) {
  return result("manual", "otp_required", {
    evidence: `${stage === "ready_to_send" ? "stopped BEFORE requesting a code (nothing sent)" : "code requested; waiting for a human-supplied code"}${instruction ? ` — ${instruction}` : ""}`,
    resumeToken: ctx.token, otpStage: stage, tabName: ctx.tabName, consents: ctx.consents.slice(),
  });
}

/** Type a human-supplied code and press the confirm button. Returns 'entered' | 'no_input'. */
async function submitCode(ctx, code, btnName = /^(verify|submit|continue|confirm|next)$/i) {
  const ok = await enterCode(ctx.tab, code);
  if (!ok) return "no_input";
  const els = await observe(ctx.tab);
  const b = matchEl(els, { roles: ["button"], name: btnName });
  if (b) await clickEl(ctx.tab, b);
  else await ctx.tab.press("input:focus", "Enter").catch(() => {});
  return "entered";
}

module.exports = { FINAL_BTN, NEXT_BTN, fingerprint, waitChange, validationErrors, findNav, submittable, merge, dryRunSummary, manual, submitAndConfirm, afterSubmitOtp, codePromptVisible, otpReq, otpRequired, submitCode, otpToken };
