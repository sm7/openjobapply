// Generic form engine: scan visible fields (label/group/required/options) -> resolve each through
// answers.js -> fill -> READ BACK to verify. Never guesses: unresolved required fields are collected
// and returned so the adapter can stop with status 'manual'.
"use strict";
const { evalIn, sleep, observe, logConsent } = require("./lib");
const { NeedsHuman, resolveAnswer, deriveProfile } = require("./answers");

// ---- in-page scanner (serialised into the page; must be self-contained) --------------------------
function scanInPage(arg) {
  const root = arg.scope ? document.querySelector(arg.scope) : document.body;
  if (!root) return { fields: [], scopeMissing: true };
  const clean = (s) => (s || "").replace(/[\u200b\u00a0]/g, " ").replace(/\s+/g, " ").trim();
  const txt = (n) => clean(n && (n.innerText || n.textContent));
  const visible = (el) => {
    if (!el) return false;
    if (el.checkVisibility && !el.checkVisibility({ visibilityProperty: true })) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    if (r.right + scrollX < -200 || r.bottom + scrollY < -200) return false; // off-screen honeypots
    if (el.closest("[aria-hidden=true]")) return false;
    return true;
  };
  const css = (el) => {
    if (el.id && document.querySelectorAll("#" + CSS.escape(el.id)).length === 1) return "#" + CSS.escape(el.id);
    const parts = [];
    for (let n = el; n && n.nodeType === 1 && n !== document.documentElement; n = n.parentElement) {
      if (n.id && document.querySelectorAll("#" + CSS.escape(n.id)).length === 1) { parts.unshift("#" + CSS.escape(n.id)); break; }
      const sib = n.parentElement ? [...n.parentElement.children].filter((c) => c.tagName === n.tagName) : [n];
      parts.unshift(n.tagName.toLowerCase() + (sib.length > 1 ? `:nth-of-type(${sib.indexOf(n) + 1})` : ""));
    }
    return parts.join(" > ");
  };
  const labelText = (l, el) => { const c = l.cloneNode(true); c.querySelectorAll("input,select,textarea,button").forEach((x) => x.remove()); return clean(c.textContent); };
  const own = (el) => {
    const out = [];
    const lb = el.getAttribute("aria-labelledby");
    if (lb) lb.split(/\s+/).forEach((id) => { const n = document.getElementById(id); if (n) out.push(txt(n)); });
    if (!out.join("").trim() && el.labels) [...el.labels].forEach((l) => out.push(labelText(l, el)));
    let s = clean(out.join(" "));
    if (!s) s = clean(el.getAttribute("aria-label"));
    if (!s) { const l = el.closest("label"); if (l) s = labelText(l, el); }
    return s;
  };
  const container = (el) => { // nearest ancestor whose text looks like a "question + control" block
    let n = el.parentElement;
    for (let i = 0; i < 4 && n && n !== root; i++, n = n.parentElement) {
      const t = txt(n);
      if (t && t.length < 300) { const stripped = clean(t.replace(clean(el.value || ""), "")); if (stripped) return stripped; }
    }
    return "";
  };
  const sel = (q) => [...root.querySelectorAll(q)];
  const out = [];
  const seenGroups = new Set();
  for (const el of sel("input, select, textarea, [role=combobox]:not(input)")) {
    const tag = el.tagName.toLowerCase();
    let type = (el.getAttribute("type") || "").toLowerCase();
    if (tag === "input" && ["hidden", "submit", "button", "image", "reset"].includes(type)) continue;
    if (el.disabled || el.getAttribute("aria-disabled") === "true") continue;
    const nameAttr = el.getAttribute("name") || "";
    if (/captcha|honey|bot-?field|g-recaptcha|hcaptcha/i.test(nameAttr + " " + el.id + " " + el.className)) continue;
    if (el.closest("[role=listbox]")) continue;
    if (tag === "input" && el.getAttribute("role") === "combobox" && el.closest("[role=combobox]") && el.closest("[role=combobox]") !== el) continue;
    const isCheck = type === "radio" || type === "checkbox";
    const isFile = type === "file";
    if (isCheck) { const l = el.labels && el.labels[0]; if (!(visible(el) || (l && visible(l)))) continue; }
    else if (isFile) { if (el.closest("[aria-hidden=true]") || /captcha|honey/i.test(nameAttr)) continue; }
    else if (!visible(el)) continue;
    if (el.readOnly && tag !== "select" && type !== "file") continue;

    let kind = tag === "textarea" ? "textarea" : tag === "select" ? "select" : type === "radio" ? "radio" : type === "checkbox" ? "checkbox" : isFile ? "file" : type === "password" ? "password" : "text";
    const role = el.getAttribute("role");
    if (role === "combobox" || (tag === "input" && el.getAttribute("aria-haspopup") === "listbox")) kind = "combobox";
    if (kind === "text" && ["email", "tel", "url", "number", "date", "datetime-local", "month", "time"].includes(type)) kind = type === "date" || type === "month" || type === "time" || type === "datetime-local" ? "date" : type;

    let label = own(el);
    let group = "";
    let required = !!el.required || el.getAttribute("aria-required") === "true";
    let options = [];
    if (kind === "radio") {
      const nm = el.name;
      const key = nm ? "r:" + nm + ":" + (el.form ? [...document.forms].indexOf(el.form) : -1) : null;
      const radios = nm ? sel(`input[type=radio][name="${CSS.escape(nm)}"]`).filter((r) => (r.form || null) === (el.form || null)) : [el];
      if (key && seenGroups.has(key)) continue;
      if (key) seenGroups.add(key);
      options = radios.map((r) => ({ label: own(r) || clean(r.value), value: r.value, selector: css(r), checked: r.checked, labelSelector: r.id ? `label[for="${CSS.escape(r.id)}"]` : null }));
      const fs = el.closest("fieldset, [role=radiogroup], [role=group]");
      if (fs) {
        const lg = fs.querySelector("legend");
        group = lg ? txt(lg) : (fs.getAttribute("aria-labelledby") ? (fs.getAttribute("aria-labelledby").split(/\s+/).map((id) => txt(document.getElementById(id))).join(" ")) : clean(fs.getAttribute("aria-label")));
      }
      if (!group) { // lowest ancestor containing every radio -> its text minus the option labels
        let a = el.parentElement;
        while (a && a !== root && !radios.every((r) => a.contains(r))) a = a.parentElement;
        if (a) { let t = txt(a); options.forEach((o) => { t = t.replace(o.label, ""); }); group = clean(t).slice(0, 250); }
      }
      required = required || radios.some((r) => r.required || r.getAttribute("aria-required") === "true") || /\*\s*$/.test(group);
      label = group;
    } else if (kind === "checkbox") {
      const fs = el.closest("fieldset, [role=group]");
      if (fs) { const lg = fs.querySelector("legend"); group = lg ? txt(lg) : clean(fs.getAttribute("aria-label")); }
      if (!label) label = container(el);
      if (/\*\s*$/.test(label)) required = true;
    } else {
      if (!label) label = clean(el.placeholder) || clean(el.getAttribute("title")) || container(el);
      if (/\*\s*$|\(required\)/i.test(label)) required = true;
    }
    if (kind === "select") {
      options = [...el.options].map((o) => ({ label: clean(o.textContent), value: o.value, selected: o.selected, placeholder: o.value === "" || /^(select|choose|please select|--)/i.test(clean(o.textContent)) }));
    }
    const value = kind === "select" ? (options.find((o) => o.selected && !o.placeholder) || {}).label || "" : kind === "radio" ? ((options.find((o) => o.checked) || {}).label || "") : kind === "checkbox" ? (el.checked ? "checked" : "") : kind === "file" ? (el.files && el.files.length ? el.files[0].name : "") : (el.value || "");
    out.push({ tag, kind, type, label: label.replace(/\s*[*\u2731]+\s*$/, ""), group: group.replace(/\s*[*\u2731]+\s*$/, ""), required, options, placeholder: el.getAttribute("placeholder") || "", name: nameAttr || el.id || "", selector: css(el), value, checked: !!el.checked, autocomplete: el.getAttribute("autocomplete") || "" });
  }
  return { fields: out };
}

