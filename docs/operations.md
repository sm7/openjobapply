# Operations guide

This document explains how to run the job application system. It is a snapshot of the code as read on 2026-11-13. It does not describe behavior that the code does not show.

## 0. Terms

Each word below has one sense in this guide.

| Term | Meaning |
|---|---|
| worker | The AI process that opens the browser and fills forms. |
| supervisor | The Python process that starts, watches and stops the worker. |
| monitor | The dashboard web server. |
| dashboard | The web page. |
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

## 1. What the system does

The command `jobapp.py` is the only command that an operator needs. It sets up the project. It checks the computer. It starts the supervisor. It manages the background services.

The supervisor starts one worker at a time. The worker looks for jobs and fills application forms. The worker uses the signed-in Chrome browser of the candidate.

**Danger.** The commands `start` and `service start` can submit real applications to real employers.

The command `setup` does not submit anything. The command `config` does not submit anything. The command `doctor` does not submit anything. The command `status` does not submit anything.

## 2. Install the prerequisites

Do these steps one time on each computer.

1. Use macOS or Linux. The code refuses other platforms.
2. Install Python 3.10 or newer. The code checks this in `doctor`.
3. Make sure that SQLite and the `ps` command work. The supervisor needs `ps` to read process identities.
4. Install Oh My Pi. The command name is `omp`. Put it on the `PATH`.
5. Install Chrome.
6. Sign in to the model provider with the command that follows.

```sh
omp login
```

7. Install the browser relay extension with the command that follows.

```sh
omp browser-relay install
```

8. Open `chrome://extensions` in Chrome.
9. Load the unpacked extension from the folder that the installer prints.
10. Sign in to the job sites in Chrome. Use the account of the candidate.
11. Start the relay in a terminal and keep it open.

```sh
omp browser-relay
```

The relay listens on port 9224 on the local computer. The supervisor checks `http://127.0.0.1:9224/json/version` before each round. When the relay does not answer, the supervisor waits and does not start a worker. The dashboard shows the state `waiting_relay`.

The computer must stay awake. The desktop session must stay logged in. Chrome and the relay must stay open. This system is not a cloud service.

On macOS, the supervisor runs `caffeinate -i -w WORKER_PID` for each worker round.
This prevents idle system sleep while the worker runs.
The assertion ends with the worker and does not change power settings.
It does not prevent sleep when you close the lid.
Keep the lid open and use external power for long campaigns.
The assertion can use more battery.

The README names omp 18.19.0 as the version that the authors tested. The command `doctor` runs `omp --help` and looks for four flags.

- `--print`
- `--wait-async`
- `--max-time`
- `--auto-approve`

## 3. First-time setup for a new candidate

Keep one separate project copy for each candidate. Never share the database, the profile or the history between two candidates.

1. Make a new copy of the project source code.
2. Do not copy `profile.json` from another candidate.
3. Do not copy the `data` folder from another candidate.
4. Do not copy the `logs` folder from another candidate.
5. Open a terminal in the new project folder.
6. Prepare a reviewed profile file and a resume file in PDF format.
7. Run the setup command with both files.

```sh
python3 jobapp.py setup --profile /path/to/reviewed-profile.json --resume /path/to/resume.pdf
```

The `--profile` option is optional. Without it, setup creates an incomplete profile that cannot submit. The `--resume` option is also optional. When you give it, setup copies the PDF to `data/resume.pdf` with mode 0600.

Setup checks that the resume file starts with the bytes `%PDF-`.

Setup creates only the files that do not exist. It never overwrites a different profile. It never overwrites a different `data/resume.pdf`. It leaves these items unchanged:

- the database
- the receipts
- the pacing history
- the platform lockouts

When `data/campaign.json` does not exist, setup creates it. Setup then sets two runtime values that are unique to the project folder:

- `service_name` becomes `jobapp-` followed by the first 8 characters of a SHA-256 hash of the project path.
- `credentials_file` becomes `~/.omp/job-applications/<service_name>/credentials.json`.

Setup also creates the folder `logs/campaign/receipts`.

Setup prints a JSON result. The field `profile_ready` tells you if the profile is complete. The field `next` tells you the next step.

9. If `profile_ready` is `false`, edit `profile.json`. Fill every required field.
10. Run the validate command.

```sh
python3 jobapp.py config validate
```

