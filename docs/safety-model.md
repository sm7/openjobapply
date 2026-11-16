# Safety model

This document lists every protection in the project. Each section gives four parts.

- The rule.
- The code that enforces the rule.
- The failure that the rule prevents.
- One example.

Every statement comes from the code in this repository. The statement names the file and the function. A statement that the code does not prove says so.

## Terms

| Term | Meaning |
|---|---|
| worker | The AI process that opens the browser and fills forms. |
| supervisor | The Python process that starts, watches and stops the worker. |
| monitor | The dashboard web server. |
| dashboard | The web page that the monitor serves. |
| job | One row in the `jobs` table. |
| application | One submitted employer application. |
| attempt | One row in the `applications` table. |
| outcome | The result of an attempt. |
| applied job | A job with status `applied`. |
| manual job | A job with status `manual`. |
| skipped job | A job with status `skipped`. |
| queued job | A job with status `queued`. |
| reservation | A row in `campaign_reservations`. |
| receipt | The employer confirmation text. |
| standing answer | An answer in `profile.json` under `answers` that applies to every employer. |
| job answer | A row in `job_answers` that applies to one job. |

## How the protections fit together

The controller is the file `src/campaign.py`. It holds the ledger rules. The worker rules forbid a direct database write of an applied outcome. The worker calls the controller commands instead.

These commands matter for safety.

- `python3 src/campaign.py reserve JOB_ID` makes a reservation.
- `python3 src/campaign.py gate apply` asks the apply pacing gate for permission.
- `python3 src/campaign.py result` records an outcome. The command reads JSON from standard input.
- `python3 src/campaign.py requeue JOB_ID` returns a reviewed job to the queue.
- `python3 src/campaign.py reconcile JOB_ID` records a receipt that a person verified.

The order before a real Submit never changes.

1. The worker reaches the final review.
2. The worker reserves the job.
3. The worker passes the apply pacing gate.
4. The worker clicks Submit once.
5. The worker looks for a receipt.
6. The worker records the outcome.

A null cap never removes four other protections. The next list names them.

- The reservation.
- The duplicate guard.
- The receipt requirement.
- The action delays.

The last paragraph of `src/CAMPAIGN_WORKER.md` states this rule.

## 1. Reservation before Submit

### Rule

The worker must reserve a job before it clicks the final Submit. The reservation must happen after the final review is ready. A denied reservation means the worker must not submit.

### Enforcing code

- File `src/campaign.py`, function `reserve`.
- File `src/campaign.py`, function `record`.
- File `src/campaign.py`, function `connect`. It creates the `campaign_reservations` table.

The function `reserve` opens the transaction with `BEGIN IMMEDIATE`. The transaction takes the database write lock before any check. Two callers cannot pass the checks at the same time.

The function `reserve` runs these checks in order.

1. For a finite `target_total`, it counts distinct applied jobs and open reservations. If the sum reaches the target, it raises `target_reached_or_reserved`. A `null` target skips only this total-count check. All other checks still apply.
2. It loads the job row. An unknown job raises `unknown_job`.
3. It calls `identity_conflicts`. A conflict raises `already_sent_or_reserved`.
4. It refuses a job with status `manual`, `skipped` or `failed`. The error is `manual_review_or_explicit_requeue_required`.
5. It also refuses a job that has a reservation row and a status other than `queued`. The error is the same.
6. It checks `max_submissions_per_day` when the cap is not null. A full cap raises `daily_submission_limit`.

After the checks pass, the function deletes the old reservation rows for this job. It deletes only rows in state `manual`, `failed` or `skipped`. It inserts a new row in state `reserved`. It sets the job status to `applying`. It commits. It writes the event `reserved`.

The function `record` enforces the other half. An outcome of `applied` needs a reservation in state `reserved`. Without it, `record` raises `application_requires_active_reservation`.

The worker rules in `src/CAMPAIGN_WORKER.md` add one more rule. The worker must reserve only after the final review is ready. The worker must not reserve at these steps.

- Form open.
- Dry run.
- Next or Review.
- A one-time code step.

### Failure prevented

- Two workers or two rounds send the same job.
- The campaign sends more applications than a finite `target_total`.
- A crash after Submit leaves no trace. The reservation is the trace.

### Example

The target is 50. The ledger holds 49 applied jobs and one open reservation. The worker calls `reserve` for a second job. The sum is 50. The call raises `target_reached_or_reserved`. The worker must not submit. The test `test_pending_reservations_protect_exact_target_and_null_caps` in `test/test_campaign.py` covers this case.