async function scanForm(tab, scope) {
  const r = await evalIn(tab, scanInPage, { scope: scope || null });
  return r.fields || [];
}

// ---- actions -------------------------------------------------------------------------------------
const digits = (s) => String(s || "").replace(/\D/g, "");
const flat = (s) => String(s || "").toLowerCase().replace(/\s+/g, " ").trim();

async function readValue(tab, f) {
  return evalIn(tab, (a) => {
    const el = document.querySelector(a.selector);
    if (!el) return null;
    if (a.kind === "select") { const o = el.options[el.selectedIndex]; return o ? (o.textContent || "").trim() : ""; }
    if (a.kind === "checkbox" || a.kind === "radio") return !!el.checked;
    if (a.kind === "file") return el.files && el.files.length ? el.files[0].name : "";
    if (a.kind === "combobox") return ((el.value || "") + " " + (el.textContent || "")).trim();
    return el.value;
  }, { selector: f.selector, kind: f.kind });
}

function sameValue(want, got, f) {
  if (f.kind === "tel" || /phone|mobile|cell/i.test(f.label)) return digits(got).endsWith(digits(want)) && digits(want).length >= 7;
  return flat(got) === flat(want) || flat(got).includes(flat(want));
}

async function setRadioOrCheck(tab, f, selector, labelSelector, on) {
  const cur = await tab.isChecked(selector).catch(() => null);
  if (cur === on) return true;
  try { on ? await tab.check(selector) : await tab.uncheck(selector); } catch { /* hidden native input: fall through to label click */ }
  if ((await tab.isChecked(selector).catch(() => null)) === on) return true;
  if (labelSelector) { try { await tab.click(labelSelector); } catch { /* ignore */ } }
  if ((await tab.isChecked(selector).catch(() => null)) === on) return true;
  await evalIn(tab, (s) => { const e = document.querySelector(s); if (e) e.click(); }, selector); // real click event on the control
  return (await tab.isChecked(selector).catch(() => null)) === on;
}

