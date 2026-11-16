// Deterministic test for the standing Yes/No answers in src/ats/answers.js: no browser, no network, no real profile.
//   bun test/fixtures/ats/answers_standing.test.js
// Each standing answer must resolve its OWN question and must not leak onto neighbouring questions whose true answer
// is different or unknown (veteran status, holding a license, convictions, clearances, the prospective employer).
"use strict";
const { resolveAnswer, NeedsHuman } = require("../../../src/ats/answers.js");

const profile = {
  candidate: { name: "Fixture Candidate", resume_path: "data/resume.pdf" },
  preferences: {},
  answers: {
    excluded_from_federal_health_programs: false,
    spouse_or_partner_of_us_military: false,
    prior_government_employment: false,
    professional_license_disciplinary_action: false,
    previously_worked_at_prospective_employer: false,
    past_employers: ["Fixture Past Employer"],
    prior_interviews_by_company: { "Acme Medical Devices": false, "Globex Life Sciences": false, "Initech Medicine Group": false, "Initech Analytics": false },
    restrictive_employer_agreements: false,
    federal_contracting_debarment_or_suspension: false,
    public_trust_ever_held: false,
    education: { bachelors_degree: true, bachelors_degree_name: "BMSc", masters_degree_name: "MPH", additional_degrees: false, formal_equivalency_obtained: false },
    relationship_with_employees: false,
    python_or_r_in_current_or_most_recent_role: false,
    python_libraries_used: { pandas: false, numpy: false, "scikit-learn": false, seaborn: false, matplotlib: false },
    databricks_advanced_sql_large_datasets: false,
    built_or_owned_business_kpis: true,
    analyses_driven_business_outcomes: true,
    business_outcome_example: "I analysed length-of-stay reports to find the root causes of delays and recommended fixes that the care team then carried out.",
    presented_to_senior_leadership: true,
    built_or_maintained_automated_data_integrations: false,
    independently_managed_remote_deliverables: true,
    vba_experience: false,
    referred_by_anyone: false,
    default_skill_years: 1,
    years_experience: { python: 2 },
  },
};
const yesNo = [{ label: "Yes", value: "Yes" }, { label: "No", value: "No" }];
const resolve = (label, over = {}) => resolveAnswer({ kind: "select", label, group: "", required: true, options: yesNo, ...over }, { profile: over.profile || profile, job: over.job });
const outcome = (label, over) => { try { return resolve(label, over).rule || "answered"; } catch (e) { if (e instanceof NeedsHuman) return "NEEDS_HUMAN"; throw e; } };

let pass = 0, fail = 0;
const check = (name, cond, extra) => { if (cond) { pass++; console.log("  ok  ", name); } else { fail++; console.log("  FAIL", name, extra === undefined ? "" : JSON.stringify(extra)); } };

console.log("# each standing answer resolves its own question to No");
for (const [label, rule] of [
  ["Are you currently excluded, debarred or suspended from participation in any federal or state health care program such as Medicare or Medicaid?", "standing_no_health_program_exclusion"],
  ["Are you listed on the OIG List of Excluded Individuals/Entities?", "standing_no_health_program_exclusion"],
  ["Are you the spouse or partner of a member of the U.S. military?", "standing_no_military_spouse"],
  ["Is your husband, wife or domestic partner currently on active duty in the armed forces?", "standing_no_military_spouse"],
  ["Have you ever been employed by a federal, state or local government agency?", "standing_no_prior_government_employment"],
  ["Have you previously worked in the public sector for a government agency?", "standing_no_prior_government_employment"],
  ["Has any disciplinary action ever been taken against a professional license you hold or have held?", "standing_no_license_discipline"],
  ["Has your license or certification ever been revoked, suspended or placed on probation?", "standing_no_license_discipline"],
]) {
  const r = resolve(label);
  check(`${rule}: ${label.slice(0, 60)}`, r.rule === rule && r.op === "choose" && r.test("No") && !r.test("Yes"), r);
}

console.log("# near-miss questions are never answered by these rules");
for (const [label, why] of [
  ["Have you ever been convicted of a crime or excluded from Medicare?", "compound conviction question: only exclusion was confirmed"],
  ["Do you currently hold an active professional license?", "holding a license is not the same as discipline"],
  ["Are you currently licensed as a registered nurse?", "holding a license"],
  ["Do you hold an active security clearance or public trust determination?", "clearance is not government employment"],
  ["Have you ever worked for a federal contractor and held a public trust clearance?", "contractor/clearance wording"],
  ["Do you have a background check or drug screen on file?", "background/drug is never inferred"],
]) check(`${why}`, outcome(label) === "NEEDS_HUMAN", outcome(label));

