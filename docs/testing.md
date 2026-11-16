# Testing

This document describes the test suite of the project.
For each test file it gives the command.
It also gives the coverage and the isolation.
It also lists what the tests do not cover.

## Test count

The count of Python tests changes when a person adds or removes a test.
Run this command to get the current count: `python3 -m unittest discover -s test -p 'test_*.py'`.
Some tests skip when `node` is absent.

## Layout of the test folder

The `test/` folder holds twelve Python test files.
The `test/fixtures/ats/` folder holds JavaScript tests and HTML pages.
The HTML pages are fake employer sites.

| Path | Kind |
|---|---|
| `test/test_applied_jobs.py` | Python `unittest` |
| `test/test_campaign.py` | Python `unittest` |
| `test/test_campaign_logs.py` | Python `unittest` |
| `test/test_dbcli.py` | Python `unittest` |
| `test/test_jobapp.py` | Python `unittest` |
| `test/test_manual_groups.py` | Python `unittest` |
| `test/test_manual_jobs.py` | Python `unittest` |
| `test/test_manual_jobs_ui.py` | Python `unittest` |
| `test/test_overview_ui.py` | Python `unittest` |
| `test/test_score_location.py` | Python `unittest` |
| `test/test_services.py` | Python `unittest` |
| `test/test_verify.py` | Python `unittest` |
| `test/fixtures/ats/*.test.js` | JavaScript fixture tests |
| `test/fixtures/ats/run.js` | JavaScript fixture test runner |

## Rules that all tests share

Each Python test file uses temporary files.
No Python test starts a real worker.
No Python test opens a browser.
Two UI test files start the program `node` to run the page script when `node` exists. The program is not a browser.
No Python test reads a credential file.
No Python test touches the real database `data/jobs.db`.
Each file adds the `src/` folder to the import path by itself.
The tests need only the Python standard library.

## Run all Python tests

Run this command from the project folder.

```sh
python3 -m unittest discover -s test
```

The command finds every file that matches `test*.py`.
The folder `test/fixtures/` holds no Python test.

## Run one Python test file

Replace the file name in this command.

```sh
python3 -m unittest discover -s test -p 'test_campaign.py'
```

## Python test files

### `test/test_applied_jobs.py`

Command:

```sh
python3 -m unittest discover -s test -p 'test_applied_jobs.py'
```

Its header says that it tests what the ledger proves about applied jobs.
The header also says that it tests what the page never receives and that the route only reads.
It uses temporary state and a server on an ephemeral port.
It uses no worker, no browser and no real database.
It tests `src/applied_jobs.py` and `src/applied_jobs_ui.py`.
It also tests the route `/api/applied` of `src/campaign_monitor.py`.
The file has 18 tests in four classes.
The classes are `ListAppliedTests`, `PrivacyTests`, `ReceiptFileTests` and `AppliedRouteTests`.

Behaviors that `ListAppliedTests` proves:

- The list holds only jobs with the status `applied`, newest applied attempt first.
- A job with several attempts appears once with its applied attempt.
- A job with several applied attempts shows the newest one.
- A job that left the status `applied` does not appear, even with an old applied attempt.
- The controller check is true only for a reservation in the state `applied`.
- The list works on a database that has no reservations table.
- The list does not change the database.

Behaviors that `PrivacyTests` proves:

- The result holds no contact detail, no password and no whole evidence text.
- The code redacts an email address or a phone number inside the confirmation clause.
- The confirmation is one bounded clause, or `None`.
- An address that fails the check never becomes a link.

Behaviors that `ReceiptFileTests` proves:

- Only a plain file name in the receipts folder counts as a receipt file.
- A named file counts as saved only when the file exists.

Behaviors that `AppliedRouteTests` proves:

- The route returns the applied jobs and a summary.
- A foreign `Host` header gets the status 403 and no job data.
- A POST to the route changes nothing in the database.
- The page holds the tab and the panel, and the script reads the route.
- The script writes server values with `textContent` only and checks the scheme of each link.

What the file does not prove:

