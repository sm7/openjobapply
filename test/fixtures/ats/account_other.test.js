// Fixture tests for the account gate in the Ashby / SmartRecruiters adapters and the shared singlepage runner
// (does not touch run.js). Needs the omp `browser` global, so run it from an omp eval cell:
//
//   const run = require("/abs/path/test/fixtures/ats/account_other.test.js"); await run(browser);
//
// src/ats/account.js is replaced by an in-test stub through the require cache (only from this file): the stub
// records every ensureAccount call and "creates the account" by calling window.__makeAccount() on the fixture
// page. No real site, no real mailbox, no credential file and no password is involved. Asserts:
//   * live + opts.allowAccountCreation === true  -> ensureAccount called exactly once, the form flow RESUMES on the
//     same tab and the application completes; the result carries extras.account
//   * dry run (even with the flag) / live without the flag -> ensureAccount NEVER called, manual stop with
//     wouldCreateAccount:true and nothing typed
//   * helper returns manual (e.g. captcha_required) -> returned as-is, nothing submitted
//   * wall still present after a "successful" helper -> account_flow_failed, never a second helper call (no loop)
"use strict";
const fs = require("fs");
const os = require("os");
const path = require("path");

module.exports = async function run(browser) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "ats-account-other-"));
  process.env.ATS_CONSENT_LOG = path.join(tmp, "consents.log");
  process.env.ATS_EVIDENCE_DIR = path.join(tmp, "evidence");
  const atsDir = path.resolve(__dirname, "../../../src/ats") + path.sep;
  for (const k of Object.keys(require.cache)) if (k.startsWith(atsDir)) delete require.cache[k];

  // ---- inject the account.js stub (the real file must exist so the require cache key resolves) ----
  const accountPath = path.join(atsDir, "account.js");
  if (!fs.existsSync(accountPath)) throw new Error("src/ats/account.js does not exist yet: the require-cache stub needs a resolvable path");
  const calls = [];
  const mode = { next: "create" }; // 'create' | 'captcha'
  const stub = {
    detectAccountState: async () => "create",
    ensureAccount: async (ctx, o) => {
      const detected = await o.detect();
      calls.push({ adapterName: o.adapterName, dryRun: ctx.dryRun, allow: ctx.opts.allowAccountCreation, detected, hasSignInFlow: o.signInFlow !== undefined });
      if (mode.next === "captcha") return { ready: false, status: "manual", result: { status: "manual", reason: "captcha_required", evidence: "stub: captcha on create form" } };
      await ctx.tab.evaluate("window.__makeAccount()");
      const account = { site: ctx.site, email: "jane.doe@example.com", action: "created" };
      return { ready: true, status: "continue", action: "created", account, extras: { account } };
    },
  };
  const Module = require("module");
  const m = new Module(accountPath);
  m.filename = accountPath; m.loaded = true; m.exports = stub;
  require.cache[accountPath] = m;

  const { Blocked, jobKey } = require("../../../src/ats/lib.js");
  const { NeedsHuman, deriveProfile } = require("../../../src/ats/answers.js");
  const { HumanPromptOtpProvider, otpToken } = require("../../../src/ats/otp.js");
  const singlepage = require("../../../src/ats/singlepage.js");
  const smartrecruiters = require("../../../src/ats/adapters/smartrecruiters.js");
  const ashby = require("../../../src/ats/adapters/ashby.js");
  const profile = JSON.parse(fs.readFileSync(path.resolve(__dirname, "../profile.fixture.json"), "utf8"));
  const root = path.join(__dirname, "account_other");
  const server = Bun.serve({ port: 0, fetch(req) { const f = Bun.file(path.join(root, new URL(req.url).pathname)); return f.size ? new Response(f, { headers: { "content-type": "text/html" } }) : new Response("nf", { status: 404 }); } });
  const base = `http://127.0.0.1:${server.port}`;
  let pass = 0, fail = 0;
  const check = (name, cond, extra) => { if (cond) { pass++; console.log("  ok  ", name); } else { fail++; console.log("  FAIL", name, extra === undefined ? "" : JSON.stringify(extra)); } };
  const opened = [];
  const open = async (name, file) => { opened.push(name); return browser.open({ name, url: `${base}/${file}`, app: "own" }); };
  const logOf = (tab) => tab.evaluate("window.__log");
  const has = (log, k) => log.filter((e) => e.k === k);

  async function runAdapter(adapter, tab, id, { dryRun, allow }) {
    const job = { id, source: "linkedin", url: `https://example.invalid/${id}`, apply_url: `https://example.invalid/${id}` };
    const url = await tab.url();
    const site = new URL(url).hostname;
    const ctx = {
      browser, tab, tabName: "provided", job, profile, P: deriveProfile(profile), opts: allow === undefined ? {} : { allowAccountCreation: allow }, dryRun, log: () => {},
      otp: new HumanPromptOtpProvider({ dir: path.join(tmp, "otp"), env: {} }),
      site, url, token: otpToken(site, jobKey(job)),
      consents: [], filled: [], skipped: [], prefilled: [], submitted: false,
    };
    try { return await adapter.apply(ctx); } catch (e) {
      if (e instanceof NeedsHuman) return { status: "manual", reason: "unanswered_questions", unanswered: [e.question] };
      if (e instanceof Blocked) return { status: "manual", reason: e.reason, evidence: e.evidence };
      return { status: "failed", reason: "adapter_error", evidence: `${e.name}: ${e.message}` };
    }
  }
  const reset = () => { calls.length = 0; mode.next = "create"; };

  try {
    console.log("# exports");
    check("singlepage exports runSinglePage/withAccountGate/accountStop/isAccountStop", ["runSinglePage", "withAccountGate", "accountStop", "isAccountStop"].every((k) => typeof singlepage[k] === "function"));

    // ======================= Ashby =======================
    console.log("# Ashby: password wall, live + allowAccountCreation -> ensureAccount once, flow resumes, applied");
    reset();
    let tab = await open("fx-acc-ab1", "ashby_account.html");
    let r = await runAdapter(ashby, tab, "fx-acc-ab-1", { dryRun: false, allow: true });
    let log = await logOf(tab);
    check("applied (confirmation page)", r.status === "applied" && /successfully submitted/i.test(r.evidence || ""), r);
    check("ensureAccount called exactly once, adapterName ashby, detect wired, generic flow", calls.length === 1 && calls[0].adapterName === "ashby" && calls[0].detected === "create" && calls[0].hasSignInFlow === false, calls);
    check("extras.account reported (site/email/action)", r.account && r.account.action === "created" && r.account.email === "jane.doe@example.com" && r.account.site === "127.0.0.1", r.account);
    check("account created once, submitted once, form filled after the account step", has(log, "ACCOUNT_CREATED").length === 1 && has(log, "SUBMITTED").length === 1 && (has(log, "form")[0] || { v: {} }).v.name === "Jane Doe", log);
    check("marketing box NOT ticked", (has(log, "form")[0] || { v: {} }).v.mk === false, log);
    check("result carries no password-like data", !/pass(word)?["']?\s*:/i.test(JSON.stringify(r).replace(/page asks for a password/gi, "")), r);

    console.log("# Ashby: dry run (even with allowAccountCreation) -> ensureAccount never called, wouldCreateAccount");
    reset();
    tab = await open("fx-acc-ab2", "ashby_account.html");
    r = await runAdapter(ashby, tab, "fx-acc-ab-2", { dryRun: true, allow: true });
    log = await logOf(tab);
    check("manual password_required + wouldCreateAccount:true", r.status === "manual" && r.reason === "password_required" && r.wouldCreateAccount === true, r);
    check("ensureAccount NOT called, nothing typed, nothing created", calls.length === 0 && log.length === 0 && (await tab.evaluate(`document.getElementById('_systemfield_name').value`)) === "", [calls, log]);

    console.log("# Ashby: live WITHOUT allowAccountCreation -> same stop, no helper call");
    reset();
    tab = await open("fx-acc-ab3", "ashby_account.html");
    r = await runAdapter(ashby, tab, "fx-acc-ab-3", { dryRun: false });
    log = await logOf(tab);
    check("manual password_required + wouldCreateAccount:true", r.status === "manual" && r.reason === "password_required" && r.wouldCreateAccount === true, r);
    check("ensureAccount NOT called, nothing created/submitted", calls.length === 0 && log.length === 0, [calls, log]);

    console.log("# Ashby: helper returns manual (captcha_required) -> passed through, nothing submitted");
    reset(); mode.next = "captcha";
    tab = await open("fx-acc-ab4", "ashby_account.html");
    r = await runAdapter(ashby, tab, "fx-acc-ab-4", { dryRun: false, allow: true });
    log = await logOf(tab);
    check("manual captcha_required (helper's result as-is)", r.status === "manual" && r.reason === "captcha_required" && /stub: captcha/.test(r.evidence || ""), r);
    check("helper called once, no submit, no account", calls.length === 1 && has(log, "SUBMITTED").length === 0 && has(log, "ACCOUNT_CREATED").length === 0, [calls, log]);

    console.log("# Ashby: helper 'succeeds' but the wall stays -> account_flow_failed, no second call (never loops)");
    reset();
    tab = await open("fx-acc-ab5", "ashby_account.html?stuck=1");
    r = await runAdapter(ashby, tab, "fx-acc-ab-5", { dryRun: false, allow: true });
    log = await logOf(tab);
    check("manual account_flow_failed", r.status === "manual" && r.reason === "account_flow_failed", r);
    check("exactly one helper call, nothing submitted", calls.length === 1 && has(log, "ACCOUNT_ATTEMPT").length === 1 && has(log, "SUBMITTED").length === 0, [calls, log]);

    console.log("# Ashby: 'Create an account to apply' text wall (no form) -> account_required in dry run, resumes live");
    reset();
    tab = await open("fx-acc-ab6", "ashby_account.html?wall=1");
    r = await runAdapter(ashby, tab, "fx-acc-ab-6", { dryRun: true, allow: true });
    check("dry run: manual account_required + wouldCreateAccount, helper not called", r.status === "manual" && r.reason === "account_required" && r.wouldCreateAccount === true && calls.length === 0, [r, calls]);
    tab = await open("fx-acc-ab7", "ashby_account.html?wall=1");
    r = await runAdapter(ashby, tab, "fx-acc-ab-7", { dryRun: false, allow: true });
    log = await logOf(tab);
    check("live: helper once, then applied on the same tab", calls.length === 1 && r.status === "applied" && r.account && r.account.action === "created" && has(log, "SUBMITTED").length === 1, [r, calls, log]);

    // ======================= SmartRecruiters =======================
    console.log("# SmartRecruiters: password wall on the profile page, live + allow -> ensureAccount once, flow resumes, applied");
    reset();
    tab = await open("fx-acc-sr1", "sr_account.html?start=profile");
    r = await runAdapter(smartrecruiters, tab, "fx-acc-sr-1", { dryRun: false, allow: true });
    log = await logOf(tab);
    check("applied (Thank you for applying)", r.status === "applied" && /thank you for applying/i.test(r.evidence || ""), r);
    check("ensureAccount once, adapterName smartrecruiters, detect wired", calls.length === 1 && calls[0].adapterName === "smartrecruiters" && calls[0].detected === "create", calls);
    check("extras.account reported", r.account && r.account.action === "created", r.account);
    check("account created once; profile Next then Submit once; marketing/talent unticked", has(log, "ACCOUNT_CREATED").length === 1 && has(log, "NEXT").length === 1 && has(log, "SUBMITTED").length === 1 && has(log, "form")[0].v.mk === false && has(log, "form")[0].v.tc === false, log);

    console.log("# SmartRecruiters: dry run (even with the flag) -> never calls the helper");
    reset();
    tab = await open("fx-acc-sr2", "sr_account.html?start=profile");
    r = await runAdapter(smartrecruiters, tab, "fx-acc-sr-2", { dryRun: true, allow: true });
    log = await logOf(tab);
    check("manual password_required + wouldCreateAccount:true", r.status === "manual" && r.reason === "password_required" && r.wouldCreateAccount === true, r);
    check("helper not called, nothing typed", calls.length === 0 && log.length === 0 && (await tab.evaluate(`document.getElementById('fn').value`)) === "", [calls, log]);

    console.log("# SmartRecruiters: live WITHOUT allowAccountCreation -> same stop");
    reset();
    tab = await open("fx-acc-sr3", "sr_account.html?start=profile");
    r = await runAdapter(smartrecruiters, tab, "fx-acc-sr-3", { dryRun: false });
    check("manual password_required + wouldCreateAccount, helper not called", r.status === "manual" && r.reason === "password_required" && r.wouldCreateAccount === true && calls.length === 0, [r, calls]);

    console.log("# SmartRecruiters: helper manual (captcha_required) passes through; stuck wall -> account_flow_failed");
    reset(); mode.next = "captcha";
    tab = await open("fx-acc-sr4", "sr_account.html?start=profile");
    r = await runAdapter(smartrecruiters, tab, "fx-acc-sr-4", { dryRun: false, allow: true });
    log = await logOf(tab);
    check("manual captcha_required, nothing submitted", r.status === "manual" && r.reason === "captcha_required" && calls.length === 1 && has(log, "SUBMITTED").length === 0, [r, calls]);
    reset();
    tab = await open("fx-acc-sr5", "sr_account.html?start=profile&stuck=1");
    r = await runAdapter(smartrecruiters, tab, "fx-acc-sr-5", { dryRun: false, allow: true });
    check("account_flow_failed after exactly one helper call", r.status === "manual" && r.reason === "account_flow_failed" && calls.length === 1, [r, calls]);

    console.log("# SmartRecruiters: 'Create an account to apply' wall -> live resumes from the posting page");
    reset();
    tab = await open("fx-acc-sr6", "sr_account.html?wall=1");
    r = await runAdapter(smartrecruiters, tab, "fx-acc-sr-6", { dryRun: false, allow: true });
    log = await logOf(tab);
    check("helper once, then applied on the same tab", calls.length === 1 && r.status === "applied" && r.account && has(log, "SUBMITTED").length === 1, [r, calls, log]);
  } finally {
    server.stop();
    for (const n of opened) { try { await browser.close({ name: n }); } catch { /* ignore */ } }
    delete require.cache[accountPath];
  }
  console.log(`\n${pass} passed, ${fail} failed`);
  return { pass, fail };
};
