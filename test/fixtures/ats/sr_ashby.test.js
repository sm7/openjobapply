// Standalone fixture tests for the SmartRecruiters and Ashby adapters (does not touch run.js).
// Needs the omp `browser` global, so run it from an omp eval cell:
//
//   const run = require("/abs/path/test/fixtures/ats/sr_ashby.test.js"); await run(browser);
//
// Serves test/fixtures/ats/sr_ashby over 127.0.0.1, drives a managed (headless) browser tab and calls the
// adapters through the same ctx shape applyOnSite builds, so it works whether or not index.js registers
// them. Asserts adapter results AND what the page recorded in window.__log (nothing submitted in dry run,
// marketing/talent-community boxes never ticked, no guessing of unknown questions). No real site is touched;
// consent logs go to a temp dir.
"use strict";
const fs = require("fs");
const os = require("os");
const path = require("path");

module.exports = async function run(browser) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "ats-sr-ashby-"));
  process.env.ATS_CONSENT_LOG = path.join(tmp, "consents.log");
  process.env.ATS_EVIDENCE_DIR = path.join(tmp, "evidence");
  const atsDir = path.resolve(__dirname, "../../../src/ats") + path.sep;
  for (const k of Object.keys(require.cache)) if (k.startsWith(atsDir)) delete require.cache[k]; // pick up this run's ATS_* env and current adapter code
  const { Blocked, jobKey } = require("../../../src/ats/lib.js");
  const { NeedsHuman, deriveProfile } = require("../../../src/ats/answers.js");
  const { HumanPromptOtpProvider, otpToken } = require("../../../src/ats/otp.js");
  const smartrecruiters = require("../../../src/ats/adapters/smartrecruiters.js");
  const ashby = require("../../../src/ats/adapters/ashby.js");
  const profile = JSON.parse(fs.readFileSync(path.resolve(__dirname, "../profile.fixture.json"), "utf8"));
  const root = path.join(__dirname, "sr_ashby");
  const server = Bun.serve({ port: 0, fetch(req) { const f = Bun.file(path.join(root, new URL(req.url).pathname)); return f.size ? new Response(f, { headers: { "content-type": "text/html" } }) : new Response("nf", { status: 404 }); } });
  const base = `http://127.0.0.1:${server.port}`;
  let pass = 0, fail = 0;
  const check = (name, cond, extra) => { if (cond) { pass++; console.log("  ok  ", name); } else { fail++; console.log("  FAIL", name, extra === undefined ? "" : JSON.stringify(extra)); } };
  const opened = [];
  const open = async (name, file) => { opened.push(name); return browser.open({ name, url: `${base}/${file}`, app: "own" }); };
  const logOf = (tab) => tab.evaluate("window.__log");
  const has = (log, k) => log.filter((e) => e.k === k);
  const consentLog = () => (fs.existsSync(process.env.ATS_CONSENT_LOG) ? fs.readFileSync(process.env.ATS_CONSENT_LOG, "utf8") : "");

  /** same ctx + error mapping as index.js applyOnSite, minus adapter selection */
  async function runAdapter(adapter, tab, id, dryRun) {
    const job = { id, source: "linkedin", url: `https://example.invalid/${id}`, apply_url: `https://example.invalid/${id}` };
    const url = await tab.url();
    const site = new URL(url).hostname;
    const ctx = {
      browser, tab, tabName: "provided", job, profile, P: deriveProfile(profile), opts: {}, dryRun, log: () => {},
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

  try {
    console.log("# adapter contract / URL matching");
    for (const a of [smartrecruiters, ashby]) check(`${a.name}: exports name/matches/apply`, typeof a.name === "string" && typeof a.matches === "function" && typeof a.apply === "function");
    check("smartrecruiters matches jobs./careers. hosts", smartrecruiters.matches("https://jobs.smartrecruiters.com/Acme/1-analyst") && smartrecruiters.matches("https://careers.smartrecruiters.com/Acme") && smartrecruiters.matches("https://jobs.smartrecruiters.com/oneclick-ui/company/Acme/publication/x"));
    check("smartrecruiters rejects lookalikes", !smartrecruiters.matches("https://notsmartrecruiters.com/x") && !smartrecruiters.matches("https://acme.com/smartrecruiters.com") && !smartrecruiters.matches("not a url"));
    check("ashby matches jobs.ashbyhq.com only for ashbyhq.com", ashby.matches("https://jobs.ashbyhq.com/acme/1234") && !ashby.matches("https://ashbyhq.com.evil.io/x") && !ashby.matches("https://jobs.lever.co/x"));

    console.log("# SmartRecruiters: posting page -> I'm interested -> profile -> Next -> questions; dry run stops before Submit");
    let tab = await open("fx-sr1", "sr_flow.html");
    let r = await runAdapter(smartrecruiters, tab, "fx-sr-1", true);
    let log = await logOf(tab);
    check("dry_run_complete (status skipped)", r.status === "skipped" && r.reason === "dry_run_complete", r);
    check("NOT submitted", has(log, "SUBMITTED").length === 0, log);
    const nx = (has(log, "NEXT")[0] || {}).v || {};
    check("profile page filled before Next (name/email twice/city/phone/resume)", nx.fn === "Jane" && nx.ln === "Doe" && nx.em === "jane.doe@example.com" && nx.em2 === nx.em && nx.city === "Springfield" && nx.ph.replace(/\D/g, "").endsWith("5550100199") && nx.resume === "resume.pdf", nx);
    check("optional message to hiring team left blank", nx.msg === "", nx);
    let form = await tab.evaluate(`(() => { const g = id => document.getElementById(id); const r = document.querySelector('input[name=auth]:checked'); return {auth: r && r.value, spons: g('spons').value, gender: g('gender').value, privacy: g('privacy').checked, mk: g('mk').checked, tc: g('tc').checked}; })()`);
    check("auth=Yes, sponsorship=No, gender declined", form.auth === "y" && form.spons === "No" && /prefer not/i.test(form.gender), form);
    check("privacy consent ticked; marketing + talent community NEVER ticked", form.privacy === true && form.mk === false && form.tc === false, form);
    check("privacy consent logged, marketing/talent not", /Privacy Policy/.test(consentLog()) && !/marketing|talent/i.test(consentLog()), consentLog());
    check("wouldSubmit report", r.wouldSubmit && /^submit$/i.test(r.wouldSubmit.submitButton) && r.wouldSubmit.fields.length >= 8 && r.wouldSubmit.consents.length === 1, r.wouldSubmit);

    console.log("# SmartRecruiters: unknown required question -> manual, not guessed");
    tab = await open("fx-sr2", "sr_flow.html?start=profile&scenario=unknown");
    r = await runAdapter(smartrecruiters, tab, "fx-sr-2", true);
    log = await logOf(tab);
    check("manual unanswered_questions", r.status === "manual" && r.reason === "unanswered_questions", r);
    check("question text returned", (r.unanswered || []).some((q) => /Epic Clarity experience/i.test(q)), r.unanswered);
    check("nothing submitted, textarea untouched", has(log, "SUBMITTED").length === 0 && (await tab.evaluate(`document.getElementById('epic').value`)) === "");

    console.log("# SmartRecruiters: account password -> manual password_required");
    tab = await open("fx-sr3", "sr_flow.html?start=profile&scenario=password");
    r = await runAdapter(smartrecruiters, tab, "fx-sr-3", true);
    log = await logOf(tab);
    check("manual password_required, nothing typed", r.status === "manual" && r.reason === "password_required" && log.length === 0 && (await tab.evaluate(`document.getElementById('fn').value`)) === "", [r, log]);

    console.log("# SmartRecruiters: captcha on Submit -> manual, never solved");
    tab = await open("fx-sr4", "sr_flow.html?start=profile&scenario=captcha");
    r = await runAdapter(smartrecruiters, tab, "fx-sr-4", false);
    log = await logOf(tab);
    check("manual captcha_required", r.status === "manual" && r.reason === "captcha_required", r);
    check("challenge shown, not submitted", has(log, "CHALLENGE").length === 1 && has(log, "SUBMITTED").length === 0, log);

    console.log("# SmartRecruiters: live mode on fixture -> confirmation detected");
    tab = await open("fx-sr5", "sr_flow.html?start=profile");
    r = await runAdapter(smartrecruiters, tab, "fx-sr-5", false);
    log = await logOf(tab);
    check("applied with confirmation", r.status === "applied" && /thank you for applying/i.test(r.evidence || ""), r);
    check("submitted exactly once", has(log, "SUBMITTED").length === 1, log);
    check("submitted form: marketing/talent unticked", (has(log, "form")[0] || { v: {} }).v.mk === false && has(log, "form")[0].v.tc === false && has(log, "form")[0].v.privacy === true, has(log, "form"));

    console.log("# Ashby: Application tab -> single-page form; dry run stops before Submit");
    tab = await open("fx-ab1", "ashby_form.html");
    r = await runAdapter(ashby, tab, "fx-ab-1", true);
    log = await logOf(tab);
    check("dry_run_complete (status skipped)", r.status === "skipped" && r.reason === "dry_run_complete", r);
    check("NOT submitted", has(log, "SUBMITTED").length === 0, log);
    form = await tab.evaluate(`(() => { const g = id => document.getElementById(id); const on = n => { const r = document.querySelector('input[name='+n+']:checked'); return r && r.id; }; return {name: g('_systemfield_name').value, email: g('_systemfield_email').value, phone: g('phone').value, li: g('li').value, resume: g('_systemfield_resume').files.length, resumeName: g('_systemfield_resume').files[0] && g('_systemfield_resume').files[0].name, auth: on('auth'), sp: on('sp'), gender: g('gender').value, vet: g('vet').value, privacy: g('privacy').checked, mk: g('mk').checked}; })()`);
    check("name/email/phone/resume filled", form.name === "Jane Doe" && form.email === "jane.doe@example.com" && form.phone.replace(/\D/g, "").endsWith("5550100199") && form.resume === 1 && form.resumeName === "resume.pdf", form);
    check("authorized=Yes, sponsorship=No (radios)", form.auth === "auth-labeled-radio-0" && form.sp === "sp-labeled-radio-1", form);
    check("EEO declined; optional LinkedIn untouched (not in profile)", form.gender === "Decline to self-identify" && form.vet === "Decline to self-identify" && form.li === "", form);
    check("privacy ticked; marketing/job-alerts NOT ticked", form.privacy === true && form.mk === false, form);
    check("privacy consent logged, marketing not", /Privacy Policy/.test(consentLog()) && !/marketing|job alerts/i.test(consentLog()), consentLog());
    check("wouldSubmit report", r.wouldSubmit && /^submit application$/i.test(r.wouldSubmit.submitButton) && r.wouldSubmit.fields.length >= 8, r.wouldSubmit);

    console.log("# Ashby: unknown required question -> manual, not guessed");
    tab = await open("fx-ab2", "ashby_form.html?open=1&scenario=unknown");
    r = await runAdapter(ashby, tab, "fx-ab-2", true);
    log = await logOf(tab);
    check("manual unanswered_questions", r.status === "manual" && r.reason === "unanswered_questions", r);
    check("question text returned", (r.unanswered || []).some((q) => /years of SAS experience/i.test(q)), r.unanswered);
    check("nothing submitted, input untouched", has(log, "SUBMITTED").length === 0 && (await tab.evaluate(`document.getElementById('q-unk').value`)) === "");

    console.log("# Ashby: account password -> manual password_required");
    tab = await open("fx-ab3", "ashby_form.html?open=1&scenario=password");
    r = await runAdapter(ashby, tab, "fx-ab-3", true);
    check("manual password_required, nothing typed", r.status === "manual" && r.reason === "password_required" && (await tab.evaluate(`document.getElementById('_systemfield_name').value`)) === "", r);

    console.log("# Ashby: captcha on Submit -> manual, never solved");
    tab = await open("fx-ab4", "ashby_form.html?open=1&scenario=captcha");
    r = await runAdapter(ashby, tab, "fx-ab-4", false);
    log = await logOf(tab);
    check("manual captcha_required", r.status === "manual" && r.reason === "captcha_required", r);
    check("challenge shown, not submitted", has(log, "CHALLENGE").length === 1 && has(log, "SUBMITTED").length === 0, log);

    console.log("# Ashby: live mode on fixture -> confirmation detected");
    tab = await open("fx-ab5", "ashby_form.html?open=1");
    r = await runAdapter(ashby, tab, "fx-ab-5", false);
    log = await logOf(tab);
    check("applied with confirmation", r.status === "applied" && /successfully submitted/i.test(r.evidence || ""), r);
    check("submitted exactly once; marketing unticked", has(log, "SUBMITTED").length === 1 && (has(log, "form")[0] || { v: {} }).v.mk === false, log);
  } finally {
    server.stop();
    for (const n of opened) { try { await browser.close({ name: n }); } catch { /* ignore */ } }
  }
  console.log(`\n${pass} passed, ${fail} failed`);
  return { pass, fail };
};