- It does not run the page script in a browser.
- It does not prove that the redaction covers every format of contact data.
- It does not read the real database, so it cannot find a screenshot file that is absent from the disk.

On 2026-11-15, the test author changed the applied module in 8 ways in scratch copies.
Each change made at least one test fail, after the author rewrote two weak tests.
The author of this document did not repeat this check.

### `test/test_campaign.py`

Command:

```sh
python3 -m unittest discover -s test -p 'test_campaign.py'
```

This file is the largest test file.
Its header says that it tests the controller.
The header says that the tests need no browser and no relay.
The header also says that the tests need no credentials and no real workers.
The controller is the module `src/campaign.py`.
The file defines a fake `Clock` class.
The tests use the fake clock to control time.
The tests also use `unittest.mock` to replace process and signal calls.

What the file isolates:

- It uses temporary directories for all state.
- It uses a fake clock.
- It replaces real worker processes with mocks.
- It replaces signals and process probes with mocks.

Behaviors that the file proves:

- A cap of zero denies the action and does not crash the status.
- A parent deadline kills the whole process group and reaps it.
- An exited leader does not leave background descendants.
- A heartbeat failure cannot skip the group kill.
- A persistent group fails closed and never overlaps a new worker.
- A stop request kills the group without a wait for the deadline.
- A stale heartbeat marks the runtime stale even when the process identifier is alive.
- An absent process identifier marks a fresh heartbeat stale.
- A terminal state stays stopped.
- Recovery is manual and idempotent.
- A recovery never becomes an automatic retry.
- A reviewed requeue keeps the history of the job.
- A reviewed requeue needs a new reservation.
- A reviewed requeue rejects a job that has an applied identity in any durable place.
- A reviewed requeue rejects an active equivalent reservation.
- A reviewed requeue needs an explicit attestation.
- A reviewed requeue needs a reason.
- A reviewed requeue needs evidence.
- A reviewed requeue rejects unknown states and rolls back as one unit.
- The requeue command line uses the same JSON formats as other commands.
- A reconcile command records a real receipt and does not submit again.
- A reconcile replay is idempotent across connections.
- A reconcile rejects an active reservation for the same job.
- A reconcile needs an explicit employer attestation.
- A reconcile needs a reason.
- A reconcile needs evidence.
- A reconcile rolls back the receipt and the reservation together.
- Progress comes from saved actions and not from liveness.
- The status contract hides secrets in telemetry.
- Reservations in the reserved state protect the exact target.
- A second supervisor fails at once on the singleton lock.
- An absent relay makes the supervisor wait.
- An absent relay makes the supervisor send a heartbeat and start no worker.
- The controller cleans orphan descendants when the leader is already gone.
- A reused worker process identifier fails closed.
- A wait for the platform reads changed caps again within one heartbeat.
- The controller gives no false alive or absent result on a permission error for a process group.
- A detached descendant group stays tracked after the leader exits.
- The controller never signals a reused group identity.
- A failed process tree probe fails closed.
- A descendant cannot take ownership of an external group.
- The activity report accepts all stages and resolves job labels.
- The activity report rejects an unknown job.
- The activity report rejects an invalid stage.
- The activity report rejects arbitrary text.
- The activity report tracks actions.
- The activity report tracks pages.
- The activity report tracks the stage clock.
- The activity report rejects foreign reports.
- The activity report rejects stale reports.
- The activity report rejects unsupervised reports.
- The controller computes activity staleness correctly.
- The controller computes supervisor health correctly.
- A worker death invalidates the activity.
- A new run invalidates the activity.
- A terminal state invalidates the activity.
- An outcome clears the activity only for the same job.
- A stale runtime snapshot does not erase the activity of a new run.
- A status read never writes the activity file.
- The supervisor clears the activity before it cleans detached processes.
- The supervisor sets the run environment and clears it at the end.
- Activity counts as progress only for a healthy current context.
- The activity command accepts JSON on standard input.
- The controller keeps outcome reasons.
- The controller bounds the length of outcome reasons.
- The controller removes secrets from outcome reasons.
- The runtime reloads paths between serial rounds.
- The runtime reloads deadlines between serial rounds.
- The runtime reloads the profile between serial rounds.
- An invalid profile or PDF fails closed with no worker and no reservation.
- An invalid runtime fails closed with no worker.
- The worker executable resolves a project path and a home path.

