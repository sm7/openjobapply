# Architecture decision record

This document records 12 design decisions. Each entry has four parts.

- The context.
- The decision.
- The alternatives that the code does not use.
- The consequence.

## How to read this record

Every entry comes from the code and the tests in this repository. Each entry names the file that shows the decision.

The repository holds no written minutes of the design talks. For this reason the list of alternatives is not a list of options that the owner argued about. Each alternative is a design that the code does not use. The text next to it says what the code shows about its cost. Not verified: whether the owner weighed each alternative.

In this document the word ledger means the SQLite database file `data/jobs.db`.

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
| manual job | A job with status `manual`. |
| skipped job | A job with status `skipped`. |
| queued job | A job with status `queued`. |
| reservation | A row in `campaign_reservations`. |
| receipt | The employer confirmation text. |
| standing answer | An answer in `profile.json` under `answers` that applies to every employer. |
| job answer | A row in `job_answers` that applies to one job. |

## Index

| Number | Decision |
|---|---|
| 1 | The supervisor and worker split |
| 2 | SQLite as the single ledger |
| 3 | The reservation table is the signal for possibly sent jobs |
| 4 | Job answers are written inside the `requeue` transaction |
| 5 | Automatic code updates on resubmission are deferred |
| 6 | A per-install dashboard token |
| 7 | Settings in two JSON files plus a validated command line |
| 8 | An allowlist of named US metros, not a pattern |
| 9 | A national scope check for state-tagged remote jobs |
| 10 | Restart only the monitor service for dashboard changes |
| 11 | The duplicate listing stays skipped |
| 12 | Two ATS paths: the adapter and the direct browser form |

## Decision 1. The supervisor and worker split

### Context

The campaign must run for many hours. A browser session can hang. An AI process can stop or loop. A machine can sleep in the middle of a form.

The target is 50 applications. A wrong Submit cannot be undone.

### Decision

Two kinds of process do two different jobs.

- The supervisor is plain Python. It lives in `src/campaign.py`, function `loop`.
- The worker is the `omp` command. The supervisor starts it once for each round.

The supervisor owns time, process safety and the ledger rules. The worker owns the browser and the form. The worker reaches the ledger only through the controller commands.

The code shows these supervisor duties.

- A singleton lock on `data/campaign-worker.lock` stops a second supervisor.
- A deadline ends a round. The default is 1200 seconds. The setting is `worker_timeout_seconds`.
- A heartbeat every 5 seconds shows that the supervisor is alive.
- The supervisor ends the whole process group of the worker. The class `WorkerTree` keeps detached children.
- The supervisor starts the worker with `--print --no-session` and a short tool list. A round has no stdin.
- The supervisor passes a new `CAMPAIGN_RUN_ID` to each round.
- The supervisor calls `recover` after each round.

### Alternatives that the code does not use

- One long AI session that loops by itself. The code shows the cost. A session that hangs cannot kill itself. Nothing then calls `recover`.
- The worker as the owner of the lock and the clock. The worker prompt in `src/CAMPAIGN_WORKER.md` says that the process exits when the worker replies without a tool call. A worker cannot guard against its own exit.
- The worker that writes outcomes into the database. The worker rules forbid a direct write of an applied outcome. The controller must check the reservation and the evidence.

### Consequence

- A crash leaves a clear state. The supervisor recovers every open reservation into a manual job.
- The worker is replaceable. The next round reads the files and the ledger again.
- The supervisor reloads the profile and the settings at the start of every round. Changes need no restart for the next round.
- A change to Python code needs a restart of the supervisor. The README says to do it at an idle boundary.
- The design adds code. The file `src/campaign.py` has over 1000 lines. Most of the lines handle process cleanup and activity reports.

Evidence is in the file `src/campaign.py`. Read the functions `loop` and `supervise_worker`. Read the functions `terminate_group` and `recover_worker`. Tests in `test/test_campaign.py` cover them. Two test names start with `test_parent_deadline` and `test_orphan_descendants`.

