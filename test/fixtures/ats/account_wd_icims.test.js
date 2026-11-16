// Fixture test for the AUTHORIZED account path in src/ats/adapters/workday.js and icims.js. Needs the omp `browser`
// global, so run it from an omp eval cell (managed headless browser; NEVER the Chrome relay, a live site or the real mailbox):
//
//   const run = require("/abs/path/test/fixtures/ats/account_wd_icims.test.js"); await run(browser);
//
// Serves account_wd/ (/wd/), account_ic/ (/ic/) and the existing workday/ (/workday/) and icims/ (/icims/) fixtures from
// 127.0.0.1. src/ats/account.js is replaced by account_wd/stub_account.js (test-only; it drives the ADAPTER hooks with FAKE
// passwords and never touches credentials.js or a mailbox). The pages POST every interesting event to /__log, so assertions
// survive the real navigations between steps. Proves: dry run / un-authorized live NEVER call ensureAccount and report
// wouldCreateAccount / wouldResetPassword; authorized live calls it once, the adapter hooks fill the fixture pages
// (terms tickbox only, marketing untouched, matching confirm password), and the application then continues on the SAME
// tab through to the confirmation page; policy stops (captcha, no-progress cap) come back as manual results.
"use strict";
const fs = require("fs");
const os = require("os");
const path = require("path");
const Module = require("module");

