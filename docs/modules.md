# Modules

This document has one section for each source file.
Each section has six parts.

- The file path.
- The single responsibility.
- The public functions or commands.
- What the file reads.
- What the file writes.
- What must never move into the file.

The facts come from a read of each file on 2026-11-13.
The sections for these files come from a read on 2026-11-15:

- `src/campaign_monitor.py`
- `src/applied_jobs.py`
- `src/applied_jobs_ui.py`
- `src/manual_jobs.py`
- `src/manual_jobs_ui.py`

Python files are in `src/`, `scripts/` and the project root.

## `src/verify_pending.js`

This module exports `verifyNext`.
The worker calls `verifyNext({browser})` from Eval JavaScript.
The helper selects one LinkedIn job with no application history.
It uses the campaign detail gate before the page load.
It reuses `detailOne` from `src/discover_linkedin.js`.
It saves the observed fields through `campaign.py save-description`.
It closes only its own browser tab.

The helper returns one of these statuses:

- `verified`: the controller saved the observed fields.
- `no-job`: no description task remains.
- `blocked`: the fetch could not finish.

Stop description work for the round on `blocked` or `cleanup_error`.
Neither status means that an application failed.
The helper never submits a form.

## `src/campaign.py`

### Responsibility

This file is the controller of the campaign. It owns six parts.

- The supervisor.
- The reservations.
- The outcomes.
- The pacing gate.
- The activity report.
- The worker process control.


### Public commands

The command line is `python3 src/campaign.py COMMAND [ARGUMENT]`.

| Command | Function |
|---|---|
| `status` | `status` |
| `activity` | `report_activity` |
| `gate load`, `gate detail`, `gate apply` | `gate` |
| `reserve JOB_ID` | `reserve` |
| `requeue JOB_ID` | `requeue` |
| `reconcile JOB_ID` | `reconcile` |
| `next-verify` | `next_verify` selects one job with no application history. |
| `save-description JOB_ID` | `save_description` saves observed fields and applies the scorer. |
| `result` | `record` |
| `loop` | `loop` |

### Other public functions

| Function | Use |
|---|---|
| `connect` | Opens the database and creates `campaign_reservations` and `job_answers`. |
| `locked` | A context manager that takes a file lock. |
| `atomic_json` | Writes a JSON file with a temporary file and a rename. |
| `event` | Appends one metadata event to `logs/campaign/events.jsonl`. |
| `runtime_status` | Reads the runtime file and computes `stale` and `no_progress`. |
| `heartbeat` | Writes `data/campaign-runtime.json`. |
| `budget` | Computes the pacing counts and `retry_at`. |
| `outcome_reason` | Removes URLs, email addresses and secrets from a reason. |
| `reviewed_answers` | Returns the job answers for jobs with status `queued` or `applying`. |
| `worker_prompt` | Builds the prompt for the worker. |
| `process_table`, `WorkerTree`, `terminate_group`, `recover_worker` | Process control for the worker. |

### Reads

- `data/campaign.json` through `settings.load_campaign`.
- `profile.json` through `settings.load_profile`.
- `data/jobs.db`.
- `data/campaign-runtime.json`, `data/campaign-activity.json` and `data/ratelimit.json`.
- `src/CAMPAIGN_WORKER.md`.
- The relay address `http://127.0.0.1:9224/json/version`.

### Writes

- All tables that `campaign.connect` creates.
- The `jobs` and `applications` tables.
- `data/campaign-runtime.json`, `data/campaign-activity.json` and `data/ratelimit.json`.
- `logs/campaign/events.jsonl`, `logs/campaign/rounds.jsonl` and the round log files.

### Must never move into this file

- Page markup or HTTP routes. They belong to `src/campaign_monitor.py`.
- Scoring rules. They belong to `src/score.py`.
- Form answers or browser steps. They belong to the worker and to `src/ats/`.
- Validation of the configuration files. It belongs to `src/settings.py`.

## `src/campaign_monitor.py`

### Responsibility

This file serves the dashboard on loopback.
It uses only the Python standard library.
It also holds the page frame, the tab bar and the Overview tab. These parts have markup, a style and a script.

### Public functions and commands