async function pickCombobox(tab, f, act) {
  await tab.click(f.selector);
  await sleep(350);
  const wanted = act.describe || act.value || "";
  if (f.tag === "input" && (f.type === "text" || f.type === "")) { try { await tab.type(f.selector, String(wanted)); await sleep(500); } catch { /* not typable */ } }
  const els = (await tab.observe()).elements.filter((e) => e.role === "option");
  const match = els.filter((e) => (act.test ? act.test(e.name || "") : flat(e.name) === flat(wanted)));
  if (match.length < 1 || (match.length > 1 && !act.pickFirst)) { try { await tab.press(f.selector, "Escape"); } catch { /* ignore */ } return { ok: false, got: `options=${els.length} matches=${match.length}` }; }
  await tab.id(match[0].id).click();
  await sleep(250);
  const got = await readValue(tab, f);
  return { ok: flat(got).includes(flat(match[0].name)) || !!got, got, chose: match[0].name };
}

/** Apply a resolved action. Returns {ok, got, shown}. */
async function applyAction(ctx, f, act) {
  const tab = ctx.tab;
  switch (act.op) {
    case "text": {
      await tab.fill(f.selector, act.value);
      const got = await readValue(tab, f);
      return { ok: sameValue(act.value, got, f), got, shown: act.value };
    }
    case "upload": {
      await tab.uploadFile(f.selector, act.path);
      await sleep(400);
      const got = await readValue(tab, f);
      return { ok: true, got, shown: act.path.split("/").pop() }; // custom uploaders hide the input; adapters re-check page text
    }
    case "check": {
      const ok = await setRadioOrCheck(tab, f, f.selector, null, true);
      return { ok, got: ok, shown: "checked" };
    }
    case "leave": {
      const ok = await setRadioOrCheck(tab, f, f.selector, null, false);
      return { ok, got: !ok, shown: "unchecked" };
    }
    case "choose": {
      if (f.kind === "select") {
        const opts = f.options.filter((o) => !o.placeholder);
        const m = opts.filter((o) => act.test(o.label) || act.test(o.value));
        if (m.length !== 1 && !(act.pickFirst && m.length > 1)) throw new NeedsHuman(f.group || f.label, `${m.length} options match "${act.describe}"`);
        await tab.select(f.selector, m[0].value);
        const got = await readValue(tab, f);
        return { ok: flat(got) === flat(m[0].label), got, shown: m[0].label };
      }
      if (f.kind === "radio") {
        const m = f.options.filter((o) => act.test(o.label));
        if (m.length !== 1 && !(act.pickFirst && m.length > 1)) throw new NeedsHuman(f.group || f.label, `${m.length} options match "${act.describe}"`);
        const ok = await setRadioOrCheck(tab, f, m[0].selector, m[0].labelSelector, true);
        return { ok, got: m[0].label, shown: m[0].label };
      }
      if (f.tag === "select") { // a native <select> that merely carries role=combobox (iCIMS): its popup is not in the DOM, so choose by option, then verify the selected one
        const opts = await evalIn(tab, (s) => { const e = document.querySelector(s); return e && e.options ? [...e.options].map((o) => ({ label: (o.textContent || "").trim(), value: o.value })) : []; }, f.selector);
        const m = opts.filter((o) => o.value !== "" && !/^(select|choose|please select|--|—)/i.test(o.label) && (act.test(o.label) || act.test(o.value)));
        if (m.length !== 1 && !(act.pickFirst && m.length > 1)) throw new NeedsHuman(f.group || f.label, `${m.length} options match "${act.describe}"`);
        await tab.select(f.selector, m[0].value);
        const sel = await evalIn(tab, (s) => { const e = document.querySelector(s); return e && e.options ? ((e.options[e.selectedIndex] || {}).textContent || "").trim() : null; }, f.selector);
        return { ok: typeof sel === "string" && flat(sel) === flat(m[0].label), got: sel, shown: m[0].label };
      }
      const r = await pickCombobox(tab, f, act);
      return { ok: r.ok, got: r.got, shown: r.chose || act.describe };
    }
    default: throw new Error(`unknown op ${act.op}`);
  }
}