### `test/test_campaign_logs.py`

Command:

```sh
python3 -m unittest discover -s test -p 'test_campaign_logs.py'
```

Its header says that it tests live logs.
The header says that the tests need no workers and no network.
The header also says that the tests need no profile and no live database.
It tests the module `src/campaign_logs.py`.
The tests use temporary event files and a temporary SQLite database.

Behaviors that the file proves:

- An absent file shows no invented activity.
- An empty file shows no invented activity.
- The defaults come from the database and do not open live state.
- The reader returns the latest 200 complete lines in time order.
- A partial write becomes visible only when the line is complete.
- The reader keeps a record at the exact start boundary.
- The reader discards a partial record at the start.
- The byte budget holds and the reader never reads the whole file.
- The reader omits an oversized record.
- An oversized record does not hide later events.
- A malformed tail does not pull in older lines beyond the line limit.
- The projection uses fixed labels and never shows raw event messages.
- An absent job gives null enrichment.
- An absent database gives null enrichment and the reader creates no file.
- Enrichment opens the database read only and does not change it.
- Outcome reasons match the earlier attempt result and have secrets removed.
- Ties between outcome reasons resolve in a stable way.
- The reader skips malformed types.
- The reader skips malformed numbers.
- The reader skips unknown enum values.
- A very large integer timestamp does not overflow the SQLite parameter.
- The reader raises a real input error.
- The reader raises a real output error.
- The reader raises a real database error.

### `test/test_dbcli.py`

Command:

```sh
python3 -m unittest discover -s test -p 'test_dbcli.py'
```

Its header says that `src/dbcli.py` must never be a way around the campaign controller.
It copies `src/dbcli.py` and `src/db.py` into a temporary folder.
It runs the command in a separate process.
It creates a temporary database from `db.SCHEMA`.
It never touches the real ledger.

Behaviors that the file proves:

- The command refuses the result `applied`.
- A refused call leaves the ledger and the job status unchanged.
- A forged receipt text does not make `applied` acceptable.
- The results `manual`, `failed` and `skipped` still record.
- The command refuses an unknown result.

On 2026-11-13, the author changed `src/dbcli.py` back to the old rules in a temporary copy. Two tests failed. This shows that the tests detect the old behavior.

### `test/test_jobapp.py`

Command:

```sh
python3 -m unittest discover -s test -p 'test_jobapp.py'
```

Its header says that it tests local command line and configuration safety.
It uses temporary files only.
It does not start a worker or a service.
It tests `jobapp.py` and `src/settings.py`.
The tests build a fixture candidate in a temporary folder.

Behaviors that the file proves:

- The `setup` command does not reset history.
- The `setup` command does not reset pacing.
- The `setup` command does not reset approved documents.
- A fresh setup invents no candidate and cannot pass the readiness check.
- The `setup` command refuses to replace a candidate that is already set.
- The `setup` command refuses to replace the approved resume.
- An invalid setting update leaves the configuration unchanged.
- A credential cannot enter the profile and does not appear in an error.
- The `setup` command does not replace a corrupt configuration file.
- The `stop` command uses the real singleton lock and starts no worker.
- A target of zero closes the campaign before the database exists.
- The `status` command before the first run only reads.
- A failed atomic write keeps the previous file.
- The readiness check needs real queries and a readable PDF.
- The command line rejects malformed years.
- The command line rejects non-finite numbers.
- A closed target and zero caps are valid.
- A boolean cap is not valid.

### `test/test_manual_jobs.py`

Command:

```sh
python3 -m unittest discover -s test -p 'test_manual_jobs.py'
```

