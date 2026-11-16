// Fixture test for src/ats/adapters/workday.js. Needs the omp `browser` global, so run it from an omp eval cell:
//
//   const run = require("/abs/path/test/fixtures/ats/workday.test.js"); await run(browser);
//
// Serves test/fixtures/ats/workday over 127.0.0.1, drives MANAGED headless tabs (never the user's Chrome relay) and
// asserts both the adapter results AND what the pages recorded in window.__log (nothing typed into sign-in, no create
// account, no forbidden control, nothing submitted in dry run). Nothing here touches a real site; consent logs and
// screenshots go to a temp dir.
"use strict";
const fs = require("fs");
const os = require("os");
const path = require("path");

module.exports = async function run(browser) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "ats-workday-"));
  process.env.ATS_CONSENT_LOG = path.join(tmp, "consents.log");
  process.env.ATS_EVIDENCE_DIR = path.join(tmp, "evidence");
  const ROOT = path.resolve(__dirname, "../../..");
  const ats = require(path.join(ROOT, "src/ats/index.js"));
  const workday = require(path.join(ROOT, "src/ats/adapters/workday.js"));
  const { Blocked, jobKey } = require(path.join(ROOT, "src/ats/lib.js"));
  const { NeedsHuman, deriveProfile } = require(path.join(ROOT, "src/ats/answers.js"));
  const profile = JSON.parse(fs.readFileSync(path.resolve(__dirname, "../profile.fixture.json"), "utf8"));

  const dir = path.join(__dirname, "workday");
  const server = Bun.serve({
    port: 0,
    fetch(req) {
      const rel = path.normalize(new URL(req.url).pathname).replace(/^(\.\.[/\\])+/, "");
      const f = Bun.file(path.join(dir, rel));
      return f.size ? new Response(f, { headers: { "content-type": "text/html" } }) : new Response("nf", { status: 404 });
    },
  });
  const base = `http://127.0.0.1:${server.port}`;
  let pass = 0, fail = 0;
  const check = (name, cond, extra) => { if (cond) { pass++; console.log("  ok  ", name); } else { fail++; console.log("  FAIL", name, extra === undefined ? "" : JSON.stringify(extra)); } };
  const logOf = async (tab) => tab.evaluate("window.__log");
  const has = (log, k) => log.filter((e) => e.k === k);
  const names = [];
  const open = async (name, file) => { names.push(name); return browser.open({ name, url: `${base}/${file}`, app: "own" }); };
  const job = (id, extra) => ({ id, source: "linkedin", url: "https://acme.wd5.myworkdayjobs.com/en-US/careers/job/Boston/RN_R12345", apply_url: "https://acme.wd5.myworkdayjobs.com/en-US/careers/job/Boston/RN_R12345", ...extra });

  // Until the parent registers the adapter in src/ats/index.js, drive it exactly as applyOnSite does (same ctx + error mapping).
  const registered = ats.adapters.some((a) => a.name === "workday");
  async function drive(tab, jb, dryRun) {
    if (registered) return ats.applyOnSite(browser, jb, profile, { tab, adapter: "workday", dryRun });
    const ctx = {
      browser, tab, tabName: "provided", job: jb, profile, P: deriveProfile(profile), opts: { dryRun }, dryRun, log: () => {},
      site: "acme.wd5.myworkdayjobs.com", url: jb.apply_url, token: jobKey(jb), consents: [], filled: [], skipped: [], prefilled: [], submitted: false,
    };
    try {
      return { ...(await workday.apply(ctx)), adapter: "workday", dryRun };
    } catch (e) {
      if (e instanceof NeedsHuman) return { status: "manual", reason: "unanswered_questions", unanswered: [e.question], evidence: e.message, adapter: "workday", dryRun };
      if (e instanceof Blocked) return { status: "manual", reason: e.reason, evidence: e.evidence, adapter: "workday", dryRun };
      return { status: "failed", reason: "adapter_error", evidence: `${e.name}: ${e.message}`, adapter: "workday", dryRun };
    }
  }

  try {
    console.log(`# registry (index.js ${registered ? "registers" : "does not yet register"} workday; driven ${registered ? "through applyOnSite" : "directly with an applyOnSite-equivalent ctx"})`);
    const m = workday.matches;
    check("matches *.myworkdayjobs.com / wdN / myworkdaysite / workday.com", m("https://acme.myworkdayjobs.com/x") && m("https://acme.wd1.myworkdayjobs.com/x") && m("https://x.wd5.myworkdayjobs.com/en-US/careers/job/a") && m("https://wd3.myworkdaysite.com/recruiting/x") && m("https://www.workday.com/en-us/company/careers.html"));
    check("does not match lookalikes / other ATS", !m("https://evilmyworkdayjobs.com/x") && !m("https://example.com/myworkdayjobs.com") && !m("https://workday.example.com/") && !m("https://workforcenow.adp.com/x") && !m("not a url"));
    check("adapter shape", workday.name === "workday" && typeof workday.apply === "function");

    console.log("# sign-in required after Apply Manually -> manual/account_required");
    let tab = await open("fx-wd-signin", "flow.html?scenario=signin");
    let r = await drive(tab, job("fx-wd-1"), true);
    let log = await logOf(tab);
    check("manual account_required", r.status === "manual" && r.reason === "account_required", r);
    check("walked Apply -> Apply Manually only", has(log, "click").map((e) => e.v).join(",") === "apply,applyManually", log);
    check("nothing typed into sign-in", has(log, "TYPED").length === 0, log);
    check("no create-account / sign-in / autofill / last-application click", has(log, "BAD_CLICK").length === 0, log);
    check("no forbidden control (talent community, alerts, social sign-in)", has(log, "FORBIDDEN_CLICK").length === 0, log);
    check("no form step reached, nothing submitted", has(log, "SUBMITTED").length === 0 && !log.some((e) => e.k === "show" && /^step/.test(e.v)), log);
    check("trail ends in account", (r.trail || []).slice(-1)[0] === "account", r.trail);

    console.log("# sign-in wall reached directly");
    tab = await open("fx-wd-signin2", "signin.html");
    r = await drive(tab, job("fx-wd-2"), true);
    log = await logOf(tab);
    check("manual account_required", r.status === "manual" && r.reason === "account_required", r);
    check("page untouched (no click, nothing typed)", log.length === 0, log);
    const vals = await tab.evaluate(`({e: document.getElementById('email').value, p: document.getElementById('pw').value})`);
    check("email/password inputs empty", vals.e === "" && vals.p === "", vals);

    console.log("# authenticated multi-step flow: dry run stops on Review before Submit");
    tab = await open("fx-wd-flow", "flow.html");
    r = await drive(tab, job("fx-wd-3"), true);
    log = await logOf(tab);
    check("dry_run_complete (status skipped)", r.status === "skipped" && r.reason === "dry_run_complete", r);
    check("wouldSubmit reports the Submit button + filled fields", r.wouldSubmit && /^Submit$/i.test(r.wouldSubmit.submitButton) && r.wouldSubmit.fields.length >= 15, r.wouldSubmit);
    check("NO submit recorded", has(log, "SUBMITTED").length === 0, log);
    check("stopped on Review (step6) and never reached done", log.filter((e) => e.k === "show").slice(-1)[0].v === "step6", log.filter((e) => e.k === "show"));
    check("trail covers all six steps", ["My Information", "My Experience", "Application Questions", "Voluntary Disclosures", "Self Identify", "Review"].every((s) => (r.trail || []).includes(`form:${s}`)), r.trail);
    const s1 = (has(log, "step1")[0] || {}).v || {}, s2 = (has(log, "step2")[0] || {}).v || {}, s3 = (has(log, "step3")[0] || {}).v || {}, s4 = (has(log, "step4")[0] || {}).v || {}, s5 = (has(log, "step5")[0] || {}).v || {};
    check("how-did-you-hear = LinkedIn (multi-select)", JSON.stringify(s1.src) === '["LinkedIn"]', s1);
    check("name via real input events (controlled field kept it)", s1.gn === "Jane" && s1.fn === "Doe" && has(log, "UNTRUSTED_INPUT").length === 0, [s1, has(log, "UNTRUSTED_INPUT")]);
    check("address from profile", s1.addr === "100 Example Street" && s1.city === "Springfield" && s1.state === "Massachusetts" && s1.zip === "01101" && s1.country === "United States of America", s1);
    check("phone code = United States of America (+1) (not the ambiguous lookalike); phone typed; extension/device/preferred-name untouched", s1.pcc === "United States of America (+1)" && s1.phone.replace(/\D/g, "").endsWith("5550100199") && s1.ext === "" && s1.device === "" && s1.pref === false && s1.addr2 === "", s1);
    check("resume uploaded once", s2.resume === "resume.pdf" && s2.uploads === 1, s2);
    check("history only verified: saved job/company/school unchanged", s2.jt === "Registered Nurse" && s2.co === "Mass General Brigham" && s2.sch === "Boston University", s2);
    check("authorized = Yes, sponsorship = No", s3.auth === "y" && s3.spons === "No", s3);
    check("voluntary disclosures declined", s4.gender === "I do not wish to answer" && s4.hisp === "I do not wish to answer" && s4.race === "I do not wish to answer" && s4.vet === "I do not wish to answer", s4);
    check("terms ticked; SMS alerts NOT ticked", s4.terms === true && s4.sms === false, s4);
    check("self identify declined", s5.disab === "decline", s5);
    const consents = fs.existsSync(process.env.ATS_CONSENT_LOG) ? fs.readFileSync(process.env.ATS_CONSENT_LOG, "utf8") : "";
    check("terms consent logged, SMS/marketing not", /Terms and Conditions/.test(consents) && !/text message|marketing/i.test(consents), consents);
    check("history reported as prefilledBySite", ((r.wouldSubmit || {}).prefilledBySite || []).some((p) => /Work Experience/.test(p.label)), (r.wouldSubmit || {}).prefilledBySite);
    check("no forbidden / bad clicks (autofill, last application, talent community)", has(log, "FORBIDDEN_CLICK").length === 0 && has(log, "BAD_CLICK").length === 0, log);
    check("Apply Manually used", has(log, "click").map((e) => e.v).join(",") === "apply,applyManually", log);

    console.log("# unanswered required question -> manual/unanswered_questions");
    tab = await open("fx-wd-unknown", "flow.html?scenario=unknown_q");
    r = await drive(tab, job("fx-wd-4"), true);
    log = await logOf(tab);
    check("manual unanswered_questions", r.status === "manual" && r.reason === "unanswered_questions", r);
    check("question text returned", (r.unanswered || []).some((q) => /active RN license/i.test(q)), r.unanswered);
    check("earlier steps done, Application Questions NOT advanced", has(log, "step1").length === 1 && has(log, "step2").length === 1 && has(log, "step3").length === 0, log);
    check("not submitted", has(log, "SUBMITTED").length === 0);
    const lic = await tab.evaluate(`document.getElementById('lic').textContent.trim()`);
    check("unknown question left unanswered on the page", lic === "Select One", lic);

    console.log("# saved work history is verified, never typed: empty required entry -> manual");
    tab = await open("fx-wd-hist", "flow.html?scenario=empty_history&start=step2");
    r = await drive(tab, job("fx-wd-5"), true);
    log = await logOf(tab);
    check("manual unanswered_questions naming the history field", r.status === "manual" && r.reason === "unanswered_questions" && (r.unanswered || []).some((q) => /Work Experience.*Job Title/i.test(q)), r);
    check("job title not typed, step not advanced", (await tab.evaluate(`document.getElementById('jt').value`)) === "" && has(log, "step2").length === 0, log);

    console.log("# resume start on a later step (session already mid-application)");
    tab = await open("fx-wd-mid", "flow.html?start=step4");
    r = await drive(tab, job("fx-wd-6"), true);
    log = await logOf(tab);
    check("dry_run_complete from step 4", r.status === "skipped" && r.reason === "dry_run_complete" && has(log, "SUBMITTED").length === 0, r);
    check("no earlier step replayed", has(log, "step1").length === 0 && has(log, "step2").length === 0 && has(log, "step3").length === 0, log);

    console.log("# live mode on the fixture submits exactly once and confirms");
    tab = await open("fx-wd-live", "flow.html");
    r = await drive(tab, job("fx-wd-7"), false);
    log = await logOf(tab);
    check("applied with confirmation page", r.status === "applied" && /application submitted|thanks for applying/i.test(r.evidence || ""), r);
    check("submitted exactly once", has(log, "SUBMITTED").length === 1, log);
  } finally {
    server.stop();
    for (const n of names) { try { await browser.close({ name: n }); } catch { /* ignore */ } }
  }
  console.log(`\n${pass} passed, ${fail} failed`);
  return { pass, fail };
};