## Decision 2. SQLite as the single ledger

### Context

The campaign must remember each job, each attempt and each reservation. It must survive restarts. The supervisor, the worker commands and the monitor all read it. The supervisor and the monitor write to it.

### Decision

One SQLite file holds the durable facts. The file is `data/jobs.db`.

These tables exist at the snapshot date.

| Table | Purpose |
|---|---|
| `jobs` | One row for each job and its status |
| `applications` | One row for each attempt |
| `campaign_reservations` | One row for each reservation |
| `job_answers` | The job answers |
| `search_log` | The time of each search query |

The module `src/db.py` creates `jobs` and `applications`. The function `connect` in `src/campaign.py` creates `campaign_reservations` and `job_answers`. The module `src/pipeline.py` creates `search_log`.

The ledger rules use transactions. The functions `reserve`, `requeue` and `reconcile` start with `BEGIN IMMEDIATE`. The database write lock makes each check and each write one step.

The job status follows one life cycle. The top comment of `src/db.py` shows it as `discovered -> scored -> queued -> applying -> applied | failed | skipped | manual`.

A few small state files stay outside the ledger. They hold values that the processes rewrite as a whole.

- `data/campaign.json` holds settings.
- `data/ratelimit.json` holds the pacing history of the controller gate.
- `data/ats-pacing.json` holds the pacing history of employer hosts.
- `data/campaign-runtime.json` and `data/campaign-activity.json` hold the live state of the supervisor.

### Alternatives that the code does not use

- Many JSON files for jobs and attempts. The code uses `BEGIN IMMEDIATE` to make a check and a write one step. A set of JSON files cannot give this guarantee.
- A server database. The tool runs on one machine for one candidate. The README says the tool is not a cloud service. The code needs no server.
- Pacing history inside SQLite. Not verified: the reason the owner kept pacing in JSON files. The pacing files have their own lock files and atomic replace.

### Consequence

- A person can inspect the full history with any SQLite client.
- The dashboard opens the ledger read-only with `PRAGMA query_only = ON` for its list.
- Old databases still open. The function `list_manual` works when `job_answers` or `campaign_reservations` is absent. The test `test_the_list_works_on_a_database_that_predates_the_new_tables` covers this.
- Writes from two processes can collide. The monitor answers with status 503 on `sqlite3.OperationalError`.

## Decision 3. The reservation table is the signal for possibly sent jobs

### Context

A manual job may be one of two kinds. The worker may have stopped before the final Submit. Or the worker may have clicked Submit and lost the receipt. The first kind is safe to retry. The second kind may produce a duplicate application.

The dashboard must show the difference to the reviewer.

### Decision

The state of the reservation row decides. A manual job with a reservation in state `manual` or `failed` is `possibly_sent`. A manual job with no such row is `not_sent`.

The function `sent_risk` in `src/manual_jobs.py` holds this rule. Its comment gives the reason. A reservation exists only after the worker finished the form and reserved a slot. Its absence means the worker stopped before the final Submit.

The reason text plays no part in this decision.

### Alternatives that the code does not use

- Read the reason text for words such as unresolved or timeout. The reason is free text from the worker. The function `outcome_reason` cuts it to 600 characters and hides URLs, emails and secrets. A reason can also be empty. The test `test_the_reason_shown_is_sanitized_and_never_empty` shows an empty reason becomes `No reason recorded`.
- Add a new column to `jobs`. This choice needs a migration. The reservation table already holds the fact.
- Treat every manual job as possibly sent. The reviewer would need a confirmation for jobs that never reached Submit. The tab would lose its signal.

### Consequence

- The signal is exact and comes from the same table that `reserve` writes.
- Old databases work. An absent table gives `not_sent`.
- The signal can only be wrong in one direction. An absent row means a stop before reserve. The worker creates the reservation just before Submit.
- A job from before the controller has no reservation. Such a job is not manual, so the signal does not apply to it.

