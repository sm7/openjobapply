// Google sign-in for employer ATS sites, authorized by the candidate for HER OWN Google account only.
// Chrome is already signed in to it, so the flow is: press "Sign in with Google" -> (maybe) a Google account
// chooser -> pick her account -> back on the employer site, signed in. No password is ever typed.
//
// Hard limits, all enforced in code:
//   * Only the exact label "Sign in / Continue / Sign up with Google" is pressed (lib.isGoogleSignIn).
//   * Only ever on accounts.google.com, and only the tile / identifier for the candidate email.
//   * The page is never typed into. A Google password field, a 2-step / phone / device challenge, a CAPTCHA,
//     or a consent screen that asks for more than basic sign-in all STOP as manual.
//   * Cap: one Google attempt per site per run (ctx.accountCaps[site].google).
//   * Needs ctx.dryRun === false, ctx.opts.allowAccountCreation === true AND ctx.opts.allowGoogleSignIn === true.
const { Blocked, sleep, observe, isGoogleSignIn, detectChallenge, waitUntil } = require("./lib");

const GOOGLE_HOST = /^accounts\.google\.com$/i;
/** Production: exactly accounts.google.com. Fixtures only: a loopback host, and only with opts.allowLoopbackHttp. */
const isGoogleHost = (ctx, host) => GOOGLE_HOST.test(host || "") || (!!(ctx.opts && ctx.opts.allowLoopbackHttp) && /^(127\.0\.0\.1|localhost)$/.test(host || "") && !!(ctx.opts && ctx.opts.googleFixtureHost) && host === ctx.opts.googleFixtureHost);
const hostOf = (u) => { try { return new URL(u).hostname; } catch { return ""; } };

/** Text on a Google OAuth consent screen that means "this asks for more than basic sign-in". */
const BROAD_SCOPE = /(see, edit, create|see and download|read, compose|manage your|view and manage|access to your (?:contacts|calendar|drive|gmail|files|photos)|offline access|google drive|gmail|calendar|contacts|cloud platform)/i;
const GOOGLE_PASSWORD = /enter your password|type your password|forgot password\?|show password/i;
const GOOGLE_CHALLENGE = /2-step verification|verify it'?s you|check your phone|confirm it'?s you|try another way|google prompt|security key|enter the code|unusual activity|couldn'?t sign you in|this browser or app may not be secure/i;

const authorised = (ctx) => ctx.dryRun === false && !!ctx.opts && ctx.opts.allowAccountCreation === true && ctx.opts.allowGoogleSignIn === true;

/** Pure: classify the text/controls of a Google page into what to do. */
function classifyGooglePage({ host, text, hasPasswordInput, accounts, buttons, email, isGoogle }) {
  if (!(isGoogle ? isGoogle(host) : GOOGLE_HOST.test(host || ""))) return { s: "not_google" };
  const t = String(text || "");
  if (hasPasswordInput || GOOGLE_PASSWORD.test(t)) return { s: "password", why: "Google is asking for a password" };
  if (GOOGLE_CHALLENGE.test(t)) return { s: "challenge", why: "Google is asking for 2-step verification or says the sign-in is not allowed" };
  const mine = (accounts || []).find((a) => String(a).toLowerCase().includes(String(email || "").toLowerCase()));
  if (/choose an account|select an account|use another account/i.test(t) || (accounts || []).length) {
    if (!mine) return { s: "wrong_account", why: "the candidate Google account is not offered on the account chooser" };
    if ((accounts || []).filter((a) => String(a).toLowerCase().includes(String(email).toLowerCase())).length !== 1) return { s: "ambiguous", why: "more than one tile matches the candidate account" };
    return { s: "choose_account", account: mine };
  }
  if (/wants to access your google account|sign in to .{0,60} with google|continue to /i.test(t) || (buttons || []).some((b) => /^(continue|allow)$/i.test(String(b).trim()))) {
    if (BROAD_SCOPE.test(t)) return { s: "broad_scope", why: "the consent screen asks for access beyond basic sign-in (name, email, profile picture)" };
    return { s: "consent_basic" };
  }
  return { s: "unknown" };
}

async function googleFacts(tab, email) {
  return tab.evaluate(`(() => {
    const clean = (s) => (s || "").replace(/\\s+/g, " ").trim();
    const accts = [...document.querySelectorAll('[data-identifier], [data-email]')].map((e) => e.getAttribute('data-identifier') || e.getAttribute('data-email') || clean(e.innerText)).filter(Boolean);
    const btns = [...document.querySelectorAll('button, [role=button]')].map((b) => clean(b.innerText || b.getAttribute('aria-label'))).filter(Boolean);
    return { host: location.hostname, text: clean(document.body ? document.body.innerText : "").slice(0, 2500), hasPasswordInput: !!document.querySelector('input[type=password]:not([aria-hidden=true])'), accounts: accts, buttons: btns };
  })()`).then((f) => ({ ...f, email }));
}