Its header says that the file tests four things.
The first is the duplicate submission guard.
The second is atomic answers.
The third is write protection.
The fourth is the handoff to the worker.
The file also tests the links on the cards. See `PostingLinkTests`.
It uses temporary state and a server on an ephemeral port.
It uses no worker, browser, or real database.
It tests `src/manual_jobs.py`, `src/manual_jobs_ui.py` and the routes of `src/campaign_monitor.py`.
The file has three test classes.
The first class is `ManualJobsTests`.
The second class is `DashboardWriteProtectionTests`.
The third class is `PostingLinkTests`.

Behaviors that `ManualJobsTests` proves:

- The code marks a job that reserved a submit slot as possibly sent.
- The code cleans the reason on screen.
- The reason on screen is never empty.
- The list works on a database that has no new tables.
- Only manual jobs appear in the list.
- The code does not requeue a possibly sent job without an explicit confirmation.
- The code requeues a confirmed possibly sent job.
- The code clears the reservation of that job.
- The code does not requeue a job that has an application elsewhere.
- The code does not resubmit a requeued job again.
- A rejected requeue leaves no answers behind.
- The code normalizes answers.
- The code ignores blank rows.
- The code refuses malformed answers.
- A refused answer changes nothing.
- A new submission replaces the earlier answers of that job.
- The worker prompt carries answers only for jobs that wait for an application.
- A round without reviewed answers has no answers block.
- The worker gets no answers for a job that has the applied status.

Behaviors that `DashboardWriteProtectionTests` proves:

- A request that does not come from the page of the dashboard changes nothing.
- A valid request requeues the job and saves its answers.
- The duplicate guard holds over HTTP and a replay is a conflict.
- A malformed request is a client error and not a server error.
- No other route accepts a write.
- A read does not change state.
- The page embeds the token and its script matches the content security policy.
- A page requested under a foreign `Host` header never receives the token or any job data. The test sends requests to five routes under five bad `Host` values. All 25 requests get the status 403.
- Both loopback spellings of the dashboard address still work.
- The page sends the header `Referrer-Policy: no-referrer`. A link to an employer site does not reveal the local address.

On 2026-11-13, the author removed the `Host` check from `do_GET` in a temporary copy. The foreign-host test failed 25 of 25 cases. This shows that the test detects the original token leak.

Behaviors that `PostingLinkTests` proves:

- A normal `http` or `https` address stays unchanged. These parts are allowed:
  - A query.
  - An encoded character.
  - A port.
  - A fragment.
- The code refuses 22 unsafe forms. The forms include these:
  - The schemes `javascript`, `data`, `file`, `ftp` and `vbscript`.
  - An address with no scheme or no host.
  - A user name or password in the address.
  - A control character. The code refuses the code points 0 to 32, 127 and 128 to 159.
  - A space.
  - A bad port or a port above 65535.
  - An address with more than 2000 characters.
  - An empty value.
- The code refuses a value that is not text.
- A database row with a hostile address never reaches the browser as a link. The test stores five rows and checks the exact list that `list_manual` returns.
- An `apply_url` that equals the `url` shows once.
- The script of the page sets `href` in one place. It never uses `innerHTML`, `outerHTML` or `insertAdjacentHTML`. It sets `target` and `rel` on each link. It refuses a scheme other than `http` and `https`.
- Every link on the page goes through the one checked helper. Each call gives the helper an address, a text and a hint. A call with no hint would show the word `undefined` in the tooltip.
- The title of a card is the link to the job page. A job with no safe address keeps a plain title. The card has no second row for the job page.

On 2026-11-13, the author changed the code in 12 ways in temporary copies. Each change made one named test fail. The first run of this check was invalid, because the test module did not load. The author ran it again with a baseline that passed. The details are in section 14 of `docs/safety-model.md`.

The author ran the real dashboard in a headless browser. All 41 manual jobs showed a link. 8 jobs showed two links. Each link had `target="_blank"` and `rel="noopener noreferrer"`. The page logged no error. No test file repeats this browser check.

### `test/test_manual_groups.py`

Command:

```sh
python3 -m unittest discover -s test -p 'test_manual_groups.py'
```