| Name | Use |
|---|---|
| `python3 src/campaign_monitor.py --port PORT` | Runs the server. The default port is 8765. |
| `make_server` | Returns an unstarted server on `127.0.0.1`. |
| `read_status` | Returns a safe projection of `campaign.status`. |
| `read_manual` | Returns the manual jobs from a read-only connection. |
| `read_applied` | Returns `{jobs, summary}` for the Applied jobs tab from a read-only connection. |

### Routes

| Route | Effect |
|---|---|
| `GET /` | Serves the page and inserts the write token. |
| `GET /api/status` | Returns the status projection. |
| `GET /api/logs` | Returns the log tail from `campaign_logs.read_logs`. |
| `GET /api/applied` | Returns the applied jobs with their confirmation detail and a summary. |
| `GET /api/manual` | Returns `{jobs, groups}`. The jobs are the manual jobs. The groups come from `manual_jobs.group_counts`. |
| `POST /api/manual/resubmit` | Saves reviewed answers and requeues one manual job. |

All other methods return status 405.
Other paths return 404.
Each response has a `Content-Security-Policy` header with a hash of the page script.

### The Overview tab in this file

The script of this file draws the Overview tab from `GET /api/status`. The tab has these parts:

- A status band with the state in words, a dot and the button `Needs you: N manual jobs`. The button opens the Manual jobs tab.
- A Progress card with the count of confirmed applications as the big number.
- A block `Jobs by current status`.
- The line `Last confirmed application`. It uses the newest row with the result `applied` among the 20 latest actions.
- A table of the latest 20 job actions. It clamps each reason to two lines and keeps the full text in a tooltip.
- The closed fold-outs `Attempt history` and `Technical details`.

The tab router in this file knows four tabs. They are `overview`, `applied`, `manual` and `logs`. It reads only the part of the address fragment before the first `/`.

### Reads

- `data/jobs.db`, through a read-only connection with `PRAGMA query_only`.
- `data/monitor-token`, through `monitor_security.load_token`.

### Writes

- Only the route `POST /api/manual/resubmit` writes. It calls `manual_jobs.resubmit` on a normal connection.

### Must never move into this file

- Rules for a requeue. They belong to `src/manual_jobs.py`.
- Checks of write requests. They belong to `src/monitor_security.py`.
- Markup of the manual tab. It belongs to `src/manual_jobs_ui.py`.
- Markup of the applied tab. It belongs to `src/applied_jobs_ui.py`.
- The rules that list applied jobs. They belong to `src/applied_jobs.py`.
- The log projection. It belongs to `src/campaign_logs.py`.
- Any code that starts or stops the worker.

## `src/campaign_logs.py`

### Responsibility

This file makes a bounded, read-only view of the events log.

### Public function

The function `read_logs(log_path=None, db_path=None)` returns a dictionary with four keys.

- `entries`
- `truncated`
- `source`
- `served_at`


### Limits

- The file reads at most the last 256 KiB.
- The file keeps at most the last 200 lines.
- The file drops a record longer than 16 KiB.
- The file keeps only known events with a fixed label.

### Reads

- `logs/campaign/events.jsonl`.
- `data/jobs.db` in read-only mode. It reads company, title and the latest reason.

### Writes

This file writes nothing.

### Must never move into this file

- Worker output or live campaign state.
- Free text from an event.
- Any write to the database.

## `src/manual_jobs.py`

### Responsibility

This file holds the domain rules for the review of manual jobs.
It has no HTTP, HTML or process code.
It never submits an application.

### Public names

| Name | Use |
|---|---|
| `list_manual` | Returns each manual job with its latest reason, `sent_risk`, `posting_url`, `apply_url`, `group`, `headline` and `questions`. |
| `GROUPS` | The tuple of the six groups in display order. Each entry has a key, a label and a hint. |
| `classify` | Returns the group key for a reason and a risk. The group is a best guess from keyword rules. It never gates a requeue. |
| `quoted_questions` | Returns the complete questions that a reason quotes. It skips a question with elided text. |
| `group_counts` | Returns all six groups in fixed order with a count each. The count can be zero. |
| `safe_link` | Returns an address only when it is safe to use as a link. Otherwise it returns `None`. |
| `sent_risk` | Returns `possibly_sent` for a reservation state `manual` or `failed`. Otherwise it returns `not_sent`. |
| `clean_answers` | Checks the rows of answers and returns a dictionary of question and answer. |
| `resubmit` | Saves the answers and requeues the job in one transaction. |
| `ManualJobError` | The error with a stable `code`. |