## 2. The apply pacing gate

### Rule

The worker must call the pacing gate before every page load. The same rule applies to every job detail load and every final Submit. A gate that answers `allowed:false` means the worker must stop and wait. Delays and lockouts stay active when every cap is null.

### Enforcing code

- File `src/campaign.py`, function `gate`. It handles the kinds `load`, `detail` and `apply`.
- File `src/campaign.py`, function `budget`. It reports the next allowed time for `status`.
- File `src/campaign.py`, function `pacing`. It reads the pacing history.
- File `src/ats/pacing.js`, function `createPacer`. It handles employer hosts.

The function `gate` works as follows.

- It takes the lock file `data/campaign-pacing.lock`. Only one caller runs at a time.
- It reads the history from `data/ratelimit.json`.
- It drops events older than 24 hours.
- It denies the call when `lockedUntil` is in the future. The reason is `site_lockout`.
- It denies an `apply` call when the apply events reach `max_submissions_per_day`.
- It denies a `load` or `detail` call when the load events reach `max_loads_per_day`.
- It denies a `detail` call when the detail events of the last hour reach `max_details_per_hour`.
- It waits a random gap after the last event. The gap is 60 to 180 seconds for `apply`. The gap is 8 to 20 seconds for the other kinds.
- It adds a longer break of 45 to 120 seconds after 4 to 7 loads.
- It writes the new event and the new history file in one atomic replace.

A cap of zero disables the action. The function returns `retry_at` as null because time cannot restore the capacity. The functions `gate` and `budget` both handle this case. The test `test_zero_caps_deny_actions_without_crashing_status_or_claiming_a_reset_time` covers it.

The function `pacing` does not hide a corrupt file. A corrupt `data/ratelimit.json` makes `json.loads` raise an error. The gate never starts from an empty history after corruption.

The file `src/ats/pacing.js` adds a second gate for employer hosts. Its history file is `data/ats-pacing.json`.

- One application runs at a time. The function `pacedApply` queues each call behind the last call.
- The gap between page loads on the same host is at least 90 seconds plus up to 30 seconds of jitter.
- A captcha, lockout or blocked signal locks the host. The first lock lasts 30 minutes. The next locks last 2 hours, 12 hours and then 24 hours.
- A lock only moves forward. Only the function `unlock` clears it.
- A corrupt state file throws an error. The code never restarts from an empty state.
- The module reads two caps from `data/campaign.json` when it loads. They are `max_submissions_per_host_per_day` and `max_submissions_per_day`.

The file `src/ats/README.md` names fixed caps of 6 per host and 25 overall. The code in `src/ats/pacing.js` reads the caps from `data/campaign.json`. Trust the code.

When the gate denies an apply call, the worker records the outcome `deferred`. The reason text is `rate limited before Submit; not sent`. The function `record` then deletes the reservation and returns the job to status `queued`. A deferred outcome does not count against the daily cap.

### Failure prevented

- A burst of requests makes an employer or LinkedIn lock the account.
- A restart resets a counter or removes a lockout.
- A cap of zero crashes the status command or promises a time that never comes.

### Example

An employer site shows a captcha. The adapter returns `manual` with reason `captcha_required`. The function `pacedApply` calls `tripHost`. The host gets a 30 minute lock. A second trip on the same host gives a 2 hour lock.

## 3. The duplicate guard `identity_conflicts`

### Rule

One employer listing must never produce two applications. Three keys identify the same listing.

- The job id.
- The job `url`.
- The pair `source` and `source_id`.

A job that matches an applied job or an open reservation stays blocked.

### Enforcing code

- File `src/campaign.py`, function `identity_conflicts`.

The function runs one SQL query. It selects every job that matches the id, the url or the source pair. It joins the reservation row of each match. It returns two values.

- The first value is true when a match has status `applied`. It is also true when a match has an applied attempt or a reservation in state `applied`.
- The second value is true when a match has a reservation in state `reserved`.

The parameter `include_self` controls whether the job itself counts. The functions `reserve` and `requeue` use `include_self=True`. The function `reconcile` uses `include_self=False` and checks the job itself in its own code.

Three callers share the one function. This design keeps the rule in one place. The worker rules in `src/CAMPAIGN_WORKER.md` also tell the worker to check equal URLs and source ids before it opens a form.

### Failure prevented