Evidence: `src/manual_jobs.py` functions `sent_risk` and `list_manual`, and the test `test_a_job_that_reserved_a_submit_slot_is_marked_possibly_sent`.

## Decision 4. Job answers are written inside the `requeue` transaction

### Context

A reviewer reads why a manual job stopped. Often the cause is an unknown question. The reviewer types the question and the answer on the dashboard. The job must return to the queue with those answers.

Two writes must happen. One returns the job to the queue. One saves the job answers.

### Decision

`campaign.requeue` accepts an `answers` argument. It saves the job answers in the same `BEGIN IMMEDIATE` transaction as the requeue. The function `resubmit` in `src/manual_jobs.py` passes the cleaned answers to it.

One error rolls back all steps. The step list is in `docs/safety-model.md`, section 7.

The function `reviewed_answers` in `src/campaign.py` sends job answers to the worker. It selects only jobs with status `queued` or `applying`. A stale answer cannot reach a job that has a final outcome.

The command line `python3 src/campaign.py requeue` passes no answers. Only the dashboard path writes job answers.

### Alternatives that the code does not use

- Save the answers first and requeue after. A failed requeue would leave answers behind. The test `test_a_rejected_requeue_leaves_no_answers_behind` shows the code does not leave them.
- Requeue first and save the answers after. A crash in between would send the job to the worker with no answers. The worker would stop on the same question again.
- Let the monitor write to the tables itself. The README says that `campaign.requeue` stays the one place where a job returns to the queue. One place means one set of checks.

### Consequence

- A reviewer never sees half a result.
- The requeue checks protect the answers too. A job with an applied twin gets no answers.
- A second save for a job replaces the old answers. The function deletes the old rows first. The test `test_resubmitting_replaces_earlier_answers_for_that_job` covers this.
- A job answer is data. It does not waive any protection. The next list names five protections that stay in force.
  - The consent limits.
  - The duplicate guard.
  - The reservation.
  - The pacing gate.
  - The receipt requirement.

## Decision 5. Automatic code updates on resubmission are deferred

### Context

A resubmission saves a job answer. A question that comes back for many employers may deserve a general rule in `src/ats/answers.js`. Someone could automate that step.

### Decision

The project does not change code from a resubmission. The README states this. The text says that automated, reviewed code updates from a resubmission are intentionally not implemented yet.

A job answer stays tied to one job. A question that comes back follows a manual path. The reviewer adds a `standing answer` to `profile.json`. A developer adds a rule and a test to `src/ats/answers.js`.

### Alternatives that the code does not use

- Let the worker or the dashboard write new rules into `src/ats/answers.js`. The worker rules say the worker must never edit source code. A generated rule would run against live employer forms without review.
- Turn every job answer into a `standing answer` at once. A true answer for one employer can be false for another. The rules for each `standing answer` in `src/ats/answers.js` have narrow patterns for this reason. The test file `test/fixtures/ats/answers_standing.test.js` checks that one rule does not leak onto a neighbor question.

### Consequence

- Each answer works for one job. The reviewer must enter a new answer for a new employer with the same question.
- Code stays under human review and test. The change is slower.
- The feature stays an open item. Not verified: whether a gap document tracks it.

## Decision 6. A per-install dashboard token

### Context

The dashboard binds to `127.0.0.1`. That alone does not block a web page in the browser of the user. The page can still send a post request to the dashboard. Another local program can do the same.

The Manual jobs tab changes state. A forged request could return a possibly sent job to the queue.

### Decision

The monitor creates a random token on first use. The file is `data/monitor-token`. The mode is `0600`. The function `load_token` in `src/monitor_security.py` creates it with `O_EXCL`. Two processes cannot create two tokens.

The dashboard page embeds the token in a meta tag. The script sends it in the header `X-Monitor-Token`. The function `check_write` compares it with `hmac.compare_digest`.

The token adds to three other checks. These are the host, the origin and the content type. The content type check forces a CORS preflight.

