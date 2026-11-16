// LinkedIn Easy Apply. One job, one gated flow. dryRun (default true) fills everything and STOPS before Submit.
//
// Rules (learned from the live form):
//  - The form is a native <dialog>; fields use <label for>/<legend>; buttons are matched by visible text.
//  - NEVER trust prefilled values. LinkedIn prefills from old applications (e.g. "SQL: 4 years"). A question is
//    answered only from the allowlist below; otherwise the application stops as 'manual' with the question text.
//  - Legal-status questions are answered only from explicit profile booleans; null/unknown => manual.
//  - Never tick optional boosters ("top choice", "follow company").
const path = require("path");
const fs = require("fs");
const rl = require("./ratelimit.js");
const { resolveAnswer, NeedsHuman } = require("./ats/answers.js");
const sleep = ms => new Promise(r => setTimeout(r, ms));
const RESUME = path.resolve(__dirname, "..", "data", "resume.pdf");

/** Returns {kind:'text'|'choice', value} or null (=> needs human). `options` are visible option labels. */
function answerFor(label, options, profile) {
  try {
    const action = resolveAnswer({ kind: options ? "select" : "text", label, required: true }, { profile });
    if (action.op === "text") return { kind: "text", value: String(action.value) };
    if (action.op === "choose") {
      const matches = options.filter(action.test);
      if (matches.length === 1 || (matches.length && action.pickFirst)) return { kind: "choice", value: matches[0] };
    }
    return null;
  } catch (err) {
    if (err instanceof NeedsHuman) return null;
    throw err;
  }
}

/** Read the open dialog into a list of questions with their controls tagged for later interaction. */
function readDialog() {
  const d = document.querySelector("dialog[open], [role=dialog][aria-modal=true]") || document.querySelector("dialog");
  if (!d) return null;
  const clean = s => (s || "").replace(/\s+/g, " ").trim();
  const qs = [];
  let n = 0;
  const tag = el => { const id = "omp" + n++; el.setAttribute("data-omp", id); return id; };
  const lab = el => {
    const explicit = el.id ? d.querySelector(`label[for="${CSS.escape(el.id)}"]`) : null;
    const labelled = (el.getAttribute("aria-labelledby") || "").split(/\s+/).map(id => document.getElementById(id)?.innerText || "").join(" ");
    return clean(explicit?.innerText || el.closest("label")?.innerText || labelled || el.getAttribute("aria-label") || "");
  };
  const visible = el => !!(el.getClientRects().length || (el.id && d.querySelector(`label[for="${CSS.escape(el.id)}"]`)?.getClientRects().length));
  const groupQuestion = el => {
    const fs = el.closest("fieldset");
    const legend = fs?.querySelector("legend");
    if (clean(legend?.innerText)) return clean(legend.innerText);
    const labelled = fs && (fs.getAttribute("aria-labelledby") || "").split(/\s+/).map(id => document.getElementById(id)?.innerText || "").join(" ");
    if (clean(labelled)) return clean(labelled);
    for (let node = fs || el.parentElement, up = 0; node && node !== d && up < 6; up++, node = node.parentElement) {
      const prev = node.previousElementSibling;
      if (prev && !prev.querySelector("input, select, textarea")) {
        const text = clean(prev.innerText).replace(/This field is required|\d+\s*\/\s*\d+|\d+ of \d+ characters/gi, "").trim();
        if (text) return text;
      }
    }
    return "";
  };
  const groups = new Map();
  for (const r of d.querySelectorAll("input[type=radio]")) {
    if (r.disabled || !visible(r)) continue;
    const key = r.name || r.closest("fieldset") || r.parentElement;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(r);
  }
  let hasResumeChoice = false;
  for (const radios of groups.values()) {
    const options = radios.map(r => {
      const label = lab(r);
      if (label) return label;
      let row = r.parentElement;
      for (let i = 0; i < 3 && row && row !== d; i++, row = row.parentElement) {
        const text = clean(row.querySelector("p, span")?.innerText);
        if (text) return text;
      }
      return "";
    });
    const question = groupQuestion(radios[0]);
    const resume = !!radios[0].closest("[class*='document-upload'], [data-test-resume]") ||
      (/resume|document/i.test(radios[0].name || "") && options.some(o => /\.pdf\b/i.test(o))) ||
      /select (or upload )?(a |your )?resume/i.test(question);
    if (resume) { hasResumeChoice = true; radios.forEach(r => r.setAttribute("data-omp-resume", "true")); continue; }
    qs.push({ type: "radio", label: question, options, ids: radios.map(tag), checked: radios.map(r => r.checked),
      required: /\*|\brequired\b/i.test(question) || radios.some(r => r.required || r.getAttribute("aria-required") === "true") });
  }
  for (const el of d.querySelectorAll("select, textarea, input:not([type=radio]):not([type=file]):not([type=hidden])")) {
    if (el.disabled || !visible(el) || ["submit", "button", "reset"].includes(el.type)) continue;
    const label = lab(el) || groupQuestion(el);
    const required = el.required || el.getAttribute("aria-required") === "true" || /\*|\brequired\b/i.test(label);
    if (el.type === "checkbox") {
      qs.push({ type: "checkbox", label, group: groupQuestion(el), ids: [tag(el)], checked: [el.checked], required, options: null });
    } else {
      qs.push({ type: el.tagName === "SELECT" ? "select" : "text", inputType: el.type, label, value: el.value || "", ids: [tag(el)],
        options: el.tagName === "SELECT" ? [...el.options].map(o => clean(o.text)).filter(Boolean) : null, required });
    }
  }
  const files = [...d.querySelectorAll("input[type=file]")].filter(el => !el.disabled && !/cover/i.test(lab(el)) &&
    (/resume|curriculum|cv\b/i.test(`${lab(el)} ${el.id} ${el.name}`) || (/resume/i.test(d.innerText) && d.querySelectorAll("input[type=file]").length === 1)));
  files.forEach(el => el.setAttribute("data-omp-resume-upload", "true"));
  const progress = (d.innerText.match(/(\d+)\s*\/\s*(\d+)\s*pages?/i) || [])[0] || null;
  const buttons = [...d.querySelectorAll("button")].filter(visible).map(b => clean(b.innerText || b.getAttribute("aria-label"))).filter(Boolean);
  return { progress, buttons, qs, hasResumeChoice, hasResumeUpload: files.length === 1, heading: clean(d.innerText).slice(0, 160), text: clean(d.innerText) };
}

