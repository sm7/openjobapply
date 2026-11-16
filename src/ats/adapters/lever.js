// Lever (jobs.lever.co). Posting page -> "Apply for this job" -> /apply single-page form
// (resume, Full name, Email, Phone, Current location, links, custom cards, EEO survey).
// Lever commonly shows hCaptcha on submit: we never solve it; the adapter returns 'manual'/captcha_required.
"use strict";
const { runSinglePage } = require("../singlepage");

const HOST = /(^|\.)lever\.co$/i;

module.exports = {
  name: "lever",
  matches(url) { try { return HOST.test(new URL(url).hostname); } catch { return false; } },
  apply(ctx) {
    return runSinglePage(ctx, {
      name: "lever",
      formReady: (fields) => fields.some((f) => /^e-?mail/i.test(f.label)) && fields.some((f) => /^full name$/i.test(f.label)),
      applyBtn: /^apply( now| for this job)?$/i,
    });
  },
};