The limits are 25 answers, 500 characters for a question and 2000 characters for an answer.

The first group is `check_employer`. A job with risk `possibly_sent` always gets it. The code gives each other job one of five more groups. The function `group_counts` lists all of them.

### Rules of `resubmit`

1. Reject a job that does not exist.
2. Reject a job that does not have status `manual`.
3. Reject a job with risk `possibly_sent` unless the reviewer confirms that no application arrived.
4. Call `campaign.requeue` with `submission_verified_absent` set to `true`.

### Reads

- The tables `jobs`, `applications`, `campaign_reservations` and `job_answers`.

### Writes

- The table `job_answers`, through `campaign.requeue`.
- The table `applications` and the status of the job, through `campaign.requeue`.

### Must never move into this file

- HTTP, HTML or process logic.
- A submit of an application.
- A bypass of the reservation, the gate or the receipt rule.

## `src/manual_jobs_ui.py`

### Responsibility

This file holds the style, the markup and the script of the Manual jobs tab.
The tab has chips and a search box. It also has group sections, the answer dialog and the Dismiss button.
The server sends each value as data. The script writes each value with `textContent`. The function `manualLink` is the one place in this tab that sets `href`.

### Public names

The file defines `STYLE`, `PANEL` and `SCRIPT`.
The file `src/campaign_monitor.py` joins them into the page.

### Reads and writes

The Python file reads nothing and writes nothing at run time.
The script in the browser keeps the ids of dismissed jobs in `localStorage` under the key `manual-dismissed`. It changes the address fragment with `history.replaceState`. Its one request that writes is the POST to `/api/manual/resubmit`.

### Must never move into this file

- Routing.
- Database access.
- Use of `innerHTML` for server data.

## `src/applied_jobs.py`

### Responsibility

This file holds the rules that list applied jobs for the Applied jobs tab.
It has no HTTP, HTML or process code.
It never writes and it never submits an application.

### Public names

| Name | Use |
|---|---|
| `list_applied` | Returns each job with status `applied`, newest applied attempt first. The limit is 500 jobs. |
| `summarize` | Returns the four counts that the page shows above the list. |
| `confirmation_sentence` | Returns one clause of the evidence that shows the employer accepted the application. The clause has contact data removed and has at most 200 characters. It returns `None` when no phrase matches. |
| `receipt_file` | Returns the name of the receipt screenshot file that the evidence names. It never returns a path. |

The function `list_applied` also uses `safe_link` from `src/manual_jobs.py`. It checks the address of the job page and the employer address.

### Reads

- The tables `jobs` and `applications`, and the table `campaign_reservations` when it exists.
- The folder `logs/campaign/receipts`. It checks only if a named file exists.

### Writes

This file writes nothing.

### Must never move into this file

- HTTP, HTML or process logic.
- The whole evidence text in a result. The page must never receive it.
- A path to a screenshot in a result.

## `src/applied_jobs_ui.py`

### Responsibility

This file holds the style, the markup and the script of the Applied jobs tab.
The server sends each value as data. The script writes each value with `textContent`. The function `appliedLink` is the one place in this tab that sets `href`.

### Public names

The file defines `STYLE`, `PANEL` and `SCRIPT`.
The file `src/campaign_monitor.py` joins them into the page.
The script asks `/api/applied` every 10 seconds while the tab is open.

### Reads and writes

The file reads nothing and writes nothing at run time.
The script sends only GET requests.

### Must never move into this file

- Routing.
- Database access.
- Use of `innerHTML` for server data.

## `src/monitor_security.py`

### Responsibility

This file protects the one route that changes state.

### Public names

| Name | Use |
|---|---|
| `load_token` | Returns the write token. It creates `data/monitor-token` with permissions 0600 on first use. |
| `check_write` | Raises `Rejected` unless the request is allowed. |
| `read_body` | Reads exactly `Content-Length` bytes. The limit is 64 KiB. |
| `Rejected` | The error with an HTTP status. |

