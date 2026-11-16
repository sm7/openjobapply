# Architecture

This document describes the whole system.
It is a snapshot of the code on 2026-11-13.
The source files are in `src/`. The main file is `src/campaign.py`.

## Purpose

The system applies to jobs for one candidate.
The candidate data is in `profile.json`.
The campaign settings are in `data/campaign.json`.
The field `target_total` defines the campaign target.
The value `null` means continuous operation without a total cap.
The system counts only distinct jobs with an employer receipt.
The campaign is complete when the count reaches a finite target. An unlimited campaign reports `complete:false` and `remaining:null`.
Skipped, manual and failed attempts do not count toward the target.

## The three processes

The system has three processes.
Each process has one role.

| Process | Entry point | Role |
|---|---|---|
| supervisor | `python3 src/campaign.py loop` | Starts, watches and stops the worker. |
| worker | The `omp` program | Opens the browser and fills forms. |
| monitor | `python3 src/campaign_monitor.py` | Serves the dashboard. |

### The supervisor

The supervisor is the function `loop` in `src/campaign.py`.
The supervisor holds the singleton lock.
The supervisor starts one worker for each round.
The supervisor writes a heartbeat to `data/campaign-runtime.json`.
The supervisor stops the worker at the end of the round.
The supervisor changes the state of reservations after the worker ends.
The supervisor does not open a browser.
The supervisor does not fill forms.

### The worker

The worker is the `omp` program. The supervisor starts it with a prompt.
The prompt comes from `src/CAMPAIGN_WORKER.md`.
The supervisor adds the current profile, the campaign settings and the status to the prompt.
The worker reads the prompt and decides what to do.
The worker opens pages in the real Chrome browser through the relay.
The worker calls `python3 src/campaign.py` for each controlled step.
The controlled steps are four commands.

- `activity`
- `gate`
- `reserve`
- `result`

The worker does not edit source code.
The worker does not edit the configuration.

### The monitor

The monitor is the file `src/campaign_monitor.py`.
The monitor runs as a separate service.
The monitor reads the database in read-only mode.
The monitor binds to `127.0.0.1` only.
The monitor serves the page and four read routes:

- `GET /api/status`
- `GET /api/logs`
- `GET /api/applied`
- `GET /api/manual`

The monitor has one route that changes state.
That route is `POST /api/manual/resubmit`.
The page has four tabs. See `docs/dashboard.md`.
The monitor does not start or stop the worker.

### How the processes relate

The processes do not call each other directly.
They share files and the database.

| From | To | Channel |
|---|---|---|
| supervisor | worker | A child process with a prompt and environment variables. |
| worker | supervisor | The command `python3 src/campaign.py` and the database. |
| supervisor | monitor | The file `data/campaign-runtime.json` and the database. |
| monitor | database | A read-only connection. |
| monitor | `data/jobs.db` | One write through `manual_jobs.resubmit`. |

The supervisor and the monitor run as two launchd services.
The two launchd labels are `com.<service_name>.applications` and `com.<service_name>.application-monitor`. The default service name is `jobapp`.
The service name comes from `runtime.service_name` in `data/campaign.json`.
The module `src/services.py` builds the labels from the service name.

## Data flow from discovery to receipt

The data moves through these steps.

1. The worker searches for jobs in the browser.
2. The worker saves the jobs to a JSON file or calls `db.upsert_job`.
3. The command `python3 src/ingest.py FILE` inserts each job into the `jobs` table.
4. The function `db.upsert_job` computes the job id from company, title and location.
5. The function `db.upsert_job` ignores a job that already exists and updates `last_seen`.
6. The function `score_row` in `src/ingest.py` scores each job with status `discovered`.
7. The function `score.evaluate` returns a score, notes and a reject reason.
8. A reject reason sets the job to `skipped` with the note `rejected:` and the reason.
9. A job without a description stays `discovered` with the note `needs_description`.
10. A job with a score of 50 or more becomes `queued`.
11. A job with a score below 50 becomes `scored`.
12. The worker selects a queued job.
13. The worker calls `gate load` before each search page.
14. The worker calls `gate detail` before each job detail page.
15. The worker fills the form and reviews the answers.
16. The worker calls `reserve JOB_ID`. The job becomes `applying`.
17. The worker calls `gate apply`.
18. The worker clicks Submit one time.
19. The worker reads the employer receipt.
20. The worker calls `result` with the outcome.
21. The function `record` writes one row to `applications`.
22. The function `record` sets the reservation state and the job status to the outcome.