- One listing appears under two job ids because LinkedIn or the employer gave it two rows. The worker could apply twice.
- A reviewer returns a job to the queue while an equal job already has a receipt.

### Example

Two job rows share one LinkedIn source id. One row has status `applied`. The other row has status `skipped`. A call to `requeue` for the skipped row raises `already_sent_or_reserved`. The test `test_a_job_already_applied_elsewhere_cannot_be_requeued` in `test/test_manual_jobs.py` uses two rows with the same url.

## 4. The employer receipt requirement for an applied outcome

### Rule

An application counts only when the worker saw an explicit receipt. These events never count.

- A timeout.
- An upload.
- A closed dialog.
- A reservation.

Without proof, the outcome is `manual`.

### Enforcing code

- File `src/campaign.py`, function `record`. It requires non-empty evidence and an active reservation for `applied`.
- File `src/campaign.py`, function `confirmed`. It counts distinct jobs with an applied attempt.
- File `src/ats/lib.js`, constant `CONFIRMATION`. It holds the receipt patterns.
- File `src/ats/flow.js`, function `awaitConfirmation`. It returns `applied` only when the page text matches `CONFIRMATION`.
- File `src/ats/adapters/icims.js`, the check near line 144. It accepts a receipt only when the page has no open fields. The page must also have no Submit control and no Next control.

The controller checks that the evidence is not empty. The controller does not read the evidence text. The adapters and the worker rules check the receipt text. For a direct browser application, `src/CAMPAIGN_WORKER.md` requires an explicit receipt. A sent message or a received message qualifies. Applicant statistics and a resume upload do not qualify.

The progress count uses `confirmed`. It counts `DISTINCT job_id` from `applications` where `result='applied'`. A reservation never raises the count.

### Failure prevented

- The campaign reports 50 applications when some were never sent.
- A thank-you text on an unfinished form counts as a receipt.
- The worker presses Submit twice on a form that did not confirm.

### Example

An iCIMS Submit button leads to a Source attribution form. The page shows a thank-you banner on the unfinished form. The adapter does not return `applied`. It fills the next step with known facts. The fixture test `test/fixtures/ats/icims.test.js` has the check `unfinished Source thank-you banner is NOT applied`. The adapter refuses to press Submit again on a form that it already submitted. It returns `no_confirmation`.

## 5. Quarantine of an unresolved submission after a worker crash

### Rule

A reservation that is still open when the worker ends is an unresolved submission. The controller records it as `manual`. The controller never counts it as applied. The controller never retries it.

### Enforcing code

- File `src/campaign.py`, function `recover`.
- File `src/campaign.py`, function `loop`. It calls `recover` at start and after each round.
- File `src/campaign.py`, function `recover_worker`. It cleans an orphan worker group first.
- File `src/campaign.py`, function `terminate_group`. It ends the worker process group.
- File `src/campaign.py`, function `reserve`. It refuses a manual job without an explicit requeue.

The function `recover` selects every reservation in state `reserved`. For each one it calls `record` with result `manual`. The reason says that the worker ended with the submission outcome unresolved. The reason also says to inspect employer evidence before any retry. The function `record` sets the reservation state to `manual` and the job status to `manual`.

The function `recover_worker` runs before `recover`. It checks the process identity of the old worker. It refuses to signal a reused process id. It raises an error to prevent an overlap.

After recovery, the reservation row stays in state `manual`. This row signals that the worker may have sent the job. The decision record `docs/decisions.md` explains why the code uses this row and not the reason text.

### Failure prevented

- A crash between Submit and the receipt leads to a second Submit in the next round.
- Two workers run at the same time after a supervisor crash.
- A job that the worker possibly sent counts as applied without proof.

### Example

The worker reserves a job and clicks Submit. The machine sleeps. The worker process dies before it records an outcome. The supervisor starts again. It calls `recover`. The job becomes `manual`. The reservation has state `manual`. The test `test_recovery_is_manual_idempotent_and_not_automatically_retryable` covers this path.

## 6. Absent evidence is never proof of absence

### Rule

No receipt does not mean no application. A person must show that the worker sent no application. The person must show this before a possibly sent job returns to the queue.

### Enforcing code

- File `src/manual_jobs.py`, function `sent_risk`.
- File `src/manual_jobs.py`, function `resubmit`.
- File `src/campaign.py`, function `requeue`. It needs the literal value `submission_verified_absent: true`.

The function `sent_risk` answers `possibly_sent` when the reservation state is `manual` or `failed`. It answers `not_sent` in all other cases. A reservation exists only after the worker finished the form and reserved a slot. A manual job with a reservation may have reached the employer.

