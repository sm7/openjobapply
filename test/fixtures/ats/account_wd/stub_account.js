// TEST-ONLY stand-in for src/ats/account.js, injected by account_wd_icims.test.js (never required by src/).
// It implements the ensureAccount contract just enough to drive the ADAPTER hooks (detect + signInFlow) against the
// local fixture pages, using FAKE passwords generated here. It never touches credentials.js or a mailbox.
"use strict";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const stub = {
  config: { email: "jane.doe@example.com", resetLink: null, verifyLink: null },
  mode: "ok", // ok | captcha | noop
  calls: [],
  states: [],
  secrets: [],
  reset() { stub.mode = "ok"; stub.calls = []; stub.states = []; stub.secrets = []; },

  fakePassword() { const p = `Fx!${Math.random().toString(36).slice(2)}Aa9#`; stub.secrets.push(p); return p; },

  /** real contract: type the password with real key events into the field described by `target` */
  async fillCredential(ctx, target, pw) {
    await ctx.tab.fill(target.selector, "");
    await ctx.tab.type(target.selector, pw);
  },
  async detectAccountState() { return "none"; },
  /** same contract as account.js#genericFillNonPassword: untick pre-ticked marketing boxes, fill the rest from the allowlist */
  async genericFillNonPassword(ctx) {
    const { scanForm, fillForm } = require("../../../../src/ats/form");
    const { classifyConsent } = require("../../../../src/ats/answers");
    for (const f of await scanForm(ctx.tab)) {
      if (f.kind === "checkbox" && f.checked && classifyConsent(`${f.group || ""} ${f.label || ""}`, ctx.profile).verdict !== "allow") await ctx.tab.uncheck(f.selector).catch(() => {});
    }
    return fillForm(ctx, { only: (f) => f.kind !== "password" });
  },

  async ensureAccount(ctx, { adapterName, detect, signInFlow: h }) {
    const { email } = stub.config;
    stub.calls.push({ adapterName, hooks: Object.keys(h).sort(), dryRun: ctx.dryRun, allow: ctx.opts && ctx.opts.allowAccountCreation, hasDetect: typeof detect === "function" });
    const root = ctx.rootTab || ctx.tab;
    const okResult = (action) => ({ ready: true, status: "continue", action, account: { site: ctx.site, email, action }, extras: { account: { site: ctx.site, email, action } } });
    if (stub.mode === "captcha") return { ready: false, status: "manual", result: { status: "manual", reason: "captcha_required", evidence: "stub: captcha on the account page" } };
    if (stub.mode === "noop") return okResult("login");

    const state = await detect(ctx);
    stub.states.push(state);
    if (state === "create") {
      await h.fillEmail(ctx, email);
      if (h.fillNames) await h.fillNames(ctx);
      await h.fillPassword(ctx, stub.fakePassword());
      await h.submit(ctx);
      const after = await detect(ctx);
      stub.states.push(after);
      if (after === "verify_email") { await root.goto(stub.config.verifyLink); await sleep(800); }
      return okResult("created");
    }
    if (state === "signin_password") {
      await h.clickForgot(ctx);
      await h.fillEmail(ctx, email);
      await h.submit(ctx);
      const sent = await detect(ctx);
      stub.states.push(sent);
      if (sent !== "reset_sent") return { ready: false, status: "manual", result: { status: "manual", reason: "account_flow_failed", evidence: `stub: expected reset_sent, saw ${sent}` } };
      await root.goto(stub.config.resetLink);
      await sleep(800);
      if (adapterName === "workday") {
        const resetState = await detect(ctx);
        stub.states.push(resetState);
        if (resetState !== "reset_form") return { ready: false, status: "manual", result: { status: "manual", reason: "account_flow_failed", evidence: `stub: expected reset_form, saw ${resetState}` } };
      }
      const pw = stub.fakePassword();
      await h.submitNewPassword(ctx, pw);
      await sleep(500);
      await h.fillEmail(ctx, email);
      await h.fillPassword(ctx, pw);
      await h.submit(ctx);
      return okResult("reset");
    }
    return { ready: false, status: "manual", result: { status: "manual", reason: "account_flow_failed", evidence: `stub: unsupported state ${state}` } };
  },
};

module.exports = stub;