/**
 * Fill every field of the current step.
 * @returns {{filled:{label,value,rule}[], skipped:{label,reason}[], unanswered:{question,required,detail}[], unverified:{label,want,got}[], consents:string[]}}
 */
async function fillForm(ctx, { scope, only } = {}) {
  ctx.P = ctx.P || deriveProfile(ctx.profile);
  const fields = (await scanForm(ctx.tab, scope)).filter((f) => !only || only(f));
  const rep = { filled: [], skipped: [], unanswered: [], unverified: [], consents: [], prefilled: [], fields: fields.length };
  for (const f of fields) {
    const q = f.group || f.label || f.placeholder || f.name || "(unlabeled)";
    let act;
    try {
      act = resolveAnswer(f, ctx);
    } catch (e) {
      if (!(e instanceof NeedsHuman)) throw e;
      const TEXTISH = ["text", "textarea", "email", "tel", "url", "number"];
      if (f.required && f.value && TEXTISH.includes(f.kind) && !(e.detail || "").includes("forbidden")) {
        // Saved site values are evidence for human review, not authorization to submit them.
        rep.prefilled.push({ label: q, value: String(f.value).slice(0, 80) });
      }
      if (f.required) rep.unanswered.push({ question: e.question, required: true, detail: e.detail });
      else rep.skipped.push({ label: q, reason: `optional, not answerable: ${e.detail || "not in allowlist"}` });
      continue;
    }
    if (act.op === "leave") {
      if (f.kind === "checkbox") {
        try {
          const r = await applyAction(ctx, f, act);
          if (!r.ok) throw new Error("checkbox could not be verified unchecked");
        } catch (e) {
          // An optional unapproved consent still must not remain preselected on Submit.
          rep.unverified.push({ label: q, want: "unchecked", got: e.message });
          rep.unanswered.push({ question: q, required: true, detail: "could not clear unapproved checkbox" });
          continue;
        }
      }
      rep.skipped.push({ label: q, reason: `left unchecked/unchanged (${act.rule})` });
      continue;
    }
    // already holds the answer? do not retype
    if (act.op === "text" && f.value && sameValue(act.value, f.value, f)) { rep.filled.push({ label: q, value: act.value, rule: act.rule, preset: true }); continue; }
    if (act.op === "choose" && f.kind === "combobox" && f.value && act.test && act.test(f.value)) {
      rep.filled.push({ label: q, value: f.value, rule: act.rule, preset: true });
      continue;
    }
    try {
      const r = await applyAction(ctx, f, act);
      if (!r.ok) {
        rep.unverified.push({ label: q, want: act.value || act.describe || act.op, got: r.got });
        if (f.required) rep.unanswered.push({ question: q, required: true, detail: "could not verify the field after filling" });
        continue;
      }
      rep.filled.push({ label: q, value: r.shown, rule: act.rule });
      if (act.consent) { logConsent(ctx, act.consent.text, act.consent.rule); rep.consents.push(act.consent.text); }
    } catch (e) {
      if (e instanceof NeedsHuman) {
        if (f.required) rep.unanswered.push({ question: e.question, required: true, detail: e.detail });
        else rep.skipped.push({ label: q, reason: e.detail });
      } else {
        rep.unverified.push({ label: q, want: act.value || act.describe || act.op, got: `error: ${e.message}` });
        if (f.required) rep.unanswered.push({ question: q, required: true, detail: `fill error: ${e.message}` });
      }
    }
  }
  return rep;
}