The function `resubmit` refuses a possibly sent job unless the caller passes `nothing_sent_confirmed` as the value `True`. The check uses `is not True`. These values all fail.

- `False`.
- `None`.
- The text `'true'`.
- The number `1`.

The error code is `confirmation_required`.

The function `requeue` checks `data.get('submission_verified_absent') is not True`. It raises `requeue_requires_submission_verified_absent`.

The worker rules in `src/CAMPAIGN_WORKER.md` say the same. The worker must not attest absence because it captured no receipt. The worker must first inspect the employer receipt or the employer status page.

### Failure prevented

- A reviewer sees an empty receipt field and returns the job. The employer already has the application.
- A timeout during Submit leads to a blind second Submit.

### Example

A job has reservation state `manual`. A reviewer opens the Manual jobs tab. The tab shows `May have been sent`. The reviewer must check the employer account and tick the confirmation. Without the tick, the monitor answers with the code `confirmation_required`. The test `test_a_possibly_sent_job_is_not_requeued_without_explicit_confirmation` covers the four bad values.

## 7. The `requeue` command and its attestation

### Rule

A job returns to the queue only through `requeue`. The caller must give a reason, evidence and the attestation `submission_verified_absent: true`. A requeue is not permission to submit.

### Enforcing code

- File `src/campaign.py`, function `requeue`.
- File `src/manual_jobs.py`, function `resubmit`. It calls `requeue` for the dashboard.

The command is `python3 src/campaign.py requeue JOB_ID`. It reads one JSON object from standard input. The object needs these fields.

- `reason` is a non-empty string.
- `evidence` is a non-empty string.
- `submission_verified_absent` is the value `true`.

The function `requeue` works inside one `BEGIN IMMEDIATE` transaction.

1. It loads the job. An unknown job raises `unknown_job`.
2. It calls `identity_conflicts`. A conflict raises `already_sent_or_reserved`.
3. It refuses a job with a status other than `manual`, `skipped` or `failed`. The error is `requeue_requires_finished_non_applied_job`.
4. It inserts an attempt with result `deferred` and run id `campaign-requeue`. The evidence column holds a JSON object with the attestation and the evidence text.
5. It deletes the reservation rows in state `manual`, `failed` or `skipped` for the job.
6. It sets the job status to `queued`.
7. It saves job answers when the caller passes them. It deletes the old answers for the job first.
8. It commits. Any error rolls back all these steps together.

The function never deletes an old attempt. The history stays complete. The next Submit still needs a new `reserve` call, a pacing gate and a receipt.

### Failure prevented

- A requeue hides the old history.
- A requeue leaves job answers behind for a request that failed.
- A requeue returns a job that has an applied twin.

### Example

A manual job stopped at a required question before the worker reserved a slot. A reviewer runs `requeue` with the reason, the evidence and the attestation. The job becomes `queued`. The ledger holds a new `deferred` attempt. The worker must still reserve the job before it submits. The tests with names that start with `test_reviewed_requeue` cover this command.

## 8. The `reconcile` command

### Rule

A person can record a receipt that the person verified by hand. The command records the receipt. The command never submits anything.

### Enforcing code

- File `src/campaign.py`, function `reconcile`.

The command is `python3 src/campaign.py reconcile JOB_ID`. It reads one JSON object from standard input. The object needs these fields.

- `employer_confirmation_verified` is the value `true`.
- `reason` is a non-empty string.
- `evidence` is a non-empty string.

The function works inside one `BEGIN IMMEDIATE` transaction.

- It calls `identity_conflicts` with `include_self=False`. An applied or reserved twin raises `already_sent_or_reserved`.
- It refuses a job that has an open reservation.
- If the job already has an applied attempt, it records nothing new. It returns `already_recorded` as true. A replay is safe.
- Otherwise the job must have status `manual` or `failed`. Any other status raises `reconcile_requires_manual_or_failed_job`.
- The job must have a reservation in state `manual` or `failed`. A manual note alone does not prove an attempt. Without the reservation the function raises `reconcile_requires_previous_reserved_attempt`.
- It inserts an attempt with result `applied` and run id `campaign-reconcile`.
- It sets the reservation state to `applied` and the job status to `applied`.

The command exists and the tests cover it. The command never sends an application.

### Failure prevented

- A real application stays marked as manual and the campaign sends it again.
- A manual job with no real attempt becomes applied by mistake.
- A replay adds a second receipt.

