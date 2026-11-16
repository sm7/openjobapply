// Standalone fixture test for src/ats/adapters/icims.js. Needs the omp `browser` global, so run it from
// an omp eval cell (managed/headless browser; never the user's relay):
//
//   const run = require("/abs/path/test/fixtures/ats/icims.test.js"); await run(browser);
//
// Serves test/fixtures/ats/icims over 127.0.0.1. icims_outer.html wraps icims_flow.html in
// iframe#icims_content_iframe like a real iCIMS career site; the page POSTs every interesting event to
// /__log so assertions survive the real navigations between steps. Nothing touches a real site.
"use strict";
const fs = require("fs");
const os = require("os");
const path = require("path");

module.exports = async function run(browser) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "ats-icims-test-"));
  process.env.ATS_CONSENT_LOG = path.join(tmp, "consents.log");
  process.env.ATS_EVIDENCE_DIR = path.join(tmp, "evidence");
  const ats = require("../../../src/ats/index.js");
  const icims = require("../../../src/ats/adapters/icims.js");
  if (!ats.adapters.includes(icims)) ats.adapters.unshift(icims); // index.js registers it for real; keep the test independent of that
  const profile = JSON.parse(fs.readFileSync(path.resolve(__dirname, "../profile.fixture.json"), "utf8"));

  const dir = path.join(__dirname, "icims");
  let events = [];
  const server = Bun.serve({
    port: 0,
    async fetch(req) {
      const u = new URL(req.url);
      if (req.method === "POST" && u.pathname === "/__log") { events.push(JSON.parse(await req.text())); return new Response("ok"); }
      const f = Bun.file(path.join(dir, u.pathname));
      return f.size ? new Response(f, { headers: { "content-type": "text/html" } }) : new Response("nf", { status: 404 });
    },
  });
  const base = `http://127.0.0.1:${server.port}`;
  let pass = 0, fail = 0;
  const check = (name, cond, extra) => { if (cond) { pass++; console.log("  ok  ", name); } else { fail++; console.log("  FAIL", name, extra === undefined ? "" : JSON.stringify(extra)); } };
  const has = (k) => events.filter((e) => e.k === k);
  const names = [];
  const apply = async (name, file, scn, opts) => {
    events = [];
    names.push(name);
    const tab = await browser.open({ name, url: `${base}/${file}`, app: "own" });
    const job = { id: name, source: "linkedin", url: "https://careers-fallon.icims.com/jobs/8548/job", apply_url: "https://careers-fallon.icims.com/jobs/8548/job" };
    const r = await ats.applyOnSite(browser, job, profile, { tab, adapter: "icims", dryRun: true, ...opts });
    await new Promise((res) => setTimeout(res, 300)); // let in-flight beacons land
    return r;
  };
  const noForbidden = () => has("FORBIDDEN_CLICK").length === 0;

  try {
    console.log("# iCIMS: host matching");
    check("matches *.icims.com / jobs-* / careers-*", ["https://x.icims.com/jobs/1/a/job", "https://jobs-fallon.icims.com/jobs/8548/job", "https://careers-fallonhealth.icims.com/jobs/8548/job"].every((u) => icims.matches(u)), null);
    check("does not match other hosts", !icims.matches("https://icims.example.com/x") && !icims.matches("https://boards.greenhouse.io/x") && !icims.matches("not a url"), null);

    console.log("# iCIMS: login wall (framed) -> account_required");
    let r = await apply("fx-icims-wall", "icims_outer.html?scn=wall&step=job", "wall");
    check("manual/account_required", r.status === "manual" && r.reason === "account_required", r);
    check("drove the iframe", r.framed === true && (r.trail || []).includes("job") && (r.trail || []).includes("wall"), r.trail);
    check("Apply clicked, nothing else (no login, no create, no social, no talent network)", has("APPLY_CLICK").length === 1 && has("LOGIN_CLICK").length === 0 && has("ACCOUNT_CREATED").length === 0 && has("EMAIL_SUBMITTED").length === 0 && noForbidden(), events);

    console.log("# iCIMS: existing account password page -> password_required");
    r = await apply("fx-icims-exists", "icims_outer.html?scn=exists&step=job", "exists");
    check("manual/password_required", r.status === "manual" && r.reason === "password_required", r);
    check("profile email submitted once, nothing typed into the password box, no reset/login", has("EMAIL_SUBMITTED").length === 1 && has("EMAIL_SUBMITTED")[0].v === "jane.doe@example.com" && has("PASSWORD_TYPED").length === 0 && has("LOGIN_SUBMITTED").length === 0 && has("PASSWORD_RESET").length === 0 && noForbidden(), events);
    check("trail email_step -> password", (r.trail || []).join(">") === "job>email_step>password", r.trail);

    console.log("# iCIMS: landing directly on the password page");
    r = await apply("fx-icims-pw", "icims_outer.html?scn=pw&step=password", "pw");
    check("manual/password_required", r.status === "manual" && r.reason === "password_required", r);
    check("nothing clicked or typed", events.length === 0, events);

    console.log("# iCIMS: new email -> create-profile page -> account_required");
    r = await apply("fx-icims-new", "icims_outer.html?scn=new&step=job", "new");
    check("manual/account_required", r.status === "manual" && r.reason === "account_required", r);
    check("account NOT created, no password typed", has("ACCOUNT_CREATED").length === 0 && has("PASSWORD_TYPED").length === 0 && noForbidden(), events);
    check("trail email_step -> register", (r.trail || []).join(">") === "job>email_step>register", r.trail);

    console.log("# iCIMS: authenticated session, multi-page dry run (framed)");
    r = await apply("fx-icims-auth", "icims_outer.html?scn=auth&step=job", "auth");
    check("skipped/dry_run_complete", r.status === "skipped" && r.reason === "dry_run_complete", r);
    check("NO submit recorded", has("SUBMITTED").length === 0, events);
    check("authenticated session recognised, framed", r.authenticated === true && r.framed === true, r);
    const prof = (has("profile")[0] || {}).v || {};
    check("resume uploaded into the iframe", prof.resume === "resume.pdf", prof);
    check("contact fields from profile (select + text)", prof.fn === "Jane" && prof.ln === "Doe" && prof.em === "jane.doe@example.com" && /555/.test(prof.ph) && prof.ad === "100 Example Street" && prof.ct === "Springfield" && prof.st === "MA" && prof.zp === "01101", prof);
    const q = (has("questions")[0] || {}).v || {};
    check("authorized=Yes, sponsorship=No, heard=LinkedIn, job alerts NOT ticked", q.auth === "y" && q.spons === "n" && q.hear === "LinkedIn" && q.alerts === false, q);
    const e = (has("eeo")[0] || {}).v || {};
    check("EEO declined; background check NOT ticked; truthfulness ticked", e.gender === "I do not wish to answer" && e.vet === "I decline to self-identify" && e.race === "Decline to self-identify" && e.bg === false && e.truth === true, e);
    check("wouldSubmit report", r.wouldSubmit && /^submit$/i.test(r.wouldSubmit.submitButton) && r.wouldSubmit.fields.length >= 12, r.wouldSubmit);
    check("trail covers every page", (r.trail || []).join(">") === "job>form>form>form>form", r.trail);
    check("no forbidden control clicked", noForbidden(), events);
    const consents = fs.readFileSync(process.env.ATS_CONSENT_LOG, "utf8");
    check("truthfulness consent logged, background check / alerts not", /true and accurate/.test(consents) && !/background|alerts/i.test(consents), consents);

    console.log("# iCIMS: required unknown question -> unanswered_questions");
    r = await apply("fx-icims-unknown", "icims_outer.html?scn=unknown&step=job", "unknown");
    check("manual/unanswered_questions", r.status === "manual" && r.reason === "unanswered_questions", r);
    check("question text returned", (r.unanswered || []).some((x) => /Epic Clarity/i.test(x)), r.unanswered);
    check("did not advance past the question page, nothing submitted", has("questions").length === 0 && has("eeo").length === 0 && has("SUBMITTED").length === 0, events);

    console.log("# iCIMS: page without an iframe (top-level in_iframe=1 page)");
    r = await apply("fx-icims-top", "icims_flow.html?scn=auth&step=profile&in_iframe=1", "auth");
    check("skipped/dry_run_complete, framed=false", r.status === "skipped" && r.reason === "dry_run_complete" && r.framed === false, r);
    check("no submit", has("SUBMITTED").length === 0, events);

    console.log("# iCIMS: live mode on the fixture submits once and detects the confirmation");
    r = await apply("fx-icims-live", "icims_outer.html?scn=live&step=job", "live", { dryRun: false, keepOpen: true });
    check("applied with confirmation", r.status === "applied" && /thank you for applying/i.test(r.evidence || ""), r);
    check("submitted exactly once", has("SUBMITTED").length === 1, events);

    console.log("# iCIMS: Submit opens Source, not a receipt; unknown source is never invented");
    r = await apply("fx-icims-source-unknown", "icims_outer.html?scn=source-unknown&step=review", "source-unknown", { dryRun: false, keepOpen: true });
    check("unfinished Source thank-you banner is NOT applied", r.status === "manual" && r.reason === "unanswered_questions" && (r.unanswered || []).some((x) => /^Source/i.test(x)), r);
    check("review submitted once; Source not submitted; no receipt", has("REVIEW_SUBMIT").length === 1 && has("SOURCE_SUBMIT").length === 0 && has("SUBMITTED").length === 0, events);
    check("continued to observed Source form", (r.trail || []).join(">") === "form>form", r.trail);

    console.log("# iCIMS: changed Source form uses known discovery source and requires a real receipt");
    r = await apply("fx-icims-source-known", "icims_outer.html?scn=source-known&step=review", "source-known", { dryRun: false, keepOpen: true });
    check("applied only after source submit reaches receipt", r.status === "applied" && /thank you for applying/i.test(r.evidence || ""), r);
    const source = (has("SOURCE_SUBMIT")[0] || {}).v || {};
    check("each distinct Submit clicked exactly once", has("REVIEW_SUBMIT").length === 1 && has("SOURCE_SUBMIT").length === 1 && has("SUBMITTED").length === 1, events);
    check("recorded LinkedIn source; source contact/channel facts left blank", source.source === "LinkedIn" && source.channel === "" && source.sourceName === "" && source.sourceEmail === "" && source.sourcePhone === "" && noForbidden(), source);

    console.log("# iCIMS: validation on the same Source form must not cause blind double Submit");
    r = await apply("fx-icims-source-invalid", "icims_outer.html?scn=source-invalid&step=review", "source-invalid", { dryRun: false, keepOpen: true });
    check("site validation remains unconfirmed", r.status === "failed" && r.reason === "validation_error_after_submit" && /Source channel requires review/.test(r.evidence || ""), r);
    check("no repeated Source Submit after validation changes page text", has("REVIEW_SUBMIT").length === 1 && has("SOURCE_SUBMIT").length === 1 && has("SUBMITTED").length === 0, events);

    console.log("# iCIMS: observed Fallon Source controls have no progress marker and native optional fields");
    r = await apply("fx-icims-source-observed", "icims_outer.html?scn=source-observed&step=review", "source-observed", { dryRun: false, keepOpen: true });
    check("recognizes Source by fields, not a required Source heading", r.status === "failed" && r.reason === "validation_error_after_submit" && (r.trail || []).join(">") === "form>form", r);
    const observedSource = (has("SOURCE_SUBMIT")[0] || {}).v || {};
    check("does not infer bare Source or fabricate source contacts", Object.keys(observedSource).length === 5 && Object.values(observedSource).every((v) => v === ""), observedSource);
    check("optional-field server validation does not repeat either Submit", has("REVIEW_SUBMIT").length === 1 && has("SOURCE_SUBMIT").length === 1 && has("SUBMITTED").length === 0 && noForbidden(), events);

    console.log("# iCIMS: dry run may navigate explicitly intermediate Submit but stops before final Source Submit");
    r = await apply("fx-icims-intermediate-dry", "icims_outer.html?scn=intermediate-source&step=intermediate", "intermediate-source");
    check("dry run reaches final Source without claiming applied", r.status === "skipped" && r.reason === "dry_run_complete" && (r.trail || []).join(">") === "form>form", r);
    check("intermediate Submit only, no final submission", has("INTERMEDIATE_SUBMIT").length === 1 && has("SOURCE_SUBMIT").length === 0 && has("SUBMITTED").length === 0, events);
    check("known source included in dry-run summary", r.wouldSubmit && r.wouldSubmit.fields.some((f) => f.rule === "heard_about_us" && f.value === "LinkedIn"), r.wouldSubmit);

    console.log("# iCIMS: unknown finality preserves dry-run stop");
    r = await apply("fx-icims-source-review-dry", "icims_outer.html?scn=source-known&step=review", "source-known");
    check("final-looking review Submit is not probed in dry run", r.status === "skipped" && r.reason === "dry_run_complete" && has("REVIEW_SUBMIT").length === 0 && has("SOURCE_SUBMIT").length === 0, r);
  } finally {
    server.stop();
    for (const n of names) { try { await browser.close({ name: n }); } catch { /* ignore */ } }
  }
  console.log(`\n${pass} passed, ${fail} failed`);
  return { pass, fail };
};