### Checks in `check_write`

A write request must pass four checks.

1. The `Host` header must be `127.0.0.1` or `localhost` with the server port.
2. A present `Origin` header must be the dashboard itself.
3. The content type must be `application/json`.
4. The header `X-Monitor-Token` must match the token.

### Reads and writes

- It reads and creates `data/monitor-token`.

### Must never move into this file

- Database access.
- HTML.
- Domain logic.

## `src/settings.py`

### Responsibility

This file validates the configuration.
It has no credentials and no network access.

### Public names

| Name | Use |
|---|---|
| `load_campaign`, `validate_campaign` | Read and check `data/campaign.json`. |
| `load_profile`, `validate_profile` | Read and check `profile.json`. |
| `default_profile` | Returns an incomplete profile for a new project. |
| `read_json`, `atomic_write_json` | Read a file and replace a file safely. |
| `resolve_path` | Resolves a path against the project root. |
| `DEFAULT_CAMPAIGN`, `DEFAULT_RUNTIME` | The default values. |
| `ConfigError` | The error for an invalid setting. |

### Rules that the file enforces

- An unknown key in the campaign, the runtime or the profile is an error.
- Six safety settings must stay `true`.

  - `skip_unknown_questions`
  - `skip_captcha`
  - `skip_sensitive_identity_fields`
  - `skip_separate_binding_legal_agreements`
  - `local_resume_only`
  - `no_further_questions`

- The profile value `submit_mode` must be `auto`.
- The profile value `salary_range_rule` must be `midpoint`.
- A profile that must be ready needs a local PDF resume.

### Reads and writes

- It reads `data/campaign.json` and `profile.json`.
- It reads the first five bytes of the resume file.
- It writes only through `atomic_write_json`, which callers use.

### Must never move into this file

- Credentials.
- Network access.
- Database access.

## `src/services.py`

### Responsibility

This file manages the two user services. It has five actions.

- `install`
- `start`
- `stop`
- `status`
- `uninstall`

Import of the file does no input or output.

### Public names

| Name | Use |
|---|---|
| `manage(action, root, campaign, replace=False)` | Runs `install`, `start`, `stop`, `status` or `uninstall`. |
| `supervisor_running(root)` | Tests the singleton lock `data/campaign-worker.lock`. |

### Behavior

- On macOS it writes launchd property files in `~/Library/LaunchAgents`.
- On Linux it writes systemd user units in `~/.config/systemd/user`.
- The names come from `runtime.service_name`. For launchd they are `com.NAME.applications` and `com.NAME.application-monitor`.
- The applications service runs `src/campaign.py loop`.
- The monitor service runs `src/campaign_monitor.py` with the dashboard port.
- A failed service restarts after 15 seconds.
- The module refuses to act on a loaded service that it does not own.
- The module refuses to start while another launcher holds the singleton lock.
- The action `start` deletes `data/campaign.stop` when the applications service is not active.
- The actions `stop` and `uninstall` stop the applications service first.

### Reads and writes

- It reads the service files, `data/campaign-runtime.json` and `data/campaign-worker.lock`.
- It writes the service files and the folder `logs/campaign`.
- It calls `launchctl`, `systemctl` and `ps`.

### Must never move into this file

- Campaign rules.
- Worker prompts.
- The stop file logic of the supervisor.

## `src/score.py`

### Responsibility

This file applies hard filters and computes the fit score of a job.

### Public names

| Name | Use |
|---|---|
| `evaluate(job, profile)` | Returns the score, the notes and a reject reason or `None`. |
| `location_ok(job, prefs)` | Tests the job location. |
| `MIN_APPLY_SCORE` | The minimum score to queue a job. The value is 50. |

### Reads and writes

- It reads the job dictionary and the profile dictionary that the caller passes.
- It writes nothing.

### Must never move into this file

- Database access.
- Any change of job status.
- Network access.

## `src/db.py`

### Responsibility

This file owns the database path, the base schema and the basic job and attempt writes.

### Public names