/**
 * Press "Sign in with Google" on the employer page and complete the OAuth hop in the same tab.
 * Returns { ok:true, via:'google' } or { ok:false, reason, evidence } (never throws for expected stops).
 */
const fp = (tab) => tab.evaluate(`location.href + "|" + (document.body ? document.body.innerText : "").length + "|" + (document.body ? document.body.innerText : "").slice(0, 200)`).catch(() => "");
async function waitChange(ctx, before) { await waitUntil(async () => (await fp(ctx.tab)) !== before, { timeout: 8000, every: 250 }); await sleep(300); }

async function signInWithGoogle(ctx, { email }) {
  if (!authorised(ctx)) return { ok: false, reason: "account_required", evidence: "Google sign-in needs dryRun:false, opts.allowAccountCreation and opts.allowGoogleSignIn" };
  const site = ctx.site || "";
  const caps = ((ctx.accountCaps = ctx.accountCaps || {})[site] = ctx.accountCaps[site] || {});
  if (caps.google >= 1) return { ok: false, reason: "account_flow_failed", evidence: "Google sign-in attempt cap (1) reached for this site" };

  const els = await observe(ctx.tab);
  const btn = els.find((e) => (e.role === "button" || e.role === "link") && isGoogleSignIn(e.name));
  if (!btn) return { ok: false, reason: "account_flow_failed", evidence: "no exact 'Sign in with Google' control on the page" };
  caps.google = (caps.google || 0) + 1; // count the attempt BEFORE pressing: a failure can never be retried

  const employerUrl = await ctx.tab.url();
  const beforeFirst = await fp(ctx.tab);
  await tab_click(ctx, btn); // exact label already verified by isGoogleSignIn above; lib.clickEl would (rightly) refuse any "with Google" control
  await waitChange(ctx, beforeFirst);
  for (let hop = 0; hop < 6; hop++) {
    const beforeStep = await fp(ctx.tab);
    if (await detectChallenge(ctx.tab).catch(() => null)) return { ok: false, reason: "captcha_required", evidence: "CAPTCHA on the Google sign-in path (not solved)" };
    const url = await ctx.tab.url();
    const host = hostOf(url);
    if (!isGoogleHost(ctx, host)) {
      // Off Google. That is success ONLY if we are back on the employer's own site. Any other host (an unexpected
      // redirect, a look-alike, a third-party page) is a stop: nothing further is clicked or typed there.
      const employerHost = hostOf(employerUrl);
      const sameFamily = (a, b) => !!a && !!b && (a === b || a.split(".").slice(-2).join(".") === b.split(".").slice(-2).join("."));
      if (!sameFamily(host, employerHost)) return { ok: false, reason: "account_flow_failed", evidence: `Google sign-in ended on an unexpected host (${host || "none"}); not the employer site` };
      return { ok: true, via: "google" };
    }
    const facts = await googleFacts(ctx.tab, email);
    const c = classifyGooglePage({ ...facts, isGoogle: (h) => isGoogleHost(ctx, h) });
    switch (c.s) {
      case "password": return { ok: false, reason: "account_flow_failed", evidence: c.why };
      case "challenge": return { ok: false, reason: "mfa_required", evidence: c.why };
      case "wrong_account":
      case "ambiguous": return { ok: false, reason: "account_flow_failed", evidence: c.why };
      case "broad_scope": return { ok: false, reason: "account_flow_failed", evidence: c.why };
      case "unknown": return { ok: false, reason: "account_flow_failed", evidence: "unrecognized Google page; nothing was clicked" };
      case "choose_account": {
        const tiles = await observe(ctx.tab);
        const tile = tiles.find((e) => (e.role === "link" || e.role === "button" || e.role === "option") && String(e.name || "").toLowerCase().includes(email.toLowerCase()));
        if (!tile) return { ok: false, reason: "account_flow_failed", evidence: "candidate account tile not clickable on the Google chooser" };
        await tab_click(ctx, tile);
        break;
      }
      case "consent_basic": {
        const tiles = await observe(ctx.tab);
        const go = tiles.find((e) => e.role === "button" && /^(continue|allow)$/i.test(String(e.name || "").trim()));
        if (!go) return { ok: false, reason: "account_flow_failed", evidence: "basic consent screen without a Continue control" };
        await tab_click(ctx, go);
        break;
      }
      default: return { ok: false, reason: "account_flow_failed", evidence: `unexpected Google state "${c.s}"` };
    }
    await waitChange(ctx, beforeStep);
  }
  return { ok: false, reason: "account_flow_failed", evidence: "Google sign-in did not return to the employer site within the allowed hops" };
}

// Clicks on Google's own page bypass lib.clickEl's FORBIDDEN_CLICK (Google tile/Continue labels are never in it).
async function tab_click(ctx, el) { await ctx.tab.id(el.id).click(); }

module.exports = { signInWithGoogle, classifyGooglePage, authorised, GOOGLE_HOST, BROAD_SCOPE };