The profile must have these items before the system starts:

- `candidate.name`, `candidate.first_name` and `candidate.last_name`
- `candidate.email` in a valid address format
- `candidate.phone`
- `candidate.country`
- `candidate.location`
- `candidate.work_authorization`
- `candidate.resume_path` that points to a local PDF
- `candidate.requires_sponsorship` set to `true` or `false`
- `preferences.min_salary_usd` as an integer
- `search.queries` with at least one query

11. Run the doctor command.

```sh
python3 jobapp.py doctor
```

The doctor prints one line for each check. The line starts with `PASS`, `WARN` or `FAIL`. The command exits with code 1 when any check fails.

| Check | What it tests |
|---|---|
| `platform` | The platform is macOS or Linux and Python is 3.10 or newer. |
| `configuration` | The campaign file and the profile are valid and the PDF opens. |
| `omp` | The `omp` command exists and lists the four required flags. |
| `process_supervision` | The process table includes the current process. |
| `chrome_relay` | The relay answers on port 9224. A failure is a warning only. |
| `model_authentication` | Always a warning. The doctor does not test model authentication. |

12. Add an offline check when the relay is not ready.

```sh
python3 jobapp.py doctor --offline
```

The option `--json` prints the report as JSON. The doctor does not read credentials. It does not send a paid model request.

13. Grant only the permissions that the candidate approved. See `docs/configuration.md`.
14. Store employer passwords if the candidate wants them stored. See section 11.

## 4. Check an existing project

Use these steps for a project that is already set up.

1. Run `python3 jobapp.py config validate`.
2. Run `python3 jobapp.py doctor`.
3. Run `python3 jobapp.py status`.

## 5. Run in the foreground

Use the foreground run to test or to watch the system in a terminal.

1. Start the relay in a second terminal with `omp browser-relay`.
2. Run the start command.

```sh
python3 jobapp.py start
```

The command checks the configuration, the profile and the `omp` command. It then checks the singleton lock `data/campaign-worker.lock`.

If another supervisor holds the lock, the command prints `Application supervisor is already running; nothing restarted.` and exits. It does not start a second worker.

If no supervisor holds the lock, the command does these actions in order:

1. It starts the monitor in a thread and prints the dashboard address.
2. It deletes the file `data/campaign.stop` when the file exists.
3. It prints a start message.
4. It runs the supervisor loop.

The default dashboard address is `http://127.0.0.1:8765/`. The monitor binds to the loopback address only.

5. Add the option `--no-dashboard` to start the supervisor alone.

```sh
python3 jobapp.py start --no-dashboard
```

6. Press Ctrl-C to stop the foreground run. The supervisor catches the signal and cleans up the worker. A submission that the supervisor cannot confirm stays in quarantine until a person reviews it.

When the foreground run ends, the dashboard of that run also ends.

## 6. Run as a background service

A background service keeps the supervisor alive after you close the terminal. The service starts when you log in.

### 6.1 Service names

| Platform | Manager | Application service | Dashboard service |
|---|---|---|---|
| macOS | launchd user agents | `com.<service_name>.applications` | `com.<service_name>.application-monitor` |
| Linux | `systemd --user` | `<service_name>-applications.service` | `<service_name>-application-monitor.service` |

The value `<service_name>` comes from `campaign.runtime.service_name`. The default service name is `jobapp`.

The macOS files are in `~/Library/LaunchAgents` with the extension `.plist`. The Linux files are in `~/.config/systemd/user`. The code never uses `sudo`.

### 6.2 Install and start on macOS

1. Run the install command.

```sh
python3 jobapp.py service install
```

2. Run the start command.

```sh
python3 jobapp.py service start
```

3. Run the status command.

```sh
python3 jobapp.py service status
```

The install command writes two plist files. It does not start anything. The plist uses `RunAtLoad`, so the service starts at login. The plist uses `KeepAlive` with `SuccessfulExit` set to `False`. This means launchd restarts the supervisor after a failure. launchd does not restart it after a clean exit. The throttle interval is 15 seconds. The exit time-out is 30 seconds.

The standard output and error logs are in `logs/campaign` with names `<label>.stdout.log` and `<label>.stderr.log`.

### 6.3 Install and start on Linux

1. Log in to a user session that has `systemd --user`.
2. Run the same three commands as for macOS.

