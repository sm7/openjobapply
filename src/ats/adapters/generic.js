// Fallback for unknown / unsupported ATSes. It is READ-ONLY: it never clicks, types or navigates.
// It names the ATS if it can (by hostname, then by embedded iframes already on the page) and
// returns status 'manual' so a human applies.
"use strict";

const KNOWN = [
  ["workday", /(^|\.)(myworkdayjobs|myworkdaysite|workday)\.com$/i],
  ["icims", /(^|\.)icims\.com$/i],
  ["smartrecruiters", /(^|\.)smartrecruiters\.com$/i],
  ["taleo", /(^|\.)taleo\.net$/i],
  ["ashby", /(^|\.)ashbyhq\.com$/i],
  ["bamboohr", /(^|\.)bamboohr\.com$/i],
  ["jobvite", /(^|\.)jobvite\.com$/i],
  ["successfactors", /(^|\.)successfactors\.(com|eu)$/i],
  ["oracle_recruiting", /(^|\.)oraclecloud\.com$/i],
  ["ultipro", /(^|\.)ultipro\.com$/i],
  ["paylocity", /(^|\.)paylocity\.com$/i],
  ["dayforce", /(^|\.)dayforcehcm\.com$/i],
  ["linkedin", /(^|\.)linkedin\.com$/i], // LinkedIn Easy Apply is a different plugin
];
const EMBED = [["greenhouse", /greenhouse\.io/i], ["lever", /lever\.co/i], ["workday", /myworkday/i], ["icims", /icims\.com/i], ["smartrecruiters", /smartrecruiters\.com/i]];

function identify(url) {
  let host = "";
  try { host = new URL(url).hostname; } catch { /* ignore */ }
  const hit = KNOWN.find(([, re]) => re.test(host));
  return hit ? hit[0] : null;
}

module.exports = {
  name: "generic",
  matches() { return true; }, // always last in the registry
  identify,
  async apply(ctx) {
    const named = identify(ctx.url);
    let embedded = null;
    try {
      const frames = await ctx.tab.evaluate(`[...document.querySelectorAll('iframe')].map(f => f.src).filter(Boolean)`);
      for (const src of frames || []) {
        const hit = EMBED.find(([, re]) => re.test(src));
        if (hit) { embedded = { ats: hit[0], src }; break; }
      }
    } catch { /* tab may be unavailable; stay read-only */ }
    if (embedded) return { status: "manual", reason: `embedded_ats:${embedded.ats}`, evidence: `employer page embeds ${embedded.ats}: ${embedded.src}` };
    if (named) return { status: "manual", reason: `unsupported_ats:${named}`, evidence: `${ctx.site} is ${named}; no adapter yet, apply manually: ${ctx.url}` };
    return { status: "manual", reason: "unknown_ats", evidence: `no adapter for ${ctx.site}; apply manually: ${ctx.url}` };
  },
};