The host check applies to every route. It does not apply to the write route only. The author first applied it to the write route only. That version served the page to any host name. The page holds the token. A `DNS rebinding` page could read the token and then use it. The author found the flaw in a review of the documents on 2026-11-13. The method `do_GET` in `src/campaign_monitor.py` now calls `check_host` first for every read route.

### Alternatives that the code does not use

- A login with a user name and password. The tool serves one local user. A password would add a secret to store and to type.
- Loopback alone. The top comment of `src/monitor_security.py` says this does not stop cross-site requests.
- One token for all installs, shipped in the code. The word per-install in the name shows the aim. Each project folder has its own secret.
- A token in a cookie. Not verified: that the owner considered it. The code uses a header, which a cross-site form cannot set.

### Consequence

- The page is the only place where the token is visible. The code never logs it. Only a request with the dashboard's own `Host` header receives the page.
- A foreground dashboard and an installed service use the same token file. The creation is race-safe.
- A user who deletes `data/monitor-token` gets a new token on the next start. An empty file raises an error.
- A user who calls the API from a script must read the token file first.

Evidence: `src/monitor_security.py` and two tests in `test/test_manual_jobs.py`. The test `test_requests_that_are_not_the_dashboards_own_page_change_nothing` covers the write route. The test `test_a_page_requested_under_a_foreign_host_never_receives_the_token_or_any_data` covers the read routes. The second test failed 25 of 25 cases on the version without the host check.

Lesson: the first attack test sent bad requests to the write route only. It never tried to read the token. Test the read path of every secret.

## Decision 7. Settings in two JSON files plus a validated command line

### Context

The campaign needs several kinds of data. It needs facts about the candidate and search rules. It needs caps, runtime paths and a target. A person must be able to read and change them. A bad value must not break a live campaign.

### Decision

Two files stay authoritative.

- `profile.json` holds the candidate, the preferences and the search rules. It also holds the answers and the permissions.
- `data/campaign.json` holds the target, the caps and the priorities. It also holds the recovery batch and the runtime settings.

The command `python3 jobapp.py config` reads and writes them. The command `config set KEY VALUE` takes a dotted key that starts with `campaign.` or `profile.`.

The module `src/settings.py` validates every write. The write runs under the lock `data/configuration.lock`. The write replaces the file in one atomic step. An invalid value leaves the old file in place.

The validator rejects unknown keys in `data/campaign.json`. It refuses to turn off six safety settings. It requires integers for caps. A cap of `null` means unlimited. A cap of zero disables the action.

One rule links two fields. A change to `salary_expectation_usd` updates `preferences` and `answers` in the same write. The two fields cannot disagree.

The supervisor loads the files again at the start of each round. A change takes effect in the next round. The supervisor does not interrupt a form that is open.

### Alternatives that the code does not use

- Environment variables or command line flags for every setting. The supervisor runs as a service. A file keeps the values in one visible place.
- A database table for settings. A person edits a JSON file with any editor. The settings stay outside the ledger.
- A free edit of the JSON by hand with no validation. The validator exists because a bad file would stop the campaign or weaken a safety rule. The test `test_invalid_setting_updates_leave_configuration_unchanged` covers this.
- One merged file. Not verified: the reason for two files. The two files have different owners. The profile describes the candidate. The campaign file describes the run.

### Consequence

- A salary change needs one command: `python3 jobapp.py config set profile.preferences.min_salary_usd 75000`.
- A change to the dashboard port or the service name needs a service update.
- The profile validator refuses a password field. The test `test_credentials_cannot_be_written_into_the_profile_or_echoed_in_errors` shows it.
- The setup command does not replace a corrupt file. The test `test_corrupt_configuration_is_not_replaced_by_setup` covers this.

## Decision 8. An allowlist of named US metros, not a pattern

### Context

LinkedIn writes many US locations as a metro area with no state code. Examples are `Greater Boston` and `San Francisco Bay Area`. The scorer rejected these jobs, even the home market of the candidate.