The install command writes two unit files. It then runs `systemctl --user daemon-reload` and `systemctl --user enable` for both units. The units use `Restart=on-failure` with a delay of 15 seconds. The stop time-out is 30 seconds.

If `systemctl --user` is not available, the command stops with an error. Use the foreground run in that case.

The README says the fixtures test the Linux service life cycle. The README says that nobody ran the service natively on Linux. This guide does not verify native Linux execution.

### 6.4 What the service commands do

| Command | Effect |
|---|---|
| `service install` | Writes the service files. Does not start anything. |
| `service start` | Starts each service that is not active. Never restarts an active service. |
| `service stop` | Stops both services. The application service stops first. |
| `service status` | Prints the state of both services. |
| `service uninstall` | Stops both services and deletes only the files that this project owns. |

The `service start` command also checks the singleton lock. If a foreground run holds the lock, the command stops with an error. Stop the foreground run first.

When the application service is not active, `service start` deletes `data/campaign.stop` before it starts.

The commands refuse to touch a loaded service that this project does not own.

`service stop` and `service uninstall` use the manager to send a termination signal. The manager waits 30 seconds and then kills the process. These commands do not write the stop file.

### 6.5 Replace a changed service definition

Use the option `--replace` after you change `service_name`, `omp_executable` or `dashboard_port`.

```sh
python3 jobapp.py service install --replace
```

Without `--replace`, the install command refuses to change a file that differs. The `--replace` option does not restart a live worker. After a port change, stop and start the services so the monitor loads the new port.

Do not move or delete the project folder while the services run.

## 7. Stop and restart without losing an application

The database keeps the history. A stop never deletes history. The risk is a stop in the middle of a Submit. Read section 13 to learn why.

### 7.1 Request a graceful stop

1. Run the stop command.

```sh
python3 jobapp.py stop
```

2. Read the JSON result. The field `stop_requested` is `true` when a supervisor holds the lock.

The command creates the file `data/campaign.stop` with mode 0600. The supervisor checks for the file in its loop. When the file exists, the supervisor stops the worker through its bounded cleanup and ends. The supervisor writes the state `stopped`.

If no supervisor runs, the command prints `stop_requested` as `false`. It does not create the file.

The stop command does not stop the dashboard service. A foreground dashboard ends with its foreground supervisor.

### 7.2 Stop the services

Run this command to stop both services.

```sh
python3 jobapp.py service stop
```

Use this command at a safe boundary. See section 13.

### 7.3 Start again

1. Run `python3 jobapp.py service start` for the background service.
2. Or run `python3 jobapp.py start` for the foreground run.

Both commands delete `data/campaign.stop` when no supervisor is active.

### 7.4 What the supervisor does after a crash or kill

When the supervisor starts, it reads `data/campaign-runtime.json`. It cleans up a worker that an earlier supervisor left behind. It then calls the function `recover`.

The function `recover` finds each reservation in the state `reserved`. It records the outcome `manual` for each one. The reason says that the worker ended with an unresolved submission outcome. The system never counts such a job as applied. A person must inspect the employer evidence before any retry.

## 8. Check status

Use any of these methods. None of them submits anything.

### 8.1 The command line

1. Run `python3 jobapp.py status`.
2. Run `python3 jobapp.py status --json` to get the same JSON. The option does not change the output.

Both commands print JSON. The fields are:

| Field | Meaning |
|---|---|
| `confirmed` | Count of unique jobs with a confirmed application. |
| `target` | The value of `campaign.target_total`; `null` means unlimited. |
| `remaining` | The finite target minus `confirmed`, never less than zero; `null` for an unlimited campaign. |
| `complete` | `true` when `confirmed` reaches a finite target; always `false` for an unlimited campaign. |
| `budget` | Counts for the pacing window and the field `retry_at`. |
| `outcomes` | Count of attempts for each outcome. |
| `runtime` | The supervisor state and process identities. |
| `activity` | What the worker reports that it does now. |
| `latest_actions` | The 20 newest attempts. |
| `counts_by_job_status` | Count of jobs for each job status. |

If `data/jobs.db` does not exist, the command prints a short result with `runtime.state` set to `not_started`.

### 8.2 The service manager

Run `python3 jobapp.py service status`. The output has one entry for each service. Each entry shows if the service has a file, if the manager loaded it and if it runs.

