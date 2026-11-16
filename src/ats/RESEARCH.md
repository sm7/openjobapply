# ATS automation prior art (researched 2026-11-11)

Question: is there an existing open-source project we can depend on for ADP WorkforceNow
and/or generic ATS (Workday, Greenhouse, Lever, iCIMS) applications with a **human-in-the-loop
OTP step** and **no stealth / CAPTCHA evasion**?

Method: GitHub search via `web_search`, repo metadata from the GitHub REST API
(`api.github.com/repos/...`: license, `pushed_at`), then **read-only shallow clones into
`/tmp/ats-research`** and `grep` of the source for ADP/WorkforceNow, OTP/verification-code,
captcha/stealth/webdriver handling. Nothing was installed or executed. Scratch clones are
outside the repo.

## Verdict (short)

**No existing project covers ADP WorkforceNow. None is suitable as a dependency.**
None of the 9 repos cloned and grepped mentions `workforcenow`/`adp.com` anywhere in its source (the
other candidates were checked by README/metadata only). The Greenhouse/Lever/Workday projects are tiny (0–4 stars), mostly
Playwright-first (our driver is the omp `browser` relay, not Playwright), and several rely on
`navigator.webdriver` patching or stealth settings, which this project forbids.

**Recommendation: build in-house (done in `src/ats/`), borrow ideas only.** Ideas borrowed
and where they landed are listed per repo below. No third-party code was copied.

## Candidates