console.log("# veteran status stays with the EEO decline path, never the military-spouse answer");
{
  const r = resolve("Are you a protected veteran?");
  check("veteran question declines instead of answering No", r.rule === "eeo_decline", r);
  check("own veteran wording is not a military-spouse answer", outcome("Are you a veteran of the U.S. military?") !== "standing_no_military_spouse", outcome("Are you a veteran of the U.S. military?"));
}

console.log("# other standing answers keep their own scope");
{
  const r = resolve("Have you ever worked for or been employed by this company?");
  check("prior-employer question still uses the prior-employer rule", r.rule === "standing_no_prior_employment_at_company", r);
  const g = resolve("Have you ever been employed by a federal, state or local government agency?");
  check("government employment does not fall into the prior-employer rule", g.rule === "standing_no_prior_government_employment", g);
}

console.log("# answers are used only for Yes/No choices and only when the profile states them");
{
  check("free-text box is not answered", outcome("Are you the spouse or partner of a member of the U.S. military?", { kind: "text", options: [] }) === "NEEDS_HUMAN");
  const bare = { ...profile, answers: { past_employers: [] } };
  for (const label of ["Are you excluded from any federal health care program such as Medicare?", "Are you the spouse or partner of a member of the U.S. military?", "Have you ever been employed by a federal, state or local government agency?", "Has any disciplinary action been taken against a professional license?"]) {
    check(`unconfirmed answer is not invented: ${label.slice(0, 50)}`, outcome(label, { profile: bare }) === "NEEDS_HUMAN", outcome(label, { profile: bare }));
  }
  const yes = { ...profile, answers: { ...profile.answers, spouse_or_partner_of_us_military: true } };
  const r = resolve("Are you the spouse or partner of a member of the U.S. military?", { profile: yes });
  check("a stated Yes is honoured, not forced to No", r.test("Yes") && !r.test("No"), r);
}

const no = (name, label, over) => {
  try {
    const r = resolve(label, over);
    check(name, r.op === "text" ? r.value === "No" : r.op === "choose" && r.test("No") && !r.test("Yes"), r);
  } catch (e) { check(name, false, e.message); }
};
const human = (name, label, over) => check(name, outcome(label, over) === "NEEDS_HUMAN", outcome(label, over));
const without = (key, value) => ({ ...profile, answers: { ...profile.answers, [key]: value } });

console.log("# interview answers are company-scoped, never applications or relationships");
for (const company of ["Acme Medical Devices", "Globex Life Sciences", "Initech Medicine Group", "Initech Analytics"]) {
  no(`interview No: ${company}`, `Have you ever interviewed at ${company}?`);
  no(`reliable job-company fallback: ${company}`, "Have you ever interviewed at this company?", { job: { company } });
  human(`missing interview fact: ${company}`, `Have you ever interviewed at ${company}?`, { profile: without("prior_interviews_by_company", {}) });
  human(`null interview fact: ${company}`, `Have you ever interviewed at ${company}?`, { profile: without("prior_interviews_by_company", { [company]: null }) });
  human(`applications not interviews: ${company}`, `Have you ever applied at ${company}?`);
}
no("observed interview wording and trademark", "Have you ever interviewed with our company before?", { job: { company: "Globex Life Sciences®" } });
human("company wording cannot borrow another employer", "Have you ever interviewed with our company before?", { job: { company: "Another Company®" } });
for (const company of ["Another Company", "Initech", "Initech Medical", "Initech Medicine Group and its affiliates", "Acme Medical Devices or Another Company"]) {
  human(`unconfirmed company/affiliate: ${company}`, `Have you ever interviewed at ${company}?`);
}
human("generic interview has no company context", "Have you ever interviewed at this company?");
human("unrelated job-company does not borrow named facts", "Have you ever interviewed at this company?", { job: { company: "Another Company" } });
human("named unknown company cannot borrow known job context", "Have you ever interviewed at Another Company?", { job: { company: "Acme Medical Devices" } });
no("exact Acme mixed employment/interview", "Have you ever interviewed or worked at Acme Medical Devices?");
human("mixed employment/interview needs employment fact", "Have you ever interviewed or worked at Acme Medical Devices?", { profile: without("previously_worked_at_prospective_employer", null) });
human("mixed employment/interview retains resume-employer guard", "Have you ever interviewed or worked at Acme Medical Devices?", { profile: without("past_employers", ["Acme Medical Devices"]) });
human("mixed interview/application is unknown", "Have you ever interviewed or applied at Acme Medical Devices?");
human("interview/relationship mixed question is unknown", "Have you ever interviewed at Acme Medical Devices or known any employees?");
human("past-employer prior-work guard retained", "Have you ever worked at Fixture Past Employer?");
human("interview free text not auto-answered", "Have you ever interviewed at Acme Medical Devices?", { kind: "text", options: [] });