### Example

A worker ended after Submit and `recover` marked the job as `manual`. A person opens the employer account and sees the application. The person runs `reconcile` with the receipt text. The job becomes `applied`. The count of confirmed jobs rises by one. The test `test_reconcile_records_real_receipts_without_new_submission_or_target_permission` covers this case.

## 9. Per-site account limits

### Rule

The account helper makes at most a fixed number of actions on one employer site. A call that is not a dry run needs two options in the call. The options are `dryRun:false` and `allowAccountCreation:true`.

### Enforcing code

- File `src/ats/account.js`, function `ensureAccount`. It holds the counters in `ctx.accountCaps`.
- File `src/ats/account.js`, functions `authorised` and `assertAuthorised`.
- File `src/ats/account.js`, function `gatedClick`. It is the only way to press account controls.

The counters belong to one site key and one context. The limits are in this table.

| Action | Limit | Error when full |
|---|---|---|
| Account creation | 1 | `account_flow_failed` with text about the creation cap |
| Password reset request | 1 | `account_flow_failed` |
| Sign-in attempt | 2 | `account_flow_failed` with text about the login cap |
| Verification wait | 1 | `account_flow_failed` with text about the wait cap |

The function `authorised` is true only when `ctx.dryRun === false` and `ctx.opts.allowAccountCreation === true`. In every other case the helper returns `manual` with reason `account_required`. The result carries the flags `wouldCreateAccount`, `wouldResetPassword` and `wouldLogin`. The helper types nothing, clicks nothing and reads no mailbox.

Four situations return `manual`.

- A captcha.
- An SMS or phone code.
- A lockout.
- An unclear page.

The helper never loops. It never guesses a password. It never solves a captcha.

The profile must grant the permission for an action before the worker enables it. The permissions are `account_creation`, `password_reset`, `google_sign_in` and `mailbox_verification`. An employer rule in `application_instructions` overrides a grant.

These limits are upper bounds. A limit is not a grant.

### Failure prevented

- An account loop locks the candidate out of an employer site.
- Many reset emails trigger a captcha.
- An account action runs in a dry run.

### Example

Two sign-in attempts fail on one site. A third attempt reaches the check `caps.login >= 2`. The helper stops with `account_flow_failed` and the evidence `login attempt cap (2) reached for this site`.

## 10. The ban on reading the credential file

### Rule

The worker must never touch the credential file. This ban covers four acts.

- Read the file.
- Open the file.
- Search the file.
- Copy the file.

The owner-only vault that `ensureAccount` uses is the one place for a password.

### Enforcing code

- File `src/campaign.py`, function `worker_prompt`. It removes the key `runtime` from the configuration that it shows to the worker. The credential path lives under `runtime`.
- File `src/campaign.py`, function `loop`. It passes the vault path to the worker process in the environment variable `ATS_CREDENTIALS_FILE`.
- File `src/ats/credentials.js`. It keeps the vault outside the project folder by default. Its functions `list` and `redact` never return a password.
- File `src/ats/account.js`, functions `fillCredential`, `redactResult` and `scrub`. They keep the password out of results and logs.
- File `src/campaign.py`, function `outcome_reason`. It hides secrets in every reason that the dashboard shows.
- File `jobapp.py`, command `credential`. It asks for the password at a hidden terminal prompt. It never accepts the password as an argument.
- File `src/settings.py`. The profile validator refuses unknown fields. The test `test_credentials_cannot_be_written_into_the_profile_or_echoed_in_errors` shows that a password cannot enter `profile.json`.

The ban is a written rule in `src/CAMPAIGN_WORKER.md`. The code hides the path and strips secrets. Not verified: a block by the system that stops the worker from access to the file. The worker runs as the same user as the owner of the file.

### Failure prevented

- A password appears in a log.
- A password appears in a database row or a screenshot name.
- A worker prompt leaks the vault location.

### Example

A reviewer reads the Manual jobs tab. The latest reason of a job contains a password in the form `password=abc`. The function `outcome_reason` shows `password=[redacted]`. The test `test_outcome_reason_retention_bounding_and_secret_redaction` covers this behavior.

## 11. The consent policy

### Rule

The worker ticks a consent box only when every obligation has explicit permission.
An allowed phrase does not authorize an unrelated obligation in the same box.

### Enforcing code

