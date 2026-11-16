// Runner for single-page application forms (Greenhouse, Lever, Ashby, SmartRecruiters' last page): optionally
// press the page's Apply control to reach the form, fill it through answers.js, stop with 'manual' on
// anything unanswerable, stop BEFORE the final Submit in dry-run, and (live only) submit + confirm.
//
// Account walls: an adapter's formReady/scan may throw Blocked('password_required'|'account_required').
// withAccountGate turns that into either (a) the usual manual stop (+ wouldCreateAccount:true) or, only when
// ctx.dryRun === false AND ctx.opts.allowAccountCreation === true, ONE call to account.js ensureAccount
// (authorized create / password-reset path), after which the same tab's form flow is resumed once.
// Greenhouse and Lever never throw those reasons, so their behavior is unchanged.
"use strict";
const { sleep, observe, matchEl, clickEl, visibleText, detectChallenge, result, waitUntil, snippet, Blocked } = require("./lib");
const { deriveProfile } = require("./answers");
const { scanForm, fillForm } = require("./form");
const F = require("./flow");

const CLOSED = /no longer (available|accepting)|(job|position|posting) (is )?(no longer|not) (available|found|open)|this job has (been )?(closed|filled)|posting has expired|page you('re| are) looking for/i;

/**
 * @param {object} ctx adapter context
 * @param {{name:string, formReady:(fields:object[])=>boolean, applyBtn?:RegExp, submitBtn?:RegExp, scope?:string}} cfg
 */
function runSinglePage(ctx, cfg) { return withAccountGate(ctx, cfg.name, () => runSinglePageOnce(ctx, cfg)); }

async function runSinglePageOnce(ctx, cfg) {
  ctx.P = ctx.P || deriveProfile(ctx.profile);
  ctx.channel = ctx.opts.otpChannel || (ctx.otp && ctx.otp.channel) || "email";
  const tab = ctx.tab;

  // resume: the site is already asking for the emailed security code after a previous Submit
  if (await F.codePromptVisible(tab)) return F.afterSubmitOtp(ctx);

  let fields = await scanForm(tab, cfg.scope);
  if (!cfg.formReady(fields)) {
    const text = await visibleText(tab);
    if (CLOSED.test(text) && !fields.length) return result("skipped", "posting_closed", { evidence: (text.match(CLOSED) || [""])[0] });
    const apply = matchEl(await observe(tab), { roles: ["button", "link"], name: cfg.applyBtn || /^apply( now| for this job| for this position)?$/i });
    if (apply) {
      const before = await F.fingerprint(tab);
      await clickEl(tab, apply);
      await F.waitChange(tab, before, 15000);
    }
    fields = await waitUntil(async () => { const f = await scanForm(tab, cfg.scope); return cfg.formReady(f) ? f : null; }, { timeout: 15000, every: 700 }) || [];
    if (!fields.length) return F.manual(ctx, "form_not_found", { evidence: (await snippet(tab, 300)) });
  }

  const rep = await fillForm(ctx, { scope: cfg.scope });
  F.merge(ctx, rep);
  const blocked = F.submittable(rep);
  if (blocked) return F.manual(ctx, "unanswered_questions", { unanswered: rep.unanswered.map((u) => u.question), evidence: blocked });

  const final = matchEl(await observe(tab), { roles: ["button"], name: cfg.submitBtn || F.FINAL_BTN });
  if (!final) return F.manual(ctx, "submit_button_missing");

  if (ctx.dryRun) {
    return result("skipped", "dry_run_complete", { dryRun: true, wouldSubmit: F.dryRunSummary(ctx, final.name), evidence: `stopped before "${final.name}"`, consents: ctx.consents.slice(), tabName: ctx.tabName });
  }
  // live: bot checks are for humans; we only detect them
  const ch = await detectChallenge(tab);
  if (ch) return F.manual(ctx, "captcha_required", { evidence: `bot check present (not solved): ${ch}` });
  await sleep(500);
  const out = await F.submitAndConfirm(ctx, final);
  ctx.submitted = true;
  return out;
}

const ACCOUNT_STOPS = new Set(["password_required", "account_required"]);
const isAccountStop = (e) => e instanceof Blocked && ACCOUNT_STOPS.has(e.reason);
const accountsEnabled = (ctx) => ctx.dryRun === false && !!ctx.opts && ctx.opts.allowAccountCreation === true;

/**
 * Handle one account/password wall. Returns null when the account step succeeded and the form flow may resume,
 * otherwise the manual result to return. Never loops: at most one ensureAccount call per run context.
 */
async function accountStop(ctx, adapterName, e) {
  if (!accountsEnabled(ctx)) {
    const why = ctx.dryRun === false ? "account creation is not enabled for this run (opts.allowAccountCreation)" : "dry run: no account is created and no reset is requested";
    return F.manual(ctx, e.reason, { wouldCreateAccount: true, evidence: `${e.evidence || e.reason}; ${why}` });
  }
  if (ctx.accountAttempted) return F.manual(ctx, "account_flow_failed", { evidence: `account step already ran once for this application but the page still shows ${e.reason}` });
  ctx.accountAttempted = true;
  const { ensureAccount, detectAccountState } = require("./account");
  ctx.log && ctx.log("account_gate", { adapter: adapterName, wall: e.reason });
  const r = await ensureAccount(ctx, { adapterName, detect: () => detectAccountState(ctx.tab) });
  if (r && r.status === "continue") {
    if (r.account) ctx.account = r.account;
    return null;
  }
  return (r && r.result) || F.manual(ctx, "account_flow_failed", { evidence: "account helper returned no continuation" });
}

const withAccount = (ctx, out) => (ctx.account && out && !out.account ? { ...out, account: ctx.account } : out);

/** Run fn(); on an account wall resolve it through accountStop and, if it succeeded, run fn() once more on the same tab. */
async function withAccountGate(ctx, adapterName, fn) {
  try { return withAccount(ctx, await fn()); } catch (e) {
    if (!isAccountStop(e)) throw e;
    const stop = await accountStop(ctx, adapterName, e);
    if (stop) return withAccount(ctx, stop);
  }
  try { return withAccount(ctx, await fn()); } catch (e) {
    if (!isAccountStop(e)) throw e;
    return withAccount(ctx, await accountStop(ctx, adapterName, e)); // attempted already -> account_flow_failed
  }
}

module.exports = { runSinglePage, withAccountGate, accountStop, isAccountStop };