The constant `MIN_APPLY_SCORE` is 50. It is in `src/score.py`.

### Scoring in `evaluate`

The function `evaluate` in `src/score.py` first applies hard filters.
Each filter returns a reject reason.
The filters run in this order.

1. The job has the flag `closed`. The reason is `closed`.
2. The location is not acceptable. The reason is `location`.
3. The employment type does not match. The reason is `employment_type` or `employment_description`.
4. The title has an excluded word. The reason starts with `title_excluded:`.
5. The title has a word that is too senior. The reason starts with `too_senior:`.
6. The title has a word that is out of lane. The reason starts with `out_of_lane:`.
7. The title has no included word. The reason is `title_no_match`.
8. The salary midpoint is below the minimum. The reason starts with `salary_below_min:`.
9. The salary is unknown and the profile does not allow it. The reason is `salary_unknown`.
10. The description asks for too many years. The reason starts with `requires_`.

A job that passes all filters gets a score from 0 to 100.
The score has these parts.

| Part | Points |
|---|---|
| A core title word | 40 |
| A generic analyst title with a health word in the text | 20 |
| Any other title | 5 |
| Each skill found in the text | 5, up to 40 |
| Each core domain word found | 5, up to 15 |
| The job states a salary | 5 |
| Entry level text and a title without analyst | Minus 10 |

The function rounds the score and limits it to 100.

### Rescoring

The command `python3 src/ingest.py --rescore` scores the jobs again.
It touches only jobs without an attempt.
It touches jobs with status `discovered`, `scored` or `queued`.
It also touches skipped jobs with a note that starts with `rejected:`.
It never changes a job that has a row in `applications`.

## Job status lifecycle

The comment in `src/db.py` shows this order.

```
discovered -> scored -> queued -> applying -> applied | failed | skipped | manual
```

The code has more moves than this order. A job can return to `queued`.

| Status | Meaning | What sets it |
|---|---|---|
| `discovered` | The job is new or has no description. | `db.upsert_job` through the column default. `score_row` in `src/ingest.py` for a job without a description. |
| `scored` | The job has a score below 50. | `score_row` in `src/ingest.py`. |
| `queued` | The job is ready to apply. | `score_row` for a score of 50 or more. `campaign.requeue`. `campaign.record` for the outcome `deferred`. |
| `applying` | The job has an active reservation. | `campaign.reserve`. |
| `applied` | The job has an employer receipt. | `db.record_application` through `campaign.record`. `campaign.reconcile`. |
| `failed` | The attempt failed. | `db.record_application` through `campaign.record`. |
| `manual` | A person must act. | `db.record_application` through `campaign.record`. `campaign.recover` for an unresolved reservation. |
| `skipped` | The job is not eligible or was skipped. | `score_row` for a reject reason. `db.record_application` through `campaign.record`. `dbcli.py save-verification` for a closed job. |

The function `db.record_application` maps the result to the status with the same name.
The mapping covers `applied`, `failed`, `manual` and `skipped`.

### Moves out of a finished status

A finished status is `manual`, `skipped` or `failed`.
Only `campaign.requeue` moves a finished job back to `queued`.
The requeue needs a reason, evidence and `submission_verified_absent` set to `true`.
The requeue writes one row to `applications` with the result `deferred`.
That row has the run id `campaign-requeue`.
The dashboard calls `requeue` only for a manual job.
The function `campaign.reconcile` moves a manual or failed job to `applied`.
The reconcile needs an inspected employer receipt.
The reconcile writes one row with the run id `campaign-reconcile`.

## The SQLite database

The database file is `data/jobs.db`.
The function `db.connect` creates the tables `jobs` and `applications`.
The function `campaign.connect` creates `campaign_reservations` and `job_answers`.
The function `pipeline._ensure` creates `search_log`.
The function `pipeline._ensure` also adds the columns `apply_method` and `verified_at` to `jobs`.

### Table `jobs`

One row is one job.

