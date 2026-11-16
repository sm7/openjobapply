# src/ats — employer ATS applications

Applies on employer ATS sites that LinkedIn Easy Apply cannot cover. Drives the omp `browser`
global; no third-party dependencies (see `RESEARCH.md` for why).

```js
const { applyOnSite, HumanPromptOtpProvider } = require("./src/ats");
const r = await applyOnSite(browser, job, profile, {
  dryRun: true,                 // default. Only `false` is live.
  otpProvider: new HumanPromptOtpProvider(),   // default if omitted
  log: (event, data) => {},
  // tab: <BrowserTab>          drive an already-open tab (use this to RESUME after an OTP)
  // target: "jobId=598826"     adopt one of the user's relay tabs (never navigated)
  // adapter: "adp"             force an adapter (fixtures)
  // otpChannel: "sms"|"email"  ADP delivery channel (default sms)
});
```

`job` = a `jobs` row (`{id, url, apply_url?, source}`); `profile` = parsed `profile.json`.

## Result

`{status, reason, evidence?, unanswered?, consents?, ...extras}`

| status | meaning |
|---|---|
| `applied` | a confirmation page was observed after the final Submit (live only) |
| `manual` | a human is needed; `reason` says why (below); `unanswered[]` holds the on-screen question text |
| `failed` | submit pressed but no confirmation / site validation errors / adapter exception |
| `skipped` | nothing sent: posting closed, already applied, **or a dry run finished** (`reason:'dry_run_complete'`, `wouldSubmit:{url, submitButton, fields[], prefilledBySite[], skipped[], consents[]}`) |

Extras: `adapter`, `dryRun`, `tabName` (tab to resume on), `trail[]` (ADP states visited),
`resumeToken` + `otpStage` (`otp_required`), `screenshot` paths under `logs/ats/` (override `ATS_EVIDENCE_DIR`).

`manual` reasons: `otp_required`, `otp_invalid`, `unanswered_questions`, `captcha_required`,
`validation_error`, `consent_not_allowed`, `unsupported_ats:<name>`, `embedded_ats:<name>`,
`unknown_ats`, `form_not_found`, `submit_button_missing`, `verification_ui_unrecognized`,
`unrecognized_page`, `stuck_<state>`, `forbidden_control`, `contact_*`, `profile_incomplete`.

Callers should map: `applied`→applied, `failed`→failed, `manual`→manual, `skipped`→skipped
(do NOT record a dry run as applied; `skipped/dry_run_complete` is the dry-run success value).

## OTP hand-off (ADP; Greenhouse post-submit codes)

No code is ever read from the candidate's phone or Gmail by default. `OtpProvider` is pluggable
(`authorizeSend(req)`, `getCode(req)`, `sent(req)`); `HumanPromptOtpProvider` is the default.
A dedicated job-application mailbox that the candidate configures can be added as another
provider with no adapter changes.

1. Run reaches the verification dialog and **stops before Send**:
   `manual / otp_required / otpStage:'ready_to_send' / resumeToken`.
2. Human: `bun src/ats/otp.js send <token>` (or `provider.allowSend(token)`, or `ATS_OTP_SEND=1`,
   or `new HumanPromptOtpProvider({interactive:true})` for a TTY y/N prompt), then re-run
   `applyOnSite` **with `tab:`** on the same tab. The adapter clicks Send once →
   `otp_required / 'awaiting_code'` (never re-sends on re-runs).
3. Human reads the code and runs `bun src/ats/otp.js code <token> 123456` (or `provider.supplyCode`,
   `ATS_OTP_CODE`, `interactive:true`, `waitMs`), re-runs → the adapter types the code and continues.
   A wrong code → `otp_invalid` (not retried).
Pending instructions: `data/otp/<token>.pending.json`; `bun src/ats/otp.js list`.

## Safety rules enforced in code

- Never clicks social sign-in, talent-community, alert/subscribe or e-sign controls (`lib.js#clickEl` refuses).
- Captcha/bot checks are only **detected** → `manual/captcha_required`. No solving, stealth, proxies.
- Answers only via `answers.js` allowlist: names/email/phone/location(+state/country derived from
  `profile.location`), resume, work authorization = Yes (only if `work_authorization` is
  `authorized_no_sponsor`, `authorized`, or `authorized_ead`), sponsorship = No (only if `requires_sponsorship === false`), EEO = decline,
  "how did you hear" = job.source when LinkedIn/Indeed. Anything else → `NeedsHuman(question)`.
  Optional unknown fields are left blank; required unknown fields stop the run **before** Next/Submit.
- Consents (`classifyConsent`) use the reviewed profile. Privacy/portal/accuracy clauses retain their existing rules. Employment background checks, employment drug screening, employment credit checks and future-vacancy application retention/contact require their separate `profile.answers.consents` grants. No grant means no new permission.
- Application-status texts remain undecided and unapproved. Marketing, job alerts, promotional texts, government/criminal-record releases and liability waivers remain forbidden. Mixed consent requires every obligation to be recognized and authorized. One allowed phrase never approves an unknown or forbidden remainder.
- Public Trust never held does not establish eligibility to obtain it. Interview history is employer-scoped, and prior employment must still exclude the resume's actual employers. No added degree or equivalency grants permission to choose an inaccurate dropdown credential.
- Every filled field is read back; a mismatch on a required field → manual.
- Final Submit is pressed only when `dryRun === false` and zero required fields are unanswered/unverified.