Foreign locations use the same shape. Examples are `Greater London Area` and `Greater Sydney Area`.

### Decision

The scorer accepts a metro name only when the name is on a fixed list. The list is `_US_METROS` in `src/score.py`. The regular expression `_METRO_AREA` allows the optional word `Greater`. It allows an optional suffix such as `Area` or `Metroplex`. It allows an optional tag such as `(Hybrid)`.

The comment in the code gives the rule. The shape alone is not evidence of a US city. A company name or an unlisted place never counts. To add a market, extend the list. A closed default is the safe direction.

### Alternatives that the code does not use

- A pattern such as `Greater <word> Area`. The test `test_metro_wording_for_foreign_cities_is_rejected` lists foreign places with this exact shape. A pattern would accept them.
- A look-up through a geography service. The module `src/score.py` has no network access. Not verified: that the owner considered a service.
- A rule that any location without a country is a US location. The test `test_a_company_name_in_the_location_field_is_never_a_place` shows that a company name can land in the location column.

### Consequence

- US metros on the list pass. Foreign metros fail.
- A real US metro that is not on the list fails until someone adds it. This is a known cost of a closed default.
- An explicit foreign country still overrides a metro that looks American. The function `_us_location` checks the foreign names first.
- The tests in `test/test_score_location.py` protect both sides.

## Decision 9. US remote tags enter review unless an explicit restriction excludes the candidate

### Context

LinkedIn can tag a national remote job with one city or state.
The tag alone does not establish an employer restriction.

### Decision

The scorer accepts a known US remote tag for further review.
The worker must check the actual requirements before Submit.
The function `_remote_eligible` in `src/score.py` keeps explicit restrictions first.
The function `home_state` reads the home state from `candidate.state` or from the end of `candidate.location`.
The scorer applies every state rule to that state.

1. Reject text that excludes the home state.
2. Reject an eligible-state list that omits the home state.
3. Reject foreign-only work.
4. Accept a known US location tag after these checks.
5. Accept an explicit national scope or a scope that names the home state.
6. Reject an unknown location with no scope evidence.

The phrase `<State> not excluded` permits applicants from that state.
The scorer respects this negation.
When the profile gives no home state, no text can pass an eligible-state list.

### Alternatives that the code does not use

- Accept every remote location without checks.
- Reject every job with a state tag.
- Treat a scorer pass as permission to submit.

### Consequence

- A known US remote tag can enter review without an explicit scope statement.
- Explicit residency restrictions still win.
- The tests in `test/test_score_location.py` cover both boundaries.
- Free text can contain a restriction that the patterns do not recognize.
- A scorer pass remains a screen, not proof of eligibility.


## Decision 10. Restart only the monitor service for dashboard changes

### Context

Two launchd services run for each service name. Their labels are `com.<service_name>.applications` and `com.<service_name>.application-monitor`. The default service name is `jobapp`. The first runs the supervisor. The second runs the dashboard.

A dashboard code change does not affect the supervisor. A restart of the supervisor can end a round in the middle of a form or in the middle of a Submit.

### Decision

A change to the dashboard files restarts only the monitor service. These files are the dashboard files.

- `src/campaign_monitor.py`.
- `src/manual_jobs_ui.py`.
- `src/monitor_security.py`.

The supervisor stays up.

The code supports this split. The module `src/services.py` builds two independent services. The function `manage` documents that a start never restarts an active job. The monitor and the supervisor have separate entry points. The README says to restart the supervisor only at an idle boundary and never during an uncertain Submit.

One limit applies. The module `src/manual_jobs.py` imports `campaign`. A change to `src/campaign.py` affects the monitor and the supervisor. The supervisor then needs its own restart at an idle boundary.

### Alternatives that the code does not use

- Restart both services after every change. This choice risks the live round and an open reservation. The quarantine protects the ledger. It still turns a live job into a manual job.
- Restart nothing and wait. The old dashboard code would serve until a later restart. The new tab would not appear.
- Reload code inside the live monitor. Not verified: that the monitor can reload code. No code for it exists.