Its header says that it tests the group of a stopped job and the questions that the dialog prefills.
The header says that the group is a best guess and decides only what the dashboard shows first.
The tests use sample reasons. The reasons follow the text that the worker recorded.
Some tests use a temporary SQLite database.
It tests `src/manual_jobs.py`.
The file has 13 tests.

Behaviors that the file proves:

- Each known sample text lands in its group.
- The group `check_employer` always wins for a job with risk `possibly_sent`.
- An empty reason gives the group `other` and never an error.
- An unanswered question outranks a sign-in word in the same reason.
- A clearance stop is a decision.
- The function returns complete quoted questions in order.
- A question with elided text is never offered.
- The code ignores a quote that is not a question. It also ignores a repeat.
- The count of questions stops at the limit of 25 answers.
- The function `group_counts` lists the six groups in fixed order with zero counts.
- The counts add up to the listed jobs and each group has text.
- Each listed job carries its group, its headline and its questions.
- The listing of groups does not change the ledger.

What the file does not prove:

- It does not prove that every real reason lands in the right group.
- It does not test a reason text that nobody recorded yet.

On 2026-11-15, the test author changed the code of the group rules in 6 ways in scratch copies.
Each change made at least one test fail.
The author of this document did not repeat this check.

### `test/test_manual_jobs_ui.py`

Command:

```sh
python3 -m unittest discover -s test -p 'test_manual_jobs_ui.py'
```

Its header says that it makes static checks on the Manual jobs tab.
The checks cover what the markup and the script contain.
They do not cover how a browser renders them.
The tests read the text of `PANEL` and `SCRIPT` in `src/manual_jobs_ui.py`.
They also read the page in `src/campaign_monitor.py`.
The file has 27 tests in three classes.
The classes are `PanelTests`, `ScriptTests` and `PageScriptTests`.

Behaviors that `PanelTests` proves:

- The panel has the controls for the chips and the search. It also has the controls for the dismiss toggle and the dialog.
- Element ids are unique and each label points at an element.
- Each static form control and each static button has an accessible name.
- The chip group and the search box have names.
- The panel says that the group is a best guess. It also says what dismiss does.
- The intro says that a return to the queue submits nothing by itself.
- The markup does not use the word `resubmit`.
- The page embeds this panel.

Behaviors that `ScriptTests` proves:

- The script writes no server value as markup.
- The only write is the unchanged request to `/api/manual/resubmit`. It has three keys.
- Dismiss and restore never touch the server.
- Dismissed ids live in `localStorage` under the key `manual-dismissed`, and a storage error never throws.
- The page forgets a dismissed job that left the list.
- A refresh skips an open dialog and redraws only changed data.
- A redraw never clears the search box.
- The group filter lives in the address fragment. The script adds no history entry and does not leave the tab.
- The dialog prefills one row for each question, with a limit of 25 rows. It shows one blank row when it finds no question.
- Dialog rows have accessible names.
- A job with risk `possibly_sent` still needs the confirmation box before the request.
- Every job can open the dialog, whatever its group.
- A failed copy of the job id is silent.
- The card hides the full reason and the job id behind a `details` element.
- The badge shows only for a job with risk `possibly_sent`.
- The script text does not use the word `resubmit`, except in the route name.

Behaviors that `PageScriptTests` proves:

- The page script parses under `node`. The test skips when `node` is absent.
- The page script and the style of this tab are part of the page.

What the file does not prove:

- It does not run a click, a filter or the dialog. It reads the text of the script.
- It does not prove how the tab looks.
- It does not prove that the clipboard copy works.
- It does not prove that a screen reader reads the names.

### `test/test_overview_ui.py`

Command:

```sh
python3 -m unittest discover -s test -p 'test_overview_ui.py'
```

Its header says that it tests the Overview tab.
The tests build the page through `campaign_monitor.make_server` on an ephemeral port with temporary state.
They use no worker, no browser and no real database.
The file has 17 tests in two classes.
The classes are `OverviewPageTests` and `OverviewScriptBehaviorTests`.

