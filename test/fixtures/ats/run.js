// Fixture test runner for src/ats. Needs the omp `browser` global, so run it from an omp eval cell:
//
//   const run = require("/abs/path/test/fixtures/ats/run.js"); await run(browser);
//
// Serves test/fixtures/ats over 127.0.0.1, drives a managed (headless) browser tab, and asserts
// both the adapter results AND what the fixture page recorded in window.__log (e.g. that no
// verification code was sent, no forbidden control was clicked, nothing was submitted in dry run).
// Nothing here touches a real site; real consent/OTP files go to a temp dir.
"use strict";
const fs = require("fs");
const os = require("os");
const path = require("path");

module.exports = async function run(browser) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "ats-test-"));
  process.env.ATS_CONSENT_LOG = path.join(tmp, "consents.log");
  process.env.ATS_EVIDENCE_DIR = path.join(tmp, "evidence");
  const ats = require("../../../src/ats/index.js");
  const profile = JSON.parse(fs.readFileSync(path.resolve(__dirname, "../profile.fixture.json"), "utf8"));
  const server = Bun.serve({ port: 0, fetch(req) { const f = Bun.file(path.join(__dirname, new URL(req.url).pathname)); return f.size ? new Response(f, { headers: { "content-type": "text/html" } }) : new Response("nf", { status: 404 }); } });
  const base = `http://127.0.0.1:${server.port}`;
  let pass = 0, fail = 0;
  const check = (name, cond, extra) => { if (cond) { pass++; console.log("  ok  ", name); } else { fail++; console.log("  FAIL", name, extra === undefined ? "" : JSON.stringify(extra)); } };
  const logOf = async (tab) => tab.evaluate("window.__log");
  const has = (log, k) => log.filter((e) => e.k === k);
  const open = async (name, file) => browser.open({ name, url: `${base}/${file}`, app: "own" });
  const provider = (extra) => new ats.HumanPromptOtpProvider({ dir: path.join(tmp, "otp"), env: {}, ...extra });
  const job = (id, extra) => ({ id, source: "linkedin", url: "https://workforcenow.adp.com/x", apply_url: "https://workforcenow.adp.com/x", ...extra });

  try {
    console.log("# ADP: dry run to OTP without a human");
    let tab = await open("fx-adp", "adp_flow.html?privacy=narrow");
    let otp = provider();
    let jb = job("fx-adp-1");
    let r = await ats.applyOnSite(browser, jb, profile, { tab, adapter: "adp", dryRun: true, otpProvider: otp });
    let log = await logOf(tab);
    check("status manual/otp_required", r.status === "manual" && r.reason === "otp_required", r);
    check("stage ready_to_send + resume token", r.otpStage === "ready_to_send" && /^[0-9a-f]{12}$/.test(r.resumeToken || ""), r);
    check("NO code sent", has(log, "SEND_CODE").length === 0, log);
    check("no forbidden click (social sign-in/talent community)", has(log, "FORBIDDEN_CLICK").length === 0, log);
    check("consent logged for privacy", /Privacy Policy/.test(fs.readFileSync(process.env.ATS_CONSENT_LOG, "utf8")));
    check("contact data typed", has(log, "contact").length === 1 && has(log, "contact")[0].v.mob.replace(/\D/g, "").endsWith("5550100199"), has(log, "contact"));
    check("pending file written", fs.existsSync(path.join(tmp, "otp", `${r.resumeToken}.pending.json`)));
    const token = r.resumeToken;

    console.log("# ADP: human authorizes send -> code requested once, then waits for code");
    otp.allowSend(token);
    r = await ats.applyOnSite(browser, jb, profile, { tab, adapter: "adp", dryRun: true, otpProvider: otp });
    log = await logOf(tab);
    check("otp_required awaiting_code", r.status === "manual" && r.reason === "otp_required" && r.otpStage === "awaiting_code", r);
    check("exactly one SMS code sent", has(log, "SEND_CODE").length === 1 && has(log, "SEND_CODE")[0].v === "sms", has(log, "SEND_CODE"));
    r = await ats.applyOnSite(browser, jb, profile, { tab, adapter: "adp", dryRun: true, otpProvider: otp });
    log = await logOf(tab);
    check("re-run without code does NOT resend", has(log, "SEND_CODE").length === 1 && r.reason === "otp_required", r);

    console.log("# ADP: wrong code is not retried");
    otp.supplyCode(token, "999999");
    r = await ats.applyOnSite(browser, jb, profile, { tab, adapter: "adp", dryRun: true, otpProvider: otp });
    check("otp_invalid manual", r.status === "manual" && r.reason === "otp_invalid", r);

    console.log("# ADP: correct code -> form -> dry run stops before Submit");
    otp.supplyCode(token, "123456");
    r = await ats.applyOnSite(browser, jb, profile, { tab, adapter: "adp", dryRun: true, otpProvider: otp });
    log = await logOf(tab);
    check("dry_run_complete (status skipped)", r.status === "skipped" && r.reason === "dry_run_complete", r);
    check("NOT submitted", has(log, "SUBMITTED").length === 0);
    const s1 = (has(log, "step1")[0] || {}).v || {}, s2 = (has(log, "step2")[0] || {}).v || {};
    check("resume uploaded", s1.resume === "resume.pdf", s1);
    check("authorized=yes, sponsorship=no", s1.auth === "y" && s1.spons === "n", s1);
    check("location from profile, phone-extension/website left blank", s1.loc === "Greater Boston Area, MA" && s1.ext === "" && s1.web === "", s1);
    check("EEO declined", s2.gender === "I do not wish to answer" && s2.vet === "I decline to self-identify" && JSON.stringify(s2.race) === '["d"]', s2);
    check("truthfulness ticked; job-alert + background-check NOT ticked", s2.truth === true && s2.alerts === false && s2.bg === false, s2);
    check("wouldSubmit report", r.wouldSubmit && /Submit Application/i.test(r.wouldSubmit.submitButton) && r.wouldSubmit.fields.length >= 6, r.wouldSubmit);
    check("truthfulness consent logged, marketing not", /true and accurate/.test(fs.readFileSync(process.env.ATS_CONSENT_LOG, "utf8")) && !/marketing/i.test(fs.readFileSync(process.env.ATS_CONSENT_LOG, "utf8")));

    console.log("# ADP: actual privacy dialog is reviewed, not just its title");
    for (const [suffix, name] of [["", "third-party sharing"], ["?privacy=unknown", "unknown location tracking"]]) {
      const privacyTab = await open(`fx-adp-privacy-${name}`, `adp_flow.html${suffix}`);
      const privacyResult = await ats.applyOnSite(browser, job(`fx-adp-privacy-${name}`), profile, { tab: privacyTab, adapter: "adp", dryRun: true, otpProvider: provider() });
      const privacyLog = await logOf(privacyTab);
      check(`${name} stops before Consent`, privacyResult.status === "manual" && privacyResult.reason === "consent_not_allowed" && !has(privacyLog, "click").some((e) => e.v === "consent"), privacyResult);
      await privacyTab.close();
    }

    console.log("# ADP: unknown required question -> manual with question text");
    tab = await browser.open({ name: "fx-adp2", url: `${base}/adp_flow.html?start=s-step1&scenario=unknown_q`, app: "own" });
    r = await ats.applyOnSite(browser, job("fx-adp-2"), profile, { tab, adapter: "adp", dryRun: true, otpProvider: provider() });
    log = await logOf(tab);
    check("manual unanswered_questions", r.status === "manual" && r.reason === "unanswered_questions", r);
    check("question text returned", (r.unanswered || []).some((q) => /years of SQL experience/i.test(q)), r.unanswered);
    check("did not advance", has(log, "step1").length === 0);

    console.log("# ADP: the reviewed job answer (jobAnswers option) settles exactly that question");
    r = await ats.applyOnSite(browser, job("fx-adp-2"), profile, { tab, adapter: "adp", dryRun: true, otpProvider: provider(), jobAnswers: { "How many years of SQL experience do you have?": "6" } });
    log = await logOf(tab);
    check("answered from the job answer, dry run completes", r.status === "skipped" && r.reason === "dry_run_complete", r);
    check("the exact answer was typed", (await tab.evaluate("document.getElementById('yrs').value")) === "6", await tab.evaluate("document.getElementById('yrs').value"));
    check("the rule is reported", (r.wouldSubmit.fields || []).some((x) => x.rule === "reviewed_job_answer" && x.value === "6"), r.wouldSubmit);

    console.log("# ADP: live mode on fixture submits only after full run");
    tab = await open("fx-adp3", "adp_flow.html?start=s-step1");
    r = await ats.applyOnSite(browser, job("fx-adp-3"), profile, { tab, adapter: "adp", dryRun: false, otpProvider: provider() });
    log = await logOf(tab);
    check("applied with confirmation", r.status === "applied" && /thank you/i.test(r.evidence || ""), r);
    check("submitted exactly once", has(log, "SUBMITTED").length === 1);

    console.log("# Greenhouse");
    tab = await open("fx-gh", "greenhouse_form.html");
    r = await ats.applyOnSite(browser, job("fx-gh-1", { url: "https://boards.greenhouse.io/x/jobs/1", apply_url: "https://boards.greenhouse.io/x/jobs/1" }), profile, { tab, adapter: "greenhouse", dryRun: true, otpProvider: provider() });
    log = await logOf(tab);
    check("dry_run_complete", r.status === "skipped" && r.reason === "dry_run_complete", r);
    let form = (await tab.evaluate(`(() => { const g = id => document.getElementById(id); return {fn:g('first_name').value, ln:g('last_name').value, em:g('email').value, ph:g('phone').value, resume:g('resume').files.length, cover:g('cover').files.length, li:g('li').value, q1:g('q1').value, q2:g('q2').value, g:g('g').value, h:g('h').value, v:g('v').value, d:g('d').value, mk:g('mk').checked, trap:g('trap').value, q3:g('q3').value}; })()`));
    check("fields filled", form.fn === "Jane" && form.ln === "Doe" && form.em === "jane.doe@example.com" && /555/.test(form.ph) && form.resume === 1, form);
    check("auth Yes / sponsorship No", form.q1 === "Yes" && form.q2 === "No", form);
    check("EEO decline", form.g === "Decline To Self Identify" && form.h === "Decline To Self Identify" && /wish/.test(form.v) && /do not want/.test(form.d), form);
    check("cover untouched, marketing unticked, honeypot untouched", form.cover === 0 && form.mk === false && form.trap === "", form);
    check("not submitted", has(log, "SUBMITTED").length === 0);

    tab = await open("fx-gh2", "greenhouse_form.html?open=1&scenario=unknown");
    r = await ats.applyOnSite(browser, job("fx-gh-2", { apply_url: "https://boards.greenhouse.io/x/jobs/2" }), profile, { tab, adapter: "greenhouse", dryRun: true, otpProvider: provider() });
    check("manual: unconfirmed EAD category not guessed", r.status === "manual" && (r.unanswered || []).some((q) => /ead category/i.test(q)), r);

    tab = await open("fx-gh3", "greenhouse_form.html?scenario=code");
    otp = provider();
    jb = job("fx-gh-3", { apply_url: "https://boards.greenhouse.io/x/jobs/3" });
    r = await ats.applyOnSite(browser, jb, profile, { tab, adapter: "greenhouse", dryRun: false, otpProvider: otp });
    check("post-submit code -> otp_required awaiting_code", r.status === "manual" && r.reason === "otp_required" && r.otpStage === "awaiting_code", r);
    otp.supplyCode(r.resumeToken, "123456");
    r = await ats.applyOnSite(browser, jb, profile, { tab, adapter: "greenhouse", dryRun: false, otpProvider: otp });
    log = await logOf(tab);
    check("resume with code -> applied", r.status === "applied", r);
    check("one-digit boxes received 123456", has(log, "code_entered").some((e) => e.v === "123456"), has(log, "code_entered"));

    tab = await open("fx-gh4", "greenhouse_form.html?scenario=captcha");
    r = await ats.applyOnSite(browser, job("fx-gh-4", { apply_url: "https://boards.greenhouse.io/x/jobs/4" }), profile, { tab, adapter: "greenhouse", dryRun: false, otpProvider: provider() });
    check("captcha -> manual, not solved", r.status === "manual" && r.reason === "captcha_required", r);

    console.log("# Lever");
    tab = await open("fx-lv", "lever_form.html");
    r = await ats.applyOnSite(browser, job("fx-lv-1", { apply_url: "https://jobs.lever.co/x/1" }), profile, { tab, adapter: "lever", dryRun: true, otpProvider: provider() });
    form = await tab.evaluate(`(() => { const g = id => document.getElementById(id); return {name:g('name').value, email:g('email').value, ph:g('phone').value, loc:g('loc').value, org:g('org').value, terms:g('terms').checked, auth:[...document.querySelectorAll('input[name=auth]')].map(x=>x.checked), gender:[...document.querySelectorAll('input[name=gender]')].map(x=>x.checked), race:[...document.querySelectorAll('input[name=race]')].map(x=>x.checked), resume:g('resume').files.length, log: window.__log}; })()`);
    check("dry_run_complete", r.status === "skipped" && r.reason === "dry_run_complete", r);
    check("name/email/phone/location/resume", form.name === "Jane Doe" && form.email === "jane.doe@example.com" && /555/.test(form.ph) && form.loc === "Greater Boston Area, MA" && form.resume === 1, form);
    check("current company = Not currently employed (confirmed unemployed); privacy ticked", form.org === "Not currently employed" && form.terms === true, form);
    check("auth=Yes; gender=decline; race=decline only", JSON.stringify(form.auth) === "[true,false]" && JSON.stringify(form.gender) === "[false,false,true]" && JSON.stringify(form.race) === "[false,false,true]", form);
    check("not submitted", form.log.filter((e) => e.k === "SUBMITTED").length === 0);

    console.log("# Generic fallback");
    tab = await open("fx-gen", "lever_form.html");
    r = await ats.applyOnSite(browser, job("fx-g-2", { url: `${base}/lever_form.html`, apply_url: `${base}/lever_form.html` }), profile, { tab, dryRun: true });
    log = await logOf(tab);
    check("unknown host -> manual unknown_ats, page untouched", r.status === "manual" && r.reason === "unknown_ats" && log.length === 0, [r, log]);
    const routes = { "https://workforcenow.adp.com/a": "adp", "https://job-boards.greenhouse.io/a": "greenhouse", "https://jobs.lever.co/a": "lever", "https://acme.wd5.myworkdayjobs.com/a": "workday", "https://x.icims.com/a": "icims", "https://jobs.smartrecruiters.com/a": "smartrecruiters", "https://jobs.ashbyhq.com/a": "ashby", "https://example.org/a": "generic", "https://evil-icims.com.attacker.net/a": "generic" };
    const wrong = Object.entries(routes).filter(([u, want]) => ats.pickAdapter(u).name !== want);
    check("registry picks by hostname (lookalike hosts fall through to generic)", wrong.length === 0, wrong);
  } finally {
    server.stop();
    for (const n of ["fx-adp", "fx-adp2", "fx-adp3", "fx-gh", "fx-gh2", "fx-gh3", "fx-gh4", "fx-lv", "fx-gen"]) { try { await browser.close({ name: n }); } catch { /* ignore */ } }
  }
  console.log(`\n${pass} passed, ${fail} failed`);
  return { pass, fail };
};