## Layout

`index.js` registry + `applyOnSite` · `adapters/{adp,greenhouse,lever,icims,smartrecruiters,ashby,generic}.js` ·
`singlepage.js` (Greenhouse/Lever runner) · `form.js` (DOM scan/fill/verify) · `answers.js` · `otp.js` ·
`flow.js`, `lib.js` shared · `RESEARCH.md`.
Add an ATS: create `adapters/<name>.js` exporting `{name, matches(url), apply(ctx)}` and add it to
`adapters` in `index.js` before `generic`.

## Tests

`test/fixtures/ats/run.js` (44 assertions) serves local fixtures (ADP flow, Greenhouse, Lever) and
asserts both results and what the page recorded (no code sent, nothing submitted in dry run, forbidden
controls untouched). It needs the omp `browser` global:
`const run = require("/abs/test/fixtures/ats/run.js"); await run(browser)` from an eval cell.

## Known limitations

- ADP email-code login was verified live: a user-supplied code opened the authenticated six-step application for C3 requisition 1175. No final application submission occurred.
- The live dry run verified the preselected Country against the profile-derived United States, then stopped for Address Line 1, City and ZIP. Site-prefilled address details require candidate confirmation and are not accepted as verified answers.
- OTP navigation matches Send and Verify specifically, not the contact page's background Continue. A Zip Code field plus a generic "verification process" notice must not be classified as an OTP prompt.
- Regression fixtures include the background Continue control and post-login Zip Code/verification notice; all 44 assertions passed after these fixes.
- Required saved/prefilled answers not derivable from the reviewed profile stop the flow. A focused regression verifies that a saved "99 years of SQL" answer cannot advance or submit.
- Workday: account forms are distinguished structurally. A Create Account form is not a password-reset form merely because it includes a Forgot Password link. Native email-provider choices are supported; failed Google navigation retains its actual blocker rather than searching Google's page for employer controls. Account creation/reset and password handling still require the authorized account flow, with per-site caps and no credential logging. These paths are fixture-verified, not a guarantee of employer acceptance.
- Taleo and other systems without an adapter return `unsupported_ats:<name>` or `unknown_ats`. The campaign worker may inspect and complete the real form with native browser controls under the same known-answer, consent, final-review, reservation, pacing and explicit-receipt requirements. Missing adapter support is not itself a need for human input, and a direct-browser review is not an adapter dry-run success.
- iCIMS: drives the embedded iframe and reuses authenticated sessions. A plain `Submit` on an
  explicitly numbered non-final step is navigation, including in dry runs; otherwise dry runs stop
  before Submit. In live runs, a changed Source attribution form or numbered intermediate form after
  Submit is filled through the existing answer allowlist, with at most four such continuations.
  The same form is never blindly submitted twice, even if validation changes its text. Thank-you
  text on an unfinished form is not a receipt; `applied` requires a receipt with no remaining
  application fields or navigation controls. Unknown required source answers stop the flow;
  optional Source Name/Email/Phone fields are not invented. Bare `Source`/`Source Channel CCC`
  controls are not implicitly mapped to a job board or employer career site. Local fixtures cover
  these stages, including the observed Fallon Source control shape (no progress marker, native
  optional fields, Source Name selector/text input). Employer-hosted wrappers do not match by host;
  pass `adapter: "icims"` or an adopted tab.
  Live Fallon applications can continue after Source into Veteran/Disability packets embedded in
  nested `iframe[name=icims_formFrame]`. This adapter follows the outer application frame only;
  nested packets, their application-only signatures, and employer-career-site Source mapping are
  not automated here. The Source continuation fixtures do not establish end-to-end live coverage.
- SmartRecruiters: real `oneclick-ui` pages render fields in shadow DOM, which `form.js` does not scan. On those pages the adapter returns `manual/form_not_found` with `shadowDom:true` instead of guessing. Light-DOM (classic) forms work. Fixture-verified only.
- Ashby: selectors come from third-party documentation, not a live page. The "Autofill from resume" dropzone also receives the resume. Fixture-verified only.
- `pacing.js`: per-host employer pacing (one application in flight, 90s+ between same-host loads, 6/host/day, 25/day overall, 30m/2h/12h/24h backoff after CAPTCHA or lockout). Wrap calls with `pacedApply(applyOnSite, browser, job, profile, opts)`. A refused call returns `manual/rate_limited` with `retryAt`; callers must record that as `deferred` (see `src/campaign.py`), never as a terminal `manual`.
- Greenhouse/Lever adapters have only been run against local fixtures. React-select / Google-Places
  typeahead widgets (e.g. Greenhouse "Location (City)", Lever "Current location") are handled by a best
  effort click-type-pick routine; if it cannot verify the value on a required field the result is `manual`.
- Required-ness is inferred from `required`/`aria-required`/trailing `*`; a site-enforced field without
  these markers shows up as a `validation_error` manual result instead.
- Sites that need an account password, date pickers, or file types other than a resume → manual.
- Employer pages that embed Greenhouse/Lever in an iframe are reported (`embedded_ats:*`), not driven.
- No rate limiting here (ratelimit.js is LinkedIn-only); callers should space employer applications.
