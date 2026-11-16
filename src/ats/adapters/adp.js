// ADP WorkforceNow (workforcenow.adp.com recruitment career center).
// Observed flow: job page -> Apply -> Privacy Policy dialog (Consent & Continue) -> "Tell us about
// yourself" (name/email/mobile, Continue) -> "Get your verification code" (SMS or email, Send)
// -> human supplies code -> application steps -> Submit.
//
// The adapter is a STATE MACHINE keyed off what is on screen, so it can be started from any step and
// resumed on the same tab after a human supplied an OTP. It uses role/name observation (no ids).
"use strict";
const {
  sleep, observe, matchEl, clickEl, visibleText, evalIn, detectChallenge, logConsent, jobKey, result, Blocked, snippet, CONFIRMATION, waitUntil,
} = require("../lib");
const { classifyConsent, deriveProfile } = require("../answers");
const { scanForm, fillForm } = require("../form");
const F = require("../flow");

const HOST = /(^|\.)(workforcenow|recruiting|myjobs)\.adp\.com$/i;

const RE = {
  already: /you('ve| have) already (applied|submitted)|already submitted an application/i,
  closed: /no longer (available|accepting)|position (has been )?(filled|closed)|(job|posting|requisition) (is )?(no longer|not) (available|found|open)|posting has expired/i,
  verifyEnter: /(enter|type|input).{0,40}(verification|security|one[- ]time|confirmation) code|code (we|was|has been) sent|we('ve| have) sent (you )?(a|an) /i,
  verifyChoose: /get your verification code|verify your identity|choose how (you|to)|where (should|would you like) (we|us|you)( to)? (send|receive)|receive a verification code/i,
  contact: /tell us about yourself/i,
  codeBox: /code|otp|verif|pin/i,
};

/**
 * What is on screen right now? ADP's dialogs expose their heading only as the dialog's accessible
 * name (e.g. dialog "Get your verification code"), not in innerText, so both are searched.
 */
async function detectState(ctx) {
  const tab = ctx.tab;
  const body = await visibleText(tab);
  const els = await observe(tab);
  const dlg = els.find((e) => e.role === "dialog" || e.role === "alertdialog");
  const text = `${dlg ? dlg.name || "" : ""}\n${body}`;
  const btn = (name) => matchEl(els, { roles: ["button", "link"], name });
  const textboxCode = matchEl(els, { roles: ["textbox"], name: RE.codeBox });
  const channelRadios = els.filter((e) => e.role === "radio");

  if (CONFIRMATION.test(body)) return { s: "done", text, els };
  if (RE.already.test(body)) return { s: "already_applied", text, els };
  if (RE.closed.test(body)) return { s: "closed", text, els };
  if (textboxCode && btn(/^verify$/i)) return { s: "verify_enter", text, els };
  if (dlg && textboxCode && (RE.verifyEnter.test(text) || /verif|security|one[- ]time/i.test(dlg.name || ""))) return { s: "verify_enter", text, els };
  if (!dlg && textboxCode && RE.verifyEnter.test(body) && !RE.contact.test(body)) return { s: "verify_enter", text, els };
  if (RE.verifyChoose.test(text) || (dlg && channelRadios.length >= 1 && btn(/^send/i))) return { s: "verify_choose", text, els };
  if (btn(/consent/i) || (/privacy policy/i.test(text) && btn(/^(i )?(accept|agree|consent)( (&|and) continue)?$/i))) return { s: "privacy", text, els };
  if (RE.contact.test(body) && matchEl(els, { roles: ["textbox"], name: /first name/i })) return { s: "contact", text, els };
  if (btn(/^apply( now| for this job| for this position)?$/i)) return { s: "job", text, els };
  const fields = await scanForm(tab);
  if (fields.length) return { s: "form", text, els, fields };
  return { s: "unknown", text, els };
}

async function valueOf(tab, el) { try { return await tab.id(el.id).value(); } catch { return ""; } }

async function stepPrivacy(ctx, st) {
  // Inspect the actual visible dialog, not a synthesized privacy title that could hide
  // a bundled screening/release/unknown authorization. Ignore unrelated page chrome.
  const text = await evalIn(ctx.tab, () => {
    const dialog = [...document.querySelectorAll('[role="dialog"], [role="alertdialog"]')]
      .find((el) => el.getBoundingClientRect().width && el.getBoundingClientRect().height && getComputedStyle(el).visibility !== "hidden");
    return dialog ? `${dialog.getAttribute("aria-label") || ""} ${dialog.innerText || ""}` : "";
  });
  const verdict = text ? classifyConsent(text, ctx.profile) : { verdict: "unknown" };
  if (verdict.verdict !== "allow") return F.manual(ctx, "consent_not_allowed", { evidence: `dialog is not a recognized privacy notice: ${st.text.slice(0, 200)}` });
  const b = matchEl(st.els, { roles: ["button", "link"], name: /consent|^(i )?(accept|agree)/i });
  if (!b) return F.manual(ctx, "privacy_button_missing");
  if ((b.states || []).includes("disabled")) return F.manual(ctx, "privacy_button_disabled", { evidence: "Consent button is disabled (likely requires scrolling the notice)" });
  logConsent(ctx, text, verdict.rule);
  await clickEl(ctx.tab, b);
  return null;
}

async function stepContact(ctx, st) {
  const P = ctx.P;
  const want = [
    [/^first name/i, P.first, false],
    [/^last name/i, P.last, false],
    [/^e-?mail/i, P.email, false],
    [/^(mobile|phone)/i, P.phoneDigits, true],
  ];
  for (const [re, val, isPhone] of want) {
    if (!val) return F.manual(ctx, "profile_incomplete", { unanswered: [String(re)] });
    const el = matchEl(await observe(ctx.tab), { roles: ["textbox"], name: re });
    if (!el) return F.manual(ctx, "contact_field_missing", { unanswered: [String(re)] });
    const cur = await valueOf(ctx.tab, el);
    const same = isPhone ? cur.replace(/\D/g, "").endsWith(val) : cur.trim().toLowerCase() === val.toLowerCase();
    if (!same) {
      await ctx.tab.id(el.id).fill(val);
      const after = await valueOf(ctx.tab, matchEl(await observe(ctx.tab), { roles: ["textbox"], name: re }) || el);
      const ok = isPhone ? after.replace(/\D/g, "").endsWith(val) : after.trim().toLowerCase() === val.toLowerCase();
      if (!ok) return F.manual(ctx, "contact_fill_unverified", { unanswered: [String(re)] });
      ctx.filled.push({ label: el.name, value: isPhone ? P.phone : val, rule: "contact" });
    } else ctx.filled.push({ label: el.name, value: isPhone ? P.phone : val, rule: "contact", preset: true });
  }
  const cont = matchEl((await observe(ctx.tab)), { roles: ["button"], name: /^continue$/i });
  if (!cont) return F.manual(ctx, "continue_button_missing");
  await clickEl(ctx.tab, cont);
  return null;
}

/** verify_choose: needs human go-ahead before a code is sent. */
async function stepVerifyChoose(ctx, st) {
  const req = F.otpReq(ctx, "ready_to_send");
  if (!(await ctx.otp.authorizeSend(req))) {
    return F.otpRequired(ctx, "ready_to_send", `bun src/ats/otp.js send ${ctx.token}`);
  }
  const els = await observe(ctx.tab);
  const P = ctx.P;
  const isSms = (n) => (/\d/.test(n) && P.phoneDigits && n.replace(/\D/g, "").endsWith(P.phoneDigits)) || /\b(text|sms)\b/i.test(n);
  const isEmail = (n) => (n.includes("@") && P.email && n.toLowerCase().includes(P.email.toLowerCase())) || /\be-?mail\b/i.test(n);
  const want = ctx.channel === "email" ? isEmail : isSms;
  const wantRe = { test: (n) => want(String(n)) };
  const pick = els.find((e) => ["radio", "checkbox", "option", "tab", "button", "link"].includes(e.role) && wantRe.test(e.name || "") && !/^send\b|cancel|back|close/i.test(e.name || ""));
  const sendOnly = els.find((e) => e.role === "button" && /^send\b/i.test(e.name || "") && wantRe.test(e.name || ""));
  if (sendOnly) {
    await clickEl(ctx.tab, sendOnly);
  } else {
    if (!pick) return F.manual(ctx, "verification_ui_unrecognized", { evidence: `no "${ctx.channel}" option found; ${st.text.slice(0, 200)}` });
    const host = await ctx.tab.evaluate(`(${((channel) => {
      const id = channel === "email" ? "emailIdentifierId" : "phoneIdentifierId";
      const el = document.getElementById(id);
      return el && el.tagName === "WFN-RADIO-BUTTON"
        ? { id, selected: el.getAttribute("radio-group-value") === el.getAttribute("value") }
        : null;
    }).toString()})(${JSON.stringify(ctx.channel)})`);
    if (host) {
      if (!host.selected) await ctx.tab.click(`#${host.id}`);
      const selected = await ctx.tab.evaluate(`(${((id) => {
        const el = document.getElementById(id);
        return el && el.getAttribute("radio-group-value") === el.getAttribute("value");
      }).toString()})(${JSON.stringify(host.id)})`);
      if (!selected) return F.manual(ctx, "verification_channel_not_selected");
    } else if (pick.role === "radio" || pick.role === "checkbox") {
      await ctx.tab.id(pick.id).check();
    } else await clickEl(ctx.tab, pick);
    await sleep(300);
    const send = matchEl(await observe(ctx.tab), { roles: ["button"], name: /^(send|get code|send code)$/i });
    if (!send) return F.manual(ctx, "send_button_missing");
    await clickEl(ctx.tab, send);
  }
  await ctx.otp.sent(req);
  ctx.log && ctx.log("otp_sent", { channel: ctx.channel, token: ctx.token });
  await sleep(1500);
  return null;
}

async function stepVerifyEnter(ctx) {
  const req = F.otpReq(ctx, "awaiting_code");
  const code = await ctx.otp.getCode(req);
  if (!code) return F.otpRequired(ctx, "awaiting_code", `bun src/ats/otp.js code ${ctx.token} <code>`);
  const before = await F.fingerprint(ctx.tab);
  const r = await F.submitCode(ctx, code, /^verify$/i);
  if (r === "no_input") return F.manual(ctx, "code_input_not_found");
  await F.waitChange(ctx.tab, before, 15000);
  const text = await visibleText(ctx.tab);
  if (/incorrect|invalid|expired|did not match|try again/i.test(text) && /verification|code/i.test(text)) {
    return F.manual(ctx, "otp_invalid", { evidence: "site rejected the supplied code; not retrying (avoid lockout). Request a new code." });
  }
  return null;
}

async function stepForm(ctx, st) {
  const rep = await fillForm(ctx);
  F.merge(ctx, rep);
  const blocked = F.submittable(rep);
  const nav = await F.findNav(ctx.tab);
  if (blocked) return F.manual(ctx, "unanswered_questions", { unanswered: rep.unanswered.map((u) => u.question), evidence: blocked });
  const ch = await detectChallenge(ctx.tab);
  if (ch) return F.manual(ctx, "captcha_required", { evidence: `bot check present: ${ch}` });

  if (nav.final) {
    if (ctx.dryRun) return result("skipped", "dry_run_complete", { dryRun: true, wouldSubmit: F.dryRunSummary(ctx, nav.final.name), evidence: `stopped before "${nav.final.name}"`, consents: ctx.consents.slice(), tabName: ctx.tabName });
    const r = await F.submitAndConfirm(ctx, nav.final);
    ctx.submitted = true;
    return r;
  }
  if (!nav.next) return F.manual(ctx, "no_navigation_button", { evidence: (await snippet(ctx.tab, 300)) });
  const before = await F.fingerprint(ctx.tab);
  await clickEl(ctx.tab, nav.next);
  const changed = await F.waitChange(ctx.tab, before, 12000);
  if (!changed) {
    const errs = await F.validationErrors(ctx.tab);
    return F.manual(ctx, "validation_error", { evidence: errs.join(" | ") || "page did not advance", unanswered: errs });
  }
  return null;
}

async function apply(ctx) {
  ctx.P = ctx.P || deriveProfile(ctx.profile);
  ctx.channel = ctx.opts.otpChannel || (ctx.otp && ctx.otp.channel) || "sms";
  const trail = [];
  let same = 0, last = null;
  for (let i = 0; i < 40; i++) {
    const st = await detectState(ctx);
    trail.push(st.s);
    ctx.log && ctx.log("adp_state", { s: st.s, i });
    same = st.s === last ? same + 1 : 0;
    last = st.s;
    if (same >= 3 && !["unknown"].includes(st.s)) return F.manual(ctx, `stuck_${st.s}`, { trail });
    const ch = st.s !== "done" ? await detectChallenge(ctx.tab) : null;
    if (ch) return F.manual(ctx, "captcha_required", { evidence: `bot check present (not solved): ${ch}`, trail });

    let out = null;
    switch (st.s) {
      case "done":
        return ctx.submitted
          ? result("applied", "confirmation_page", { evidence: st.text.replace(/\s+/g, " ").slice(0, 300), consents: ctx.consents.slice(), trail })
          : result("skipped", "confirmation_page_already_shown", { evidence: st.text.replace(/\s+/g, " ").slice(0, 300), trail });
      case "already_applied": return result("skipped", "already_applied", { evidence: (st.text.match(RE.already) || [""])[0], trail });
      case "closed": return result("skipped", "posting_closed", { evidence: (st.text.match(RE.closed) || [""])[0], trail });
      case "job": {
        const before = await F.fingerprint(ctx.tab);
        await clickEl(ctx.tab, matchEl(st.els, { roles: ["button", "link"], name: /^apply( now| for this job| for this position)?$/i }));
        await F.waitChange(ctx.tab, before, 15000);
        break;
      }
      case "privacy": { const before = await F.fingerprint(ctx.tab); out = await stepPrivacy(ctx, st); if (!out) await F.waitChange(ctx.tab, before, 15000); break; }
      case "contact": { const before = await F.fingerprint(ctx.tab); out = await stepContact(ctx, st); if (!out) { await F.waitChange(ctx.tab, before, 15000); if ((await detectState(ctx)).s === "contact") { const errs = await F.validationErrors(ctx.tab); out = await F.manual(ctx, "contact_validation_error", { evidence: errs.join(" | ") || "contact step did not advance", unanswered: errs }); } } break; }
      case "verify_choose": out = await stepVerifyChoose(ctx, st); if (!out) { await waitUntil(async () => (await detectState(ctx)).s !== "verify_choose", { timeout: 20000 }); } break;
      case "verify_enter": out = await stepVerifyEnter(ctx, st); break;
      case "form": out = await stepForm(ctx, st); break;
      default: {
        // transitional / unrecognized: give it a moment, then hand to a human
        await sleep(2000);
        if (trail.filter((x) => x === "unknown").length >= 3) return F.manual(ctx, "unrecognized_page", { evidence: st.text.replace(/\s+/g, " ").slice(0, 300), trail });
      }
    }
    if (out) { out.trail = trail; return out; }
  }
  return F.manual(ctx, "too_many_steps", { trail });
}

module.exports = {
  name: "adp",
  matches(url) { try { return HOST.test(new URL(url).hostname); } catch { return false; } },
  apply,
  _detectState: detectState,
};