- File `src/ats/answers.js`, function `classifyConsent`. It checks the clause against reviewed profile grants.
- File `src/ats/answers.js`, function `resolveAnswer`. Its checkbox branch applies the verdict.
- File `src/ats/lib.js`, constant `FORBIDDEN_CLICK`. The function `clickEl` refuses these controls.
- File `data/consent_policy.md`. It is the written policy of the candidate.
- File `src/settings.py`, function `validate_campaign`. It does not allow the safety flags to be false.

The classifier reads the profile grants in `answers.consents`.
No grant means no permission for a new consent category.
The classifier keeps forbidden and unknown obligations separate from approved obligations.

The checkbox branch acts on the verdict.

- Verdict `allow`: the form engine ticks the box and logs the text in `logs/consents.log`.
- Verdict `forbid` on an optional box: the engine leaves the box empty.
- Verdict `forbid` on a required box: the code throws `NeedsHuman`, and the outcome is `manual`.
- Verdict `unknown` on a required box: the code throws `NeedsHuman`.
- Verdict `unknown` on an optional box: the engine leaves the box empty.
- An equal opportunity box: the engine ticks only a decline option.

The user approved these scopes on 2026-11-14:

- Employment background checks.
- Employment drug tests.
- Employment credit checks.
- Retention of the application and contact about future vacancies.

The last grant does not authorize the `marketing` category.
Application-status text messages remain undecided and unapproved.
These scopes remain forbidden:

- The `marketing` category and job alerts.
- Promotional texts.
- Government or criminal-record releases.
- Liability waivers.
- Arbitration and new non-compete agreements.
- Unrelated third-party data use.

Standard privacy notices and application accuracy statements keep their prior rules.
A check consent does not supply a factual criminal-history answer.
A check consent does not prove Public Trust eligibility.

The campaign flag `standard_application_privacy_and_accuracy_attestation` has the default `false`. The worker rules say the worker needs the profile permission before it ticks a privacy or accuracy statement.

### Failure prevented

- The candidate agrees to email offers without a decision.
- The candidate agrees to a check or a bound agreement without a reviewed grant.
- A combined box passes because its first words are harmless.

### Example

A box says that the candidate agrees to the privacy policy and to get email offers. The forbid pattern matches the words about email updates. The verdict is `forbid`. If the box is required, the application stops as `manual`. If the box is optional, the box stays empty.

## 12. Unknown required facts become manual outcomes

### Rule

The code never guesses a required fact. A question with no rule and no profile data becomes a `manual` outcome. The outcome holds the exact question.

### Enforcing code

- File `src/ats/answers.js`, class `NeedsHuman` and function `resolveAnswer`. The function `need` throws `NeedsHuman` when the profile has no value.
- File `src/ats/index.js`, function `applyOnSite`. It turns `NeedsHuman` into `manual` with reason `unanswered_questions`.
- File `src/ats/form.js` and the adapters. They stop before Next or Submit when a required field has no answer.
- File `src/settings.py`, function `validate_campaign`. Six settings must stay `true`. A change raises `this safety policy cannot be disabled`.
- File `src/CAMPAIGN_WORKER.md`. It tells the worker to record the exact text of an unknown question as manual.

Six settings need this protection.

- `skip_unknown_questions`.
- `skip_captcha`.
- `skip_sensitive_identity_fields`.
- `skip_separate_binding_legal_agreements`.
- `local_resume_only`.
- `no_further_questions`.

A `standing answer` applies to its own question only. The rules in `src/ats/answers.js` use narrow patterns on purpose. They answer only a Yes or No choice field. A free-text box stays with a human. A rule does not apply when the profile has no stated value.

A job answer applies to one job only. The worker uses it only when the form asks the same question. A job answer is data. It is never permission.

### Failure prevented

- The worker invents a license.
- The worker invents a clearance, a citizenship fact or a reference.
- A `standing answer` for one question leaks onto a neighbor question with a different true answer.

### Example

A form asks whether the candidate is a veteran. The code does not answer No. The question is an equal opportunity question. The code picks the decline option. A question about a military spouse has its own rule. The test file `test/fixtures/ats/answers_standing.test.js` checks that the two rules stay apart. It also checks that an unconfirmed answer gives `NEEDS_HUMAN`.

## 13. The dashboard write guard

### Rule

The dashboard changes state through one route only. Every route first checks the `Host` header. The write route accepts a request only when four checks pass.

- The host check.
- The origin check.
- The content type check.
- The token check.

### Enforcing code