| Repo | License | Last push | ATS coverage | Stealth / CAPTCHA | OTP / human step | Reuse |
|---|---|---|---|---|---|---|
| [KrishK21/one-click-apply](https://github.com/KrishK21/one-click-apply) | MIT | 2026-11-07 | Greenhouse, Lever, Ashby (Playwright) | No solver, no stealth. `captchaVisible()` detects hCaptcha/reCAPTCHA/Cloudflare/Arkose and **waits for the human** (`filler.js:525,585`). | Greenhouse post-submit emailed security code: waits for human, **or optionally reads the user's inbox via IMAP** (`email.js`) | Reference only. IMAP-inbox reading is explicitly forbidden here (no reading the candidate's Gmail). Idea borrowed: detect-captcha-then-hand-to-human; label→answer rules table in `answers.js`. |
| [ChamPro/ats-autofill](https://github.com/ChamPro/ats-autofill) | MIT | 2026-07-02 | Workday, Greenhouse, Lever, Ashby, Dayforce, Oracle (Claude Code plugin; in-page JS engine) | No stealth/captcha hits in source. **Never clicks Submit, never creates accounts, never uploads files.** | none | Reference only (Claude-extension plugin, not a library). Ideas borrowed: guard-words in label→field mapping (no phone into "Phone Extension", no name into "Emergency Contact"), *readback is the verdict* (verify each field after fill), exact/unique option matching, never guess ambiguous options. Its Workday notes (React controlled inputs ignore untrusted events; `data-automation-id` selectors; validation only on Save) inform our Workday limitation notes. |
| [dyyfk/auto-apply](https://github.com/dyyfk/auto-apply) | none declared (all rights reserved) | 2026-08-05 | Ashby, Greenhouse, Lever (Playwright, CDP Chrome) | Skips `g-recaptcha` textareas only. No stealth. | Greenhouse security-code boxes handled (auto-advancing OTP inputs: focus first box, type code) | Reference only — **no license**, cannot reuse code. Idea: OTP boxes may be N single-digit inputs; type into the first. Re-implemented in `form.js#enterCode`. |
| [devdattatalele/auto-apply](https://github.com/devdattatalele/auto-apply) | MIT | 2026-04-15 | multi-ATS (Playwright, Claude Code) | Skips recaptcha fields ("Cannot auto-solve") — no solver, no stealth | none | Reference. Idea: planner that returns `skipped` with reason instead of guessing. |
| [Vikhyat-Chauhan/ats-agent](https://github.com/Vikhyat-Chauhan/ats-agent) | MIT | 2026-06-12 | Workday, iCIMS, Greenhouse, Lever (LangGraph + browser-use + Playwright) | no stealth/captcha hits | human review of low-confidence fields (`accept/correct/choose`) | Not reusable: Python + LLM fine-tuning loop + browser-use agent that guesses answers (violates "never guess"). |
| [ilyakarakotov/auto-apply](https://github.com/ilyakarakotov/auto-apply) | MIT | 2026-06-15 | generic via Claude Code + Playwright | **REJECT — `filler.template.js` "stealth: lower the hCaptcha/bot risk score" patches `navigator.webdriver` and `window.chrome`.** | email-gate skip | Rejected. Not even as reference. |
| [sudheerbez/job-apply-agent](https://github.com/sudheerbez/job-apply-agent) | none | 2026-04-02 | LinkedIn, Indeed, Greenhouse, Lever, Workday (Playwright + OpenAI) | **REJECT — `browser_manager.py` overrides `navigator.webdriver`, README/config advertise "stealth"/anti-detection; OpenAI guesses answers.** | none | Rejected. |
| [tmason10/job-application-automation](https://github.com/tmason10/job-application-automation) | none | 2026-05-04 | Greenhouse, Lever, Ashby (Playwright) | Semi-auto: "human solves CAPTCHAs and clicks Submit"; no solver | human pause for captcha/submit | Reference only (no license). Matches our pause model. |
| [iknalos/ApplyPilot](https://github.com/iknalos/ApplyPilot) | none | 2026-08-14 | Greenhouse/Lever API mapping, bookmarklet, Claude agent for Workday | no stealth hits | none | Reference only (no license). Idea: Greenhouse/Lever question schemas are available from public job-board JSON APIs (not used; we read the DOM so we answer what the user actually sees). |
| [speedyapply/JobSpy](https://github.com/speedyapply/JobSpy) | MIT | 2026-10-27 | Job **discovery** scraper only (LinkedIn/Indeed/Glassdoor/…) | not audited (discovery only) | n/a | Not relevant to applying (discovery is already covered by `src/discover_*.js`). |
| [browserbase/stagehand](https://github.com/browserbase/stagehand) | MIT | 2026-11-12 | generic AI browser SDK | README-level check only; the hosted Browserbase product advertises stealth/captcha features | n/a | Heavy dependency + LLM in the answer loop + Playwright/CDP driver; our driver is omp `browser`. Not adopted. |
| [browser-use/browser-use](https://github.com/browser-use/browser-use) | MIT | 2026-11-12 | generic AI browser agent | README: Cloud "stealth browsers and proxies", "solve CAPTCHAs automatically" (library can disable) | n/a | Not adopted: LLM agent guesses; ecosystem built around stealth/captcha products. |
| Fill Hero ([fillhero.com/autofill/adp-workforce-now](https://fillhero.com/autofill/adp-workforce-now)) | closed-source Chrome extension | n/a | **Only ADP-specific tool found**: autofills WFN application fields from a stored profile | n/a | n/a | Not open source → cannot reuse. Confirms the field set is label-based. |
| ADP Developer "Job Applications V2 API" ([guide](https://developers.adp.com/articles/preview/guide-job-applications-v2-api-guide-for-adp-workforce-now-0)) | ADP partner API | — | ADP WFN | n/a | n/a | Employer-side API (for ADP customers/partners), not candidate-side. Not usable. |

## ADP WorkforceNow specifically

- No open-source candidate automation exists (searched GitHub + web for `workforcenow`,
  "ADP WorkforceNow job application automation/autofill"; only hits were career-center pages,
  ADP's employer API, and the closed Fill Hero extension).
- Observed live flow (this repo's session): job page → Apply → Privacy Policy dialog
  (Consent & Continue) → "Tell us about yourself" → "Get your verification code"
  (SMS or email, Send) → human OTP → application form. ADP also publishes (applyvita.com guide)
  a per-job AI-review opt-out; we do not touch it.
- Role/name-based observation works (no iframes were present at the contact step), so the
  adapter is written against accessible roles/labels, not brittle generated ids.

## Decision

1. **Build in-house** on omp's `browser` global — zero new dependencies.
2. **Borrowed ideas** (re-implemented, no code copied): human hand-off on CAPTCHA/OTP instead
   of solving (one-click-apply, tmason10); readback verification + guard words +
   never-guess option matching (ats-autofill); `skipped`/manual with reason instead of guessing
   (devdattatalele); OTP multi-box entry (dyyfk).
3. **Explicitly rejected:** any `navigator.webdriver`/stealth patching (ilyakarakotov,
   sudheerbez), cloud stealth/captcha products (browser-use cloud, Browserbase), IMAP inbox
   reading (one-click-apply `email.js`) — conflicts with hard constraints.
4. Future OTP automation must be via a **dedicated job-application mailbox the candidate configures**,
   implemented as another `OtpProvider` (see `src/ats/otp.js`).