module.exports = async function run(browser) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "ats-account-wdic-"));
  process.env.ATS_CONSENT_LOG = path.join(tmp, "consents.log");
  process.env.ATS_EVIDENCE_DIR = path.join(tmp, "evidence");
  process.env.ATS_CREDENTIALS_FILE = path.join(tmp, "credentials.json"); // never the real vault (guarded again before the real-account phase)
  const ROOT = path.resolve(__dirname, "../../..");
  const stubPath = path.join(__dirname, "account_wd", "stub_account.js");
  const stub = require(stubPath);

  // ---- inject the stub for `require("../account")` from the two adapters (and only them), then restore ----------
  const realAccount = path.join(ROOT, "src/ats/account.js");
  const resolveOrig = Module._resolveFilename;
  const hadCache = Object.prototype.hasOwnProperty.call(require.cache, realAccount);
  const cacheOrig = require.cache[realAccount];
  Module._resolveFilename = function (request, parent, ...rest) {
    if (request === "../account" && parent && /src[\\/]ats[\\/]adapters[\\/](workday|icims)\.js$/.test(parent.filename || "")) return stubPath;
    return resolveOrig.call(this, request, parent, ...rest);
  };
  if (fs.existsSync(realAccount)) require.cache[realAccount] = { id: realAccount, filename: realAccount, loaded: true, exports: stub, children: [], paths: [] };

  const ats = require(path.join(ROOT, "src/ats/index.js"));
  const profile = JSON.parse(fs.readFileSync(path.resolve(__dirname, "../profile.fixture.json"), "utf8"));
  const EMAIL = profile.candidate.email;

  const dirs = { wd: path.join(__dirname, "account_wd"), ic: path.join(__dirname, "account_ic"), workday: path.join(__dirname, "workday"), icims: path.join(__dirname, "icims") };
  let events = [];
  const server = Bun.serve({
    port: 0,
    async fetch(req) {
      const u = new URL(req.url);
      if (req.method === "POST" && u.pathname === "/__log") { events.push(JSON.parse(await req.text())); return new Response("ok"); }
      const m = u.pathname.match(/^\/(wd|ic|workday|icims)\/([\w.-]+)$/);
      const f = m && Bun.file(path.join(dirs[m[1]], m[2]));
      return f && f.size ? new Response(f, { headers: { "content-type": "text/html" } }) : new Response("nf", { status: 404 });
    },
  });
  const base = `http://127.0.0.1:${server.port}`;

  let pass = 0, fail = 0;
  const check = (name, cond, extra) => { if (cond) { pass++; console.log("  ok  ", name); } else { fail++; console.log("  FAIL", name, extra === undefined ? "" : JSON.stringify(extra)); } };
  const has = (k) => events.filter((e) => e.k === k);
  const kinds = () => events.map((e) => e.k);
  const noBad = () => has("FORBIDDEN_CLICK").length === 0 && has("BAD_CLICK").length === 0;
  const names = [];
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  async function drive(adapter, name, url, opts) {
    events = [];
    stub.reset();
    names.push(name);
    stub.config.resetLink = adapter === "workday" ? `${base}/wd/flow.html?scenario=signin&view=reset` : `${base}/ic/outer.html?scn=exists&step=reset`;
    stub.config.verifyLink = adapter === "workday" ? `${base}/wd/flow.html?scenario=create_verify&view=verified` : `${base}/ic/outer.html?scn=new_verify&step=verified`;
    const tab = await browser.open({ name, url, app: "own" });
    const apply = adapter === "workday" ? "https://acme.wd5.myworkdayjobs.com/en-US/careers/job/Boston/RN_R12345" : "https://careers-fallon.icims.com/jobs/8548/job";
    const job = { id: name, source: "linkedin", url: apply, apply_url: apply };
    const r = await ats.applyOnSite(browser, job, profile, { tab, adapter, keepOpen: true, ...opts });
    await sleep(400); // let in-flight beacons land
    return { r, tab };
  }
  const wd = (name, scenario, opts) => drive("workday", name, `${base}/wd/flow.html?scenario=${scenario}`, opts);
  const ic = (name, scn, opts) => drive("icims", name, `${base}/ic/outer.html?scn=${scn}`, opts);
  const noSecrets = (r) => !stub.secrets.some((s) => JSON.stringify(r).includes(s));
  const LIVE = { dryRun: false, allowAccountCreation: true };

  try {
    // =============================================================================================== Workday
    console.log("# Workday: dry run never calls ensureAccount");
    let { r, tab } = await wd("fx-acc-wd-dry-create", "create");
    check("manual/account_required + wouldCreateAccount", r.status === "manual" && r.reason === "account_required" && r.wouldCreateAccount === true && !r.wouldResetPassword, r);
    check("ensureAccount NOT called", stub.calls.length === 0, stub.calls);
    check("nothing typed, nothing created, no forbidden control", has("ACCOUNT_CREATED").length === 0 && has("ACCOUNT_REJECTED").length === 0 && noBad(), events);
    const v = await tab.evaluate(`({e: document.getElementById('ca-email').value, p: document.getElementById('ca-pw').value, t: document.getElementById('ca-terms').checked})`);
    check("create form left empty and unticked", v.e === "" && v.p === "" && v.t === false, v);
    check("trail ends in account", (r.trail || []).slice(-1)[0] === "account", r.trail);

    ({ r } = await wd("fx-acc-wd-dry-signin", "signin"));
    check("sign-in wall: wouldCreateAccount + wouldResetPassword reported", r.status === "manual" && r.reason === "account_required" && r.wouldCreateAccount === true && r.wouldResetPassword === true, r);
    check("no Forgot click, no reset request, ensureAccount not called", has("FORGOT_CLICK").length === 0 && has("RESET_REQUESTED").length === 0 && stub.calls.length === 0 && noBad(), events);

    ({ r } = await wd("fx-acc-wd-dry-allow", "create", { dryRun: true, allowAccountCreation: true }));
    check("dry run wins over allowAccountCreation (no call, wouldCreateAccount)", r.status === "manual" && r.wouldCreateAccount === true && stub.calls.length === 0 && has("ACCOUNT_CREATED").length === 0, [r, stub.calls]);

    ({ r } = await wd("fx-acc-wd-dry-chooser", "signin_chooser"));
    check("chooser page, dry run: recognized, wouldResetPassword reported, not 'unrecognized'", r.status === "manual" && r.reason === "account_required" && r.wouldResetPassword === true, r);
    check("chooser page, dry run: pressed NOTHING (neither Google nor email), ensureAccount not called", has("CHOSE_EMAIL").length === 0 && has("FORBIDDEN_CLICK").length === 0 && stub.calls.length === 0 && noBad(), events);

    console.log("# Workday: live without opts.allowAccountCreation stays a hard stop");
    ({ r } = await wd("fx-acc-wd-live-noallow", "create", { dryRun: false }));
    check("manual/account_required, ensureAccount NOT called, nothing created", r.status === "manual" && r.reason === "account_required" && r.wouldCreateAccount === true && stub.calls.length === 0 && has("ACCOUNT_CREATED").length === 0, [r, stub.calls]);
    check("evidence names the missing opt-in", /allowAccountCreation/.test(r.evidence || ""), r.evidence);

    console.log("# Workday: authorized live, NEW account -> application continues on the same tab");
    ({ r, tab } = await wd("fx-acc-wd-live-create", "create", LIVE));
    check("applied (confirmation page)", r.status === "applied" && /application submitted|thanks for applying/i.test(r.evidence || ""), r);
    check("ensureAccount called exactly once with adapterName=workday, live + allowed", stub.calls.length === 1 && stub.calls[0].adapterName === "workday" && stub.calls[0].dryRun === false && stub.calls[0].allow === true && stub.calls[0].hasDetect, stub.calls);
    check("hooks offered: clickCreate/clickForgot/fillEmail/fillPassword/submit/submitNewPassword", ["clickCreate", "clickForgot", "fillEmail", "fillPassword", "submit", "submitNewPassword"].every((k) => stub.calls[0].hooks.includes(k)), stub.calls[0]);
    check("detect() reported create despite the visible Sign In / Forgot password links", stub.states[0] === "create", stub.states);
    const created = (has("ACCOUNT_CREATED")[0] || {}).v || {};
    check("account created with the profile email, matching confirm password, terms ticked", created.email === EMAIL && created.match === true && created.terms === true && created.pwLen >= 12, created);
    check("marketing/alerts tickbox untouched; no forbidden/bad click", has("MARKETING_TICKED").length === 0 && created.marketing === false && noBad(), events);
    check("result carries account extras (site, email, action) and no password", r.account && r.account.action === "created" && r.account.email === EMAIL && noSecrets(r), r.account);
    const consents = fs.readFileSync(require(path.join(ROOT, "src/ats/lib.js")).CONSENT_LOG, "utf8");
    check("the account-terms tickbox was logged as a consent (allowlist); no marketing/alerts consent", /I have read and agree to the Terms and Conditions/.test(consents) && !/marketing|job alerts?/i.test(consents), consents);
    check("continued after the account: later steps ran and Submit pressed exactly once", (r.trail || []).includes("account") && (r.trail || []).includes("form:My Information") && (r.trail || []).includes("form:Review") && (await tab.evaluate(`window.__log.filter((e) => e.k === 'SUBMITTED').length`)) === 1, r.trail);

    console.log("# Workday: authorized live, EXISTING account -> forgot password -> reset -> login -> continues");
    ({ r, tab } = await wd("fx-acc-wd-live-reset", "signin", LIVE));
    check("applied (confirmation page)", r.status === "applied" && /application submitted|thanks for applying/i.test(r.evidence || ""), r);
    check("states: signin_password then reset_sent", stub.states.slice(0, 2).join(",") === "signin_password,reset_sent", stub.states);
    check("the emailed link is structurally recognized as reset_form, not create", stub.states.includes("reset_form"), stub.states);
    check("Forgot -> reset requested for the profile email -> reset done (matching passwords) -> login ok, in order",
      kinds().filter((k) => ["FORGOT_CLICK", "RESET_REQUESTED", "RESET_DONE", "LOGIN_OK"].includes(k)).join(",") === "FORGOT_CLICK,RESET_REQUESTED,RESET_DONE,LOGIN_OK" && has("RESET_REQUESTED")[0].v === EMAIL && has("RESET_DONE")[0].v.match === true, events);
    check("no wrong login attempt, no Create Account click, no forbidden control", has("LOGIN_FAIL").length === 0 && has("ACCOUNT_CREATED").length === 0 && noBad(), events);
    check("extras: action reset, no password anywhere in the result", r.account && r.account.action === "reset" && noSecrets(r), r.account);
    check("Submit pressed exactly once", (await tab.evaluate(`window.__log.filter((e) => e.k === 'SUBMITTED').length`)) === 1);

    console.log("# Workday: provider chooser (Google + email buttons, no inputs) -> adapter detect() must say choose_method");
    ({ r, tab } = await wd("fx-acc-wd-live-chooser", "signin_chooser", LIVE));
    check("detect() reported choose_method FIRST (the live bug returned signin_email and found no email field)", stub.states[0] === "choose_method", stub.states);
    check("ensureAccount was called once, for the workday adapter, live and authorized", stub.calls.length === 1 && stub.calls[0].adapterName === "workday" && stub.calls[0].dryRun === false, stub.calls);
    check("Google never clicked", has("FORBIDDEN_CLICK").length === 0 && noBad(), events);

    console.log("# Workday: account creation followed by an email-verification page");
    ({ r, tab } = await wd("fx-acc-wd-live-verify", "create_verify", LIVE));
    check("detect saw verify_email; the emailed link was opened in the SAME tab; applied", stub.states.join(",") === "create,verify_email" && has("VERIFIED").length === 1 && r.status === "applied", [stub.states, r]);

    console.log("# Workday: policy stops come back as manual");
    events = []; stub.reset(); stub.mode = "captcha";
    names.push("fx-acc-wd-captcha2");
    const t2 = await browser.open({ name: "fx-acc-wd-captcha2", url: `${base}/wd/flow.html?scenario=create`, app: "own" });
    const wjob = { id: "fx-acc-wd-captcha2", source: "linkedin", url: "https://acme.wd5.myworkdayjobs.com/en-US/careers/job/Boston/RN_R12345", apply_url: "https://acme.wd5.myworkdayjobs.com/en-US/careers/job/Boston/RN_R12345" };
    r = await ats.applyOnSite(browser, wjob, profile, { tab: t2, adapter: "workday", keepOpen: true, ...LIVE });
    await sleep(300);
    check("ensureAccount manual result passed through (captcha_required), nothing created", r.status === "manual" && r.reason === "captcha_required" && stub.calls.length === 1 && has("ACCOUNT_CREATED").length === 0 && has("SUBMITTED").length === 0, r);

    stub.reset(); events = []; stub.mode = "noop";
    names.push("fx-acc-wd-noop");
    const t3 = await browser.open({ name: "fx-acc-wd-noop", url: `${base}/wd/flow.html?scenario=create`, app: "own" });
    r = await ats.applyOnSite(browser, { ...wjob, id: "fx-acc-wd-noop" }, profile, { tab: t3, adapter: "workday", keepOpen: true, ...LIVE });
    check("an account flow that makes no progress is capped (2 runs) -> manual/account_flow_failed, never loops", r.status === "manual" && r.reason === "account_flow_failed" && stub.calls.length === 2, [r.reason, stub.calls.length]);
    stub.mode = "ok";

    // =============================================================================================== iCIMS
    console.log("# iCIMS: dry run never calls ensureAccount");
    ({ r } = await ic("fx-acc-ic-dry-exists", "exists"));
    check("manual/password_required + wouldResetPassword", r.status === "manual" && r.reason === "password_required" && r.wouldResetPassword === true && !r.wouldCreateAccount, r);
    check("only the email step ran: no Forgot, no reset, no login; ensureAccount not called", has("EMAIL_SUBMITTED").length === 1 && has("FORGOT_CLICK").length === 0 && has("RESET_REQUESTED").length === 0 && has("LOGIN_OK").length === 0 && has("LOGIN_FAIL").length === 0 && stub.calls.length === 0 && noBad(), events);
    check("trail email_step -> password", (r.trail || []).join(">") === "job>email_step>password", r.trail);

    ({ r } = await ic("fx-acc-ic-dry-new", "new"));
    check("manual/account_required + wouldCreateAccount", r.status === "manual" && r.reason === "account_required" && r.wouldCreateAccount === true && !r.wouldResetPassword, r);
    check("profile NOT created, ensureAccount not called", has("ACCOUNT_CREATED").length === 0 && stub.calls.length === 0 && noBad(), events);

    ({ r } = await ic("fx-acc-ic-dry-allow", "new", { dryRun: true, allowAccountCreation: true }));
    check("dry run wins over allowAccountCreation", r.status === "manual" && r.wouldCreateAccount === true && stub.calls.length === 0 && has("ACCOUNT_CREATED").length === 0, [r, stub.calls]);

    console.log("# iCIMS: live without opts.allowAccountCreation stays a hard stop");
    ({ r } = await ic("fx-acc-ic-live-noallow", "exists", { dryRun: false }));
    check("manual/password_required, not called, no Forgot click", r.status === "manual" && r.reason === "password_required" && stub.calls.length === 0 && has("FORGOT_CLICK").length === 0, [r, stub.calls]);
    check("evidence names the missing opt-in", /allowAccountCreation/.test(r.evidence || ""), r.evidence);

    console.log("# iCIMS: the Log In / Create a Profile choice wall stays manual even when authorized");
    ({ r } = await ic("fx-acc-ic-wall", "wall", LIVE));
    check("manual/account_required, ensureAccount not called, no wall button clicked", r.status === "manual" && r.reason === "account_required" && stub.calls.length === 0 && noBad(), [r, events]);

    console.log("# iCIMS: authorized live, NEW profile -> application continues");
    ({ r } = await ic("fx-acc-ic-live-new", "new", LIVE));
    check("applied with confirmation", r.status === "applied" && /thank you for applying/i.test(r.evidence || ""), r);
    check("ensureAccount called once (adapterName=icims, framed scope)", stub.calls.length === 1 && stub.calls[0].adapterName === "icims" && stub.calls[0].dryRun === false && stub.calls[0].allow === true && stub.states[0] === "create", [stub.calls, stub.states]);
    check("hooks offered incl. fillNames", ["clickCreate", "clickForgot", "fillEmail", "fillNames", "fillPassword", "submit", "submitNewPassword"].every((k) => stub.calls[0].hooks.includes(k)), stub.calls[0]);
    const ac = (has("ACCOUNT_CREATED")[0] || {}).v || {};
    check("profile created from the allowlist: names, email, privacy tickbox, matching confirm password", ac.fn === "Jane" && ac.ln === "Doe" && ac.em === EMAIL && ac.match === true && ac.priv === true && ac.pwLen >= 12, ac);
    check("pre-ticked marketing/alerts box UNticked (never ticked); no forbidden/bad click", ac.alerts === false && !has("MARKETING_TICKED").some((e) => e.v === true) && noBad(), events);
    check("extras: action created, no password in the result", r.account && r.account.action === "created" && r.account.email === EMAIL && noSecrets(r), r.account);
    check("application continued past the account, Submit exactly once", has("SUBMITTED").length === 1 && has("profile").length === 1 && has("questions").length === 1, events.map((e) => e.k));

    console.log("# iCIMS: new profile then email verification");
    ({ r } = await ic("fx-acc-ic-live-verify", "new_verify", LIVE));
    check("states create,verify_email; emailed link opened in the same tab; applied", stub.states.join(",") === "create,verify_email" && has("VERIFIED").length === 1 && r.status === "applied", [stub.states, r]);

    console.log("# iCIMS: authorized live, EXISTING account -> forgot password -> reset -> login");
    ({ r } = await ic("fx-acc-ic-live-reset", "exists", LIVE));
    check("applied with confirmation", r.status === "applied" && /thank you for applying/i.test(r.evidence || ""), r);
    check("states signin_password,reset_sent", stub.states.slice(0, 2).join(",") === "signin_password,reset_sent", stub.states);
    check("Forgot -> reset requested for the profile email -> reset done -> login ok, in order",
      kinds().filter((k) => ["FORGOT_CLICK", "RESET_REQUESTED", "RESET_DONE", "LOGIN_OK"].includes(k)).join(",") === "FORGOT_CLICK,RESET_REQUESTED,RESET_DONE,LOGIN_OK" && has("RESET_REQUESTED")[0].v === EMAIL && has("RESET_DONE")[0].v.match === true, events.map((e) => e.k));
    check("no failed login, nothing created, no forbidden control, no password in the result", has("LOGIN_FAIL").length === 0 && has("ACCOUNT_CREATED").length === 0 && noBad() && r.account && r.account.action === "reset" && noSecrets(r), events);
    check("Submit exactly once", has("SUBMITTED").length === 1, events.map((e) => e.k));

    console.log("# iCIMS: policy stop passes through");
    stub.reset(); events = []; stub.mode = "captcha";
    names.push("fx-acc-ic-captcha");
    const t4 = await browser.open({ name: "fx-acc-ic-captcha", url: `${base}/ic/outer.html?scn=new`, app: "own" });
    const ijob = { id: "fx-acc-ic-captcha", source: "linkedin", url: "https://careers-fallon.icims.com/jobs/8548/job", apply_url: "https://careers-fallon.icims.com/jobs/8548/job" };
    r = await ats.applyOnSite(browser, ijob, profile, { tab: t4, adapter: "icims", keepOpen: true, ...LIVE });
    await sleep(300);
    check("manual/captcha_required, nothing created", r.status === "manual" && r.reason === "captcha_required" && stub.calls.length === 1 && has("ACCOUNT_CREATED").length === 0, r);

    stub.reset(); events = []; stub.mode = "noop";
    names.push("fx-acc-ic-noop");
    const t5 = await browser.open({ name: "fx-acc-ic-noop", url: `${base}/ic/outer.html?scn=new`, app: "own" });
    r = await ats.applyOnSite(browser, { ...ijob, id: "fx-acc-ic-noop" }, profile, { tab: t5, adapter: "icims", keepOpen: true, ...LIVE });
    check("no-progress account flow capped (2 runs) -> manual/account_flow_failed", r.status === "manual" && r.reason === "account_flow_failed" && stub.calls.length === 2, [r.reason, stub.calls.length]);
    stub.mode = "ok";

    // ============================================================================ real account.js + fake mailbox
    // Same fixture pages, but the REAL ensureAccount (src/ats/account.js) runs against the adapters' hooks, with a fake
    // mailbox object (no real mail is ever read) and a temp credentials file.
    const cred = require(path.join(ROOT, "src/ats/credentials.js"));
    const realPhase = fs.existsSync(realAccount) && String(cred.FILE).startsWith(tmp);
    console.log(`# real account.js + fake mailbox ${realPhase ? "" : "(SKIPPED: src/ats/account.js missing or credentials file not the temp one)"}`);
    if (realPhase) {
      Module._resolveFilename = resolveOrig;
      delete require.cache[realAccount];
      const real = require(realAccount);
      check("adapters now resolve the real account.js (not the stub)", real !== stub && typeof real.ensureAccount === "function");
      const mailbox = {
        log: [],
        markTriggered(req, kind) { this.log.push(`trigger:${kind}`); },
        async getResetLink() { this.log.push("read:reset"); return stub.config.resetLink; },
        async getVerificationLink() { this.log.push("read:verify"); return stub.config.verifyLink; },
        async getCode() { this.log.push("read:code"); return null; },
      };
      const REAL = { ...LIVE, mailbox, allowLoopbackHttp: true };
      const vault = () => (fs.existsSync(cred.FILE) ? JSON.parse(fs.readFileSync(cred.FILE, "utf8")).entries : []);
      const fresh = () => { try { fs.rmSync(cred.FILE); } catch { /* none yet */ } mailbox.log = []; };
      const clean = (r) => { const pws = vault().map((e) => e.password); return !pws.some((p) => JSON.stringify(r).includes(p)) && !pws.some((p) => events.some((e) => JSON.stringify(e).includes(p))); };

      fresh();
      ({ r } = await wd("fx-acc-real-wd-create", "create", REAL));
      check("workday create: applied, action created, Submit once", r.status === "applied" && r.account && r.account.action === "created" && has("ACCOUNT_CREATED").length === 1, r);
      check("workday create: credential persisted (0600) with the profile email; page accepted it (match + terms)", vault().length === 1 && vault()[0].email === EMAIL && vault()[0].origin === "created" && (fs.statSync(cred.FILE).mode & 0o777) === 0o600 && has("ACCOUNT_CREATED")[0].v.match === true && has("ACCOUNT_CREATED")[0].v.terms === true && has("ACCOUNT_CREATED")[0].v.pwLen === vault()[0].password.length, [vault().map((e) => ({ ...e, password: "…" })), has("ACCOUNT_CREATED")]);
      check("workday create: password in no result and no logged event; marketing never ticked", clean(r) && !has("MARKETING_TICKED").some((e) => e.v === true), r);
      check("workday create: mailbox only armed (verify/code), nothing read", mailbox.log.every((x) => x.startsWith("trigger:")), mailbox.log);

      fresh();
      ({ r, tab } = await wd("fx-acc-real-wd-reset", "signin", REAL));
      check("workday reset: applied, action reset", r.status === "applied" && r.account && r.account.action === "reset", r);
      check("workday reset: Forgot -> reset request -> reset done -> login ok, in order, no failed login", kinds().filter((k) => ["FORGOT_CLICK", "RESET_REQUESTED", "RESET_DONE", "LOGIN_OK", "LOGIN_FAIL"].includes(k)).join(",") === "FORGOT_CLICK,RESET_REQUESTED,RESET_DONE,LOGIN_OK", events);
      check("workday reset: new password persisted as origin=reset BEFORE use; mailbox read once (reset link only)", vault().length === 1 && vault()[0].origin === "reset" && has("RESET_DONE")[0].v.pwLen === vault()[0].password.length && mailbox.log.filter((x) => x.startsWith("read:")).join() === "read:reset", [vault().map((e) => e.origin), mailbox.log]);
      check("workday reset: password in no result/event", clean(r), r);

      // The page seen live on a Workday tenant: only "Sign in with Google" + "Sign in with email" (no inputs).
      fresh();
      ({ r, tab } = await wd("fx-acc-real-wd-chooser", "signin_chooser", REAL));
      check("workday chooser: applied, action reset (existing account, no saved password)", r.status === "applied" && r.account && r.account.action === "reset", r);
      check("workday chooser: 'Sign in with email' pressed once; Google NEVER clicked", has("CHOSE_EMAIL").length === 1 && has("FORBIDDEN_CLICK").length === 0 && noBad(), events);
      check("workday chooser: email chosen BEFORE the forgot/reset sequence, then reset -> login ok, no failed login", kinds().filter((k) => ["CHOSE_EMAIL", "FORGOT_CLICK", "RESET_REQUESTED", "RESET_DONE", "LOGIN_OK", "LOGIN_FAIL"].includes(k)).join(",") === "CHOSE_EMAIL,FORGOT_CLICK,RESET_REQUESTED,RESET_DONE,LOGIN_OK", events);
      check("workday chooser: password persisted (origin reset), mailbox read once (reset only), no password leak", vault().length === 1 && vault()[0].origin === "reset" && mailbox.log.filter((x) => x.startsWith("read:")).join() === "read:reset" && clean(r), [vault().map((e) => e.origin), mailbox.log]);

      // Detection and selection must agree on native visible controls even when their accessible name differs.
      fresh();
      ({ r, tab } = await wd("fx-acc-real-wd-native-chooser", "signin_chooser_native", REAL));
      check("workday native chooser: visible email method selected once and application completed", r.status === "applied" && r.account && r.account.action === "reset" && has("CHOSE_EMAIL").length === 1, r);
      check("workday native chooser: Google untouched, reset/login capped, no password leak", noBad() && has("FORBIDDEN_CLICK").length === 0 && has("RESET_REQUESTED").length === 1 && has("LOGIN_OK").length === 1 && clean(r), events);

      fresh();
      ({ r, tab } = await wd("fx-acc-real-wd-native-chooser-dry", "signin_chooser_native", { ...REAL, dryRun: true }));
      check("workday native chooser dry run: no method/reset/login, no credentials or mailbox access", r.status === "manual" && r.reason === "account_required" && has("CHOSE_EMAIL").length === 0 && has("RESET_REQUESTED").length === 0 && has("LOGIN_OK").length === 0 && vault().length === 0 && mailbox.log.length === 0 && noBad(), r);

      fresh();
      ({ r } = await ic("fx-acc-real-ic-new", "new", REAL));
      check("icims new: applied, action created, Submit once", r.status === "applied" && r.account && r.account.action === "created" && has("ACCOUNT_CREATED").length === 1 && has("SUBMITTED").length === 1, r);
      const icc = (has("ACCOUNT_CREATED")[0] || {}).v || {};
      check("icims new: names/email from profile, privacy ticked, pre-ticked marketing UNticked, matching confirm", icc.fn === "Jane" && icc.ln === "Doe" && icc.em === EMAIL && icc.priv === true && icc.alerts === false && icc.match === true, icc);
      check("icims new: credential persisted (0600) and matches what the page accepted; no password leak", vault().length === 1 && vault()[0].origin === "created" && (fs.statSync(cred.FILE).mode & 0o777) === 0o600 && icc.pwLen === vault()[0].password.length && clean(r), r);

      fresh();
      ({ r } = await ic("fx-acc-real-ic-reset", "exists", REAL));
      check("icims reset: applied, action reset, Submit once", r.status === "applied" && r.account && r.account.action === "reset" && has("SUBMITTED").length === 1, r);
      check("icims reset: Forgot -> reset request -> reset done -> login ok, in order, no failed login", kinds().filter((k) => ["FORGOT_CLICK", "RESET_REQUESTED", "RESET_DONE", "LOGIN_OK", "LOGIN_FAIL"].includes(k)).join(",") === "FORGOT_CLICK,RESET_REQUESTED,RESET_DONE,LOGIN_OK", events.map((e) => e.k));
      check("icims reset: persisted origin=reset, mailbox read once (reset), no password leak", vault().length === 1 && vault()[0].origin === "reset" && mailbox.log.filter((x) => x.startsWith("read:")).join() === "read:reset" && clean(r), [vault().map((e) => e.origin), mailbox.log]);

      fresh();
      ({ r } = await ic("fx-acc-real-ic-verify", "new_verify", REAL));
      check("icims new + verification: link read once and opened in the same tab; applied", r.status === "applied" && has("VERIFIED").length === 1 && mailbox.log.filter((x) => x === "read:verify").length === 1, [r.status, r.reason, mailbox.log]);

      fresh();
      ({ r } = await ic("fx-acc-real-ic-dry", "new", { dryRun: true, allowAccountCreation: true, mailbox }));
      check("real account.js never reached in dry run (adapter stops first): nothing created, no vault, mailbox untouched", r.status === "manual" && r.wouldCreateAccount === true && has("ACCOUNT_CREATED").length === 0 && vault().length === 0 && mailbox.log.length === 0, [r, mailbox.log]);
    }
  } finally {
    server.stop();
    Module._resolveFilename = resolveOrig;
    if (fs.existsSync(realAccount)) { if (hadCache) require.cache[realAccount] = cacheOrig; else delete require.cache[realAccount]; }
    for (const n of names) { try { await browser.close({ name: n }); } catch { /* ignore */ } }
  }
  console.log(`\n${pass} passed, ${fail} failed`);
  return { pass, fail };
};