- File `src/monitor_security.py`, function `check_host`. Every route calls it, reads included.
- File `src/monitor_security.py`, function `check_write`.
- File `src/monitor_security.py`, function `load_token`.
- File `src/monitor_security.py`, function `read_body`.
- File `src/campaign_monitor.py`, method `do_POST` and function `make_server`.

The function `check_host` rejects any request whose `Host` header is not `127.0.0.1:PORT` or `localhost:PORT`. This rule covers these routes:

- The page `/`.
- The status route.
- The logs route.
- The manual route.
- The applied route.

The function `check_write` calls `check_host`. Then it rejects a request in these cases.

| Check | Status |
|---|---|
| The `Host` header is not `127.0.0.1:PORT` or `localhost:PORT` | 403 |
| An `Origin` header is present and is not the dashboard | 403 |
| The content type is not `application/json` | 415 |
| The `X-Monitor-Token` header is absent or wrong | 403 |

The function `read_body` rejects three more cases.

| Case | Status |
|---|---|
| No `Content-Length` header | 411 |
| A negative length | 400 |
| A body above 64 KiB | 413 |

The token comparison uses `hmac.compare_digest`. The function `load_token` creates the token file `data/monitor-token` with mode `0600` on first use. It uses `O_EXCL`, so two processes cannot create different tokens. The dashboard page embeds the token. The page has a Content Security Policy that allows only its own script by hash.

These methods get status 405.

- PUT and PATCH.
- DELETE and OPTIONS.
- CONNECT, TRACE and HEAD.

### Failure prevented

- A web page in the browser of the user posts to the local dashboard.
- A DNS rebind attack reaches the dashboard under another host name. It cannot read the token from the page, because `check_host` refuses the read.
- Another local program requeues jobs without the token.

### Example

A forged request carries the header `Origin: https://evil.example`. The function `check_write` raises `Rejected` with status 403. The database does not change. The test `test_requests_that_are_not_the_dashboards_own_page_change_nothing` in `test/test_manual_jobs.py` sends seven bad requests. It compares the database before and after.

A second example covers the read route. A page asks for `/` with the header `Host: evil.example`. The function `check_host` returns status 403. The body holds no token. The test `test_a_page_requested_under_a_foreign_host_never_receives_the_token_or_any_data` sends 25 such requests. An earlier version of the monitor failed all 25.

## 14. Links on the Manual jobs tab

### Rule

An address from the scraper never becomes a link on the page until the server has checked it. The page checks it a second time. The page sets `href` in one place only.

### Enforcing code

- File `src/manual_jobs.py`, function `safe_link`. It accepts only `http` and `https` addresses that have a host name. It refuses a user name or password in the address. It refuses every code point from 0 to 32. This range includes the space and the control characters. It also refuses the code point 127 and the range from 128 to 159. It refuses a port outside 0 to 65535. It refuses an address with more than 2000 characters. It refuses a value that is not text.
- File `src/manual_jobs.py`, function `list_manual`. It sends `null` in place of an address that fails the check. It also sends `null` for the `apply_url` when it equals the `url`.
- File `src/manual_jobs_ui.py`, function `manualLink`. The title link, the employer link and the dialog links all use it. It parses the address with `new URL`. It refuses a scheme other than `http` and `https`. It sets `target` to `_blank` and `rel` to `noopener noreferrer`.
- File `src/campaign_monitor.py`. It sends the header `Referrer-Policy: no-referrer` with every page.

### Failure prevented

- A scraped value such as `javascript:` or `data:` runs code in the dashboard page. The page holds the write token, so this code could requeue jobs.
- An address such as `https://www.linkedin.com@other.example/` looks like LinkedIn but goes to another host.
- The new tab uses `window.opener` to reach the dashboard page.
- The employer site reads the local address of the dashboard from the `Referer` header.

### Example

The scraper stores `javascript:alert(1)` as the `url` of a job. The function `safe_link` returns `None`. The tab shows the job with no link. The test `test_a_poisoned_database_row_never_reaches_the_browser_as_a_link` in `test/test_manual_jobs.py` stores five rows. It checks the exact list that the server returns. The five rows hold these addresses:

- A good address and a good employer address.
- A hostile address and a hostile employer address.
- The same address for the `url` and the `apply_url`.
- A good address and no employer address.
- A good address and a hostile employer address.

### Limit

A safe address can still lead to a page that is not trustworthy. The scraper chose that page. The check proves the form of the address. It does not prove that the page is safe. The author checked the form only.

### Evidence