Behaviors that `OverviewPageTests` proves:

- The status band comes first and says the worker state in words.
- The control `Needs you` is a button that opens the Manual jobs tab through the tab code.
- Attempt history and Technical details start closed. They are native `details` elements.
- The block with the exit reason and the retry time shows only when the state is not `working`.
- The block Jobs by current status is the main count block and the latest actions stay.
- The header and the footer use the new text about a return to the queue.
- The script writes no server value as HTML. The hash in the content security policy matches the script.
- The script renders both target shapes.
- The page script is valid JavaScript. The test skips when `node` is absent.

Behaviors that `OverviewScriptBehaviorTests` proves. These tests run the script under `node` against a small DOM stub:

- A null target shows the confirmed count as the big number. It shows no target and no count of what remains.
- A finite target keeps `X / target` and the count of what remains.
- The age of the last confirmed application comes only from a confirmed row in the latest actions.
- The band names the state in words and marks the dot by level.
- The exit reason and the retry time show only when the state is not `working`.
- The count on the `Needs you` button comes from the manual job count in the status.
- Data from a failed refresh has the label `last known`.
- A click on the `Needs you` button selects the Manual jobs tab and moves the focus there.

What the file does not prove:

- The DOM stub is not a browser. The tests do not check layout.
- No test measures the height of the page.
- No test checks the shape of the dot as drawn by a browser.

### `test/test_score_location.py`

Command:

```sh
python3 -m unittest discover -s test -p 'test_score_location.py'
```

Its header says that it tests the location rule of the scorer.
It uses pure functions only.
It uses no database, browser, or network.
It tests the function `location_ok` in `src/score.py`.
The tests use the preference `location_mode` with the value `united_states`.

Behaviors that the file proves:

- The scorer accepts LinkedIn metro names for US markets.
- The scorer rejects metro names for foreign cities.
- A company name in the location field is never a place.
- Locations that worked before still work.
- An explicit foreign country overrides a metro name that looks American.
- An explicit national scope or a scope that names the home state qualifies a remote job that has a state tag.
- A residency exclusion applies only to the home state of the profile.
- A state list that omits the home state rejects the job.
- A profile with no home state cannot pass a state list.
- A full state name matches its abbreviation in a city list.
- A known US remote tag can enter review without an explicit scope statement.
- A scope statement never overrides an explicit restriction.
- A location that was eligible stays eligible without a description.

### `test/test_verify.py`

Run this command:

```sh
python3 -m unittest discover -s test -p 'test_verify.py'
```

These tests use a temporary database and a controlled profile.
They prove these behaviors:

- The selector chooses a high-score job with no application history.
- The selector leaves terminal jobs alone.
- A description can move a job into the queue.
- Salary and experience restrictions still reject a job.
- A closed page becomes skipped.
- Jobs with history cannot change through this command.
- Invalid data leaves the database unchanged.
- Neither command records an application or changes a reservation.
- Both commands work on a database without the old pipeline columns.

The browser smoke check used a local job page and an isolated database.
The actual helper read the page after the campaign gate.
It stored the observed description and employer URL.
The scorer moved that job to `queued`.
The next helper call returned `no-job`.
The smoke check submitted no application.


### `test/test_services.py`

Command:

```sh
python3 -m unittest discover -s test -p 'test_services.py'
```

Its header says that it tests file system and backend behavior.
It never calls a native service manager.
It tests `src/services.py`.
The class `Manager` is a fixture for the service manager transport.
It is not an application process.
The tests build launchd and systemd files in a temporary folder.

Behaviors that the file proves:

