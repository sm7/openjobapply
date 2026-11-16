// Greenhouse hosted boards (boards.greenhouse.io, job-boards.greenhouse.io, boards.eu.greenhouse.io).
// Single-page form; some boards email a security code after Submit (handled via the OtpProvider).
// Employer sites that EMBED Greenhouse (gh_jid / iframe) are reported by the generic adapter as
// embedded_ats:greenhouse with the iframe URL so the caller can re-run on the board URL.
"use strict";
const { runSinglePage } = require("../singlepage");

const HOST = /(^|\.)greenhouse\.io$/i;

module.exports = {
  name: "greenhouse",
  matches(url) { try { return HOST.test(new URL(url).hostname); } catch { return false; } },
  apply(ctx) {
    return runSinglePage(ctx, {
      name: "greenhouse",
      formReady: (fields) => fields.some((f) => /^e-?mail/i.test(f.label)) && fields.some((f) => /first name/i.test(f.label)),
      applyBtn: /^apply( now| for this job)?$/i,
    });
  },
};