### 8.3 The dashboard

Open `http://127.0.0.1:8765/` in a browser. See section 9.

### 8.4 The log files

The worker writes one log file for each round in `logs/campaign`. The file name starts with the date and time. The supervisor appends one JSON line for each round to `logs/campaign/rounds.jsonl`. The line has these fields:

- `at`
- `exit`
- `confirmed`
- `log`
- `log_bytes`
- `exit_reason`

## 9. Read the dashboard

The page has four tabs. This section covers the Overview tab and the Live logs tab. See `docs/dashboard.md` for the full description of every tab.
The Applied jobs tab lists the confirmed applications with their confirmation clause. The Manual jobs tab lists the jobs that need a person.

The page reads `/api/status` every 5 seconds. It reads `/api/logs` every 2 seconds while the Live logs tab is open.

### 9.1 The Overview tab

| Part | What it shows |
|---|---|
| Status band | The supervisor state in words, a dot and the button `Needs you: N manual jobs`. The button opens the Manual jobs tab. |
| Last exit reason and next retry | The last exit reason and the next retry time. They show only when the worker is not working. |
| Progress | The count of confirmed applications as the big number. A finite target adds `X / target` and the remaining count. The line `Last confirmed application` shows the age of the newest applied row among the 20 latest actions. |
| Jobs by current status | Count of jobs for each job status. |
| Current application | The company, role and stage that the worker reports. |
| Latest 20 job actions | The newest attempts with company, role, outcome and reason. |
| Attempt history | A closed fold-out with the count of attempts, the count of submission attempts in the last 24 hours and the count of attempts for each outcome. |
| Technical details | A closed fold-out with the heartbeat age, the worker age, the last recorded activity, the process ids, the worker state code and the restart count. |

The dashboard can show nine supervisor states:

- `starting`
- `working`
- `waiting_relay`
- `waiting_platform`
- `between_rounds`
- `stopped`
- `complete`
- `error`
- `stale`

Read these facts about the Overview tab:

- A fresh heartbeat shows that the supervisor lives. It does not show an application submission.
- The state `stale` means the heartbeat is old or the supervisor process is gone.
- The text `no recent activity` is a caution. It is not proof of a stuck worker.
- The panel Current application is old when the report is stale or the worker is not healthy. The heading then changes to Last reported application.
- Only confirmed unique jobs count toward the target. The attempts table counts every attempt.
- The Latest 20 job actions table shows the reason for four outcomes. These outcomes are `manual`, `failed`, `deferred` and `skipped`.
- When a refresh fails, the page shows a red banner and keeps the last known data.
- The two fold-outs start closed.

### 9.2 The Live logs tab

The tab shows the operational activity log in a window. The newest 200 entries show, oldest first. Each row has a time and an event name in brackets. It also has a message. Some rows have job context.

Controls:

1. Press Pause to stop the automatic refresh. Press the button again to resume.
2. Clear the check box Auto-follow latest to keep your scroll position.

The status line shows `Live`, `Paused` or `Offline`. When it shows `Offline`, the entries are old data.

The tab does not show private model thought. It does not show credentials. It does not show mailbox content.

## 10. Change a setting

Use `config set` for every change. The command validates the whole file before it writes. It writes the file in one atomic replace. An invalid value leaves the old file unchanged. The command also takes a lock so two commands cannot write at the same time.

The key must start with `campaign.` or `profile.`. The value is JSON when it parses as JSON. Otherwise the value is a plain string.

Always quote a JSON array or object in the shell.

### 10.1 Change the salary

1. Set the lowest salary that the candidate accepts.

```sh
python3 jobapp.py config set profile.preferences.min_salary_usd 75000
```

2. Set the salary that the forms receive.

```sh
python3 jobapp.py config set profile.preferences.salary_expectation_usd 90000
```

The second command also writes the same value to `profile.answers.salary_expectation_usd`. The same happens when you set the answers field. The two fields never differ.

The values above are examples. Use the values that the candidate approves.

For an advertised range, the system compares the midpoint of the range with `min_salary_usd`. It does not use the highest value.

### 10.2 Change the target

```sh
python3 jobapp.py config set campaign.target_total null
```

Use `null` for continuous operation without a total application cap.
Status returns these values:

- `target:null`
- `remaining:null`
- `complete:false`