- The launchd install does not start the service.
- A repeated install keeps the files.
- Special paths survive a systemd install.
- Special paths survive the ownership check.
- Special paths survive a start.
- The `status` action never creates state and never removes the stop file.
- A start is idempotent when an owned supervisor is in flight.
- An active foreground launcher refuses all service starts.
- An explicit start clears the stop file only when it starts the application.
- The code does not control a foreign loaded job through an owned disk file.
- A foreign Linux unit refuses replacement and uninstall.
- A malformed plist is a refusal and not a parser crash.
- A difference in an owned file needs `--replace` and does not restart.
- A foreign second file prevents a partial install even with `--replace`.
- A symbolic link never counts as owned.
- An uninstall stops owned services and keeps unrelated files.
- A stop uses bounded manager cleanup and does not change the stop file.
- The code does not report a manager failure as success.
- The code does not report a permission failure on status as stopped.
- A Linux host with no user manager gives a clear error and changes nothing.
- The code rejects a root with a newline.
- The code rejects a root with the name `omp`.
- A start does not install the service by itself.

## JavaScript fixture tests

The folder `test/fixtures/ats/` tests the browser adapters in `src/ats/`.
The tests use fake employer pages.
The pages are HTML files in the subfolders of `test/fixtures/ats/`.
Each test serves its pages from `127.0.0.1`.
No test contacts a real employer site.
No test uses a real mailbox or a real credential.

### Which runtime runs the fixture tests

Most fixture tests need the `browser` global.
The `browser` global exists only in the Eval runtime of omp.
Run these tests from an Eval cell.
The Eval runtime is Bun.
The command `bun` is not on the shell PATH.
Do not run `bun` in a shell.
Two fixture tests need no browser.
These two are `answers_standing.test.js` and `pacing.test.js`.
Their headers show a `bun` command.
The `node` command also runs `pacing.test.js`, as its header says.
The header of `answers_standing.test.js` names only `bun`.
Run both from an Eval cell when `bun` is not on the PATH.

### How to run a browser fixture test

Open an Eval cell in the JavaScript language.
Enter code of this form.
Replace the path with the absolute path of the project.

```js
const run = require("/absolute/path/to/test/fixtures/ats/account.test.js");
await run(browser);
```

Each file exports one asynchronous function.
The function takes the `browser` global.
The function opens managed headless tabs.
The function never uses the Chrome relay.

### Fixture test files

#### `test/fixtures/ats/run.js`

This file is the main runner for `src/ats`.
It serves the folder `test/fixtures/ats/` over `127.0.0.1`.
It drives a managed headless tab.
It checks adapter results and the `window.__log` record of the page.
It proves that the adapter sent no verification code.
It proves that the adapter clicked no forbidden control.
It proves that the adapter submitted nothing in a dry run.
It writes consent and evidence files to a temporary folder.
Its output has sections for four adapters.
These adapters have sections:

- ADP.
- Greenhouse.
- Lever.
- Generic.
The ADP sections test the one time code step.
The ADP sections prove that the adapter does not retry a wrong code.
The ADP sections prove that an unknown required question gives a manual result with the question text.

#### `test/fixtures/ats/workday.test.js`

This file tests `src/ats/adapters/workday.js`.
It serves `test/fixtures/ats/workday/`.
It proves that the adapter types nothing into sign in.
It proves that the adapter creates no account.
It proves that the adapter uses no forbidden control.
It proves that the adapter submits nothing in a dry run.

#### `test/fixtures/ats/icims.test.js`

This file tests `src/ats/adapters/icims.js`.
It serves `test/fixtures/ats/icims/`.
The page `icims_outer.html` wraps `icims_flow.html` in an iframe.
The iframe has the identifier `icims_content_iframe`.
The page posts each event to `/__log`.
The assertions survive the real page changes between steps.

#### `test/fixtures/ats/sr_ashby.test.js`

This file tests the SmartRecruiters and Ashby adapters.
It serves `test/fixtures/ats/sr_ashby/`.
It proves that the adapter submits nothing in a dry run.
It proves that the adapter never ticks a box for promotion mail.
It proves the same for talent community boxes.
It proves that the adapter does not guess an unknown question.

#### `test/fixtures/ats/account.test.js`

This file tests `src/ats/account.js` and `src/ats/mailbox_account.js`.
It serves a small in-memory employer site.
The site has five endpoints.
These are register, lookup, login, reset, and verify.
The credentials go to a temporary vault.
The mailbox is a fake object.
A fake Gmail tab covers the gates of the provider.