console.log("# restrictive agreement fact is not willingness or new authorization");
const agreement = "Have you signed confidentiality/noncompetition/nonsolicitation/invention/other restrictive agreement favoring another employer?";
no("exact historical restrictive agreement question", agreement);
no("historical noncompete with previous employer", "Have you ever signed a non-compete agreement with a previous employer?");
human("restrictive agreement absence is unknown", agreement, { profile: without("restrictive_employer_agreements", undefined) });
human("restrictive agreement null is unknown", agreement, { profile: without("restrictive_employer_agreements", null) });
human("willingness to sign is not history", "Are you willing to sign a noncompetition agreement favoring another employer?");
human("unrelated signed agreement not covered", "Have you signed a settlement agreement with another employer?");
human("mixed waiver/history is not covered", "Have you signed a noncompetition agreement with another employer and waived liability?");
human("historical agreement free text stays manual", agreement, { kind: "text", options: [] });

console.log("# federal contracting is distinct from OIG and criminal history");
const federal = "Have you ever been debarred or suspended from federal contracting?";
const oigFederal = "Self-Disclosure of OIG Exclusion and Federal Government Debarment.";
no("federal contracting No", federal);
no("exact OIG + federal self-disclosure No", oigFederal);
no("combined OIG/federal history No", "Have you been excluded from Medicare or debarred from federal contracting?");
human("federal answer absence unknown", federal, { profile: without("federal_contracting_debarment_or_suspension", undefined) });
human("federal answer null unknown", federal, { profile: without("federal_contracting_debarment_or_suspension", null) });
human("combined needs federal fact", oigFederal, { profile: without("federal_contracting_debarment_or_suspension", null) });
human("combined needs OIG fact", oigFederal, { profile: without("excluded_from_federal_health_programs", null) });
human("combined cannot say No when OIG is true", oigFederal, { profile: without("excluded_from_federal_health_programs", true) });
human("combined cannot say No when contracting is true", oigFederal, { profile: without("federal_contracting_debarment_or_suspension", true) });
human("contracting + convictions not inferred", "Have you been debarred from federal contracting or convicted of a crime?");
human("OIG+contracting+criminal not inferred", `${oigFederal} Have you ever been convicted of a crime?`);
human("contracting is not prior government employment", "Have you ever worked for a federal contracting company?");
human("state contracting not federal grant", "Have you ever been debarred from state contracting?");
human("contracting disclosure free text stays manual", federal, { kind: "text", options: [] });

console.log("# Public Trust held is not eligibility or a general clearance");
const trust = "Have you ever held a Public Trust clearance?";
no("Public Trust never held", trust);
human("Public Trust absence unknown", trust, { profile: without("public_trust_ever_held", undefined) });
human("Public Trust null unknown", trust, { profile: without("public_trust_ever_held", null) });
for (const label of ["Are you eligible for a Public Trust clearance?", "Have you ever held a security clearance?", "Have you ever held a Public Trust or Secret clearance?", "Have you ever held a Public Trust clearance and are you eligible now?", "Do you currently hold a Public Trust determination?"]) human(label, label);

console.log("# additional education and formal equivalency have their own facts");
for (const [key, label, rule] of [
  ["additional_degrees", "Do you have any additional degrees beyond your BMSc and MPH?", "standing_additional_degrees"],
  ["formal_equivalency_obtained", "Have you obtained a formal equivalency evaluation for your BMSc?", "standing_formal_equivalency"],
]) {
  no(rule, label);
  for (const absent of [undefined, null]) human(`${key}: missing/null`, label, { profile: without("education", { ...profile.answers.education, [key]: absent }) });
  const r = resolve(label, { options: [{ label: "BMSc", value: "BMSc" }, { label: "Doctor of Medicine", value: "MD" }] });
  check(`${key} never substitutes a wrong credential`, !r.test("BMSc") && !r.test("Doctor of Medicine") && !r.test("Bachelor's degree"), r);
}
for (const label of ["What is your highest degree?", "Do you have a US medical degree?", "Is your BMSc equivalent to a US MD?", "Do you have any additional professional certifications?", "Have you obtained a professional license through equivalency?"]) human(label, label);
{
  const bachelor = resolve("Do you have a bachelor's degree?");
  check("existing confirmed bachelor fact unchanged", bachelor.test("Yes") && !bachelor.test("No"), bachelor);
}