| Column | Type | Meaning |
|---|---|---|
| `id` | TEXT | The first 16 characters of the SHA-1 of normalized company, title and location. |
| `source` | TEXT | The source of the job. The schema comment names `indeed`, `linkedin` and `company`. |
| `source_id` | TEXT | The identifier at the source. |
| `url` | TEXT | The job page address. |
| `apply_url` | TEXT | The address of the employer application. |
| `title` | TEXT | The job title. |
| `company` | TEXT | The employer name. |
| `location` | TEXT | The job location. |
| `remote` | INTEGER | 1 when the job is remote. |
| `salary_min` | INTEGER | The lower salary bound. |
| `salary_max` | INTEGER | The upper salary bound. |
| `description` | TEXT | The job description. |
| `employment_type` | TEXT | The employment type. |
| `distance_miles` | REAL | The distance from the candidate. |
| `posted` | TEXT | The posting date text. |
| `easy_apply` | INTEGER | 1 when the source has a short apply flow. |
| `score` | REAL | The fit score. |
| `score_notes` | TEXT | Skill notes or `rejected:` and a reason. |
| `status` | TEXT | The job status. The default is `discovered`. |
| `first_seen` | REAL | The Unix time of first insert. |
| `last_seen` | REAL | The Unix time of the last sighting. |
| `apply_method` | TEXT | Added by `pipeline._ensure`. |
| `verified_at` | REAL | Added by `pipeline._ensure`. |

The index `idx_jobs_status` covers `status` and `score`.

Writers of `jobs`:

- `db.upsert_job` inserts rows and updates `last_seen`.
- `ingest.score_row` updates `score`, `score_notes` and `status`.
- `campaign.reserve`, `campaign.requeue`, `campaign.reconcile` and `campaign.record` update `status`.
- `db.record_application` updates `status`.
- `dbcli.py save-verification` updates the description, the salary and `verified_at`.

### Table `applications`

One row is one attempt.

| Column | Type | Meaning |
|---|---|---|
| `id` | INTEGER | The key. It increases for each row. |
| `job_id` | TEXT | The job. |
| `attempted` | REAL | The Unix time of the attempt. |
| `result` | TEXT | The outcome. The values are `applied`, `failed`, `manual`, `skipped` and `deferred`. |
| `reason` | TEXT | The detail of a failure or skip. |
| `evidence` | TEXT | The receipt text or a screenshot path. |
| `run_id` | TEXT | The run that wrote the row. |

The schema comment names four results.
The function `requeue` also writes the result `deferred`.
The index `idx_app_job` covers `job_id`.

Writers of `applications`:

- `db.record_application` inserts a row. `campaign.record` calls it with the run id `campaign-50`.
- `campaign.requeue` inserts a `deferred` row with the run id `campaign-requeue`.
- `campaign.reconcile` inserts an `applied` row with the run id `campaign-reconcile`.
- `dbcli.py record` inserts a row for the results `failed`, `manual` and `skipped`. It refuses `applied`.

### Table `campaign_reservations`

One row is one reservation. The key is `job_id`.

| Column | Type | Meaning |
|---|---|---|
| `job_id` | TEXT | The job. |
| `reserved_at` | REAL | The Unix time of the reservation. |
| `state` | TEXT | The state. |
| `reason` | TEXT | The reason of the final state. |

A reservation has one of five states.

- `reserved`
- `applied`
- `manual`
- `failed`
- `skipped`

Writers of `campaign_reservations`:

- `campaign.reserve` inserts a row with state `reserved`. It first deletes an old row with state `manual`, `failed` or `skipped`.
- `campaign.record` sets the state to the outcome. For `deferred` it deletes the row with state `reserved`.
- `campaign.reconcile` sets the state to `applied`.
- `campaign.requeue` deletes rows with state `manual`, `failed` or `skipped`.

### Table `job_answers`

One row is one job answer. The key is `job_id` and `question`.

| Column | Type | Meaning |
|---|---|---|
| `job_id` | TEXT | The job. |
| `question` | TEXT | The exact question. |
| `answer` | TEXT | The answer. |
| `created_at` | REAL | The Unix time of the save. |

Only `campaign.requeue` writes this table.
It deletes all rows for the job and inserts the new rows in one transaction.
The function `reviewed_answers` reads the rows for jobs with status `queued` or `applying`.
The supervisor puts these answers in the worker prompt.

### Table `search_log`

One row is one search query. The key is `query`.

| Column | Type | Meaning |
|---|---|---|
| `query` | TEXT | The search text. |
| `last_run` | REAL | The Unix time of the last search. |
| `new_jobs` | INTEGER | The count of new jobs from that search. |