The Overview tab hides the target and the `remaining` count. Its caption reads `confirmed applications`.
All safety rules still apply:

- Pacing.
- Platform lockouts.
- Eligibility.
- Known answers.
- Reservations.
- Duplicate prevention.
- Employer receipts.

A finite target must be an integer of zero or more.
The value zero closes the campaign.
When confirmed applications reach a finite target, the supervisor writes `complete` and ends.
The service manager does not restart a clean exit.
A target change does not start a stopped worker.
Run `python3 jobapp.py service start` only when you want the worker to resume.

### 10.3 Change the model

```sh
python3 jobapp.py config set campaign.worker_model 'provider/model-id'
```

Use `null` to remove the model and use the default of `omp`.

```sh
python3 jobapp.py config set campaign.worker_model null
```

The supervisor passes the value to the worker with the option `--model` at the start of the next round.

### 10.4 Change the pacing limits

These keys control how often the worker acts. A value of `null` means no limit. A value of `0` disables the action.

```sh
python3 jobapp.py config set campaign.max_submissions_per_day 5
python3 jobapp.py config set campaign.max_loads_per_day 200
python3 jobapp.py config set campaign.max_details_per_hour 30
python3 jobapp.py config set campaign.max_submissions_per_day null
```

The values above are examples only.

These keys control how long the supervisor waits between rounds:

```sh
python3 jobapp.py config set campaign.runtime.success_pause_seconds 60
python3 jobapp.py config set campaign.runtime.idle_pause_seconds 300
python3 jobapp.py config set campaign.runtime.worker_timeout_seconds 1200
```

The supervisor uses `success_pause_seconds` after a round that raised the count of confirmed applications. It uses `idle_pause_seconds` after any other round.

The pause between browser actions and the platform lockouts always apply. No setting removes them.

### 10.5 Change the dashboard port

```sh
python3 jobapp.py config set campaign.runtime.dashboard_port 8765
```

The value must be from 1 to 65535. A monitor that already runs keeps the old port. See section 6.5.

### 10.6 When a change takes effect

The command prints this message after each change: next worker round. The dashboard port and the service name need a service update. See `docs/configuration.md` for the full table.

Python source changes need a supervisor restart. See section 12.

## 11. Store an employer password

Never put a password in a configuration file. Never put it in a command argument. Never put it in a log. Never send it in a chat message.

1. Open a real terminal. The script refuses to run when standard input is not a terminal.
2. Run the credential command with the employer site name.

```sh
python3 jobapp.py credential employer.example --email candidate@example.com
```

3. Type the password at the hidden prompt.
4. Type the password again to confirm it.

The option `--email` is optional. Without it, the command uses `candidate.email` from the profile. The command stops with an error when it finds no email address.

The script stores the password in the vault file from `campaign.runtime.credentials_file`. The script writes the file with mode 0600. The script normalizes the site name. For example, it reduces a Workday host to `<tenant>.myworkdayjobs.com`.

If an entry for the same site and email exists, the script tells you that it will replace the entry. The script writes a log line with the site and email to `credentials_audit.log` in the vault folder. The log line has no password.

The model provider login stays in the own storage of `omp`. This system never stores it.

## 12. Restart the supervisor after a code change

Python code loads once when the supervisor starts. A change to a Python file has no effect until the supervisor restarts. The supervisor reloads configuration at each round. It does not reload code.

A restart at the wrong time can leave an application in doubt. Restart only at a safe idle boundary.

### 12.1 The safe-restart conditions

All of these conditions must be true at the same time:

1. The runtime state is `between_rounds`.
2. No worker process exists.
3. No row in `campaign_reservations` has the state `reserved`.
4. The activity file has no report. Or the stage of the report is `idle`. Or the stage is `searching`.
5. The file `data/campaign.stop` does not exist.

If any condition is false, do not restart. Wait and check again.

### 12.2 Check the conditions

1. Read the runtime state and the activity.

```sh
python3 jobapp.py status --json
```

2. Find `runtime.state` in the output. The value must be `between_rounds`.
3. Find `runtime.worker_pid`. The value must be `null`.
4. Find `activity`. The value must be `null`, or its `stage` must be `idle` or `searching`.
5. Count the reservations in the state `reserved`.