const yes = (name, label, over) => {
  try { const r = resolve(label, over); check(name, r.op === "choose" && r.test("Yes") && !r.test("No"), r); } catch (e) { check(name, false, e.message); }
};

console.log("# confirmed role, skill, outcome, integration and referral facts keep exact scope");
no("Python/R current-or-recent role No", "Have you used Python or R in your current or most recent role?");
human("role-scoped Python/R absent", "Have you used Python or R in your current or most recent role?", { profile: without("python_or_r_in_current_or_most_recent_role", undefined) });
human("role-scoped Python/R null", "Have you used Python or R in your current or most recent role?", { profile: without("python_or_r_in_current_or_most_recent_role", null) });
check("general Python years remain 2", resolve("How many years of experience do you have with Python?", { kind: "text", options: [] }).value === "2");
human("role-specific Python years do not use general history", "How many years of experience do you have with Python in your current role?", { kind: "text", options: [] });
for (const lib of ["pandas", "numpy", "scikit-learn", "seaborn", "matplotlib"]) {
  no(`denied library Yes/No: ${lib}`, `Do you have experience with ${lib}?`);
  check(`denied library years override default: ${lib}`, resolve(`How many years of experience do you have with ${lib}?`, { kind: "text", options: [] }).value === "0");
  human(`library fact absent: ${lib}`, `Do you have experience with ${lib}?`, { profile: without("python_libraries_used", {}) });
}
no("every listed denied Python library", "Have you used any of the following Python libraries: pandas, numpy and scikit-learn");
human("mixed denied and unknown library", "Have you used any of the following Python libraries: pandas and tensorflow");
check("unknown library falls back to default years", resolve("How many years of experience do you have with tensorflow?", { kind: "text", options: [] }).value === "1");
no("VBA experience No", "Do you have experience with VBA?");
check("VBA years override default", resolve("How many years of experience do you have with VBA?", { kind: "text", options: [] }).value === "0");
human("VBA absent", "Do you have experience with VBA?", { profile: without("vba_experience", undefined) });
no("exact combined Databricks and advanced SQL", "Do you have experience with Databricks and advanced SQL on large datasets?");
human("combined Databricks/SQL absent", "Do you have experience with Databricks and advanced SQL on large datasets?", { profile: without("databricks_advanced_sql_large_datasets", undefined) });
human("Databricks alone is not answered by the combined No", "Do you have experience with Databricks?");
human("advanced SQL alone is not answered by the combined No", "Do you have experience with advanced SQL?");
human("advanced SQL years are not default years", "How many years of experience do you have with advanced SQL?", { kind: "text", options: [] });
human("Databricks years are not default years", "How many years of experience do you have with Databricks?", { kind: "text", options: [] });
yes("business KPIs Yes", "Have you built or owned business KPIs?");
yes("business outcomes Yes", "Have your analyses driven business outcomes?");
yes("senior leadership Yes", "Have you presented to senior leadership?");
yes("independent remote deliverables Yes", "Have you independently managed remote deliverables?");
no("automated integrations No", "Have you built or maintained automated data integrations (APIs, database connections, scheduled file imports)?");
human("KPIs absent", "Have you built or owned business KPIs?", { profile: without("built_or_owned_business_kpis", undefined) });
human("business outcomes null", "Have your analyses driven business outcomes?", { profile: without("analyses_driven_business_outcomes", null) });
human("leadership null", "Have you presented to senior leadership?", { profile: without("presented_to_senior_leadership", null) });
human("remote deliverables absent", "Have you independently managed remote deliverables?", { profile: without("independently_managed_remote_deliverables", undefined) });
human("integrations null", "Have you built or maintained automated data integrations?", { profile: without("built_or_maintained_automated_data_integrations", null) });
human("different integration scope stays manual", "Have you built or maintained automated data pipelines?");
human("business outcome yes/no is not a metric", "Have your analyses saved $1 million?");
{
  const outcome = resolve("Please provide an example of how your analyses drove business outcomes", { kind: "textarea", options: [] });
  check("business outcome example is exact reviewed truthful text", outcome.op === "text" && outcome.value === profile.answers.business_outcome_example && !/\d+\s*%|\$/.test(outcome.value), outcome);
  human("outcome example needs confirmed outcome fact", "Please provide an example of how your analyses drove business outcomes", { kind: "textarea", options: [], profile: without("analyses_driven_business_outcomes", false) });
  human("outcome example needs reviewed text", "Please provide an example of how your analyses drove business outcomes", { kind: "textarea", options: [], profile: without("business_outcome_example", "") });
  human("outcome-adjacent request for metrics stays manual", "Provide the exact percentage your analysis improved revenue", { kind: "textarea", options: [] });
}
no("direct referral No", "Did anyone refer you?");
human("referral absent", "Did anyone refer you?", { profile: without("referred_by_anyone", undefined) });
human("referral null", "Did anyone refer you?", { profile: without("referred_by_anyone", null) });
check("referral-name text is not invented", resolve("Employee referral name", { kind: "text", options: [] }).value === "No referral");
human("referral-name text needs No fact", "Employee referral name", { kind: "text", options: [], profile: without("referred_by_anyone", null) });
human("employee reference stays manual", "Employer reference name", { kind: "text", options: [] });
human("referred patients stays manual", "How many patients have you referred?", { kind: "text", options: [] });
human("reference check is not referral", "Can we contact your references?");
{
  const heard = resolveAnswer({ kind: "select", label: "How did you hear about us?", group: "", required: true, options: [{ label: "LinkedIn", value: "LinkedIn" }, { label: "Indeed", value: "Indeed" }, { label: "No", value: "No" }] }, { profile, job: { source: "linkedin" } });
  check("source remains LinkedIn and not referral No", heard.rule === "heard_about_us" && heard.test("LinkedIn") && !heard.test("No"), heard);
}