### Consequence

- The dashboard changes go live without a break in the worker.
- The two services can run different code versions for a time. The supervisor keeps the old controller code until its next restart.
- The change of a dashboard port needs an explicit stop and start of the monitor. The README says so.
- Not verified: the exact commands that the session ran to restart the monitor. This document holds no record of them.

## Decision 11. The duplicate listing stays skipped

### Context

LinkedIn can show one listing under two job rows. One row can have status `applied`, with an applied attempt. The other row can have status `skipped` with the score note `rejected:location` and no attempt.

### Decision

The skipped row stays skipped. The code does not merge it. The code does not requeue it.

The duplicate guard makes this safe. The function `identity_conflicts` finds both rows through the pair `source` and `source_id`. Any call to `reserve`, `requeue` or `reconcile` on the skipped row stops with `already_sent_or_reserved`.

### Alternatives that the code does not use

- Delete the duplicate row. The ledger keeps history. The code never deletes a job or an attempt in its safety commands.
- Mark the duplicate as applied. No receipt exists for that row. An applied outcome needs a reservation and evidence.
- Requeue the duplicate. The guard refuses it. A second application to the same listing is the failure that the guard prevents.

### Consequence

- The count of confirmed jobs stays honest. It counts distinct jobs with an applied attempt.
- A third row for the same ADP listing exists with status `discovered`. The guard blocks it too. Its status text does not matter.
- Two rows for one listing stay in the ledger. A person who reads the ledger must know this. Not verified: that a tool lists such pairs for the reviewer.

## Decision 12. Two ATS paths: the adapter and the direct browser form

### Context

Employer sites use many application systems. The project has adapters for seven of them. Other forms have no adapter. A layout change can also break an adapter.

### Decision

The worker has two ways to fill an employer form.

Path 1 is the adapter. The file `src/ats/index.js` holds the registry. The next list names the adapters in order.

- `adp`.
- `greenhouse`.
- `lever`.
- `workday`.
- `icims`.
- `smartrecruiters`.
- `ashby`.
- `generic`. It comes last.

The call is `applyOnSite`. The worker wraps it in `pacedApply` from `src/ats/pacing.js`. The worker always runs a dry run first. A dry run returns `skipped` with reason `dry_run_complete`. It never submits.

Path 2 is the direct browser form. The worker uses it for an adapter result of `unsupported_ats:*`, `unknown_ats` or `form_not_found`. It also uses it for a layout mismatch. The worker inspects the real form. It fills the form with native browser controls. The rule in `src/CAMPAIGN_WORKER.md` says that an absent adapter is a technical limit. It does not prove that a person must act.

Both paths use the same protections.

- The same verified answers and consent rules apply.
- The worker reserves the job just before Submit.
- The worker passes the apply pacing gate.
- The outcome needs a receipt.

A direct browser review does not count as an adapter dry-run success.

### Alternatives that the code does not use

- Adapters only. Every unsupported site would become a manual job. The worker rules call this a technical limit and not a human need.
- Direct browser only. The adapters give a repeatable dry run, a field read-back and tested rules. The fixture tests in `test/fixtures/ats` cover them.
- A generic form filler that guesses fields. The file `src/ats/answers.js` states that nothing guesses and that every answer needs a rule and profile data.

### Consequence

- More jobs reach a result. Few jobs stop only for lack of an adapter.
- The direct path has less automatic checks. The worker must read each field. The adapters read back every filled field.
- Account actions stay behind `ensureAccount` on both paths. The worker must not click account controls by hand.
- A tested adapter can still meet a new layout. The iCIMS adapter needed a change for a Source attribution step. The fixture tests record the observed shape.

## What this record does not verify

- The record does not verify the intent of the owner for any alternative.
- The record does not verify live behavior on Linux.
- The record does not verify the exact commands that the session ran to restart services.