On 2026-11-13, the author changed the code in 12 ways in temporary copies. Each change made one named test fail. The changes were these:

- One change accepted every scheme.
- One change allowed a user name in the address.
- One change allowed control characters.
- One change allowed an address with no host.
- One change removed the length limit.
- One change removed the port check.
- Two changes sent the raw address.
- Two changes removed a protection in the page.
- One change added a second place that writes HTML.
- One change removed the `Referrer-Policy` header.

The first run of this check was invalid, because the test module did not load. The author found this. The author ran the check again with a baseline that passed.

## 15. Privacy of the Applied jobs tab

### Rule

The page of the Applied jobs tab never receives the whole evidence text of an attempt. Evidence can hold contact data. The page receives one bounded clause with contact data removed. It also receives the name of one screenshot file. The name is never a path. The route only reads, and a foreign `Host` header gets the status 403.

### Enforcing code

- File `src/applied_jobs.py`, function `confirmation_sentence`. It keeps one clause that starts at a phrase from a fixed list. It passes the clause through `campaign.outcome_reason`. That function replaces URLs, email addresses and secrets. The function then replaces a phone number with `[redacted phone]`. It cuts the clause to 200 characters. It returns `None` when no phrase matches.
- File `src/applied_jobs.py`, function `receipt_file`. It accepts only a plain file name that follows `logs/campaign/receipts/`. The name has 1 to 120 safe characters. The extension is `.webp`, `.png`, `.jpg` or `.jpeg`. A path with `..` or a subfolder gives no name.
- File `src/applied_jobs.py`, function `list_applied`. It builds a fixed set of fields. The set has no field for the evidence or the reason. It checks only that the named file exists. It sends both addresses through `safe_link`.
- File `src/campaign_monitor.py`, function `read_applied`. It opens the database read only with `PRAGMA query_only`.
- File `src/campaign_monitor.py`, method `do_GET`. It calls `check_host` first and returns a fixed text with status 503 when the read fails.
- File `src/applied_jobs_ui.py`. The script writes each value with `textContent`. The function `appliedLink` checks the scheme a second time.

### Failure prevented

- A phone number or an email address from the evidence reaches the page.
- A password or a token from the evidence reaches the page.
- A path outside `logs/campaign/receipts` reaches the page.
- A page under a foreign host name reads the list of applied jobs.
- A hostile address becomes a link.

### Example

A job has the status `applied`. Its evidence holds four private values:

- A contact email.
- A phone number.
- A street.
- The text `password=hunter2`.

The evidence also holds the clause `Your application was sent to Employer!`. The test `test_the_page_never_receives_contact_details_or_the_whole_evidence` in `test/test_applied_jobs.py` lists the job. It checks that none of these values appears in the result. It also checks that the result has no `evidence` field.

### Limit

The redaction covers only the patterns in the code. The phone rule matches a number with separators in the form of three, three and four digits. A number without separators passes through. A name or a street inside the clause also passes through. The author did not test more formats.

The monitor does not serve screenshot files. The page shows a file name and cannot open the file.

## Known limits of the code

The code does not enforce every rule. Some rules live only in the worker rules in `src/CAMPAIGN_WORKER.md`.

- The file `src/dbcli.py` has a `record` command. Until 2026-11-13 it accepted the result `applied` with no reservation, and the worker rules were the only block. A review of this documentation found the gap. The command now refuses `applied` and exits with an error. The file `src/run.js` records a would-be `applied` outcome as `manual` with its evidence instead. A query on 2026-11-13 found two applied rows with no applied reservation. These two rows have the run ids `run-test-001` and `adp-submit-confirmed`. Both predate the controller and both have employer receipt text. No other applied row lacks a reservation, so the ledger shows no use of this path since the controller started. This query cannot show a use that left no row. The tests in `test/test_dbcli.py` cover the refusal. The worker can still run `src/dbcli.py` through the `bash` tool, but that command can no longer record `applied`.
- The function `record` in `src/campaign.py` checks that the evidence is not empty. It does not check that the text is a receipt.
- The ban on the credential file has no block by the system. See section 10.

These limits mean the worker rules are part of the safety model. A change to the worker rules changes the safety model.

## What this document does not verify

- The document does not verify that the receipt text matches the real page. The adapters and the worker check the text.
- The document does not verify a block by the system on the credential file.
- The document does not verify behavior on Linux. The README says nobody ran native Linux execution on this machine.
- The document does not verify that the redaction in section 15 covers every format of contact data. It lists only the patterns that the code holds.