const clickBtn = (tab, re) => tab.evaluate(src => {
  const d = document.querySelector("dialog[open], [role=dialog][aria-modal=true]") || document.querySelector("dialog");
  const b = d && [...d.querySelectorAll("button")].find(x => !x.disabled && new RegExp(src, "i").test((x.innerText || x.getAttribute("aria-label") || "").trim()));
  if (b) b.click();
  return !!b;
}, re);

async function applyEasy(browser, job, profile, { dryRun = true, log = () => {}, tabName = "easyapply" } = {}) {
  const res = { status: "failed", reason: "", unanswered: [], consents: [], steps: [] };
  await rl.gate("detail");
  const tab = await browser.open({ name: tabName, url: job.url, app: "relay", persist: true, timeout: 60 });
  let resumeUploaded = false;
  const discard = async () => {
    try {
      await tab.evaluate(() => { const d = document.querySelector("dialog[open], [role=dialog][aria-modal=true]"); d && d.querySelector("button[aria-label='Dismiss']")?.click(); });
      await sleep(1000);
      await tab.evaluate(() => { [...document.querySelectorAll("button")].find(b => /^discard$/i.test(b.innerText.trim()))?.click(); });
    } catch {}
  };
  try {
    await sleep(2500);
    const page = { url: await tab.url(), title: await tab.title(), text: await tab.text() };
    if (rl.checkPage(page)) return { ...res, status: "manual", reason: "linkedin_challenge_lockout" };
    if (/no longer accepting applications/i.test(page.text.slice(0, 1500))) return { ...res, status: "skipped", reason: "closed" };

    const opened = await tab.evaluate(() => {
      const el = [...document.querySelectorAll("button")].find(b => /^easy apply/i.test((b.getAttribute("aria-label") || b.innerText || "").trim()));
      if (!el) return false;
      el.click();
      return true;
    });
    if (!opened) return { ...res, status: "skipped", reason: "no_easy_apply_button" };
    await sleep(2500);

    for (let step = 0; step < 10; step++) {
      let m = await tab.evaluate(readDialog);
      if (!m) { res.reason = "dialog_missing"; return res; }
      res.steps.push(`${m.progress || "?"}: ${m.buttons.join("|")}`);
      log(`step ${m.progress}: ${m.buttons.join(" | ")}`);

      // Only a fresh upload from this run can establish the resume's provenance.
      // A final-review summary mentioning a resume is not a resume-selection page.
      const finalReview = m.buttons.some(b => /^submit application\b/i.test(b));
      if (!finalReview && (m.hasResumeChoice || m.hasResumeUpload) && !resumeUploaded) {
        if (!m.hasResumeUpload || !fs.existsSync(RESUME)) {
          await discard();
          return { ...res, status: "manual", reason: "local_resume_upload_unavailable" };
        }
        const before = await tab.evaluate(() => [...document.querySelectorAll("dialog[open] input[type=radio], [role=dialog][aria-modal=true] input[type=radio]")].map(r => `${r.id}|${r.value}`));
        await tab.uploadFile("[data-omp-resume-upload='true']", RESUME);
        await sleep(2500);
        m = await tab.evaluate(readDialog);
        if (!m) return { ...res, status: "manual", reason: "dialog_missing_after_resume_upload" };
        const selected = await tab.evaluate(prior => {
          const d = document.querySelector("dialog[open], [role=dialog][aria-modal=true]");
          if (!d || /upload failed|unable to upload|error uploading|file (is )?too large/i.test(d.innerText)) return null;
          const radios = [...d.querySelectorAll("input[data-omp-resume=true]")];
          const candidates = radios.filter(r => !prior.includes(`${r.id}|${r.value}`));
          const matches = candidates.filter(r => {
            const label = r.id && d.querySelector(`label[for="${CSS.escape(r.id)}"]`);
            if (/\bresume\.pdf\b/i.test(label?.innerText || r.closest("label")?.innerText || "")) return true;
            for (let row = r.parentElement, up = 0; row && row !== d && up < 5; up++, row = row.parentElement) {
              if (row.querySelectorAll("input[type=radio]").length !== 1) break;
              if (/\bresume\.pdf\b/i.test(row.innerText || "")) return true;
            }
            return false;
          });
          if (matches.length === 1) {
            matches[0].setAttribute("data-omp-uploaded-resume", "true");
            return "radio";
          }
          // Some forms have no saved-document cards: the actual file input is the selection.
          const file = d.querySelector("input[data-omp-resume-upload=true]");
          if (!radios.length && file?.files?.length === 1 && file.files[0].name === "resume.pdf" && /\bresume\.pdf\b/i.test(d.innerText)) return "file";
          return null;
        }, before);
        if (selected === "radio") {
          await tab.check("[data-omp-uploaded-resume='true']");
          resumeUploaded = await tab.isChecked("[data-omp-uploaded-resume='true']");
        } else resumeUploaded = selected === "file";
        if (!resumeUploaded) {
          await discard();
          return { ...res, status: "manual", reason: "uploaded_local_resume_not_verified_selected" };
        }
        res.steps.push("Uploaded and selected local data/resume.pdf");
        m = await tab.evaluate(readDialog);
        if (!m) return { ...res, status: "manual", reason: "dialog_missing_after_resume_selection" };
      }

      for (const q of m.qs) {
        if (q.type === "checkbox") {
          const selector = `[data-omp='${q.ids[0]}']`;
          if (/\btop choice\b|\bfollow\b/i.test(q.label)) {
            if (q.checked[0]) await tab.uncheck(selector);
            if (q.required) res.unanswered.push(q.label);
            continue;
          }
          try {
            const action = resolveAnswer({ ...q, kind: "checkbox" }, { profile, job });
            if (action.op === "check") {
              if (!q.checked[0]) await tab.check(selector);
              if (action.consent) res.consents.push(action.consent);
            } else if (action.op === "leave" && q.checked[0]) await tab.uncheck(selector);
          } catch (err) {
            if (!(err instanceof NeedsHuman)) throw err;
            if (q.required || q.checked[0]) res.unanswered.push(q.label || "(unlabelled checkbox)");
          }
          continue;
        }
        const a = q.inputType === "password" ? null : answerFor(q.label, q.options, profile);
        const hasValue = q.type === "radio" ? q.checked.some(Boolean) : !!q.value;
        if (!a) {
          // Unknown question. A prefilled value is NOT accepted as an answer.
          if (q.required || hasValue) res.unanswered.push(q.label || "(unlabelled field)" + (hasValue ? ` [prefilled by LinkedIn: ${q.value || "selected"}]` : ""));
          continue;
        }
        if (q.type === "select") await tab.select(`[data-omp='${q.ids[0]}']`, a.value);
        else if (q.type === "radio") {
          const i = q.options.findIndex(o => o.trim().toLowerCase() === a.value.trim().toLowerCase());
          if (i < 0) { res.unanswered.push(q.label); continue; }
          if (!q.checked[i]) await tab.evaluate(id => document.querySelector(`[data-omp='${id}']`).click(), q.ids[i]);
        } else if (q.value !== a.value) await tab.fill(`[data-omp='${q.ids[0]}']`, a.value);
      }
      if (res.unanswered.length) {
        await discard();
        return { ...res, status: "manual", reason: "unanswered_or_unverified_question" };
      }

      const names = m.buttons.map(b => b.toLowerCase());
      if (names.some(n => n.startsWith("submit application"))) {
        if (!resumeUploaded) {
          await discard();
          return { ...res, status: "manual", reason: "local_resume_not_uploaded_this_run" };
        }
        if (dryRun) { await discard(); return { ...res, status: "skipped", reason: "dry_run_complete: reached Submit; not clicked" }; }
        const ready = await tab.evaluate(() => {
          const d = document.querySelector("dialog[open], [role=dialog][aria-modal=true]");
          return !!d && [...d.querySelectorAll("button")].some(b => !b.disabled && b.getClientRects().length && /^submit application\b/i.test((b.innerText || b.getAttribute("aria-label") || "").trim()));
        });
        if (!ready) { await discard(); return { ...res, status: "manual", reason: "submit_button_not_ready" }; }
        await rl.gate("apply");
        const clicked = await clickBtn(tab, "^submit application\\b");
        if (!clicked) return { ...res, status: "failed", reason: "submit_button_unavailable" };
        await sleep(3500);
        const confirmation = await tab.evaluate(() => {
          // Look only at an explicit confirmation surface, never "applications submitted" statistics.
          const surfaces = [...document.querySelectorAll("dialog[open], [role=dialog], [role=alert], [role=status], .artdeco-toast-item")];
          for (const el of surfaces) {
            if (!el.getClientRects().length) continue;
            const text = (el.innerText || "").replace(/\s+/g, " ").trim();
            const match = text.match(/\byour application (?:was|has been) (?:sent|submitted)(?: to\b[^.!]*[.!]?)?|\bapplication (?:successfully submitted|submitted successfully|sent successfully)\b/i);
            if (match) return match[0];
            const heading = [...el.querySelectorAll("h1, h2, h3, [role=heading]")].find(h => /^(your )?application (sent|submitted)[.!]?$/i.test((h.innerText || "").trim()));
            if (heading) return heading.innerText.trim();
          }
          return null;
        });
        return confirmation ? { ...res, status: "applied", reason: "submitted", evidence: confirmation }
                            : { ...res, status: "failed", reason: "submit_clicked_no_explicit_confirmation" };
      }
      const advanced = (await clickBtn(tab, "^review$")) || (await clickBtn(tab, "^next$")) || (await clickBtn(tab, "^continue"));
      if (!advanced) { await discard(); return { ...res, reason: "no_next_button" }; }
      await sleep(1800);
      // validation errors keep us on the same page
      const err = await tab.evaluate(() => /this field is required|please enter|select an option|enter a valid/i.test((document.querySelector("dialog[open]") || document.body).innerText));
      if (err) { await discard(); return { ...res, status: "manual", reason: "form_validation_error" }; }
    }
    await discard();
    return { ...res, reason: "too_many_steps" };
  } finally {
    try { await browser.close({ name: tabName }); } catch {}
  }
}

module.exports = { applyEasy, answerFor, readDialog };
