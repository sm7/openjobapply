# OpenJobApply

A local, profile-driven job-application assistant. A Python CLI supervises one AI application worker and a local dashboard. Applications use your signed-in Chrome, a reviewed candidate profile, a local PDF resume, and persistent SQLite history. Nothing runs in the cloud.

> **Responsible use.** `start` and `service start` can submit **real** job applications on your behalf. Setup, configuration, diagnostics and status do not submit anything. Many job sites forbid automated applications in their terms of service, and automation can get an account restricted or banned. You are responsible for every application sent, for the accuracy of your profile, and for following each site's terms and the law. Review the dry-run output before you enable live submission. The software is provided as is, with no warranty.

## Requirements

- **macOS or Linux, Python 3.10+**, including SQLite and normal POSIX process tools (`ps`). No Python packages need installing.
- **[Oh My Pi (`omp`)](https://ohmypi.xyz/)** on PATH, authenticated to your model provider. The local installation was checked with omp 18.19.0; `doctor` checks the required unattended-worker flags.
- Chrome with the **omp browser-relay extension**, signed in to the appropriate job sites. The relay server must be running on its default local port, **9224**.
- A readable PDF resume and reviewed profile. Do not copy another candidate's identity, answers, permissions or application history.

Install/authenticate omp using its own tooling, then set up Chrome once:

```sh
omp login
omp browser-relay install
```

Load the unpacked extension in `chrome://extensions` using the directory printed by the installer. Run the relay and keep it available:

```sh
omp browser-relay
```

Applications need an awake machine, a usable logged-in desktop session, Chrome and the relay. This is not a cloud service. Without the relay the supervisor waits safely; provider-authentication failures are reported rather than treated as submitted applications.

## Setup

Run these commands from the project folder, or invoke `jobapp.py` by its absolute path. Paths inside configuration are resolved relative to the project, not the shell's current directory; `~` is expanded for the local user.

Copy the example profile and fill in **every** field you can confirm. `profile.json` is the single source of truth for your identity, answers and permissions, and it is gitignored:

```sh
cp profile.example.json profile.json
python3 jobapp.py setup --resume /path/to/resume.pdf
python3 jobapp.py config validate
python3 jobapp.py doctor
python3 jobapp.py status
```

Without a filled-in profile, the project stays **incomplete and non-submittable**. Before starting, provide real full/first/last names, email, phone, country, location, work-authorization description, explicit sponsorship answer, local PDF and nonempty search queries. Review the remaining job preferences, experience, standing answers and permissions as well. `null` means unknown: a form that asks for an unknown fact stops for you. Missing facts are never invented.

Setup creates missing files/directories only. It refuses to overwrite a different candidate or approved resume, and does not reset the database, receipts, pacing history or platform lockouts. Keep a separate project copy/history for each candidate.

## Run

### Foreground: macOS or Linux

```sh
python3 jobapp.py start
```

The supervisor and dashboard run in this terminal. Ctrl-C performs bounded worker cleanup; interrupted submissions remain quarantined until evidence is reviewed. `start --no-dashboard` runs only the supervisor. Starting again while its singleton lock is held does not launch a second worker or restart the existing one.

Dashboard: **http://127.0.0.1:8765** by default, loopback-only. **Overview** and **Live logs** are read-only and distinguish actual worker activity from service heartbeat; liveness is not proof of a submission. **Manual jobs** is the one tab that can change state; see below.

From another terminal:

```sh
python3 jobapp.py status --json
python3 jobapp.py stop
```

`stop` requests a graceful application stop, retaining all history. A separately installed dashboard stays available; a foreground dashboard exits with its foreground supervisor.

### Background user service

```sh
python3 jobapp.py service install
python3 jobapp.py service start
python3 jobapp.py service status
```

- **macOS:** user LaunchAgents under `~/Library/LaunchAgents`.
- **Linux:** `systemd --user` units under `~/.config/systemd/user`. A functioning systemd user session is required; otherwise use foreground `start`.

Install writes/registers owned definitions but does not start applications. Start is idempotent and never restarts an already-active application. The application service restarts on failure, not on successful completion or explicit stop. The dashboard is supervised independently. No `sudo` is used.

```sh
python3 jobapp.py service stop
python3 jobapp.py service uninstall
```

These stop both owned services; uninstall also removes only their owned definitions, not project data. Foreign service files/loaded jobs are refused. Use a distinct `campaign.runtime.service_name` for each separately deployed project.

After deliberately changing a service definition, update it explicitly:

```sh
python3 jobapp.py service install --replace
```

This does not restart a live worker. A dashboard-port change needs an explicit stop/start to replace the loaded monitor. Updating Python source also requires a supervisor restart; do that at an idle boundary, never during an uncertain Submit. Do not move/delete a project directory while its installed services are running.

## Configure

Two files remain authoritative:

- **`profile.json`** — candidate identity, resume, geography, salary, search queries, confirmed answers and permissions.
- **`data/campaign.json`** — submission target, model, caps, priorities, recovery batch and runtime settings.

```sh
python3 jobapp.py config show profile
python3 jobapp.py config show campaign
python3 jobapp.py config set profile.preferences.min_salary_usd 75000
python3 jobapp.py config set profile.preferences.salary_expectation_usd 90000
python3 jobapp.py config set profile.search.queries '["healthcare data analyst", "population health analyst"]'
python3 jobapp.py config set campaign.target_total null
python3 jobapp.py config set campaign.worker_model 'provider/model-id'
python3 jobapp.py config set campaign.max_submissions_per_day null
python3 jobapp.py config validate
```

Values accept JSON numbers, booleans, arrays, objects and `null`; unquoted non-JSON values are strings. Writes are validated, serialized and atomically replaced. Invalid changes leave the previous file intact. Updating either salary-expectation field through the CLI updates its paired preference/answer in the same file, avoiding conflicting form answers.

Known salary ranges use their **midpoint**, not the maximum. Unknown salary follows `preferences.unknown_salary` (`allow` or `reject`). Candidate-specific exceptions belong in reviewed `application_instructions`, scoped to an exact job. Configuration cannot waive receipt, duplicate, reservation, CAPTCHA or sensitive-data protections. Only automatic submission mode is implemented; unsupported mode/range-rule values are rejected rather than silently ignored.

Runtime settings live under `campaign.runtime`:

| Setting | Purpose |
|---|---|
| `omp_executable` | PATH name or explicit executable path |
| `worker_timeout_seconds` | Both worker CLI time limit and supervisor deadline |
| `success_pause_seconds` | Between-round pause after a confirmed submission |
| `idle_pause_seconds` | Between-round pause without a confirmed submission |
| `dashboard_port` | Local dashboard port |
| `service_name` | Unique local service namespace |
| `credentials_file` | Owner-only employer vault location; never its contents |

```sh
python3 jobapp.py config set campaign.runtime.worker_timeout_seconds 1200
python3 jobapp.py config set campaign.runtime.idle_pause_seconds 300
python3 jobapp.py config set campaign.runtime.dashboard_port 8765
```

An upgraded supervisor reloads model, executable, deadline, profile and credential-path settings at each worker round. Between-round waits re-read their configured duration. A running form is not interrupted to apply configuration changes. Numeric caps use **`null` for unlimited** and **zero to disable** the action; target zero closes the campaign. **`campaign.target_total: null` enables continuous operation without a total application cap**; status reports `target` and `remaining` as `null` and `complete` as `false`. Finite nonnegative integer targets remain supported (default: `50`). Action spacing and real platform lockouts always remain enforced. Some host/run policies are worker/ATS-enforced, not additional global controller counters.

### Permissions and secrets

New profiles grant no account/reset/Google/mailbox permissions by default. Grant only actions actually authorized by the candidate:

```sh
python3 jobapp.py config set profile.application_permissions.account_creation true
python3 jobapp.py config set profile.application_permissions.password_reset true
python3 jobapp.py config set profile.application_permissions.google_sign_in true
python3 jobapp.py config set profile.application_permissions.mailbox_verification true
```

The current account helper couples creation and reset, so that helper path requires **both** grants. Employer-specific restrictions in `application_instructions` still override general grants. Privacy/accuracy attestations require `campaign.standard_application_privacy_and_accuracy_attestation`; separate binding agreements and background/drug/credit checks remain excluded.

Keep passwords, provider keys and tokens **out of configuration, command arguments, logs and chat**. Store an employer password through the hidden terminal prompt:

```sh
python3 jobapp.py credential employer.example --email candidate@example.com
```

This uses the configured vault path and never accepts a password argument. Model-provider authentication remains in omp's own auth storage. `doctor` does not inspect the vault or test provider authentication with a paid request. Its warnings identify checks not performed; `doctor --offline --json` skips the relay connection check too.

Profile, resumes, state and logs are private and gitignored. When moving computers, review configuration and authenticate the new local browser/provider; do not publish or blindly copy auth stores.

## Recovery and evidence

SQLite history, reservations, pacing history and explicit employer receipts survive restarts. The supervisor enforces a singleton worker, bounded lifetime and owned-descendant cleanup. A timeout/upload/reservation is never counted as a successful submission. Unknown required facts, actual CAPTCHA/MFA and ambiguous legal eligibility are recorded blockers, not guesses.

Operator-reviewed recovery uses the existing controller:

- `python3 src/campaign.py requeue JOB_ID`: JSON stdin with nonempty `reason`, `evidence` and `submission_verified_absent: true`. Use only after a documented pre-submission failure or inspected unsent draft/history establishes no application was sent. Applied/active equivalents are rejected and old history retained.
- `python3 src/campaign.py reconcile JOB_ID`: JSON stdin with `employer_confirmation_verified: true`, `reason` and actual receipt `evidence`. Records a verified historical receipt for a manual/failed attempt with a finished reservation **without resending**; same-job replay is idempotent.

Neither command bypasses submission safety. `manual_recovery_job_ids` and its request timestamp define a finite reviewed batch, rather than endless retries of genuine blockers. Recovery events are visible in Live logs without exposing raw receipt evidence, credentials or private worker output.

### Manual jobs tab

The dashboard's **Manual jobs** tab lists every job the campaign could not finish, with the reason it stopped (sanitized: URLs, emails and secrets are redacted). Each job shows one of two states:

- **Not submitted:** the worker stopped before reserving a submit slot, so no final Submit was attempted.
- **May have been submitted:** a submit slot had been reserved, so an application might have reached the employer. Missing evidence is not proof of absence.

Choose **Add answers and resubmit**, enter each question as the employer words it with your answer, and save. The job is queued and the answers are stored with it **in the same database transaction**, so a rejected request never leaves orphaned answers. A job that may have been submitted additionally requires you to tick a confirmation that you checked the employer account and no application was received.

Saved answers are data for the worker, delivered each round only for jobs still waiting to be applied to. The worker uses one only for that exact job and only when a form asks that exact question. They never override consent limits or the duplicate, reservation, pacing and receipt protections, and a question you did not answer still stops the job with its exact wording, so you can add it. Resubmitting never submits an application by itself.

**Answers are not code changes.** Saving an answer here does not generalize it to other employers, and does not change the answer engine. A recurring question is better handled as a reviewed standing answer in `profile.json`, with a rule and test in `src/ats/answers.js`. Automated, reviewed code updates from a resubmission are intentionally not implemented yet.

Every dashboard route, reads included, first requires the request's Host to be the dashboard's own loopback address, which defeats DNS rebinding. Without that, a hostile web page could fetch the dashboard under its own hostname, copy the write token out of the page, and use it. This is the only route that changes state. It additionally requires any Origin to be the dashboard itself, a JSON body, and the per-install secret in `data/monitor-token` (mode 0600, created on first start, never logged, embedded only in the dashboard page). Everything else is refused before the database is touched.

Code layout: `src/manual_jobs.py` holds the rules, `src/monitor_security.py` the request guard, `src/manual_jobs_ui.py` the tab's markup/styles/script, and `src/campaign_monitor.py` only routes. `campaign.requeue` stays the single place a job returns to the queue.

## Verification

```sh
python3 -m unittest discover -s test -p 'test_*.py'
```

Tests use isolated state and simulated workers/managers, not real applications. The Python tests need only the standard library. The JavaScript fixture tests under `test/fixtures/ats/` need [Bun](https://bun.sh); the browser fixtures also need the `omp` `browser` global and are run from an `omp` eval cell, as described in each test's header. All test data is fictional and comes from `test/fixtures/profile.fixture.json`. Linux service lifecycle/escaping is fixture-tested; native Linux execution has not been exercised by the maintainers.