| Name | Use |
|---|---|
| `connect` | Opens `data/jobs.db`, creates `jobs` and `applications`, and adds missing columns. |
| `job_key` | Returns the job id from company, title and location. |
| `upsert_job` | Inserts a job or updates `last_seen`. |
| `record_application` | Inserts an attempt and sets the job status. |
| `load_profile` | Reads `profile.json` without validation. |
| `ROOT`, `DB_PATH` | The project root and the database path. |

### Reads and writes

- It reads and writes `data/jobs.db`.
- It reads `profile.json` in `load_profile`.

### Must never move into this file

- Reservation logic. It belongs to `src/campaign.py`.
- Scoring.
- Process control.

## `src/ingest.py`

### Responsibility

This file inserts discovered jobs and scores them.

### Public commands

| Command | Effect |
|---|---|
| `python3 src/ingest.py FILE` | Inserts the jobs in a JSON file and scores jobs with status `discovered`. |
| `python3 src/ingest.py --rescore` | Scores again the jobs that `RESCORABLE` selects. |
| `python3 src/ingest.py --report` | Prints the status counts, the top queued jobs and the attempt counts. |

### Reads and writes

- It reads the JSON file and `profile.json`.
- It writes `score`, `score_notes` and `status` in `jobs`.

### Must never move into this file

- A write of an `applied` outcome.
- A change to a job that has a row in `applications`.

## `src/pipeline.py`

### Responsibility

This file picks the next unit of work from the database.

### Public commands

- `next-search` prints the next query that is at least 24 hours old.
- `next-detail` prints the next job that has no verification.
- `next-apply [easy|external]` prints the next verified queued job.
- `mark-search QUERY [NEW_JOBS]` writes `search_log`.
- `status` prints counts.

This file creates the table `search_log` and the columns `apply_method` and `verified_at`.
I did not verify that any other file calls this file.

## `src/dbcli.py`

### Responsibility

This file is a database command line for `src/run.js`.
All input is JSON on standard input.

### Public commands

- `get-job` prints one job row.
- `save-verification` updates the description, the salary and `verified_at`.
- `record` calls `db.record_application` for the results `failed`, `manual` and `skipped`. It refuses the result `applied` and exits with an error.

### Must never move into this file

- Any path that records the result `applied`. Only `campaign.record` may record it, because only that function needs a reservation and a receipt. A test in `test/test_dbcli.py` fails if this file accepts `applied`.

## `src/ratelimit.js`

### Responsibility

This file is the JavaScript pacing gate for LinkedIn page loads and submits.

### Public names

The file exports seven names.

- `gate`
- `trip`
- `checkPage`
- `status`
- `unlock`
- `RateLimited`
- `CFG`


### Reads and writes

- It reads `data/campaign.json` when it loads.
- It reads and writes `data/ratelimit.json`.

### Must never move into this file

- Reservation logic.
- Any browser step.

## `jobapp.py`

### Responsibility

This file is the command line for the operator.
It sets up, validates, starts, stops and reports.

### Public commands

| Command | Effect |
|---|---|
| `python3 jobapp.py setup [--profile FILE] [--resume FILE]` | Creates missing configuration. It never overwrites a candidate or a different resume. |
| `python3 jobapp.py config show [campaign\|profile]` | Prints a configuration file. |
| `python3 jobapp.py config set KEY VALUE` | Changes one setting and validates the result. |
| `python3 jobapp.py config validate` | Checks both files. |
| `python3 jobapp.py doctor [--offline] [--json]` | Checks the prerequisites. |
| `python3 jobapp.py start [--no-dashboard]` | Runs the supervisor and the dashboard in the terminal. |
| `python3 jobapp.py stop` | Creates `data/campaign.stop`. |
| `python3 jobapp.py status [--json]` | Prints the status without a start. |
| `python3 jobapp.py service ACTION [--replace]` | Calls `services.manage`. |
| `python3 jobapp.py credential SITE [--email EMAIL]` | Calls `scripts/store_credential.py`. |

### Reads

- `data/campaign.json`, `profile.json` and `data/jobs.db`.

### Writes