console.log("# relationship follow-up name box is N/A only while the standing answer is No");
{
  const txt = { kind: "text", options: [] };
  const q = "Do you have any personal or working relationships with any current employees? If yes, please provide the name(s).";
  const r = resolve(q, txt);
  check("follow-up name box answers N/A", r.op === "text" && r.value === "N/A" && r.rule === "standing_no_relationship_followup_na", r);
  human("standing Yes is never answered N/A", q, { ...txt, profile: { ...profile, answers: { ...profile.answers, relationship_with_employees: true } } });
  human("unknown relationship fact is not answered N/A", q, { ...txt, profile: without("relationship_with_employees", undefined) });
  human("reference box is not N/A", "Please provide the name(s) of a professional reference. If yes, include relationship to employees.", txt);
  human("bare name box without relationship context", "If yes, please provide the name(s).", txt);
}
console.log("# standing rules answer only their own question (near-misses must stop for a human)");
{
  const authorized = { ...profile, candidate: { ...profile.candidate, work_authorization: "authorized_ead", requires_sponsorship: false, has_ead: true } };
  const job = { company: "Acme Health" };
  for (const q of [
    "Have you worked with SQL?",
    "Have you ever worked with healthcare claims data?",
    "Have you worked as a data analyst in a healthcare setting?",
    "Did you work with Epic Clarity at your last job?",
    "Have you worked for a payer before?",
    "Have you ever worked in a hospital?",
  ]) human(`not an employer question: ${q}`, q, { job });
  for (const q of [
    "Have you ever worked for this company?",
    "Have you ever been employed by our company?",
    "Have you ever worked for Acme Health?",
    "Have you ever worked at this company?",
  ]) no(`employer question still answered No: ${q}`, q, { job });
  human("another employer's name is not answered by the job-company rule", "Have you ever worked for Globex Health?", { job });
  for (const q of [
    "Are you willing to work onsite in our New York City office?",
    "Are you able to work a hybrid schedule from our local office?",
    "Are you comfortable working in a city or county government setting?",
  ]) human(`not government history: ${q}`, q, { job });
  for (const q of [
    "Have you ever been employed by a federal, state or local government agency?",
    "Have you previously worked in the public sector for a government agency?",
  ]) no(`government history still answered No: ${q}`, q, { job });
  for (const q of [
    "Do you have experience with HEDIS measure exclusions for Medicare populations?",
    "Do you have experience with Medicaid exclusion criteria?",
    "Describe your reporting on Medicare Advantage suspensions data",
  ]) human(`not a program-exclusion question: ${q}`, q, { job });
  no("program exclusion about the candidate still answered", "Are you currently excluded, debarred or suspended from any federal health care program such as Medicare?", { job });
  for (const q of [
    "Do you have experience building relationships with clinical staff?",
    "Describe your skills managing relationships with directors",
  ]) human(`not a personal-relationship question: ${q}`, q, { job });
  no("personal relationship with an employee still answered", "Do you have a personal or family relationship with any of our employees?", { job });
  for (const q of [
    "Are you eligible to work in Canada?",
    "Are you legally authorized to work in the United Kingdom?",
    "Are you authorized to work in the United States or Canada?",
    "Are you legally eligible to work for employers in India?",
  ]) human(`work authorization outside the US: ${q}`, q, { job, profile: authorized });
  for (const q of [
    "Are you legally authorized to work in the United States?",
    "Are you authorized to work in the U.S.?",
    "Are you legally authorized to work in the US?",
    "Are you legally authorized to work?",
  ]) yes(`US work authorization still answered Yes: ${q}`, q, { job, profile: authorized });
  human("US work authorization needs the profile fact", "Are you legally authorized to work in the United States?", { job });
}