```sh
python3 -c "import sqlite3; c=sqlite3.connect('file:data/jobs.db?mode=ro', uri=True); print(c.execute(\"SELECT COUNT(*) FROM campaign_reservations WHERE state='reserved'\").fetchone()[0])"
```

The output must be `0`.

6. Check that the stop file does not exist.

```sh
test ! -e data/campaign.stop && echo no-stop-file
```

The output must be `no-stop-file`.

7. Read all five results again. A new round can start between two reads. A round start changes `runtime.state` to `working`.

The pause between rounds is at least `success_pause_seconds` or `idle_pause_seconds`. Use this pause as the window for the restart.

### 12.3 Restart

1. Run `python3 jobapp.py service stop`.
2. Run `python3 jobapp.py service start`.
3. Run `python3 jobapp.py status --json`.
4. Check that `runtime.supervisor_pid` has a new value.
5. Check that `runtime.state` is not `error`.

For a foreground run, press Ctrl-C and run `python3 jobapp.py start` again.

If you changed a service definition, run `python3 jobapp.py service install --replace` before the start.

## 13. Why a restart during a Submit is unsafe

The worker reserves a submit slot before it presses the final Submit button. The reservation has the state `reserved`.

After the worker presses Submit, the employer may accept the application at once. The worker then reads the receipt and records the outcome. This takes time.

If the supervisor stops in that time, the system has no receipt. The employer may still have the application. The system cannot know.

The supervisor then does this on the next start:

1. It finds the row in `campaign_reservations` with the state `reserved`.
2. It records the outcome `manual` for the job.
3. It writes a reason. The reason says that the outcome of the submission is unknown.
4. It never counts the job as applied.

The job needs a person to check the employer account. If the person sends the job again without a check, the employer receives two applications. The duplicate protections exist to prevent this.

For this reason the dashboard marks such a job with the badge `May have been sent`. A person must confirm that no application arrived before the job can go back to the queue.

## 14. Move the project to a new computer

The project holds private data. Do not publish it. Do not copy authentication stores without a review.

1. Run `python3 jobapp.py service stop` on the old computer. Wait for the stop to finish.
2. Run `python3 jobapp.py status --json`. Check that `runtime.state` is `stopped` and `runtime.worker_pid` is `null`.
3. Copy the whole project folder to the new computer. The folder holds `profile.json`, `data/campaign.json`, `data/jobs.db` and `data/resume.pdf`.
4. Install the prerequisites from section 2 on the new computer.
5. Sign in with `omp login` on the new computer.
6. Install the relay extension and sign in to the job sites in the new Chrome.
7. Open `data/campaign.json`. Check `runtime.credentials_file`. Check that the path is correct for the new computer.
8. Store each employer password again with the `credential` command. The vault file does not move with the project folder.
9. Run `python3 jobapp.py config validate`.
10. Run `python3 jobapp.py doctor`.
11. Run `python3 jobapp.py service install`.
12. Run `python3 jobapp.py service start`.

The service files contain absolute paths. A copy of the old service files does not work. Always install the services again on the new computer.

Do not run the system on two computers with the same database copy. The duplicate protection depends on one database.

The code does not show what happens when `data/campaign-runtime.json` names a process that lives on the new computer. Avoid the question. Copy the folder only after a clean stop.

## 15. Recover a manual job from the command line

The dashboard Manual jobs tab is the normal way. Two commands in `src/campaign.py` also exist. Both read JSON from standard input.

| Command | Use |
|---|---|
| `python3 src/campaign.py requeue JOB_ID` | Return a job to the queue after a person verified that no application was sent. |
| `python3 src/campaign.py reconcile JOB_ID` | Record a verified historical receipt for a manual or failed attempt without a new submission. |

The `requeue` input needs a nonempty `reason`, a nonempty `evidence` and `submission_verified_absent` set to `true`. The `reconcile` input needs `employer_confirmation_verified` set to `true`, a `reason` and the receipt `evidence`.

`src/campaign.py` has more commands:

- `status`
- `activity`
- `gate`
- `reserve`
- `result`
- `loop`

Do not run `loop` by hand. Use `jobapp.py start` or the service.

## 16. Run the tests

```sh
python3 -m unittest discover -s test -p 'test_*.py'
```

The tests use isolated state and simulated workers. They do not submit real applications. The test count changes as people add tests. Run `python3 -m unittest discover -s test -p 'test_*.py'` to get the current count.
