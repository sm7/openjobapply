// SmartRecruiters (jobs.smartrecruiters.com, careers.smartrecruiters.com, smartr.me).
// Posting page -> "I'm interested" -> oneclick-ui application: a profile page (name, email + email
// confirmation, city, phone, resume) with "Next", then a questions page (employer questions, EEO,
// consents) ending in "Submit". Pages are driven one at a time through form.js/answers.js: every
// intermediate page must be fully answered before "Next"; the last page is handed to runSinglePage,
// which stops BEFORE Submit in dry-run and (live only) submits and waits for the confirmation.
//
// Bot checks are only detected (captcha_required). A password field or a sign-in/create-account wall is
// an account stop: by default (dry run, or live without opts.allowAccountCreation) it is reported as
// manual password_required / account_required with wouldCreateAccount:true. Only a live run with
// opts.allowAccountCreation === true hands it to account.js ensureAccount (the authorized create /
// password-reset path, via singlepage.withAccountGate) and then resumes the form on the same tab.
// Limitation: SmartRecruiters' newer oneclick-ui renders its fields inside shadow DOM (spl-* components).
// form.js scans the light DOM only, so such a page is reported as manual/form_not_found with
// shadowDom:true instead of being filled.
"use strict";
const { observe, matchEl, clickEl, visibleText, evalIn, waitUntil, result, snippet, Blocked } = require("../lib");
const { deriveProfile } = require("../answers");
const { scanForm, fillForm } = require("../form");
const { runSinglePage, withAccountGate } = require("../singlepage");
const F = require("../flow");

const HOST = /(^|\.)(smartrecruiters\.com|smartr\.me)$/i;
const INTERESTED = /^(i['\u2019]?m interested|apply( now| for this job| for this position)?)$/i;
const CONFIRMED = /thank(s| you) for (applying|your application)|your application (was|has been) (submitted|sent|received)|you('ve| have) (successfully )?applied/i;
const ACCOUNT_WALL = /(sign in|log ?in|create (an )?account|register) (to|before|in order to) (apply|continue|submit)|you (must|need to) (sign in|log ?in|create an account)/i;
const MAX_PAGES = 5;

// A hidden resume input must not make a posting page look like the form, so file fields alone never count.
// Thrown (not returned) so the password wall surfaces from inside runSinglePage's scans too.
const isForm = (fields) => {
  if (fields.some((f) => f.kind === "password")) throw new Blocked("password_required", "page asks for a password (account sign-in/creation)");
  return fields.some((f) => /e-?mail|first name|^name$/i.test(f.label) || ["radio", "checkbox", "select"].includes(f.kind));
};

/** In-page: does the page hold SmartRecruiters' shadow-DOM components that the light-DOM scanner cannot see? */
const hasShadowForm = (tab) => evalIn(tab, () => [...document.querySelectorAll("*")].some((e) => e.shadowRoot && /^(spl|oc)-/i.test(e.tagName)));

async function oneclickHref(tab) {
  return evalIn(tab, () => { const a = document.querySelector('a[href*="/oneclick-ui/"]'); return a ? a.href : null; });
}

/** Posting page -> application form. Click the page's own link; if the site opened it elsewhere, follow its href. */
async function enterForm(ctx) {
  const tab = ctx.tab;
  const apply = matchEl(await observe(tab), { roles: ["button", "link"], name: INTERESTED });
  if (apply) {
    const before = await F.fingerprint(tab);
    await clickEl(tab, apply);
    await F.waitChange(tab, before, 15000);
  }
  const ready = () => waitUntil(async () => (isForm(await scanForm(tab)) ? true : null), { timeout: 12000, every: 700 });
  if (await ready()) return true;
  const href = await oneclickHref(tab);
  if (href && HOST.test(new URL(href).hostname)) {
    await tab.goto(href);
    return !!(await ready());
  }
  return false;
}

function apply(ctx) { return withAccountGate(ctx, "smartrecruiters", () => applyOnce(ctx)); }

async function applyOnce(ctx) {
  ctx.P = ctx.P || deriveProfile(ctx.profile);
  const tab = ctx.tab;
  let entered = false;

  for (let page = 0; page < MAX_PAGES; page++) {
    const fields = await scanForm(tab);
    if (!isForm(fields)) {
      if (entered || !(await enterForm(ctx))) break; // runSinglePage reports closed/form_not_found
      entered = true;
      continue;
    }
    const nav = await F.findNav(tab);
    if (nav.final || !nav.next) break; // last page: runSinglePage fills it and stops/submits

    const rep = await fillForm(ctx);
    F.merge(ctx, rep);
    const blocked = F.submittable(rep);
    if (blocked) return F.manual(ctx, "unanswered_questions", { unanswered: rep.unanswered.map((u) => u.question), evidence: blocked });
    const before = await F.fingerprint(tab);
    await clickEl(tab, nav.next);
    if (!(await F.waitChange(tab, before, 15000))) {
      const errs = await F.validationErrors(tab);
      return F.manual(ctx, "validation_error", { evidence: errs.join(" | ") || `"${nav.next.name}" did not advance` });
    }
  }

  let out = await runSinglePage(ctx, {
    name: "smartrecruiters",
    formReady: isForm,
    applyBtn: INTERESTED,
  });

  if (out.reason === "form_not_found") {
    const text = await visibleText(tab);
    if (ACCOUNT_WALL.test(text)) throw new Blocked("account_required", (text.match(ACCOUNT_WALL) || [""])[0]);
    if (await hasShadowForm(tab)) return F.manual(ctx, "form_not_found", { shadowDom: true, evidence: "SmartRecruiters oneclick-ui renders its fields in shadow DOM (spl-*), which the form engine cannot scan; apply manually" });
  }
  if (out.status === "failed" && out.reason === "no_confirmation" && CONFIRMED.test(await visibleText(tab))) {
    out = result("applied", "confirmation_page", { evidence: (await snippet(tab, 300)), consents: ctx.consents.slice(), tabName: ctx.tabName });
  }
  return out;
}

module.exports = {
  name: "smartrecruiters",
  matches(url) { try { return HOST.test(new URL(url).hostname); } catch { return false; } },
  apply,
};