The command `python3 src/pipeline.py mark-search` writes this table.
The command `next-search` returns the next query that is at least 24 hours old.
The supervisor code does not call `pipeline.py`. The worker prompt does not name it.

## Files in `data/`

The table lists the files that exist in `data/` on the snapshot date.
A file not in the code I read has the note "writer not verified".

| File | What it holds |
|---|---|
| `jobs.db` | The SQLite database. |
| `campaign.json` | The campaign settings. The operator edits it. |
| `campaign-runtime.json` | The supervisor state and heartbeat. The supervisor writes it. |
| `campaign-activity.json` | The current activity that the worker reports. |
| `ratelimit.json` | The pacing events and the platform lockout. |
| `ats-pacing.json` | The per-host pacing state for employer sites. The file `src/ats/pacing.js` writes it. |
| `campaign-worker.lock` | The singleton lock of the supervisor. |
| `campaign-activity.lock` | The lock for the activity and the heartbeat. |
| `campaign-events.lock` | The lock for the events log. |
| `campaign-pacing.lock` | The lock for the pacing gate. |
| `configuration.lock` | The lock for edits of the configuration. `jobapp.py` takes it. |
| `campaign.stop` | The stop file. It does not exist while the system runs normally. |
| `monitor-token` | The write token of the dashboard. It has owner-only permissions. |
| `otp` | The folder for one-time code hand-off files. |
| `resume.pdf` | The local resume. `jobapp.py setup` copies it. |
| `consent_policy.md` | The consent policy. `src/ats/answers.js` refers to it. |
| `_last_apply.json` | Writer not verified. |
| `campaign-discovered.json` | Writer not verified. |
| `campaign-round-discovered.json` | Writer not verified. |
| `campaign-round-index.json` | Writer not verified. |
| `campaign-salient.json` | Writer not verified. |
| `discovered_run1.json` | Writer not verified. |
| `discovered_linkedin_run1.json` | Writer not verified. |
| `ea_page1.json` and `ea_page1_raw.json` | Writer not verified. |

The `campaign.json` file also holds `runtime` settings. These settings include the service name and the dashboard port.

## Files in `logs/`

| File or folder | What it holds |
|---|---|
| `logs/campaign/events.jsonl` | One JSON line for each event. The function `campaign.event` writes it. |
| `logs/campaign/rounds.jsonl` | One JSON line for each finished round. |
| `logs/campaign/<time>.log` | The full output of one worker round. |
| `logs/campaign/receipts/` | Receipt screenshots. The worker saves them. `jobapp.py setup` creates the folder. |
| `logs/campaign/<label>.stdout.log` | The standard output of a launchd service. |
| `logs/campaign/<label>.stderr.log` | The standard error of a launchd service. |
| `logs/ats/` | Evidence screenshots from the adapters. The folder name can change with `ATS_EVIDENCE_DIR`. |
| `logs/consents.log` | One line for each consent that an adapter accepted. |
| `logs/mailbox_otp.log` | The audit log of one-time code reads. |
| `logs/mailbox_account.log` | The audit log of account mail reads. |
| `logs/runs.jsonl` | The summary of each run of `src/run.js`. |
| `logs/profile_changes.log` | Writer not verified. |

The events log holds metadata only.
The events log allows twelve fields.

- `job_id`
- `result`
- `worker_pid`
- `supervisor_pid`
- `exit_code`
- `exit_reason`
- `restart_count`
- `confirmed`
- `action`
- `retry_at`
- `stage`
- `run_id`

The function `event` drops all other fields.

## How the supervisor starts a worker

The supervisor runs the function `loop`.
These steps start one worker.

1. Reload `data/campaign.json` with `settings.load_campaign`.
2. Read the status with the function `status`.
3. Stop if the count of receipts reaches the target. The state becomes `complete`.
4. Wait if the budget has a `retry_at` time. The state becomes `waiting_platform`.
5. Wait if the previous round ended too recently. The state becomes `between_rounds`.
6. Call `relay_connected`. This function reads `http://127.0.0.1:9224/json/version`.
7. Wait five seconds if the relay has no connection. The state becomes `waiting_relay`.
8. Load the profile with `ready=True`. The load fails if the profile is not complete.
9. Find the `omp` program with `worker_executable`.
10. Build the prompt with `worker_prompt`.
11. Open a new log file in `logs/campaign/`.
12. Make a new run id from `uuid.uuid4().hex`.
13. Clear the activity file.
14. Start the process with `subprocess.Popen`.

