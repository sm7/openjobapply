// Pure consumer-behavior fixture: bun test/fixtures/ats/answers_consents.test.js
// Uses synthetic profiles and an in-memory DOM, never a browser, network or real profile.
"use strict";
const fs = require("fs");
const os = require("os");
const path = require("path");
const vm = require("vm");
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "ats-consent-pure-"));
process.env.ATS_CONSENT_LOG = path.join(tmp, "consents.log");
const { classifyConsent, resolveAnswer, NeedsHuman } = require("../../../src/ats/answers");
const { fillForm } = require("../../../src/ats/form");
const profile = { answers: { consents: {
  employment_background_check: true,
  employment_drug_screening: true,
  employment_credit_check: true,
  application_status_text_messages: null,
  marketing: false,
  future_opportunity_retention_contact: true,
  government_criminal_records_release: false,
  liability_waiver: false,
} } };
const options = [{ label: "Yes", value: "Yes" }, { label: "No", value: "No" }];
const field = (label, required = true) => ({ kind: "checkbox", label, group: "", required });
const resolve = (label, p = profile, over = {}) => resolveAnswer({ ...field(label), ...over }, { profile: p });
const isManual = (fn) => { try { fn(); return false; } catch (e) { if (e instanceof NeedsHuman) return true; throw e; } };
const grants = (key, value) => ({ answers: { consents: { ...profile.answers.consents, [key]: value } } });
let pass = 0, fail = 0;
const check = (name, condition, extra) => { if (condition) { pass++; console.log("  ok  ", name); } else { fail++; console.log("  FAIL", name, extra === undefined ? "" : JSON.stringify(extra)); } };

const allowed = [
  ["employment_background_check", "I authorize the company to run an employment background check"],
  ["employment_drug_screening", "I agree to pre-employment drug screening"],
  ["employment_credit_check", "I consent to a credit check for employment purposes"],
  ["future_opportunity_retention_contact", "I consent to retain my application for future employment opportunities and contact me about future opportunities"],
  ["future_opportunity_retention_contact", "I consent to my application being retained for future employment opportunities and to be contacted about future opportunities"],
  ["future_opportunity_retention_contact", "I agree to contact me about future employment opportunities"],
];
console.log("# each reviewed consent checks only its own explicit scope");
for (const [key, label] of allowed) {
  const action = resolve(label);
  check(`explicit grant: ${label}`, action.op === "check" && action.consent?.text === label, action);
  for (const value of [undefined, null, false]) {
    const p = grants(key, value);
    check(`${key} ${String(value)} cannot authorize required consent`, isManual(() => resolve(label, p)));
    check(`${key} ${String(value)} leaves optional unchecked`, resolve(label, p, { required: false }).op === "leave");
  }
  check("legacy no-profile classifier remains conservative", classifyConsent(label).verdict !== "allow");
  check("empty profile cannot authorize", isManual(() => resolve(label, {})));
}
const combined = "I authorize an employment background check and pre-employment drug screening and a credit check for employment purposes";
check("all explicitly granted employment checks can combine", resolve(combined).op === "check");
for (const key of ["employment_background_check", "employment_drug_screening", "employment_credit_check"]) {
  check(`combined screening requires ${key}`, isManual(() => resolve(combined, grants(key, null))));
}
for (const label of [
  "Are you willing to undergo a background check as a condition of employment?",
  "Are you willing to submit to pre-employment drug screening?",
  "Do you consent to a credit check for employment purposes?",
  "If hired, would you be willing to submit a background check?",
]) {
  const action = resolve(label, profile, { kind: "select", options });
  check(`willingness resolves to Yes: ${label}`, action.op === "choose" && action.test("Yes") && !action.test("No") && !!action.consent, action);
  check("willingness is blocked without reviewed grants", isManual(() => resolve(label, {}, { kind: "select", options })));
}

