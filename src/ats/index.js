// Employer-ATS application plugin.
//
//   applyOnSite(browser, job, profile, opts) ->
//     {status:'applied'|'failed'|'manual'|'skipped', reason, evidence?, unanswered?, consents?, ...extras}
//
//   job     {id, url, apply_url?, source?, title?, company?}       (rows of data/jobs.db work as-is)
//   profile parsed profile.json
//   opts    {dryRun=true, otpProvider, log, tab?, target?, tabName?, adapter?, otpChannel?, app?, keepOpen?}
//             dryRun   anything but `false` is a dry run: fill, then stop BEFORE the final Submit
//             tab      an already-open BrowserTab to drive (resume / user's own tab)
//             target   URL/title substring of a user tab to adopt through the relay (never navigated)
//             adapter  force an adapter by name (tests / local fixtures)
//   extras  resumeToken, otpStage, wouldSubmit (dry run), unanswered[], trail[], adapter, tabName
//
// Dry-run completion is reported as status 'skipped', reason 'dry_run_complete' (+ wouldSubmit),
// so a dry run can never be mistaken for an application that was sent.
"use strict";
const { Blocked, jobKey, result } = require("./lib");
const { NeedsHuman, deriveProfile } = require("./answers");
const { HumanPromptOtpProvider, OtpProvider, otpToken } = require("./otp");

const adp = require("./adapters/adp");
const greenhouse = require("./adapters/greenhouse");
const lever = require("./adapters/lever");
const workday = require("./adapters/workday");
const icims = require("./adapters/icims");
const smartrecruiters = require("./adapters/smartrecruiters");
const ashby = require("./adapters/ashby");
const generic = require("./adapters/generic");

/** Registry: first matching adapter wins; generic must stay last. */
const adapters = [adp, greenhouse, lever, workday, icims, smartrecruiters, ashby, generic];

function pickAdapter(url, forced) {
  if (forced) {
    const a = adapters.find((x) => x.name === forced);
    if (!a) throw new Error(`unknown adapter "${forced}" (have: ${adapters.map((x) => x.name).join(", ")})`);
    return a;
  }
  return adapters.find((a) => a.matches(url));
}

function hostOf(url) { try { return new URL(url).hostname; } catch { return ""; } }

async function acquireTab(browser, job, o, url, adapter) {
  if (o.tab) return { tab: o.tab, name: o.tabName || "provided", adopted: true };
  if (adapter.name === "generic" && generic.identify(url)) return { tab: null, name: null, adopted: false }; // known-unsupported: no page load at all
  if (o.target) {
    const name = o.tabName || `ats-${jobKey(job)}`;
    const tab = await browser.open({ name, app: o.app || "relay", target: o.target }); // adopt: do NOT navigate the user's tab
    return { tab, name, adopted: true };
  }
  const name = o.tabName || `ats-${jobKey(job)}`;
  const tab = await browser.open({ name, url, ...(o.app ? { app: o.app } : {}) });
  return { tab, name, adopted: false };
}

async function applyOnSite(browser, job, profile, opts = {}) {
  const o = { ...opts };
  const dryRun = o.dryRun !== false;
  const log = typeof o.log === "function" ? o.log : () => {};
  if (!job || !(job.apply_url || job.url)) return result("failed", "job_without_url");
  if (!profile || !profile.candidate) return result("failed", "profile_invalid", { evidence: "profile.candidate missing" });
  if (!dryRun && !browser) return result("failed", "no_browser");

  const url = job.apply_url || job.url;
  let adapter;
  try { adapter = pickAdapter(url, o.adapter); } catch (e) { return result("failed", "adapter_error", { evidence: e.message }); }

  let held = null;
  try {
    held = await acquireTab(browser, job, o, url, adapter);
    const liveUrl = held.tab ? await held.tab.url().catch(() => url) : url;
    // adopting a user's tab: the real page URL decides the adapter unless one was forced
    if (!o.adapter && held.adopted && liveUrl && !adapter.matches(liveUrl)) adapter = pickAdapter(liveUrl);
    const site = hostOf(liveUrl) || hostOf(url);
    const ctx = {
      browser, tab: held.tab, tabName: held.name, job, profile, P: deriveProfile(profile), opts: o, dryRun, log,
      jobAnswers: o.jobAnswers && typeof o.jobAnswers === "object" ? o.jobAnswers : {}, // reviewed per-job answers from the round header
      otp: o.otpProvider || new HumanPromptOtpProvider({ channel: o.otpChannel }),
      site, url: liveUrl, token: otpToken(site, jobKey(job)),
      consents: [], filled: [], skipped: [], prefilled: [], submitted: false,
    };
    log("start", { adapter: adapter.name, site, dryRun });
    const r = await adapter.apply(ctx);
    const out = { ...r, adapter: adapter.name, dryRun };
    if (out.status === "applied" && held.tab && !held.adopted && !o.keepOpen) { try { await held.tab.close(); } catch { /* best effort */ } }
    return out;
  } catch (e) {
    if (e instanceof NeedsHuman) return result("manual", "unanswered_questions", { unanswered: [e.question], evidence: e.message, adapter: adapter.name, dryRun });
    if (e instanceof Blocked) return result("manual", e.reason, { evidence: e.evidence, adapter: adapter.name, dryRun });
    log("error", { message: e.message });
    return result("failed", "adapter_error", { evidence: `${e.name}: ${e.message}`, adapter: adapter.name, dryRun, tabName: held && held.name });
  }
}

module.exports = { applyOnSite, pickAdapter, adapters, NeedsHuman, HumanPromptOtpProvider, OtpProvider, otpToken };
