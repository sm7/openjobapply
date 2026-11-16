// Ashby hosted job boards (jobs.ashbyhq.com/<company>/<job id>[/application]).
// The posting has an "Overview" and an "Application" tab; the Application tab is one page: resume,
// Name, Email, optional contact/location, custom questions (Yes/No radios, text, selects), EEO survey
// and "Submit Application". The form loads asynchronously ("Fetching application form").
// Driven by singlepage.js: stop BEFORE Submit in dry-run; live: submit + wait for
// "Your application was successfully submitted".
//
// Bot checks are only detected (captcha_required). Ashby needs no account; if a password field or a
// sign-in wall shows up anyway it is an account stop: by default (dry run, or live without
// opts.allowAccountCreation) manual password_required / account_required with wouldCreateAccount:true.
// Only a live run with opts.allowAccountCreation === true hands it to account.js ensureAccount (the
// authorized create / password-reset path, via singlepage.withAccountGate), then resumes on the same tab.
// Note: the page's "Autofill from resume" dropzone is a resume file field too, so the profile resume is
// attached to it as well as to the real Resume field (same file). Ashby's resume parser is not under
// our control; every field is read back after filling and a mismatch on a required field stops the run.
"use strict";
const { visibleText, snippet, result, Blocked } = require("../lib");
const { runSinglePage, withAccountGate } = require("../singlepage");
const F = require("../flow");

const HOST = /(^|\.)ashbyhq\.com$/i;
const CONFIRMED = /application was (successfully )?submitted|thank you for (applying|your application)|we('ve| have) received your application/i;
const ACCOUNT_WALL = /(sign in|log ?in|create (an )?account|register) (to|before|in order to) (apply|continue|submit)|you (must|need to) (sign in|log ?in|create an account)/i;

const hasLabel = (fields, re) => fields.some((f) => re.test(f.label));
// Thrown (not returned) so it surfaces from inside runSinglePage's scans, whenever the form finishes loading.
const passwordWall = () => new Blocked("password_required", "page asks for a password (account sign-in/creation)");
const formReady = (fields) => {
  if (fields.some((f) => f.kind === "password")) throw passwordWall();
  return hasLabel(fields, /^e-?mail/i) && hasLabel(fields, /^(full )?name$/i);
};
const cfg = { name: "ashby", formReady, applyBtn: /^(apply( now| for this job| for this position)?|application)$/i };

function applicationUrl(url) {
  try {
    const u = new URL(url);
    if (!HOST.test(u.hostname) || /\/application\/?$/.test(u.pathname)) return null;
    u.pathname = u.pathname.replace(/\/+$/, "") + "/application";
    u.search = "";
    u.hash = "";
    return u.href;
  } catch { return null; }
}

function apply(ctx) { return withAccountGate(ctx, "ashby", () => applyOnce(ctx)); }

async function applyOnce(ctx) {
  const tab = ctx.tab;
  let out = await runSinglePage(ctx, cfg);
  if (out.reason === "form_not_found") {
    // the posting's own "Application" tab was not found: open the canonical /application page of the same job
    const target = applicationUrl(await tab.url());
    if (target) {
      await tab.goto(target);
      out = await runSinglePage(ctx, cfg);
    }
  }
  if (out.reason === "form_not_found") {
    const text = await visibleText(tab);
    if (ACCOUNT_WALL.test(text)) throw new Blocked("account_required", (text.match(ACCOUNT_WALL) || [""])[0]);
  }
  if (out.status === "failed" && out.reason === "no_confirmation" && CONFIRMED.test(await visibleText(tab))) {
    out = result("applied", "confirmation_page", { evidence: (await snippet(tab, 300)), consents: ctx.consents.slice(), tabName: ctx.tabName });
  }
  return out;
}

module.exports = {
  name: "ashby",
  matches(url) { try { return HOST.test(new URL(url).hostname); } catch { return false; } },
  apply,
};