/** Type a verification code into a single input or N one-digit boxes. */
async function enterCode(tab, code) {
  const boxes = await evalIn(tab, () => {
    const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 2 && r.height > 2 && getComputedStyle(e).visibility !== "hidden"; };
    return [...document.querySelectorAll("input")].filter((i) => vis(i) && ["text", "tel", "number", "", "password"].includes((i.type || "").toLowerCase()) && !i.disabled)
      .map((i, n) => ({ n, max: i.maxLength, label: (i.getAttribute("aria-label") || (i.labels && i.labels[0] && i.labels[0].textContent) || i.placeholder || i.name || "").trim(), id: i.id }));
  });
  const oneDigit = boxes.filter((b) => b.max === 1);
  if (oneDigit.length >= 4) { // auto-advancing OTP boxes: focus the first, type the digits
    const first = await evalIn(tab, () => { const i = [...document.querySelectorAll("input")].find((x) => x.maxLength === 1 && x.getBoundingClientRect().width > 2); i && i.focus(); return !!i; });
    if (!first) return false;
    await tab.type("input:focus", code);
    return true;
  }
  const els = await observe(tab);
  const el = els.find((e) => e.role === "textbox" && /code|otp|verif|pin/i.test(e.name || ""));
  if (el) { await tab.id(el.id).fill(code); return true; }
  const only1 = boxes.length === 1 ? boxes[0] : null;
  if (only1 && only1.id) { await tab.fill(`#${only1.id}`, code); return true; }
  return false;
}

module.exports = { scanForm, fillForm, readValue, enterCode, applyAction };