The command line for the worker has these parts.

- `omp --print --no-session --wait-async`
- `--model` and the model name. The supervisor adds this only when `worker_model` has a value
- `--no-extensions --auto-approve`
- `--tools read,bash,eval,web_search,write`
- `--max-time` and the round timeout in seconds
- The prompt text

The process starts in a new session with `start_new_session=True`.
The process has no standard input.
Its output goes to the round log file.
The supervisor sets these environment variables for the worker.

| Variable | Value |
|---|---|
| `PI_BROWSER_RELAY` | `1` |
| `CAMPAIGN_RUN_ID` | The run id of this round. |
| `ATS_CREDENTIALS_FILE` | The credentials file from `runtime.credentials_file`. |

After the start, the supervisor sets the state to `working`.
It records the worker process id, the start time and the run id.
It writes a `worker_start` event.

## How the supervisor stops a worker

The function `supervise_worker` watches the worker.
It loops every 0.25 seconds.
It writes a heartbeat every 5 seconds.
It stops the worker for one of these reasons.

| Exit reason | Cause |
|---|---|
| `worker_exit` | The worker ended with code 0. |
| `worker_failed` | The worker ended with a code other than 0. |
| `worker_timeout` | The round ran longer than `worker_timeout_seconds`. |
| `stop_file` | The file `data/campaign.stop` exists. |
| `sigterm` | The supervisor received SIGTERM. |
| `sigint` | The supervisor received SIGINT. |

Other exit reasons are `supervisor_error`, `recovered_worker` and `target_reached`.
These come from the supervisor and not from the watch loop.

The function `terminate_group` always runs, also after a normal exit.
A worker can leave child processes. These steps remove them.

1. Capture the full process tree with `WorkerTree.capture`.
2. Send SIGTERM to each process group that the worker tree owns.
3. Wait up to 5 seconds. Send SIGTERM again to groups that appear during the wait.
4. Send SIGKILL to the groups that remain.
5. Wait up to 5 more seconds.
6. Raise an error if a group still exists. The supervisor then refuses to start a second worker.

The function refuses to signal the process group of the supervisor.
The function refuses to signal a group when the process identity does not match.
The supervisor reads the process start time to check the identity.

## One round, step by step

This section describes one round from the view of the supervisor and the worker.

### Supervisor steps

1. Start the worker as described above.
2. Watch the worker until it ends or the supervisor must stop it.
3. Remove the worker process tree.
4. Call `recover`. This function turns each reservation with state `reserved` into a `manual` outcome.
5. Set the state to `between_rounds`.
6. Write a `worker_end` event.
7. Append one line to `logs/campaign/rounds.jsonl`.
8. Compute the pause. Use `success_pause_seconds` if the count of receipts rose. Use `idle_pause_seconds` if not.
9. Start the next round after the pause.

The default `success_pause_seconds` is 60. The default `idle_pause_seconds` is 300.
The pause uses durable outcomes only. It does not read the worker output.

### Worker steps

The file `src/CAMPAIGN_WORKER.md` defines these steps.

1. Read `profile.json` and `data/campaign.json`.
2. Run `python3 src/campaign.py status`.
3. Exit if the campaign is complete.
4. Report activity before each real step.
5. Process the jobs in `priority_job_ids` first.
6. Review the jobs in `manual_recovery_job_ids` next.
7. Search for new jobs one page at a time.
8. Call `gate load` before each search page.
9. Call `gate detail` before each job detail page.
10. Index the new jobs with `src/ingest.py` or `db.upsert_job`.
11. Open the application and fill only verified answers.
12. For an employer site, call `pacedApply` with `dryRun:true` first.
13. Review the answers before submit.
14. Run `python3 src/campaign.py reserve JOB_ID`. Do not submit if the command denies the slot.
15. Run `python3 src/campaign.py gate apply`. Do not submit if the command denies the gate.
16. Click Submit one time.
17. Read the employer receipt.
18. Save a screenshot to `logs/campaign/receipts` if possible.
19. Run `python3 src/campaign.py result` with the outcome as JSON.

The worker must not reserve a job before the final review.
The worker must not retry a job that has an applied result or an active reservation.
Without a receipt the outcome is `manual`.