- `data/campaign.json`, `profile.json` and `data/resume.pdf`, under the lock `data/configuration.lock`.
- The folder `logs/campaign/receipts`.
- The file `data/campaign.stop`.

### Must never move into this file

- Application logic.
- Pacing.
- Any step that submits an application.

## `scripts/store_credential.py`

### Responsibility

This script stores one employer password in the credential vault from a hidden prompt.

### Command

```
python3 scripts/store_credential.py SITE EMAIL
```

### Behavior

- The script refuses to run when standard input is not a terminal.
- The script reads the password twice with `getpass`.
- The script never takes the password as an argument and never prints it.
- The script writes the vault file with permissions 0600.
- The script appends the site and the email to an audit log. It never writes the password.
- The function `site_key` mirrors `siteKey` in `src/ats/credentials.js`.

### Reads and writes

- It reads and writes the vault file. The path comes from `ATS_CREDENTIALS_FILE`.
- It appends to `credentials_audit.log` in the same folder.

### Must never move into this file

- Any output of a password.
- Any other credential store.

## `scripts/check_ste.py`

### Responsibility

This script checks Markdown files against the mechanical rules of the standard.

### Command

```
python3 scripts/check_ste.py [--summary] FILE...
```

### Behavior

- It skips literal text. The file `docs/README.md` defines literal text.
- It checks seven kinds of problem.

  - Sentence length.
  - Semicolons.
  - Parentheses.
  - Words that end in "ing".
  - Possible passive voice.
  - Modal address to "you".
  - Lists of three items in a sentence.

- It does not check rule 4 or rule 6.
- It exits with 1 when it finds a violation.

### Reads and writes

- It reads the files that you name.
- It writes only to standard output.

### Must never move into this file

- A claim of ASD-STE100 certification.
- Any edit of a document.

## `src/ats/`

The folder `src/ats/` holds the JavaScript code for employer sites.
The file `src/ats/index.js` exports `applyOnSite`.
The function `applyOnSite` picks the first adapter that matches the address.
The adapter `generic` must stay last.
A dry run is the default. It returns status `skipped` with reason `dry_run_complete`.

### Adapters in `src/ats/adapters/`

| Adapter | What it does |
|---|---|
| `adp.js` | Applies on ADP WorkforceNow. It is a state machine that can resume after a one-time code. |
| `greenhouse.js` | Applies on Greenhouse hosted boards through `singlepage.js`. |
| `lever.js` | Applies on Lever through `singlepage.js`. It stops with `captcha_required` when a captcha appears. |
| `workday.js` | Applies on Workday sites. It is a state machine that uses stable `data-automation-id` hooks. |
| `icims.js` | Applies on iCIMS sites. It works inside the nested frame. |
| `smartrecruiters.js` | Applies on SmartRecruiters. It fills one page at a time. |
| `ashby.js` | Applies on Ashby hosted boards through `singlepage.js`. |
| `generic.js` | Names an unknown site and returns `manual`. It never clicks or types. |

### Shared files in `src/ats/`

| File | What it does |
|---|---|
| `index.js` | The entry point and the adapter registry. |
| `lib.js` | Result builders, the consent log, guarded clicks and page probes. |
| `form.js` | Scans form fields, fills them and reads them back. |
| `answers.js` | Maps questions to answers with an allowlist. An unknown question stops as manual. |
| `flow.js` | Page fingerprints, the submit gate and the final confirmation. |
| `singlepage.js` | The runner for forms with one page. |
| `account.js` | The gated account flow through `ensureAccount`. |
| `credentials.js` | The credential vault for employer accounts. |
| `google_signin.js` | The gated Google sign-in for the candidate account. |
| `mailbox_otp.js` | Reads one-time codes from the mailbox under strict gates. |
| `mailbox_account.js` | Reads account verification and reset links under strict gates. |
| `otp.js` | The hand-off of one-time codes from a person. |
| `pacing.js` | The per-host pacing gate and `pacedApply`. |

I read only the header comments of the shared files and the adapters.
I did not verify the full behavior of each file.

### Other files in `src/`

The folder `src/` also holds four other files.

- `run.js`
- `apply_easy.js`
- `discover_linkedin.js`
- `verify_external.js`

This document does not describe these files. I did not verify their behavior.