console.log("# privacy is not a new authorization, history answer, or blanket prefix");
for (const label of [
  "I have read and agree to the Privacy Notice for job applicants.",
  "I have read and agree to the Terms and Conditions",
  "I certify that the information I have provided is true and accurate.",
  "I certify that the information provided is true and accurate.",
]) check(`unchanged standard consent: ${label}`, resolve(label, {}).op === "check");
for (const label of [
  "I agree to an employment background check and authorize biometric collection",
  "I agree to a pre-employment background check and authorize biometric collection",
  "I agree to the privacy policy and ongoing location tracking",
  "I agree to the privacy policy and authorize facial recognition",
  "I agree to an employment background check and authorize future unspecified checks",
  "I authorize a background check",
  "I authorize the company to run a background check",
  "Employment background check",
  "I have read and agree to biometric collection",
  "I agree to retain my application indefinitely",
  "I agree to receive application status text messages",
  "I agree to the Privacy Policy and receive application status text messages",
  "I consent to retain my application for future opportunities and receive promotional SMS",
  "I authorize an employment background check and agree to binding arbitration",
  "I authorize an employment background check and waive liability",
  "I authorize an employment background check and a government criminal records release",
  "I authorize government and law enforcement agencies to release criminal records and waive liability",
  "I agree to the privacy policy and authorize government and law enforcement agencies to release criminal records and waive liability",
  "I authorize an employment background check and a release of information",
  "I consent to retain my application for future opportunities and share my data with third parties",
  "I agree to the Privacy Policy and a non-compete agreement",
  "I agree to the privacy policy and electronic signing",
  "I agree to receive marketing emails",
  "I agree to receive job alerts",
  "I agree to join the talent community for future opportunities",
]) {
  check(`required mixed/unknown/forbidden stops: ${label}`, isManual(() => resolve(label)));
  check(`optional mixed/unknown/forbidden unchecked: ${label}`, resolve(label, profile, { required: false }).op === "leave");
}
for (const label of [
  "Have you ever been convicted of a crime?",
  "Do you have a background check or drug screen on file?",
  "Have you ever failed a pre-employment drug screening?",
  "Have you ever been denied employment because of a credit check?",
  "Are you willing to undergo an employment background check and have you ever been convicted of a crime?",
]) check(`screening is not factual history: ${label}`, isManual(() => resolve(label, profile, { kind: "select", options })));

// Evaluate the actual serialized scanner/action callbacks against a minimal synthetic DOM.
// No source-string matching or substituting scanner results: fillForm consumes real fields.
function fakeTab(controls, stuck = false) {
  const root = { querySelectorAll: () => controls };
  for (const [index, el] of controls.entries()) Object.assign(el, {
    id: `consent-${index}`, tagName: "INPUT", className: "", labels: [], parentElement: root,
    getAttribute(name) { return name === "type" ? "checkbox" : name === "aria-label" ? this.label : null; },
    getBoundingClientRect() { return { width: 20, height: 20, right: 20, bottom: 20 }; },
    closest() { return null; },
    click() { if (!stuck) this.checked = !this.checked; },
  });
  const document = {
    body: root,
    querySelector(selector) { return controls.find((el) => `#${el.id}` === selector); },
    querySelectorAll(selector) { return selector.startsWith("#") ? controls.filter((el) => `#${el.id}` === selector) : controls; },
  };
  const context = { document, CSS: { escape: (s) => s }, scrollX: 0, scrollY: 0 };
  return {
    async evaluate(expression) { return vm.runInNewContext(expression, context); },
    async isChecked(selector) { return document.querySelector(selector).checked; },
    async uncheck(selector) { if (!stuck) document.querySelector(selector).checked = false; },
    async check(selector) { if (!stuck) document.querySelector(selector).checked = true; },
  };
}
async function consumers() {
  console.log("# fillForm clears preselected unapproved optional controls and verifies it");
  const controls = [
    { label: allowed[0][1], required: true, checked: false },
    { label: "I agree to privacy policy and ongoing location tracking", required: false, checked: true },
    { label: "I authorize an employment background check and waive liability", required: false, checked: true },
    { label: "I agree to application status text messages", required: true, checked: false },
  ];
  const ctx = { tab: fakeTab(controls), profile, job: { id: "fixture" }, site: "fixture.invalid", consents: [], dryRun: true };
  const report = await fillForm(ctx);
  check("approved screening was actually checked", controls[0].checked && report.consents.length === 1, report);
  check("preselected unknown and forbidden were actually cleared", !controls[1].checked && !controls[2].checked && report.skipped.length === 2, report);
  check("required status SMS remains unresolved", report.unanswered.length === 1 && /status text messages/.test(report.unanswered[0].question), report);
  const stuckControl = { label: "I agree to privacy policy and ongoing location tracking", required: false, checked: true };
  const stuckReport = await fillForm({ ...ctx, tab: fakeTab([stuckControl], true), consents: [] });
  check("failed optional uncheck becomes blocking, not silent consent", stuckControl.checked && stuckReport.unanswered.length === 1 && stuckReport.unanswered[0].required && stuckReport.unverified.length === 1, stuckReport);
}
consumers().then(() => {
  console.log(`\n${pass} passed, ${fail} failed`);
  fs.rmSync(tmp, { recursive: true, force: true });
  process.exit(fail ? 1 : 0);
}).catch((error) => { console.error(error); fs.rmSync(tmp, { recursive: true, force: true }); process.exit(1); });