### The commands of `src/campaign.py`

| Command | Input | Effect |
|---|---|---|
| `status` | None | Prints the status as JSON. |
| `activity` | JSON on standard input | Records the current activity. |
| `gate load`, `gate detail`, `gate apply` | None | Waits for a pacing slot and records the event. |
| `reserve JOB_ID` | None | Reserves a submit slot. |
| `requeue JOB_ID` | JSON on standard input | Returns a finished job to `queued`. |
| `reconcile JOB_ID` | JSON on standard input | Records a verified historical receipt. |
| `result` | JSON on standard input | Records the outcome of an attempt. |
| `loop` | None | Runs the supervisor. |

The program prints one JSON value to standard output on success.
On an error it prints a JSON object with the key `error` to standard error. The exit code is 2.

### Reservation rules

The function `reserve` runs in one `BEGIN IMMEDIATE` transaction.
It refuses to reserve in these cases.

| Error | Cause |
|---|---|
| `target_reached_or_reserved` | Receipts plus active reservations reach the target. |
| `unknown_job` | The job does not exist. |
| `already_sent_or_reserved` | An equivalent job has a receipt or an active reservation. |
| `manual_review_or_explicit_requeue_required` | The job status is `manual`, `skipped` or `failed`. |
| `daily_submission_limit` | The daily submission cap is reached. |

An equivalent job has the same id, the same `url` or the same `source` and `source_id`.
A reservation sets the job status to `applying`.

### Outcome rules

The function `record` accepts five results.

- `applied`
- `manual`
- `failed`
- `skipped`
- `deferred`

The result `applied` needs evidence and a reservation with state `reserved`.
The result `deferred` deletes the reservation and sets the job to `queued`.
A `deferred` result writes no row to `applications`.
All other results write one row and update the reservation and the job.
Each recorded result clears the activity for that job.

## The heartbeat

The supervisor writes `data/campaign-runtime.json` at least every 5 seconds.
Each write sets `heartbeat_at` to the current time.
The function `runtime_status` marks the state `stale` in these cases.

- The heartbeat is older than 20 seconds.
- The supervisor process is not alive.

This rule does not apply to the states `stopped`, `complete` and `error`.

The runtime file has these fields.

| Field | Meaning |
|---|---|
| `state` | The supervisor state. |
| `heartbeat_at` | The Unix time of the last heartbeat. |
| `supervisor_pid` | The process id of the supervisor. |
| `worker_pid` | The process id of the worker. |
| `worker_started_at` | The Unix time that the worker started. |
| `worker_run_id` | The run id of the worker. |
| `last_progress_at` | The Unix time of the last durable progress. |
| `next_retry_at` | The Unix time of the next retry. |
| `restart_count` | The count of worker restarts. |
| `last_exit_reason` | The exit reason of the last worker. |

The supervisor also writes `started_at` and `worker_tree` to this file.
The code sets nine states.

- `starting`
- `working`
- `stopping`
- `between_rounds`
- `waiting_platform`
- `waiting_relay`
- `complete`
- `stopped`
- `error`


### Progress

Progress is a durable action. Log volume is not progress.
The function `progress_at` returns the latest of four times.

- The latest `attempted` time in `applications`.
- The latest `reserved_at` time in `campaign_reservations`.
- The latest pacing event.
- The latest valid activity report.

The field `no_progress` is true when the age of the last progress reaches 300 seconds.

### Activity

The worker reports activity with `python3 src/campaign.py activity`.
The input has only five keys.

- `stage`
- `job_id`
- `action`
- `page`
- `pages`

The function `report_activity` checks the input.
It also checks that the caller is a child of the current worker process.
It compares the start time of the worker with the saved start time. The limit is 3 seconds.
An activity report older than 300 seconds is `stale`.
The dashboard shows company and title from the database only.

## The singleton lock

The file `data/campaign-worker.lock` is the singleton lock.
The function `loop` opens the file and calls `fcntl.flock` with `LOCK_EX` and `LOCK_NB`.
A second supervisor fails at once with the error `campaign supervisor already running`.
The kernel releases the lock when the supervisor process ends.
The function `services.supervisor_running` tests the same lock to see if a supervisor runs.
`jobapp.py start` and `jobapp.py stop` use that function.

## How a crash is recovered

