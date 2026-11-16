// Deterministic question -> answer mapping. THE ALLOWLIST: a question is answered only if a rule
// below matches it AND the profile holds the verified data the rule needs. Everything else throws
// NeedsHuman(questionText). Nothing here guesses, infers experience, or calls a model.
//
// resolveAnswer(field, ctx) returns an action for the form engine (src/ats/form.js):
//   {op:'text',   value, rule}                      type into a text-like input / textarea
//   {op:'choose', test(optionLabel)->bool, describe, rule, pickFirst?}   select/radio/combobox option
//   {op:'check',  rule, consent?:{text, rule}}      tick a checkbox (consents are logged by the engine)
//   {op:'leave',  rule}                             deliberately leave unticked/unchanged
//   {op:'upload', path, rule}                       resume upload
// ctx = {profile, job}.
"use strict";
const path = require("path");
const ROOT = path.resolve(__dirname, "..", "..");

class NeedsHuman extends Error {
  /** @param {string} question the on-screen question text a human must answer */
  constructor(question, detail) {
    super(`needs human: ${question}${detail ? ` (${detail})` : ""}`);
    this.name = "NeedsHuman";
    this.question = question;
    this.detail = detail || null;
  }
}

const US_STATES = new Set("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split(" "));
const STATE_NAMES = {
  AL: "Alabama", AK: "Alaska", AZ: "Arizona", AR: "Arkansas", CA: "California", CO: "Colorado", CT: "Connecticut", DE: "Delaware",
  DC: "District of Columbia", FL: "Florida", GA: "Georgia", HI: "Hawaii", ID: "Idaho", IL: "Illinois", IN: "Indiana", IA: "Iowa",
  KS: "Kansas", KY: "Kentucky", LA: "Louisiana", ME: "Maine", MD: "Maryland", MA: "Massachusetts", MI: "Michigan", MN: "Minnesota",
  MS: "Mississippi", MO: "Missouri", MT: "Montana", NE: "Nebraska", NV: "Nevada", NH: "New Hampshire", NJ: "New Jersey",
  NM: "New Mexico", NY: "New York", NC: "North Carolina", ND: "North Dakota", OH: "Ohio", OK: "Oklahoma", OR: "Oregon",
  PA: "Pennsylvania", RI: "Rhode Island", SC: "South Carolina", SD: "South Dakota", TN: "Tennessee", TX: "Texas", UT: "Utah",
  VT: "Vermont", VA: "Virginia", WA: "Washington", WV: "West Virginia", WI: "Wisconsin", WY: "Wyoming",
};

/** Verified facts derived from profile.json. Anything not present stays null (=> NeedsHuman). */
function deriveProfile(profile) {
  const c = (profile && profile.candidate) || {};
  const nameParts = (c.name || "").trim().split(/\s+/);
  const first = c.first_name || nameParts[0] || null;
  const last = c.last_name || (nameParts.length > 1 ? nameParts[nameParts.length - 1] : null);
  let digits = (c.phone || "").replace(/\D/g, "");
  if (digits.length === 11 && digits[0] === "1") digits = digits.slice(1);
  const loc = c.location || null;
  const state = c.state || ((loc || "").match(/,\s*([A-Z]{2})\s*$/) || [])[1] || null;
  return {
    first, last,
    full: c.name || (first && last ? `${first} ${last}` : null),
    email: c.email || null,
    phone: c.phone || null,
    phoneDigits: digits.length === 10 ? digits : null,
    location: loc,
    city: c.city || null,
    state: state && US_STATES.has(state) ? state : null,
    zip: c.zip || null,
    street: c.street || null,
    country: c.country || (state && US_STATES.has(state) ? "United States" : null),
    linkedin: c.linkedin_url || null,
    website: c.website || null,
    resumePath: c.resume_path ? path.resolve(ROOT, c.resume_path) : null,
    // Only the exact values recorded in profile.json produce a work-authorization answer.
    authorized: ["authorized_no_sponsor", "authorized", "authorized_ead"].includes(c.work_authorization) ? true : null,
    needsSponsorship: c.requires_sponsorship === false ? false : c.requires_sponsorship === true ? true : null,
  };
}

const norm = (s) => String(s || "").toLowerCase().replace(/[*\u2731]/g, " ").replace(/\(required\)|\brequired\b/g, " ").replace(/[:?]+\s*$/g, "").replace(/\s+/g, " ").trim();

const DECLINE = /(decline|opt[- ]?out|do(n'?t| not) wish|prefer not|not wish to|rather not|choose not|do(n'?t| not) want to (answer|disclose|self)|not to (answer|disclose|say|self)|wish not|not (disclose|specified))/i;
const YES = /^\s*(yes|y|true)\s*[.!]?\s*$/i;
const NO = /^\s*(no|n|false)\s*[.!]?\s*$/i;
const EEO_LABEL = /\b(gender|sex|race|racial|ethnic\w*|hispanic|latin[oax]|veteran|disabilit\w*|disabled|sexual orientation|transgender|pronouns?)\b/i;
const IDENTITY_GUARD = /(emergency|reference|referr|supervisor|manager|spouse|relative|parent|guardian|beneficiar|previous|former|friend|colleague|employer)/i;
const CHOICE = new Set(["select", "radio", "combobox"]);

// ---- consent classification (data/consent_policy.md) -------------------------------------------
// FORBID always wins over ALLOW, so "I agree to the privacy policy and to receive marketing" is refused.
const CONSENT_FORBID = /(marketing|newsletter|promotional|job alerts?|email (updates|alerts)|keep me (informed|updated|posted)|receive (text|sms|emails?|updates|notifications)|text messag|\bsms\b|talent (community|network|pool)|future (opportunities|openings|positions|roles)|other (opportunities|positions|roles)|background (check|screen|investigation)|consumer report|credit|drug|arbitrat|non-?compete|e-?sign|electronic (signature|signing|consent|record)|sign electronically|binding|waive|third[- ]part(y|ies)|share my (data|information|profile)|fcra|release of (information|records)|contact my (current|previous|former))/i;

// Reviewed grants remove ONLY their explicitly matched scopes from the old deny list. The
// remainder must consist entirely of recognized consent language, not a privacy-prefix escape.
const CONSENT_HARD_FORBID = /\b(marketing|newsletter|promotional|job alerts?|arbitrat\w*|non[- ]?compete|binding|waiv\w*|liability|government|criminal|fcra|release|third[- ]part(?:y|ies)|talent (?:community|network|pool)|e[- ]?sign\w*|electronic (?:signature|signing|consent|record)|sms|text messag\w*)\b|share my (?:data|information|profile)|contact my (?:current|previous|former)/i;
const SCREENING_SCOPES = [
  ["employment_background_check", /\bbackground (?:checks?|screening|screens?|investigations?)\b/gi],
  ["employment_drug_screening", /\bdrug (?:tests?|testing|screens?|screening)\b/gi],
  ["employment_credit_check", /\bcredit (?:checks?|screening|reports?)\b/gi],
];
const RETENTION_SCOPE = /\b(?:(?:retain|retaining|retention of|store|storing|storage of|keep|keeping) (?:my |your |the )?(?:application(?: data| information)?|candidate (?:data|profile)|personal (?:data|information)|resume)|(?:my|your|the) (?:application(?: data| information)?|candidate (?:data|profile)|personal (?:data|information)|resume)(?: being)? (?:retained|stored|kept))\b/gi;
const FUTURE_SCOPE = /\b(?:future|other) (?:employment |job |career )?(?:opportunities|openings|positions|roles)\b/gi;
const CONTACT_SCOPE = /\b(?:contact|contacting|contacted|consider|considering|considered)(?: me| you| the candidate)?\b/gi;

/** @returns {{verdict:'allow'|'forbid'|'unknown', rule:string}} */
function classifyConsent(text, profile) {
  const t = String(text || "").replace(/[*\u2731]/g, " ").replace(/^if hired,\s*/i, "For employment, ").replace(/\s+/g, " ").trim();
  const hard = t.match(CONSENT_HARD_FORBID);
  if (hard) return { verdict: "forbid", rule: `forbid:${hard[0].toLowerCase()}` };
  const grants = profile?.answers?.consents || {};
  const matched = [];
  let rest = t;
  for (const [key, re] of SCREENING_SCOPES) {
    re.lastIndex = 0;
    if (!re.test(rest)) continue;
    // A bare "background check" is not an employment-scoped authorization.
    if (grants[key] !== true || !/\b(employment|employer|hiring|pre[- ]employment|job application)\b/i.test(t) ||
        !/\b(?:i (?:agree|consent|authorize)|are you willing|would you be willing|do you (?:consent|agree))\b/i.test(t)) {
      return { verdict: "forbid", rule: `forbid:unapproved_${key}` };
    }
    re.lastIndex = 0;
    rest = rest.replace(re, " ");
    matched.push(key);
  }
  RETENTION_SCOPE.lastIndex = FUTURE_SCOPE.lastIndex = CONTACT_SCOPE.lastIndex = 0;
  const retention = RETENTION_SCOPE.test(rest);
  const future = FUTURE_SCOPE.test(rest);
  CONTACT_SCOPE.lastIndex = 0;
  const contact = CONTACT_SCOPE.test(rest);
  if (future && (retention || contact)) {
    if (grants.future_opportunity_retention_contact !== true || !/\bi (?:agree|consent|authorize)\b/i.test(t)) return { verdict: "forbid", rule: "forbid:unapproved_future_opportunity_retention_contact" };
    rest = rest.replace(RETENTION_SCOPE, " ").replace(FUTURE_SCOPE, " ").replace(CONTACT_SCOPE, " ");
    matched.push("future_opportunity_retention_contact");
  }
  const forbidden = rest.match(CONSENT_FORBID);
  if (forbidden) return { verdict: "forbid", rule: `forbid:${forbidden[0].toLowerCase()}` };
  // Match complete known objects/attestations; "I have read" alone grants nothing.
  rest = rest.replace(/\b(?:privacy (?:policy|notice|statement|terms)|data (?:privacy|protection|processing)|terms (?:of|and|&) (?:use|service|conditions)|portal terms|candidate (?:privacy|terms))(?: for (?:job )?applicants)?\b/gi, () => { matched.push("standard_privacy_terms"); return " "; });
  rest = rest.replace(/\bwe collect and process your personal data to evaluate your application\b/gi, " ");
  rest = rest.replace(/\b(?:i )?(?:certify|attest|affirm|confirm|declare|acknowledge|represent) that (?:the )?information (?:(?:i have )?(?:provided|submitted|given|entered)|contained (?:herein|in (?:my|this|the) application)) is (?:true|accurate|correct|complete|truthful)(?: (?:and|&) (?:true|accurate|correct|complete|truthful))*\b/gi, () => { matched.push("truthfulness"); return " "; });
  // Only grammatical glue is ignored. Unknown substantive words (even after an allowed
  // privacy/check phrase) mean the entire control needs review.
  rest = rest.replace(/\b(?:i have read|i understand|i agree|i accept|i acknowledge|i consent|i authorize|do you consent|do you agree|are you willing|would you be willing|willing|consent|authorize|agree|accept|undergo|submit to|submit a|being|be|the company|my employer|your employer|to run|to conduct|conduct|run|as a condition of employment|for employment purposes|for employment|for hiring purposes|pre[- ]employment|employment|for job applicants|notice|required to apply|adp|continue|cancel)\b/gi, " ")
    .replace(/\b(?:and|or|to|a|an|the|of|for|in|with|about|regarding|concerning|that|my|your|me|by|yes)\b|[^\w\s]/gi, " ").trim();
  if (rest || !matched.length) return { verdict: "unknown", rule: "unrecognized_consent_scope" };
  return { verdict: "allow", rule: `allow:${[...new Set(matched)].join("+")}` };
}

// ---- helpers ----------------------------------------------------------------------------------
const question = (f) => (f.group && f.group.trim()) || f.label || f.placeholder || f.name || "(unlabeled field)";

function textOrChoose(f, value, chooseTest, describe, rule) {
  if (CHOICE.has(f.kind)) return { op: "choose", test: chooseTest, describe, rule };
  if (f.kind === "text" || f.kind === "textarea" || f.kind === "email" || f.kind === "tel" || f.kind === "url" || f.kind === "number") return { op: "text", value, rule };
  throw new NeedsHuman(question(f), `rule ${rule} cannot fill ${f.kind}`);
}
const eqTest = (s) => (o) => norm(o) === norm(s);
function need(v, f, what) { if (v == null || v === "") throw new NeedsHuman(question(f), `profile.json has no ${what}`); return v; }

function yesNo(f, yes, rule) {
  if (typeof yes !== "boolean") throw new NeedsHuman(question(f), `${rule}: unconfirmed boolean`);
  return textOrChoose(f, yes ? "Yes" : "No", (o) => (yes ? YES : NO).test(o), yes ? "Yes" : "No", rule);
}

// Companies come from the profile (answers.prior_interviews_by_company), never from this file. A company absent from the profile is unknown.
function interviewCompany(target, job, companies) {
  const t = norm(target).replace(/[.,®™]/g, "").replace(/ before$/, "").trim();
  const named = companies.find((name) => norm(name) === t);
  if (named) return named;
  if (/^(this company|our company|the company|here|us)$/.test(t)) {
    return companies.find((name) => norm(name) === norm(job?.company).replace(/[®™]/g, "").trim()) || null;
  }
  return null;
}

function deniedSkill(skill, answers) {
  const s = norm(skill);
  if (/^(?:vba|visual basic for applications(?: \(vba\))?)$/.test(s) && answers.vba_experience === false) return "vba";
  const key = s === "numpy" ? "numpy" : s === "scikit learn" || s === "sklearn" ? "scikit-learn" : s;
  return ["pandas", "numpy", "scikit-learn", "seaborn", "matplotlib"].includes(key) && answers.python_libraries_used?.[key] === false ? key : null;
}

// Words that name a government employer. Used so "work ... in our City office" is not read as public-sector history.
const GOVERNMENT = String.raw`\b(?:(?:federal|state|local|municipal|county|city|public)\s+(?:government|governmental|agency|agencies|sector|service|department)|government(?:al)?|public sector)\b`;
// Places other than the United States that make a work-authorization question a human decision.
const NON_US_PLACE = /\b(canada|canadian|united kingdom|uk|great britain|england|ireland|india|mexico|australia|new zealand|germany|france|spain|italy|netherlands|europe|european union|eu|china|japan|singapore|brazil|philippines|uae|israel)\b/;

// Questions that a reviewed answer must never settle: consents, legal releases, checks and identity numbers keep their own rules.
const REVIEWED_ANSWER_EXCLUDED = /\b(background|drug|credit|marketing|newsletter|promotional|arbitrat\w*|non[- ]?compete|binding|waiv\w*|liability|criminal|convict\w*|felon\w*|release|e[- ]?sign\w*|electronic signature|ssn|social security|password)\b/;

/** The reviewed answer for this exact question, or null. `ctx.jobAnswers` is {question: answer} for this one job. */
function reviewedAnswer(f, ctx, A, keys, yesNoChoice) {
  const wanted = new Set(keys.filter(Boolean));
  if (REVIEWED_ANSWER_EXCLUDED.test(keys[0] || "")) return null;
  for (const [map, rule] of [[ctx.jobAnswers, "reviewed_job_answer"], [A.reviewed_questions, "reviewed_profile_answer"]]) {
    if (!map || typeof map !== "object") continue;
    for (const [q, raw] of Object.entries(map)) {
      if (!wanted.has(norm(q))) continue;
      const text = typeof raw === "boolean" ? (raw ? "Yes" : "No")
        : typeof raw === "number" && Number.isFinite(raw) ? String(raw)
        : typeof raw === "string" ? raw.trim() : "";
      if (!text) continue;
      if (yesNoChoice) {
        if (!/^(yes|no)$/i.test(text)) throw new NeedsHuman(question(f), `${rule}: the reviewed answer is not Yes or No`);
        return yesNo(f, /^yes$/i.test(text), rule);
      }
      return textOrChoose(f, text, eqTest(text), text, rule);
    }
  }
  return null;
}

/**
 * @param {object} f scanned field: {kind,type,label,group,required,options,placeholder,name,value}
 * @param {{profile:object, job?:object}} ctx
 */
function resolveAnswer(f, ctx) {
  const profile = ctx.profile || {};
  const A = profile.answers || {};
  const C = profile.candidate || {};
  const prefs = profile.preferences || {};
  const P = ctx.P || deriveProfile(ctx.profile);
  const label = norm(f.label);
  const grp = norm(f.group);
  const both = `${grp} ${label}`.trim();

  // ---- checkboxes: EEO groups, then consent policy
  if (f.kind === "checkbox") {
    if (EEO_LABEL.test(grp) || (EEO_LABEL.test(label) && !grp)) {
      return DECLINE.test(f.label) ? { op: "check", rule: "eeo_decline" } : { op: "leave", rule: "eeo_non_decline_option" };
    }
    const c = classifyConsent(`${f.group || ""} ${f.label || ""}`, profile);
    if (c.verdict === "allow") return { op: "check", rule: c.rule, consent: { text: (f.label || f.group || "").trim(), rule: c.rule } };
    if (c.verdict === "forbid") {
      if (f.required) throw new NeedsHuman(question(f), `required consent is forbidden by policy (${c.rule})`);
      return { op: "leave", rule: c.rule };
    }
    if (!f.required) return { op: "leave", rule: c.rule };
    throw new NeedsHuman(question(f), "unrecognized checkbox/consent");
  }

  // ---- file uploads
  if (f.kind === "file") {
    if (/\b(resume|r\u00e9sum\u00e9|cv|curriculum vitae)\b/.test(label) && !/cover/.test(label)) {
      return { op: "upload", path: need(P.resumePath, f, "candidate.resume_path"), rule: "resume" };
    }
    throw new NeedsHuman(question(f), "file other than resume");
  }

  if (f.kind === "password") throw new NeedsHuman(question(f), "password/account creation needs a human");

  // ---- EEO / demographic: always decline to self-identify
  if (EEO_LABEL.test(both) && !/sponsor|authori[sz]ed to work/.test(both)) {
    if (!CHOICE.has(f.kind)) throw new NeedsHuman(question(f), "demographic free-text; policy is decline");
    const opts = (f.options || []).filter((o) => !o.placeholder).map((o) => o.label || o.value || "");
    const hasDecline = opts.some((o) => DECLINE.test(o));
    // User-confirmed standing answers, used ONLY when the form forces a Yes/No (no decline option offered).
    // Covers exactly two questions; race, gender, disability and everything else still require a decline option.
    const D = A.demographics_when_no_decline_option || {};
    const yesNoOnly = opts.length > 0 && !hasDecline && opts.some((o) => YES.test(o)) && opts.some((o) => NO.test(o));
    const isHispanic = /\b(hispanic|latin[oax])\b/i.test(both);
    const isVeteran = /\bveteran\b/i.test(both) && !/disabilit|protected/i.test(both);
    if (yesNoOnly && isHispanic && typeof D.hispanic_or_latino === "boolean") return yesNo(f, D.hispanic_or_latino, "eeo_forced_yes_no_hispanic");
    if (yesNoOnly && isVeteran && typeof D.veteran === "boolean") return yesNo(f, D.veteran, "eeo_forced_yes_no_veteran");
    return { op: "choose", test: (o) => DECLINE.test(o), describe: "decline to self-identify", rule: "eeo_decline", pickFirst: true };
  }

  // ---- User-confirmed standing answers (2026-11-12), valid across ALL companies. Yes/No choice fields ONLY: a free-text box
  // (e.g. "name the employee") still goes to a human, and a question asking something else about employees does not match.
  const yesNoChoice = CHOICE.has(f.kind) && (f.options || []).some((o) => YES.test(o.label || o.value || "")) && (f.options || []).some((o) => NO.test(o.label || o.value || ""));

  // ---- Reviewed exact-question answers: job answers saved from the dashboard first, then profile.answers.reviewed_questions.
  // They match the question text exactly (after normalising case, spacing and punctuation) and never settle consent, legal or
  // identity questions: those keep their own rules.
  const reviewed = reviewedAnswer(f, ctx, A, [both, label, grp], yesNoChoice);
  if (reviewed) return reviewed;

  // Willingness/authorization is a consent, never a background-history fact.
  if (yesNoChoice && /^(?:if hired,? )?(are you willing|would you be willing|do you (?:consent|agree))\b/.test(both) &&
      /\b(background|drug|credit)\b/.test(both)) {
    const consent = classifyConsent(both, profile);
    if (consent.verdict !== "allow") throw new NeedsHuman(question(f), `unapproved screening consent (${consent.rule})`);
    return { ...yesNo(f, true, consent.rule), consent: { text: question(f), rule: consent.rule } };
  }

  // Interviews are independent from applications, employment and relationships.
  const interview = both.match(/^(?:have you|did you) (?:ever |previously )?(interview(?:ed)?(?: or (?:worked|been employed))?|(?:worked|been employed) or interview(?:ed)?) (?:at|for|with|by) (.+)$/);
  if (interview) {
    const company = interviewCompany(interview[2], ctx.job, Object.keys(A.prior_interviews_by_company || {}));
    const prior = company && A.prior_interviews_by_company?.[company];
    const employment = /\b(worked|employed)\b/.test(interview[1]);
    if (!yesNoChoice || typeof prior !== "boolean" ||
        (employment && (A.previously_worked_at_prospective_employer !== false ||
          (A.past_employers || []).some((e) => both.includes(norm(e)) || norm(ctx.job?.company) === norm(e))))) {
      throw new NeedsHuman(question(f), "unconfirmed company-scoped interview/employment history");
    }
    return yesNo(f, prior, employment ? "standing_prior_interview_or_employment" : "standing_prior_interview");
  }

  // Signed restrictions favoring another employer are history, NOT consent to sign.
  if (/^(?:have you (?:ever )?(?:signed|entered into)|are you (?:currently )?(?:bound by|subject to))\b/.test(both) &&
      /\b(confidentiality|non[- ]?competition|non[- ]?compete|non[- ]?solicitation|invention|restrictive)\b/.test(both) &&
      /\b(agreement|agreements|contract|contracts)\b/.test(both) &&
      /\b(?:(?:another|other|prior|previous|current|former) employer|employer other than)\b/.test(both)) {
    const scope = both
      .replace(/^(?:have you (?:ever )?(?:signed|entered into)|are you (?:currently )?(?:bound by|subject to))\s*/, "")
      .replace(/\b(?:favoring|in favor of|with|for|benefiting) (?:an? |any )?(?:another|other|prior|previous|current|former) employer$/, "")
      .replace(/\b(?:a|an|any|and|or|other|confidentiality|non[- ]?competition|non[- ]?compete|non[- ]?solicitation|invention|assignment|restrictive|agreement|agreements|contract|contracts)\b|[^\w\s]/g, " ").trim();
    if (!yesNoChoice || scope || /\b(willing|will you|would you|agree to sign|arbitrat\w*|waiv\w*|criminal|convict\w*|interview\w*)\b/.test(both)) {
      throw new NeedsHuman(question(f), "restrictive agreement scope is not historical only");
    }
    return yesNo(f, A.restrictive_employer_agreements, "standing_restrictive_employer_agreements");
  }

  // Holding Public Trust is not eligibility, nor a general clearance grant.
  if (/\bpublic trust\b/.test(both)) {
    if (yesNoChoice && /^(?:have you ever (?:held|had|been granted)|have you (?:previously )?held) (?:a )?public trust(?: (?:clearance|determination|position|status))?$/.test(both)) {
      return yesNo(f, A.public_trust_ever_held, "standing_public_trust_ever_held");
    }
    throw new NeedsHuman(question(f), "Public Trust eligibility/other clearance is unconfirmed");
  }

  // No additional education does not change the recorded degrees or grant US equivalency. Degree names come from the profile.
  const reEsc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const degreeNames = [A.education?.bachelors_degree_name, A.education?.masters_degree_name].filter((d) => typeof d === "string" && d.trim()).map((d) => reEsc(norm(d)));
  const ownDegrees = degreeNames.length ? `${degreeNames.join(" and ")}|` : "";
  const ownBachelor = degreeNames.length ? `|${degreeNames[0]}` : "";
  if (new RegExp(`^(?:do you (?:have|hold)|have you (?:earned|obtained|completed)) (?:any )?(?:additional|other) degrees?(?: (?:beyond|besides|in addition to) (?:your )?(?:${ownDegrees}listed degrees|degrees listed))?$`).test(both)) {
    return yesNo(f, A.education?.additional_degrees, "standing_additional_degrees");
  }
  if (new RegExp(`^(?:have you (?:ever )?(?:obtained|received|completed)|do you have) (?:a |an )?(?:formal |official )?(?:u\\.?s\\.? |us )?(?:degree |education |credential )?equivalen(?:cy|ce)(?: (?:evaluation|assessment|certificate))?(?: (?:for|of) (?:your )?(?:foreign|international|medical${ownBachelor})(?: degree| education| credentials)?)?$`).test(both)) {
    return yesNo(f, A.education?.formal_equivalency_obtained, "standing_formal_equivalency");
  }

  // Contracting debarment and health-program exclusion are separate facts.
  const federalDebarment = /\b(?:federal(?: government)? (?:contracting|contracts?|debarment)|(?:contracting|contracts?) with (?:the )?federal government)\b/.test(both) &&
    /\b(?:debar\w*|suspen\w*)\b/.test(both);
  const healthExclusion = /\b(?:medicare|medicaid|health[- ]?care programs?|oig|office of inspector general)\b/.test(both) &&
    /\b(?:exclu\w+|debar\w*|suspen\w*)\b/.test(both);
  if (federalDebarment) {
    if (!yesNoChoice || /\b(criminal|convict\w*|felon\w*|background|drug|credit|licen[cs]\w*|eligible|eligibility)\b/.test(both) ||
        (healthExclusion && (A.excluded_from_federal_health_programs !== false || A.federal_contracting_debarment_or_suspension !== false))) {
      throw new NeedsHuman(question(f), "unconfirmed combined federal contracting disclosure");
    }
    return yesNo(f, A.federal_contracting_debarment_or_suspension, healthExclusion ? "standing_no_oig_or_federal_debarment" : "standing_federal_contracting_debarment");
  }

  // Role-scoped Python/R usage must never overwrite general historical Python years.
  if (/^(?:have you|did you) (?:used|worked with) (?:python or r|r or python) in your (?:current|most recent|current or most recent) (?:role|job|position)$/.test(both) ||
      /^do you (?:use|have experience (?:using|with)) (?:python or r|r or python) in your (?:current|most recent|current or most recent) (?:role|job|position)$/.test(both)) {
    if (!yesNoChoice) throw new NeedsHuman(question(f), "role-scoped Python/R needs Yes/No choices");
    return yesNo(f, A.python_or_r_in_current_or_most_recent_role, "standing_role_python_or_r");
  }
  const libraryQuestion = both.match(/^(?:have you (?:ever )?used|do you (?:use|have (?:any |prior |hands-on )?experience (?:with|using|in))) (.+)$/);
  if (libraryQuestion) {
    const skill = deniedSkill(libraryQuestion[1], A);
    if (skill) return yesNo(f, false, `standing_no_${skill}`);
    const libraries = libraryQuestion[1].match(/^(?:any of )?(?:the following )?python libraries[: ]+(.+)$/);
    if (libraries) {
      const names = libraries[1].split(/,\s*|\s+(?:and|or)\s+/).filter(Boolean);
      if (names.length && names.every((name) => deniedSkill(name, A))) return yesNo(f, false, "standing_no_listed_python_libraries");
      throw new NeedsHuman(question(f), "unconfirmed Python library usage");
    }
  }
  if (/^(?:do you have experience (?:with|using)|have you (?:ever )?(?:used|worked with)) databricks (?:and|with) advanced sql (?:to (?:analyze|query|process)|(?:on|for|with)) large (?:datasets|data sets)$/.test(both)) {
    if (!yesNoChoice) throw new NeedsHuman(question(f), "combined Databricks/SQL needs Yes/No choices");
    return yesNo(f, A.databricks_advanced_sql_large_datasets, "standing_combined_databricks_advanced_sql");
  }
  if (/^(?:have you|did you) (?:ever )?(?:built|build|owned|own|built or owned|built and owned|build or own) (?:business |business-critical )?(?:kpis|key performance indicators)$/.test(both)) {
    if (!yesNoChoice) throw new NeedsHuman(question(f), "KPI ownership needs Yes/No choices");
    return yesNo(f, A.built_or_owned_business_kpis, "standing_business_kpis");
  }
  if (/^(?:have|did) your (?:analyses|analysis|analytical (?:work|insights)) (?:driven|drive|influenced|influence) (?:business outcomes|business decisions|measurable business outcomes)$/.test(both)) {
    if (!yesNoChoice) throw new NeedsHuman(question(f), "analysis impact needs Yes/No choices");
    return yesNo(f, A.analyses_driven_business_outcomes, "standing_analysis_business_outcomes");
  }
  if (/^(?:(?:please )?(?:provide|give|share|describe) (?:an? |one )?example (?:of (?:how |when )?|where )|(?:can|could) you (?:provide|give|share|describe) (?:an? |one )?example (?:of (?:how |when )?|where ))(?:your |an? )?(?:analyses|analysis|analytical (?:work|insights)) (?:drove|driven|influenced|impacted|led to) (?:a |measurable )?business (?:outcomes?|decisions?|results?)$/.test(both)) {
    if (A.analyses_driven_business_outcomes !== true) throw new NeedsHuman(question(f), "analysis outcome is unconfirmed");
    const example = need(A.business_outcome_example, f, "answers.business_outcome_example");
    return textOrChoose(f, example, eqTest(example), "reviewed business outcome example", "standing_business_outcome_example");
  }
  if (/^(?:have you|did you) (?:ever )?(?:presented|present|presented (?:findings|analyses|analysis|insights|results|recommendations)) to senior leadership$/.test(both)) {
    if (!yesNoChoice) throw new NeedsHuman(question(f), "senior leadership presentations need Yes/No choices");
    return yesNo(f, A.presented_to_senior_leadership, "standing_senior_leadership_presentations");
  }
  if (/^(?:have you|did you) (?:ever )?(?:built|build|maintained|maintain|built or maintained|built and maintained|build or maintain) automated data integrations(?: \((?:apis|database connections|scheduled file imports)(?:(?:, |,? or |,? and )(?:apis|database connections|scheduled file imports))*\))?$/.test(both)) {
    if (!yesNoChoice) throw new NeedsHuman(question(f), "automated integrations need Yes/No choices");
    return yesNo(f, A.built_or_maintained_automated_data_integrations, "standing_automated_data_integrations");
  }
  if (/^(?:have you|did you) (?:ever )?independently (?:managed|manage|delivered|deliver) (?:remote deliverables|deliverables (?:while working remotely|remotely|in a remote (?:role|environment)))$/.test(both)) {
    if (!yesNoChoice) throw new NeedsHuman(question(f), "independent remote delivery needs Yes/No choices");
    return yesNo(f, A.independently_managed_remote_deliverables, "standing_independent_remote_deliverables");
  }

  // Nobody referred the candidate. Discovery source (LinkedIn/Indeed) is a separate fact.
  if (/^(?:did anyone refer you|has anyone referred you|were you referred by (?:anyone|someone|an? (?:employee|person|individual))|did (?:an? )?employee refer you)(?: (?:for|to) (?:this|the) (?:job|role|position|company))?$/.test(both)) {
    if (!yesNoChoice) throw new NeedsHuman(question(f), "referral status needs Yes/No choices");
    return yesNo(f, A.referred_by_anyone, "standing_personal_referral");
  }
  if (A.referred_by_anyone === false && ["text", "textarea"].includes(f.kind) &&
      /^(?:employee referral name|referral name|name of (?:the |your )?(?:employee|person) who referred you|if (?:yes|referred),? (?:please )?(?:provide|enter) (?:the )?(?:referrer's name|name of (?:the )?employee who referred you))$/.test(both)) {
    return { op: "text", value: /^if /.test(both) ? "N/A" : "No referral", rule: "standing_no_personal_referral_name" };
  }

  // Personal relationship (family / close friend) with an existing employee, officer or director.
  if (yesNoChoice && typeof A.relationship_with_employees === "boolean" &&
      /\b(personal|family|familial|close|friendship|friends?|relationships?|related|relatives?)\b.{0,120}\b(employees?|team members?|officers?|directors?|staff|associates?)\b|\b(employees?|team members?|officers?|directors?|staff)\b.{0,120}\b(related to you|your (family|relatives?|friends?)|personal relationships?|close friend)|\b(family( members?)?|relatives?|friends?)\b.{0,60}\b(work|works|working|employed)\b.{0,20}\b(at|for|with|by|here|in)\b|\bdo you know (anyone|someone|any(one)? (person|individual)s?)\b.{0,40}\b(work|works|working|employed)\b.{0,20}\b(at|for|by|here)\b/.test(both) &&
      !/\b(experience|building|build|managing|manage|developing|develop|fostering|stakeholders?|clients?|clinical|cross[- ]functional|collaborat\w+|skills?)\b/.test(both) &&
      !/\b(former|previously|ever (been )?employed|worked (here|for|at)|applied|referr\w*|referred by)\b/.test(both)) {
    return yesNo(f, A.relationship_with_employees, "standing_no_relationship_with_employees");
  }
  // Intent to hold secondary / outside employment while employed here.
  if (yesNoChoice && typeof A.secondary_employment_intended === "boolean" &&
      /\b(secondary|second|outside|additional|other|concurrent|dual|moonlight\w*)\b.{0,40}\b(employment|job|work|jobs|income)\b.{0,80}\b(while|during|whilst|if hired|upon hire|as an employee)\b|\b(intend|plan|expect|wish|hope)\b.{0,60}\b(secondary|second|outside|additional|other|concurrent|dual)\b.{0,30}\b(employment|job|work)\b|\bmoonlight\w*\b/.test(both)) {
    return yesNo(f, A.secondary_employment_intended, "standing_no_secondary_employment");
  }
  // Follow-up free-text box to the relationship question ("If yes, please provide the name(s)"): "N/A", but ONLY while the standing
  // answer is No (so the text is true), and ONLY when the field itself is that conditional follow-up, never a reference/emergency-contact box.
  if (A.relationship_with_employees === false && (f.kind === "text" || f.kind === "textarea") &&
      /\bif (yes|so|applicable)\b.{0,60}\b(names?|identify|list|specify|explain|describe|provide|details?)\b|\b(names?|identify|list|specify|provide)\b.{0,40}\b(the )?(employees?|individuals?|persons?|people|names?)\b.{0,60}\b(if (yes|so|applicable))\b/.test(both) &&
      /\b(relationships?|related|relatives?|family|familial|friend\w*)\b.{0,160}\b(employees?|team members?|officers?|directors?|staff)\b|\b(employees?|team members?|officers?|directors?|staff)\b.{0,160}\b(relationships?|related|relatives?|family|familial|friend\w*)\b/.test(both) &&
      !/\b(reference|emergency|supervisor|former|previous)\b/.test(both)) {
    return { op: "text", value: "N/A", rule: "standing_no_relationship_followup_na" };
  }

  // "Have you (ever) worked for / been employed by / been an employee of THIS company (or its affiliates)?" -> No (user-confirmed 2026-11-12,
  // all companies). Safe only because the full employer history is in A.past_employers: if the question names ANY of them it is NOT answered here.
  const companyKey = norm(ctx.job?.company || "");
  const employerObject = /\b(?:for|by|at|with|of)\s+(?:this|our|the)\s+(?:company|organization|organisation|employer|firm|corporation|business)\b|\b(?:for|by|at|with)\s+(?:us|here)\b|\b(?:affiliates?|subsidiar\w+)\b/.test(both) ||
    (companyKey.length > 2 && both.includes(companyKey));
  if (yesNoChoice && A.previously_worked_at_prospective_employer === false && employerObject &&
      /\b(have you|did you|were you|are you a)\b.{0,80}\b(ever |previously |in any capacity )?(worked|work|been employed|been an? (employee|contractor|intern)|employed|served)\b.{0,80}\b(for|at|by|with|as)\b|\b(former|previous|prior|past) (employee|employer|associate|contractor|worker)\b.{0,40}\b(of|at|with)\b|\bever (worked|been employed)\b/.test(both) &&
      !/\b(referr\w*|apply|applied|application|interview\w*|relatives?|family|friends?|personal\w*|know|relationships?|currently|veteran|military|government|federal|agency|visa|sponsor\w*)\b/.test(both) &&
      !(A.past_employers || []).some((e) => both.includes(norm(e)) || norm(ctx.job?.company) === norm(e))) {
    return yesNo(f, false, "standing_no_prior_employment_at_company");
  }

  // User-confirmed standing answers (2026-11-13), valid across ALL companies. Yes/No choice fields ONLY, one question each. They run
  // BEFORE the "unconfirmed sensitive/history detail" guard below because that guard deliberately blocks professional-license wording.
  // Excluded / debarred / suspended from federal or state health-care programs (OIG LEIE, SAM, Medicare, Medicaid). A broader
  // "criminal / convicted / background check" question is NOT matched and stays with a human.
  if (yesNoChoice && typeof A.excluded_from_federal_health_programs === "boolean" &&
      /\b(exclu\w+|debar\w*|suspen\w+|sanction\w*|ineligible)\b.{0,160}\b(medicare|medicaid|federal health[- ]?care|state health[- ]?care|health[- ]?care program|government health|oig|office of inspector general)\b|\b(medicare|medicaid|federal health[- ]?care|state health[- ]?care|health[- ]?care program|oig|office of inspector general)\b.{0,160}\b(exclu\w+|debar\w*|suspen\w+|sanction\w*|ineligible)\b/.test(both) &&
      /\b(are you|have you|were you|has any|is any|is your name|listed on|currently|your name)\b/.test(both) &&
      !/\b(experience|knowledge|familiar\w*|measures?|populations?|criteria|analy\w+|reporting|data|methodolog\w+)\b/.test(both) &&
      !/\b(criminal|convict\w*|felon\w*|background|drug|credit|contracting|contracts?|federal government debarment)\b/.test(both)) {
    return yesNo(f, A.excluded_from_federal_health_programs, "standing_no_health_program_exclusion");
  }
  // Spouse / partner of a member of the U.S. military. Distinct from the candidate's OWN veteran status, which is an EEO question.
  if (yesNoChoice && typeof A.spouse_or_partner_of_us_military === "boolean" &&
      /\b(spouse|partner|husband|wife|domestic partner|significant other)\b.{0,100}\b(military|armed forces|service ?member|active duty|u\.?s\.? (army|navy|air force|marine|coast guard)|national guard)\b|\b(military|armed forces|active duty|service ?member)\b.{0,100}\b(spouse|partner|husband|wife|domestic partner)\b/.test(both) &&
      !/\b(you are a (protected )?veteran|your (own )?(military|veteran)|protected veteran|disabled veteran)\b/.test(both)) {
    return yesNo(f, A.spouse_or_partner_of_us_military, "standing_no_military_spouse");
  }
  // Prior federal / state / local GOVERNMENT employment. This is about government service, not the prospective employer, and is NOT
  // covered by previously_worked_at_prospective_employer (which deliberately excludes "government" wording).
  if (yesNoChoice && typeof A.prior_government_employment === "boolean" &&
      new RegExp(`\\b(have you|were you|are you|did you)\\b.{0,80}\\b(employed|worked|served|employee|held a position)\\b.{0,100}${GOVERNMENT}|${GOVERNMENT}.{0,40}\\b(employ\\w+|employee|official)\\b.{0,60}\\b(ever|previously|prior|past|currently)\\b`).test(both) &&
      !/\b(willing|able|comfortable|open to|prepared|military|armed forces|veteran|contractors?|contracting|clearance|public trust|security clearance)\b/.test(both)) {
    return yesNo(f, A.prior_government_employment, "standing_no_prior_government_employment");
  }
  // Disciplinary action against a PROFESSIONAL LICENSE. The profile records whether any license was disciplined; this does NOT answer whether the candidate
  // HOLDS a license or certification (that stays a blocker), only whether any discipline exists.
  if (yesNoChoice && typeof A.professional_license_disciplinary_action === "boolean" &&
      /\b(disciplin\w+|revok\w+|suspen\w+|sanction\w*|censur\w+|reprimand\w*|probation|restrict\w+|surrender\w*|limit\w+)\b.{0,100}\b(professional |medical |nursing |state )?(licen[cs]\w+|certification|credential\w*|board)\b|\b(licen[cs]\w+|certification|credential\w*)\b.{0,100}\b(disciplin\w+|revok\w+|suspen\w+|sanction\w*|censur\w+|reprimand\w*|probation|surrender\w*)\b/.test(both) &&
      !/\b(do you (currently )?(hold|have|possess)|are you (currently )?licensed|active licen[cs]e|valid licen[cs]e|what (is|are) your licen[cs]e)\b/.test(both)) {
    return yesNo(f, A.professional_license_disciplinary_action, "standing_no_license_discipline");
  }


  // EAD details, applicant history, background checks and references are never inferred.
  if (/\b(ead|employment authorization|work authorization|visa)\b.{0,50}\b(expir\w*|categor\w*|type|class|number)|\b(expir\w*|categor\w*)\b.{0,50}\b(ead|authorization|visa)\b|background (check|screen)|criminal|convict|felon|references?|professional licen[cs]/.test(both)) {
    throw new NeedsHuman(question(f), "unconfirmed sensitive/history detail");
  }

  // Citizen / permanent-resident status is distinct from permission to work.
  if (/\b(citizen(ship)?|green card|permanent resident)\b/.test(both)) {
    if (/\bnot\b|non[- ]citizen|which|country of|proof|document|number|date/.test(both)) throw new NeedsHuman(question(f), "ambiguous citizenship detail");
    const citizen = /\bcitizen(ship)?\b/.test(both);
    const resident = /green card|permanent resident/.test(both);
    if (citizen && !/\b(u\.?s\.?|united states|american)\b/.test(both)) throw new NeedsHuman(question(f), "citizenship country is not explicit");
    if ((citizen && typeof C.us_citizen !== "boolean") || (resident && typeof C.green_card_holder !== "boolean")) throw new NeedsHuman(question(f), "citizenship/residency answer is unconfirmed");
    if (citizen && resident && !/\bor\b|and\/or/.test(both)) throw new NeedsHuman(question(f), "combined citizenship question is ambiguous");
    return yesNo(f, (citizen && C.us_citizen) || (resident && C.green_card_holder), "citizen_or_resident");
  }
  if (/^(do you (currently )?(have|hold|possess) (an? )?(valid )?(ead|employment authorization document)|do you have a valid work permit)$/.test(label)) {
    return yesNo(f, need(C.has_ead, f, "candidate.has_ead"), "ead_holder");
  }

  // ---- work authorization / sponsorship (only the two policy answers)
  if (/sponsor/.test(both)) {
    if (/\bnot\b|\bwithout\b/.test(both)) throw new NeedsHuman(question(f), "negated sponsorship phrasing");
    if (!/\b(require|need)\b.{0,100}\bsponsor|\bsponsor\w*\b.{0,60}\b(required|needed)\b/.test(both)) throw new NeedsHuman(question(f), "ambiguous sponsorship question");
    if (P.needsSponsorship !== false) throw new NeedsHuman(question(f), "profile.requires_sponsorship is not false");
    return yesNo(f, false, "sponsorship_no");
  }
  if ((/(authori[sz]ed|eligible|legal(ly)?|right|permitted|permission|authori[sz]ation).{0,40}\bwork\b|work (authori[sz]ation|eligibility)/.test(both) || /^are you able to work in the united states$/.test(label)) && !/citizen|visa (type|status)|which visa|green card|permanent resident|\bh-?1b\b|\bopt\b|stem/.test(both)) {
    if (/\bnot\b|unauthori/.test(both)) throw new NeedsHuman(question(f), "negated work-authorization phrasing");
    // The profile records authorization for the United States only. Any other country is a human decision.
    const place = both.match(/\bwork(?:ing)?\s+(?:in|within)\s+(?:the\s+)?(.+)$/);
    const US_TOKEN = String.raw`(?:united states(?: of america)?|u\.?\s?s\.?(?:\s?a\.?)?|us|usa|america)(?=[\s.,;]|$)`;
    const usOnly = place && new RegExp("^" + US_TOKEN).test(place[1]) && !new RegExp("^" + US_TOKEN + "[\\s.,;]*(?:or|and)\\b").test(place[1]);
    if ((place && !usOnly) || NON_US_PLACE.test(both)) throw new NeedsHuman(question(f), "work authorization outside the United States is not in the profile");
    if (P.authorized !== true) throw new NeedsHuman(question(f), "profile.work_authorization is not authorized_no_sponsor");
    return yesNo(f, true, "work_authorized_yes");
  }

  // Confirmed standing answers. Match the question, never a prefilled value.
  if (/^(are you (currently )?employed|currently employed|current employment status|employment status)$/.test(label)) {
    const employed = need(A.currently_employed, f, "currently_employed");
    if (typeof employed !== "boolean") throw new NeedsHuman(question(f), "employment status must be boolean");
    if (/status$/.test(label)) return textOrChoose(f, employed ? "Employed" : "Not currently employed", (o) => employed ? /^employed$/i.test(norm(o)) : /^(not (currently )?employed|unemployed)$/i.test(norm(o)), employed ? "Employed" : "Not currently employed", "employment_status");
    return yesNo(f, employed, "currently_employed");
  }
  if (/^(current employer|current company|name of (your )?current employer)$/.test(label)) {
    if (A.currently_employed !== false) throw new NeedsHuman(question(f), "current employer is unconfirmed");
    return textOrChoose(f, "Not currently employed", eqTest("Not currently employed"), "Not currently employed", "current_employer_none");
  }
  if (/^(most recent (employer|company)|last employer|name of (your )?(last|most recent) employer|current (or|\/) most recent (employer|company))$/.test(label)) {
    const employer = need(A.last_employer, f, "last_employer");
    return textOrChoose(f, employer, eqTest(employer), employer, "last_employer");
  }
  if (/^(employment gap explanation|reason for (your )?(employment |career )?gap|please explain (any |your )?(employment |career )?gap(s)?|explain (your )?(employment |career )gap)$/.test(label)) {
    const value = need(A.employment_gap_explanation, f, "employment_gap_explanation");
    return textOrChoose(f, value, eqTest(value), value, "employment_gap");
  }
  if (/^(availability|when (can|could) you start|when (are|would) you (be )?available (to start|for work)|how soon (can|could) you start|notice period|what is your notice period)$/.test(label)) {
    const value = need(A.availability, f, "availability");
    if (!/^immediate(ly)?$/i.test(value)) throw new NeedsHuman(question(f), "availability is not immediate");
    return textOrChoose(f, /notice period/.test(label) ? "0" : "Immediately", (o) => /^(immediate(ly)?|available immediately|no notice( period)?|0( days?)?)$/i.test(norm(o)), "Immediately", "availability");
  }
  if (/^(what (is|are) your )?((desired|expected|target|anticipated)( annual| base| annual base| total)? (salary|compensation|pay)|(annual |base |annual base |total )?(salary|compensation|pay) (expectation(s)?|requirement(s)?|desired|expected))( in (usd|u\.s\. dollars))?( for (this|the) (position|role|job))?( \(?(usd|\$)\)?)?$/.test(label)) {
    const value = need(A.salary_expectation_usd ?? prefs.salary_expectation_usd, f, "salary_expectation_usd");
    return textOrChoose(f, String(value), (o) => norm(o).replace(/[$,\s]/g, "") === String(value), String(value), "expected_salary");
  }
  if (/^(have you completed|have you achieved|do you have) (a |an )?bachelor'?s?( degree)?( or (higher|equivalent))?$/.test(label) || /^have you completed (the following )?level of education[: ]+bachelor'?s?( degree)?$/.test(label)) {
    const degree = need(A.education && A.education.bachelors_degree, f, "answers.education.bachelors_degree");
    if (typeof degree !== "boolean" && !/^(yes|no)$/i.test(degree)) throw new NeedsHuman(question(f), "degree answer is not yes/no");
    return yesNo(f, degree === true || /^yes$/i.test(degree), "bachelors_degree");
  }
  const years = label.match(/^how many years (of )?(work )?experience (do you have )?(with|in|using) (.+)$/) || label.match(/^years of experience (with|in|using) (.+)$/);
  if (years) {
    const skill = norm(years[years.length - 1]).replace(/[?.]+$/, "").trim();
    if (/\b(?:current|most recent) (?:role|job|position)\b/.test(skill)) throw new NeedsHuman(question(f), "role-specific years are not general skill history");
    const denied = deniedSkill(skill, A);
    if (denied) return textOrChoose(f, "0", eqTest("0"), "0", `years_denied_${denied}`);
    let value = A.years_experience && A.years_experience[skill];
    if (!Number.isFinite(value) && Number.isFinite(A.default_skill_years) && !/licen[sc]e|certif|credential|clearance|degree|board|nursing|\brn\b|\bmd\b|cpa|pmp|citizen|visa|security|\bdatabricks\b|\badvanced sql\b/.test(skill)) value = A.default_skill_years;
    if (!Number.isFinite(value)) throw new NeedsHuman(question(f), `no confirmed years for ${skill}`);
    return textOrChoose(f, String(value), eqTest(String(value)), String(value), `years_${skill}`);
  }
  if (/^have you partaken in population health initiatives previously$/.test(label) || /^(have you|do you have) (any |prior )?(experience (with|in|supporting|working (with|on)) |worked (with|on) )?(healthcare population (health )?initiatives|population health initiatives)( experience| in (a )?healthcare (setting|organization))?$/.test(label)) {
    const value = need(A.population_health_initiatives, f, "population_health_initiatives");
    if (typeof value !== "boolean") throw new NeedsHuman(question(f), "healthcare initiatives answer must be boolean");
    return yesNo(f, value, "healthcare_population_initiatives");
  }
  if (/^have you supported it operations previously$/.test(label) || /^(have you|do you have) (any |prior )?(experience (with|in|supporting|working (with|on|in)) |worked (with|in) )?(it|information technology) operations( experience)?$/.test(label)) {
    const value = need(A.it_operations, f, "it_operations");
    if (typeof value !== "boolean") throw new NeedsHuman(question(f), "IT operations answer must be boolean");
    return yesNo(f, value, "it_operations");
  }
  if (/^(are you (willing|able) to relocate|willing to relocate)$/.test(label)) {
    const value = need(A.willing_to_relocate, f, "willing_to_relocate");
    if (typeof value !== "boolean") throw new NeedsHuman(question(f), "relocation answer must be boolean");
    return yesNo(f, value, "willing_to_relocate");
  }
  if (/^(maximum travel percentage|what percentage (of (the )?time )?(are you (willing|able) to|can you) travel|how much (are you (willing|able) to|can you) travel)$/.test(label)) {
    const value = need(A.max_travel_percent, f, "max_travel_percent");
    return textOrChoose(f, String(value), (o) => new RegExp(`^(up to )?${value}\\s*%$`, "i").test(norm(o)), `${value}%`, "max_travel");
  }
  const skillQuestion = label.match(/^do you have (experience (with|in|using)|knowledge of|skills (with|in)) (.+)$/);
  if (skillQuestion) {
    const skill = skillQuestion[skillQuestion.length - 1];
    if ((A.additional_skills || []).some(s => norm(s) === skill)) return yesNo(f, true, "confirmed_additional_skill");
  }

  // ---- identity / contact. Guard words keep "Emergency contact name" etc. out.
  const idish = /\b(name|e-?mail|phone|mobile|cell|telephone|address|zip|postal|city|state|province|country)\b/.test(label);
  if (idish && IDENTITY_GUARD.test(both)) throw new NeedsHuman(question(f), "identity-like field for another person/employer");

  if (/^(legal |given |preferred )?first( name)?$|^given name$|^legal first name$/.test(label)) {
    if (/preferred/.test(label)) throw new NeedsHuman(question(f), "preferred name is not in profile");
    return textOrChoose(f, need(P.first, f, "first_name"), eqTest(P.first), P.first, "first_name");
  }
  if (/^(legal |family )?(last|family)( name)?$|^surname$|^legal last name$/.test(label)) {
    return textOrChoose(f, need(P.last, f, "last_name"), eqTest(P.last), P.last, "last_name");
  }
  if (/^(full |legal full |your |candidate )?(legal )?name$|^full legal name$/.test(label)) {
    return textOrChoose(f, need(P.full, f, "name"), eqTest(P.full), P.full, "full_name");
  }
  if (/(confirm|verify|re-?enter|retype|repeat).{0,12}e-?mail|e-?mail.{0,12}(confirm|again|verification)/.test(label) || /^e-?mail( address)?$|^(your |work |personal |primary |contact )e-?mail( address)?$/.test(label)) {
    return textOrChoose(f, need(P.email, f, "email"), eqTest(P.email), P.email, "email");
  }
  if (/country (calling |dialing |phone )?code|phone country|dial(ing)? code|calling code/.test(label)) {
    need(P.country, f, "country");
    return { op: "choose", test: (o) => /united states/i.test(o), describe: "United States (+1)", rule: "phone_country_code" };
  }
  if (/\b(ext|extension)\b|fax|\bwork phone\b|\bhome phone\b/.test(label)) throw new NeedsHuman(question(f), "phone variant not in profile");
  if (/^(mobile |cell |contact |primary |telephone |phone )*(phone |number |no\.? )*(number|phone|mobile|cell)?$/.test(label) && /phone|mobile|cell|telephone/.test(label)) {
    return textOrChoose(f, need(P.phone, f, "phone"), (o) => false, P.phone, "phone");
  }
  if (/^(current |your )?location( \(city\))?$|^location \(city.{0,10}\)$|^current location$|^where are you (currently )?(located|based)$|^city, state$/.test(label)) {
    return textOrChoose(f, need(P.location, f, "location"), eqTest(P.location), P.location, "location");
  }
  if (/^city( \/ town| or town)?$/.test(label)) {
    return textOrChoose(f, need(P.city, f, "candidate.city"), eqTest(P.city), P.city, "city");
  }
  if (/^state( \/ province| or province|\/province)?$|^province$/.test(label)) {
    const st = need(P.state, f, "state (derivable from location)");
    const name = STATE_NAMES[st];
    if (f.kind === "text" || f.kind === "textarea") return { op: "text", value: st, rule: "state" };
    return { op: "choose", test: (o) => norm(o) === st.toLowerCase() || (name && norm(o) === name.toLowerCase()) || norm(o).startsWith(`${st.toLowerCase()} -`), describe: st, rule: "state" };
  }
  if (/^country( of residence| \/ region|\/region)?$|^country or region$/.test(label)) {
    const cn = need(P.country, f, "country (derivable from state)");
    return textOrChoose(f, cn, (o) => /^(united states( of america)?|usa|us|u\.s\.a?\.?)$/i.test(norm(o).replace(/\s*\(.*\)$/, "")), cn, "country");
  }
  if (/^(zip|postal)( code)?$|^zip \/ postal code$/.test(label)) return textOrChoose(f, need(P.zip, f, "candidate.zip"), eqTest(P.zip), P.zip, "zip");
  if (/^(street )?address( line 1)?$|^street$/.test(label)) return textOrChoose(f, need(P.street, f, "candidate.street"), eqTest(P.street), P.street, "street");
  if (/linkedin/.test(label)) return textOrChoose(f, need(P.linkedin, f, "candidate.linkedin_url"), (o) => false, P.linkedin, "linkedin_url");
  if (/^(personal |portfolio )?(website|web site|url|portfolio)( url)?$/.test(label)) return textOrChoose(f, need(P.website, f, "candidate.website"), (o) => false, P.website, "website");

  // ---- "how did you hear about us": only from the job's recorded discovery source
  if (/how did you (hear|find|learn)|where did you (hear|find|learn)|referral source|source of (application|referral)/.test(both)) {
    const src = ctx.job && ctx.job.source;
    const map = { linkedin: /linkedin/i, indeed: /indeed/i };
    if (!map[src]) throw new NeedsHuman(question(f), "job source not a listed job board");
    const nm = src === "linkedin" ? "LinkedIn" : "Indeed";
    return textOrChoose(f, nm, (o) => map[src].test(o), nm, "heard_about_us");
  }

  throw new NeedsHuman(question(f), f.required ? "required, not in allowlist" : "not in allowlist");
}

module.exports = { NeedsHuman, resolveAnswer, classifyConsent, deriveProfile, DECLINE, norm, STATE_NAMES };