#### `test/fixtures/ats/account_other.test.js`

This file tests the account gate in the Ashby and SmartRecruiters adapters.
It also tests the shared `singlepage` runner.
It replaces `src/ats/account.js` with a stub through the require cache.
The stub records each call of `ensureAccount`.
The header lists four proven behaviors:

- A live run with `allowAccountCreation` set to true calls `ensureAccount` once and the form flow resumes.
- A dry run never calls `ensureAccount`.
- A live run without the flag never calls `ensureAccount`.
- The test returns a manual result from the helper as it is.
- The adapter submits nothing in that case.
- A wall that remains after a helper call gives `account_flow_failed` and no second call.

#### `test/fixtures/ats/account_wd_icims.test.js`

This file tests the authorized account path in the Workday and iCIMS adapters.
It serves `account_wd/`, `account_ic/`, `workday/`, and `icims/`.
It replaces `src/ats/account.js` with `account_wd/stub_account.js`.
The stub uses fake passwords.
The stub never touches `credentials.js` or a mailbox.
The header lists these proven behaviors:

- A dry run never calls `ensureAccount` and reports `wouldCreateAccount` or `wouldResetPassword`.
- A live run without authorization never calls `ensureAccount`.
- An authorized live run calls `ensureAccount` once.
- The adapter hooks tick the terms box only.
- The adapter hooks leave the promotion box untouched.
- The adapter hooks fill the same value in the confirm password field.
- The application continues on the same tab to the confirmation page.
- A policy stop comes back as a manual result.

#### `test/fixtures/ats/answers_standing.test.js`

This file tests the profile answers in `src/ats/answers.js`.
The answers apply to every employer.
It uses no browser.
It uses no network.
It uses no real profile.
Its header gives this command:

```sh
bun test/fixtures/ats/answers_standing.test.js
```

Do not run this command in a shell that has no `bun`.
The header proves that each profile answer resolves its own question.
It also proves that the answer does not leak onto a neighbor question.
The neighbor questions cover these topics:

- Veteran status.
- A license.
- Convictions.
- Clearances.
- The prospective employer.

#### `test/fixtures/ats/pacing.test.js`

This file tests `src/ats/pacing.js`.
It uses no browser and no network.
It uses an injected clock.
It writes state to a temporary file.
The real file `data/ats-pacing.json` is never touched.
Its header gives this command:

```sh
bun test/fixtures/ats/pacing.test.js
```

The header also allows this command:

```sh
node test/fixtures/ats/pacing.test.js
```

The file counts passes and failures with a `check` helper.
It prints `ok` or `FAIL` for each check.

## Number of JavaScript tests

This document gives no total for the JavaScript tests.
The author did not run them.
The Python test count changes when a person adds or removes a test.
Run the command in the section "Run all Python tests" to see the current count.

## What the tests do not cover

- No test submits a real application to a real employer.
- No test uses the real Chrome relay.
- No test starts a real worker or a real model call.
- The tests do not check model authentication.
- No test starts launchd on macOS.
- No test starts systemd on Linux.
- The service tests use a fixture transport only.
- No test file mentions the discovery scripts for LinkedIn or Indeed.
- The verification tests exercise `src/ingest.py` and the schema setup in `src/pipeline.py`.
- The file `test/test_dbcli.py` covers the refusal of `applied` in `src/dbcli.py`. It does not cover the commands `get-job` and `save-verification`.
- No test covers `scripts/store_credential.py`.
- No test covers the Easy Apply script `src/apply_easy.js`.
- The fixture pages are not real employer pages. A real employer can change a form and break an adapter. The fixture tests cannot detect this.
- No test checks whether the worker prompt in `src/CAMPAIGN_WORKER.md` makes the model behave correctly.
- No test checks the look of a dashboard tab in a browser.
- No test checks the clipboard copy button or a screen reader.
- No test shows that every real manual reason gets the right group.
- No test checks the receipt screenshot files on disk.

## Related documents

See `docs/known-gaps.md` for the list of unverified items and absent items.