A crash can leave a worker process and an open reservation.
These steps recover the state when the next supervisor starts.

1. Read the saved runtime file.
2. Write the saved worker process id and tree back to the runtime file at once.
3. Call `recover_worker`. This checks if any saved process group is alive.
4. Check the identity of the worker leader by its start time and process group.
5. Raise an error if the identity does not match. The supervisor refuses to overlap.
6. Call `terminate_group` for the old worker tree.
7. Set `last_exit_reason` to `recovered_worker` and write a `restart` event.
8. Call `recover`. Each reservation with state `reserved` becomes a `manual` outcome.
9. Clear the worker process id and start the loop.

The recovered outcome has this reason: the worker ended with the submission outcome unresolved.
The reason also says to inspect employer evidence before a retry.
The outcome is never `applied`.
A reviewer can use `reconcile` after a real receipt exists.
A reviewer can use `requeue` after proof that the employer has no application.

The services restart a failed supervisor.

| Backend | Setting |
|---|---|
| launchd | `KeepAlive` with `SuccessfulExit` set to false and `ThrottleInterval` of 15 seconds. |
| systemd | `Restart=on-failure` and `RestartSec=15s`. |

The supervisor sets the state `error` and the reason `supervisor_error` when an exception ends it.

## Pacing gates

Pacing limits the speed of page loads and submissions.
Three gates exist. Each has its own state file.

| Gate | Code | State file | Scope |
|---|---|---|---|
| Python gate | `gate` in `src/campaign.py` | `data/ratelimit.json` | Search pages, job detail pages and submits. |
| JavaScript gate | `src/ratelimit.js` | `data/ratelimit.json` | The same events from JavaScript code. |
| Employer gate | `src/ats/pacing.js` | `data/ats-pacing.json` | Each employer host. |

### The Python gate

The command `gate KIND` accepts `load`, `detail` and `apply`.
The gate takes the lock `data/campaign-pacing.lock`.
It keeps the events of the last 24 hours.
It refuses in these cases.

| Reason | Cause |
|---|---|
| `site_lockout` | The field `lockedUntil` is in the future. |
| `daily_submission_limit` | The `apply` events reach `max_submissions_per_day`. |
| `daily_page_limit` | The `load` and `detail` events reach `max_loads_per_day`. |
| `hourly_detail_limit` | The `detail` events in one hour reach `max_details_per_hour`. |

A cap of `null` means no cap.
A cap of 0 refuses the action for ever and returns `retry_at` as null.
When the gate allows an action, it waits for a random gap since the last event.

| Kind | Gap |
|---|---|
| `load` and `detail` | 8 to 20 seconds. |
| `apply` | 60 to 180 seconds. |

After 4 to 7 loads the gate adds a pause of 45 to 120 seconds.
The gate then saves the event and returns `allowed: true`.
The wait happens inside the command. The worker must allow time for the command.

The function `budget` reports the counts for the dashboard.
The supervisor reads `budget.retry_at`. It then waits in the state `waiting_platform`.

### The platform lockout

The worker records a lockout in `data/ratelimit.json` when LinkedIn shows a challenge.
The lockout lasts 6 hours.
The file `src/ratelimit.js` sets `lockedUntil` in the function `trip`.
The function `checkPage` calls `trip` in three cases.

- The status is 429 or 999.
- The address is an authwall address.
- The page has challenge text.


### The JavaScript gate

The file `src/ratelimit.js` has the same gaps and breaks as the Python gate.
It queues calls so that only one runs at a time.
It reads the caps from `data/campaign.json` when the file loads.
It throws `RateLimited` when a cap or a lockout applies.

### The employer gate

The file `src/ats/pacing.js` paces employer sites.
It allows one employer application at a time.
It requires at least 90 seconds plus up to 30 seconds of jitter between page loads on one host.
It reads `max_submissions_per_host_per_day` and `max_submissions_per_day` from `data/campaign.json`.
A captcha or a block locks the host. The back-off has four steps.

- 30 minutes.
- 2 hours.
- 12 hours.
- 24 hours. This step repeats.

The state file is `data/ats-pacing.json`.
A corrupt state file raises an error. The gate does not start from an empty state.
The worker calls `pacedApply` to use this gate.

### What the reservation adds

The pacing gates and the reservation are separate protections.
The reservation counts the target, active reservations and daily submissions.
A cap of `null` does not disable the reservation.