console.log("# reviewed exact-question answers");
{
  const reviewedProfile = (map) => ({ ...profile, answers: { ...profile.answers, reviewed_questions: map } });
  const reviewedQ = reviewedProfile({
    "Are you willing to work onsite in our New York City office?": true,
    "Do you have experience with HEDIS measure exclusions for Medicare populations?": "Yes",
    "Have you worked with SQL?": true,
    "Do you have a US driver's license?": false,
    "How many years of experience do you have with Epic Clarity?": 3,
    "Would you undergo a background check?": true,
  });
  const job = { company: "Acme Health" };
  yes("reviewed Yes answers the exact onsite question", "Are you willing to work onsite in our New York City office?", { job, profile: reviewedQ });
  yes("reviewed text Yes works for a Yes/No field", "Do you have experience with HEDIS measure exclusions for Medicare populations?", { job, profile: reviewedQ });
  yes("reviewed answer wins over a standing rule that used to misfire", "Have you worked with SQL?", { job, profile: reviewedQ });
  yes("matching ignores case, spacing and end punctuation", "  have you   WORKED with sql ?", { job, profile: reviewedQ });
  no("reviewed No", "Do you have a US driver's license?", { job, profile: reviewedQ });
  check("reviewed number fills a text box", resolve("How many years of experience do you have with Epic Clarity?", { kind: "text", options: [], profile: reviewedQ }).value === "3");
  human("a different question is not matched by keyword", "Are you willing to work onsite in our Boston office?", { job, profile: reviewedQ });
  human("a longer question containing the reviewed one is not matched", "Are you willing to work onsite in our New York City office and relocate at your own cost?", { job, profile: reviewedQ });
  human("a reviewed answer never settles a background-check question", "Would you undergo a background check?", { job, profile: reviewedQ });
  human("no reviewed answers means the question still stops", "Are you willing to work onsite in our New York City office?", { job });
  const jobQ = "Describe your Epic Clarity experience";
  const viaJob = (answers, label, over = {}) => { try { return resolveAnswer({ kind: "text", label, group: "", required: true, options: [], ...over }, { profile, job, jobAnswers: answers }); } catch (e) { if (e instanceof NeedsHuman) return "NEEDS_HUMAN"; throw e; } };
  check("a job answer fills its exact question", viaJob({ [jobQ]: "Built Clarity reports for two years." }, jobQ).value === "Built Clarity reports for two years.");
  check("a job answer is not used for another question", viaJob({ [jobQ]: "x" }, "Describe your Caboodle experience") === "NEEDS_HUMAN");
  check("a job answer beats the profile list", (() => { const r = resolveAnswer({ kind: "text", label: jobQ, group: "", required: true, options: [] }, { profile: reviewedProfile({ [jobQ]: "profile text" }), job, jobAnswers: { [jobQ]: "job text" } }); return r.value === "job text" && r.rule === "reviewed_job_answer"; })());
  check("a job answer cannot answer a consent question", viaJob({ "Do you consent to a drug test?": "Yes" }, "Do you consent to a drug test?") === "NEEDS_HUMAN");
  check("a Yes/No field refuses a text reviewed answer", (() => { try { resolve("Do you use Epic daily?", { job, profile: reviewedProfile({ "Do you use Epic daily?": "Most days" }) }); return false; } catch (e) { return e instanceof NeedsHuman; } })());
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
