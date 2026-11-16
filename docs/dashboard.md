# Dashboard guide

This document describes the dashboard. It is a snapshot of the code as read on 2026-11-15. It comes from these files:

- `src/campaign_monitor.py`
- `src/applied_jobs.py`
- `src/applied_jobs_ui.py`
- `src/manual_jobs.py`
- `src/manual_jobs_ui.py`
- `src/monitor_security.py`

## 1. Terms

| Term | Meaning |
|---|---|
| monitor | The dashboard web server. |
| dashboard | The web page. |
| worker | The AI process that opens the browser and fills forms. |
| supervisor | The Python process that starts, watches and stops the worker. |
| job | One row in the `jobs` table. |
| application | One submitted employer application. |
| attempt | One row in the `applications` table. |
| outcome | The result of an attempt. |
| applied job | A job with status `applied`. |
| manual job | A job with status `manual`. |
| queued job | A job with status `queued`. |
| reservation | A row in `campaign_reservations`. |
| receipt | The employer confirmation text. |
| standing answer | An answer in `profile.json` under `answers` that applies to every employer. |
| job answer | A row in `job_answers` that applies to one job. |
| return to queue | The dashboard action that saves job answers and puts one manual job back in the queue. The route and the function keep the name `resubmit`. |
| group | The label that the Manual jobs tab gives to a manual job. The code guesses it from the reason text. |

## 2. Start and open the dashboard

The monitor listens on `127.0.0.1` only. The default port is 8765. The setting `campaign.runtime.dashboard_port` sets the port.

There are three ways to start the monitor:

- `python3 jobapp.py start` starts the monitor and the supervisor in one terminal.
- `python3 jobapp.py service start` starts the monitor as its own background service.
- `python3 src/campaign_monitor.py --port 8765` starts the monitor alone.

The last command accepts a port from 0 to 65535. The port 0 lets the system choose a port. The command prints the address.

Open `http://127.0.0.1:8765/` in a browser on the same computer. No other computer can reach the page.

The page has a header, four tabs and a footer. The tabs are in this order:

1. Overview
2. Applied jobs
3. Manual jobs
4. Live logs

Each tab has an address fragment that opens it directly. The fragments are `#overview`, `#applied`, `#manual` and `#logs`. The fragment `#manual/GROUP` opens the Manual jobs tab with one group filter. See section 7.1.3.

Press the Left Arrow key or the Right Arrow key to move to the next tab. Press Home for the first tab. Press End for the last tab.

The header says that the progress and the logs only show data. It tells the reader to use Manual jobs to return a blocked job to the queue. The footer says that credentials, prompts and raw evidence never show on the page.

## 3. The routes

The monitor has seven read routes and two write routes.

| Method | Path | Effect | Writes |
|---|---|---|---|
| GET | `/` | Returns the page. The page holds the token in a `meta` tag. | No |
| GET | `/api/status` | Returns a safe view of the campaign status as JSON. | No |
| GET | `/api/logs` | Returns a bounded and cleaned tail of the activity log as JSON. | No |
| GET | `/api/applied` | Returns `{jobs, summary}` as JSON. The list holds the applied jobs and their confirmation detail. | No |
| GET | `/api/applied/JOB_ID/receipt` | Returns the applied job's available, evidence-linked receipt image. | No |
| GET | `/api/skipped` | Returns skipped jobs with review and duplicate-risk information. | No |
| GET | `/api/manual` | Returns `{jobs, groups}` as JSON. The list holds the manual jobs. The groups are the six group labels with a count each. | No |
| POST | `/api/manual/resubmit` | Saves job answers and puts one manual job back in the queue. The request is the same as before the redesign. | Yes |
| POST | `/api/skipped/requeue` | Saves reviewed answers and requests a fresh eligibility review for one skipped job. | Yes |

Any other GET path returns the status 404. Any other POST path returns the status 404 with a JSON error.

Seven other methods return the status 405. The header `Allow` says `GET, POST`. The methods are:

- `HEAD`
- `PUT`
- `PATCH`
- `DELETE`
- `OPTIONS`
- `CONNECT`
- `TRACE`

### 3.1 How the read routes open the database

The database-backed read routes open `data/jobs.db` in read-only mode. They also set `PRAGMA query_only`. They never create, migrate or change the database.

If a read route fails, it returns the status 503 and a fixed text. The text has no path, no error detail and no campaign data.

### 3.2 Response headers

Every response from the read routes and the page has these headers:

- `Cache-Control: no-store`
- `X-Content-Type-Options: nosniff`
- `Referrer-Policy: no-referrer`
- `Content-Security-Policy` that allows only the one page script by its hash

The monitor does not log request paths. Request paths can hold sensitive text.

### 3.3 What the status route hides

The status route copies only known fields. It keeps these protections:

- It shows a supervisor state only when the state is in a fixed list. Otherwise it shows `stale`.
- It shows the last exit reason only when the text matches a short safe pattern.
- It shows the activity stage and action only when they are in fixed lists.
- It never returns worker output.
- It never returns prompts.
- It never returns credentials.
- It never returns raw evidence.

## 4. The Overview tab

The tab only reads data. The page asks `/api/status` every 5 seconds. A failed request shows a red banner and keeps the last known data.

The tab has these parts from top to bottom:

1. The status band.
2. The Progress card and the block Jobs by current status.
3. The panel Current application.
4. The table Latest 20 job actions.
5. The closed fold-out Attempt history.
6. The closed fold-out Technical details.

### 4.1 The status band

The band answers two questions. The first question is `Is it working?`. The second question is `Does anything need you?`.

The first question shows the supervisor state in words. A dot stands next to the words. The color of the dot is not the only signal. The shape of the dot also changes with the level.

| Level | Dot | When the level applies |
|---|---|---|
| `good` | Filled circle | The state is `working` and recent activity exists. |
| `warn` | Hollow ring | The state is not `working`, or the worker is `working` with no recent activity. |
| `bad` | Diamond | The refresh failed, the heartbeat is stale or the state is `error`. |

The words come from this table.

| State | Words on the page |
|---|---|
| `starting` | Starting |
| `working` | Working |
| `between_rounds` | Between rounds |
| `waiting_relay` | Waiting for the browser connection |
| `waiting_platform` | Waiting for a job site |
| `stopped` | Stopped |
| `complete` | Finished: target reached |
| `error` | Error |
| `stale` | Stale |

Three more texts exist:

- A heartbeat that is too old shows `Stale · last reported:` and the earlier state in words.
- A worker that works with no recent progress adds `· no recent activity`.
- A failed refresh shows `Unavailable · last known data`.

A line under the state explains it. A fresh heartbeat shows that the supervisor lives. It does not show that an application went to an employer. Only a receipt shows that.

The second question is a button. It reads `Needs you: N manual jobs`. The page takes N from the count of jobs with status `manual` in `/api/status`. For one job the button reads `Needs you: 1 manual job`. After a failed refresh the text adds `(last known)`. Before the first status the button reads `Manual jobs: waiting for status`.

Press the button to open the Manual jobs tab. The focus moves to the tab button.

The count on the button includes the jobs that a person dismissed on the Manual jobs tab. The dismiss action hides a card in one browser only. See section 7.1.4.

A block with the last exit reason and the next retry time shows only when the state is not `working`. The block also shows after a failed refresh or a stale heartbeat.

### 4.2 The Progress card

The big number is the count of confirmed applications. The caption under it reads `confirmed applications`. The note says that the count is the count of unique jobs with a confirmed application.

For a finite target the card also shows `/` and the target after the big number. The caption reads `confirmed of the target`. A line shows how many confirmed applications the campaign still needs. At the target, the note says `Target reached. Counts unique confirmed jobs.`

For `campaign.target_total:null`, the page hides the target and the count that remains. The note says `No target is set. The campaign keeps going.` The API returns these values:

- `target:null`
- `remaining:null`
- `complete:false`

Null does not mean zero or a completed target.
Finite targets keep numeric progress and completion behavior.

The card also has the line `Last confirmed application`. The page takes it from the newest row with the result `applied` among the 20 latest actions. It shows the age of that row. The page hides the line when none of the 20 rows has the result `applied`. A job that the system confirmed long ago does not appear in the 20 rows. Use the Applied jobs tab to see all confirmed jobs.

Only confirmed unique jobs count toward the target. The table of attempts counts every attempt, also the ones that failed or repeat a job.

The page does not show the count of submission attempts in the last 24 hours as confirmed applications. The count belongs to the pacing events of kind `apply`. It does not prove an application. It now sits in the fold-out Attempt history.

### 4.3 Jobs by current status

The block shows the total count of jobs. It then shows the count of jobs for each job status. This block is the main count block of the tab. It is not inside a fold-out.

### 4.4 Current application

The panel shows what the worker reports about its current job. It shows these items:

- The company.
- The role.
- The job id.
- The stage.
- The action.
- The page number.
- Two ages.

The heading changes to `Last reported application` when the report is stale or the worker is not healthy. The panel does not prove that an application went out.

### 4.5 Latest 20 job actions

The table shows the 20 newest attempts. Each row has these items:

- The time.
- The company.
- The role and the job id.
- The outcome.

The heading says that the rows are tries and not unique jobs.

A row with one of four outcomes also shows the reason. The outcomes are `manual`, `failed`, `deferred` and `skipped`. The page clamps the reason to two lines. The full text is in the tooltip of the reason.

### 4.6 The two fold-outs

Both fold-outs start closed. A native `details` element makes each fold-out.

The fold-out `Attempt history (every try, including repeats)` holds these items:

- The count of recorded attempts.
- The count of submission attempts in the last 24 hours.
- The count of attempts for each outcome.

The fold-out `Technical details` holds these items:

- The age of the heartbeat.
- The age of the worker.
- The time of the last recorded activity.
- The age of the latest recorded job action.
- The worker process id and the supervisor process id.
- The worker state code.
- The restart count.

Current job statuses and historical attempt outcomes measure different things. See `docs/operations.md` for a guide to each part.

## 5. The Applied jobs tab

The tab shows every job with the status `applied`. It shows what the database proves about each job. The tab only reads data. It never changes a job.

The page asks `/api/applied` every 10 seconds. It asks only while the tab is open. It sends no request while an earlier request is in flight. It redraws the list only when the data changed. A failed request shows a red banner and keeps the last data. The request stops after 6 seconds.

### 5.1 The summary and the search

Four counts show above the list:

- The count of applied jobs.
- The count of jobs with confirmation text.
- The count of jobs with a saved receipt screenshot.
- The count of jobs that the controller did not record.

The last count has the label `recorded outside the controller`. It counts the jobs that hold no reservation in the state `applied`.

The search box has the label `Search by company, role or place`. It matches the company, the role and the location. It ignores case. A status line says how many jobs show, for example `Showing 3 of 31`.

### 5.2 What each card shows

The list has the newest applied attempt first. Where two times are equal, the list orders by job id. The list holds at most 500 jobs. The constant `MAX_JOBS` in `src/applied_jobs.py` sets this limit.

Each job has a card with these items:

- The role title and the company. The title is a link when the address of the job page is safe.
- The badge `Applied`.
- The time of the newest applied attempt and its age.
- The location.
- The salary range.
- The employment type.
- The route.
- One sentence with the confirmation of the employer.
- A receipt screenshot preview and full-size link when available, or a note when unavailable.
- A note when the job has no applied reservation.
- A link to the employer application page when the job has one.
- A closed fold-out `More detail` with the job id and the count of attempts.

The card shows the newest attempt with the result `applied`. A job that left the status `applied` does not show, even with an old applied attempt.

The salary shows as `$LOW to $HIGH`, or as one number when only one value exists. The route has three values:

| Route text | Rule in `list_applied` |
|---|---|
| `LinkedIn Easy Apply` | The job has `easy_apply` set, or `apply_method` is `easy` or `easy_apply`. |
| `Employer site` | `apply_method` is `external` or `ats`, or the job has an `apply_url`. |
| `Route not recorded` | No other rule matches. |

### 5.3 The confirmation sentence

The evidence of an applied attempt can hold contact data. For this reason the page never gets the whole evidence text. See section 15 of `docs/safety-model.md`.

The function `confirmation_sentence` in `src/applied_jobs.py` makes the sentence. It does these steps:

1. It joins the words of the evidence with single spaces.
2. It looks for the first match of the pattern `_CONFIRMATION`. The pattern is a fixed list of phrases such as `thank you for applying` and `your application was`.
3. It keeps the text from that phrase to the end of the clause.
4. It passes the clause through `campaign.outcome_reason`. That function removes URLs, email addresses and secrets.
5. It replaces a phone number with `[redacted phone]`.
6. It cuts the result to 200 characters.

A clause ends at one of these marks when a space or the end of the text follows it:

- A full stop.
- An exclamation mark.
- A question mark.
- A semicolon.
- A quotation mark.

If no phrase matches, the card says `No confirmation sentence is recorded for this job.` The card then has a different color. This text does not prove that no application went out. It shows only that the phrase list found no match.

### 5.4 Receipt screenshots

The function `receipt_file` looks in the evidence for a path in `logs/campaign/receipts` or `logs/ats`, optionally prefixed by the current project root. NAME has 1 to 120 characters. Each character is one of these:

- A letter.
- A digit.
- A dot.
- An underscore.
- A hyphen.

NAME must end in one of four extensions:

- `.webp`
- `.png`
- `.jpg`
- `.jpeg`

The function returns the name only. It never returns a path.

The server serves only an image named by the latest applied attempt, inside the allowed receipt directories, after checking its type and path. The card displays a thumbnail and a link to the full screenshot. If the image is missing, inaccessible or invalid, the card says no screenshot is available. A missing screenshot does not trigger another application. Screenshots may contain personal information.

### 5.5 The controller note

The field `controller_checked` is true when the job also holds a reservation in the state `applied`. If it is false, the card shows a note. The note says that the application has no applied reservation and that the system recorded it outside the campaign controller. The last sentence of the note has fixed text. The code does not check the job for that sentence.

### 5.6 The links

The title and the employer link use the same address check as the Manual jobs tab. The function `safe_link` in `src/manual_jobs.py` checks each address on the server. See section 7.1.1 for the rules.

The page checks the scheme a second time. The function `appliedLink` in `src/applied_jobs_ui.py` sets `href`. It sets `target` to `_blank` and `rel` to `noopener noreferrer`. The server sends `null` for the employer link when it equals the address of the job page.

### 5.7 What the tab does not do

- It does not write to the database.
- It does not change a job or an attempt.
- It does not submit an application.
- It does not verify a receipt with the employer.
- It does not recreate missing screenshots.
- It does not send the whole evidence text to the page.

A foreign `Host` header gets the status 403 on this route, like on every route.

### 5.8 Skipped jobs

The Skipped jobs tab shows the latest reason, supports search and pagination, and offers a review dialog. The dialog requires a reason to revisit the job and accepts optional exact-question answers. If submission is uncertain, confirm against the employer account before queueing; applied or actively reserved equivalents remain blocked. Queueing requests a fresh eligibility review, not an application submission.

## 6. The Live logs tab

The tab only reads data. It asks `/api/logs` every 2 seconds. It asks only while the tab is open and not paused.

The tab shows up to 200 entries, oldest first. Each entry has a time, an event name and a message. Some entries add the company, the role and the job id.

The tab has two controls:

1. The button Pause stops the refresh. The same button then reads Resume.
2. The box Auto-follow latest keeps the window at the newest entry.

The status line shows `Live`, `Paused` or `Offline`. It also shows the time of the last good refresh.

The tab shows operational activity only. It does not show model thought, credentials or mailbox content.

## 7. The Manual jobs tab

The tab lists every manual job. A manual job is a job that the worker could not finish. The tab lets a person add answers and put the job back in the queue.

This is the one tab that changes the database.

### 7.1 What each card shows

The page asks `/api/manual` every 5 seconds while the tab is open. It does not refresh while the dialog is open. It redraws the list only when the data changed.

Each manual job has a card with these items:

- The role title and the company. The title is a link when the address is safe. See section 7.1.1.
- The badge `May have been sent`, only for a job that may have reached the employer.
- A headline of one line. The headline depends on the group.
- A second link for the employer page when the job has one.
- The age of the last stop.
- The count of saved job answers, or the text `No saved answers`.
- The text `Dismissed in this browser` on a dismissed card.
- A closed fold-out `Show the full reason`. It holds the full reason and a button with the job id.
- A button that opens the dialog.
- The button `Dismiss`, or the button `Restore` on a dismissed card.

The job id is inside the fold-out. The button with the job id copies the id to the clipboard. If the copy fails, the button does nothing.

The server orders the list with the newest stop first. Where the stop time is equal, it orders by job id. The page puts the cards in group sections. Each section has a heading with the group label and its count. A group with no job has no section.

The reason comes from the newest attempt with the outcome `manual`. The code cleans it before it shows it. The cleaner removes URLs, email addresses and secrets. If there is no attempt, the card shows `No reason recorded`.

#### 7.1.1 The links

The title of each card is a link to the job page. The page where the scraper found the job is that page. A card with an employer application page also shows a second link under the headline. A link opens the page in a new browser tab. The dashboard stays open.

| Link | Where it goes | When it shows |
|---|---|---|
| The title of the card | The page where the scraper found the job. This is the `url` of the job. | When the address is safe. Otherwise the title is plain text. |
| `Open the employer application page` | The page of the employer. This is the `apply_url` of the job. | When the job has an `apply_url`, the address differs from the `url`, and the address is safe. |

Each link has a tooltip. Each link also has an `aria-label` that says the link opens in a new tab. A screen reader reads this label.

The dialog shows both links under the headline. The dialog uses the full text `Open the job posting` for the first link, because the title is not a link in the dialog. Use these links to open the job while you read the questions.

The scraper wrote these addresses, so the dashboard does not trust them. The function `safe_link` in `src/manual_jobs.py` checks each address on the server. It accepts an address only when all these rules hold:

- The scheme is `http` or `https`.
- The address has a host name.
- The address has no user name or password. An address such as `https://www.linkedin.com@other.example/` fails.
- The address has no control character. The code refuses the code points 0 to 32, 127 and 128 to 159. A space, a tab and a new line fall in this range.
- The address has at most 2000 characters.
- The port is a number from 0 to 65535. An address with no port also passes.

If an address fails, the server sends `null` for that link. A card with no safe `url` shows its title as plain text. A card with no safe `apply_url` shows no employer link.

The page checks the scheme a second time before it sets the link. The function `manualLink` in `src/manual_jobs_ui.py` is the one place in this tab that sets `href`. It sets `target` to `_blank` and `rel` to `noopener noreferrer`. The new tab cannot reach the dashboard through `window.opener`. The dashboard sends the header `Referrer-Policy: no-referrer`. The employer site does not learn the local address of the dashboard.

A click on a link does not change the job, the queue or the database. A link is not a return to the queue.

#### 7.1.2 The groups

The function `classify` in `src/manual_jobs.py` gives each job one group. The group is a best guess. It comes from keyword rules on the reason text. The first rule that matches wins. The group decides only what the page shows first. It never allows or blocks a return to the queue. It never changes the database.

The rules run in this order:

1. A job with the badge `May have been sent` always gets the group `check_employer`.
2. Words such as `unanswered` and `required question` give the group `needs_answer`.
3. A reason with `captcha` or `password` gets the group `retry_later`.
4. Words such as `consent`, `work authorization` and `clearance` give the group `decision`.
5. Words such as `relocation` and `reside within` give the group `not_a_fit`.
6. The weak words `unknown`, `unconfirmed` and `not established` give the group `needs_answer`.
7. No match gives the group `other`.

The tuple `_GROUP_RULES` in `src/manual_jobs.py` holds the full word lists. A reason with new text can land in the wrong group. See gap 14 in `docs/known-gaps.md`.

The groups come in this order:

| Key | Label | Headline on each card |
|---|---|---|
| `check_employer` | Check the employer | This job may already have been submitted. Check the employer account first. |
| `needs_answer` | Needs your answer | The employer asks a required question that has no reviewed answer. |
| `retry_later` | Retry later | A sign-in, verification or employer site step failed. |
| `decision` | Needs your decision | This needs your decision on consent, authorization or clearance. |
| `not_a_fit` | Not a fit now | The location or eligibility does not match the current profile. |
| `other` | Other | The worker stopped for a reason that needs a review. |

The function `group_counts` returns all six groups in this fixed order. It includes the groups with a count of zero.

#### 7.1.3 The filters, the search and the address

A row of chips sits above the list. The chips are:

- `All` with the count of shown jobs.
- One chip for each group, with its count. A chip for an empty group has a dashed border.
- `May have been sent` with the count of jobs that have the badge.

Press a chip to filter the list. A second line gives a hint for the chosen group. The server sends the hint text of the group. The hint for the chip `May have been sent` tells the reader to check the employer account first. The page hides the chips when it has no job to show.

The counts on the chips come from the cards that the page can show. A dismissed job does not count unless `Show dismissed` is on.

The search box has the label `Search by company, role or reason`. It ignores case. It matches the company, the role and the full reason. It works together with the chip filter. The search box keeps its text when the page redraws.

A status line says `Showing X of Y job(s).` Y counts the jobs that stay visible. A job that a person dismissed counts only when `Show dismissed` is on.

The page keeps the chip filter in the address fragment:

| Fragment | Filter |
|---|---|
| `#manual` | All jobs. |
| `#manual/GROUP` | One group. GROUP is a group key from section 7.1.2. |
| `#manual/sent` | The jobs that may have been sent. |

The page changes the fragment with `history.replaceState`. This adds no entry to the browser history. A reload opens the same tab and the same filter. A fragment with an unknown group falls back to `All` after the first load.

#### 7.1.4 Dismiss a card

The button `Dismiss` hides one card in this browser only. It does not call the server. It does not change the job. The job stays in the manual list and in the count on the Overview button.

The page stores the ids of dismissed jobs in `localStorage` under the key `manual-dismissed`. It stores ids only. If the browser refuses the storage, the choice lasts until the page closes.

When a dismissed job leaves the manual list, the page forgets its id.

A button `Show dismissed (N)` shows only when N is more than zero. Press it to show the dismissed cards. A dismissed card has the button `Restore`. Press `Restore` to put the card back.

### 7.2 The badge

The tab has one badge.

| Badge | Condition in the code | Meaning |
|---|---|---|
| `May have been sent` | The job has a reservation in the state `manual` or `failed`. | The worker reserved a submit slot. An application may have reached the employer. |

A card with no badge has no reservation in the state `manual` or `failed`. The code then treats the job as `not_sent`. The worker stopped before the final Submit.

A reservation exists only after the worker finished the form and reserved a submit slot. For this reason a job with a reservation is a risk.

A lack of evidence is not proof that no application went out. The badge `May have been sent` stays until a person checks.

The label of the button on the card depends on the group:

| Group | Button label |
|---|---|
| `needs_answer` | `Add answers and return to queue` |
| `check_employer` | `Check, then return to queue` |
| `retry_later` | `Return to queue` |
| `decision`, `not_a_fit` and `other` | `Return to queue anyway` |

Every group can open the dialog. The label shows what the page expects from the person.

### 7.3 Step by step: return a job that needs an answer

1. Open the dashboard and select the tab Manual jobs.
2. Press the chip `Needs your answer`.
3. Find the card. Read the headline.
4. Press `Show the full reason` to read the whole reason.
5. Press `Add answers and return to queue`.
6. Read the headline in the dialog.
7. Check the question rows. Section 7.5 says how the dialog fills them.
8. Type the answer in the field with the name `Answer 1`. Use the next fields for more rows.
9. Press `Add another answer` if you need more rows.
10. Leave unused rows blank.
11. Press `Save answers and return to queue`.
12. Read the green status text. It says that the job went to the queue and how many answers the system saved.

Add only answers that are true. The worker uses an answer as a fact.

Press `Save and return to queue` with all answer fields blank to send no answer. The job then goes to the queue without a new job answer.

For a job in another group, read the note in the dialog first. The note says that an answer does not usually fix this stop. Add an answer only when you have a real one.

### 7.4 Step by step: return a job that may have been sent

1. Open the employer account for that job. Use the employer site, not the dashboard.
2. Look for an application, a confirmation email or a status page for that job.
3. If you find any sign of an application, do not return the job to the queue. Stop here.
4. If you find no sign of an application, return to the dashboard.
5. Press the chip `May have been sent` or `Check the employer`.
6. Press `Check, then return to queue`.
7. Add answers as in section 7.3.
8. Tick the box that says `This job may already have been submitted. I checked the employer account and no application was received.`
9. Press the save button.

The dialog shows the check box only for a job that has the badge `May have been sent`. The page refuses to send the request while the box is clear. The page then says `Confirm that no application was received before returning this job to the queue.` The server also refuses the request. See section 7.6.

### 7.5 The answer dialog

The dialog has these parts:

| Part | Rule |
|---|---|
| Title | `Return job to queue`. |
| Job line | The company, the role and the job id. |
| Headline | The headline of the group. |
| Group note | A note for each group except `needs_answer`. See below. |
| Links | The links of section 7.1.1, when the job has safe addresses. |
| `Show the full reason` | A fold-out with the full reason. It opens at the start when the dialog has no prefilled question. |
| Answer rows | One row for each complete question in the reason, or one blank row. Each row has a question field and an answer field. |
| Question field | At most 500 characters. Its name is `Question N`. |
| Answer field | At most 2000 characters. Its name is `Answer N`. |
| `Add another answer` | Adds one more row. It stops at 25 rows. |
| Confirmation box | Shows only for a job that may have been sent. |
| `Cancel` | Closes the dialog and sends nothing. |
| Save button | The text is `Save and return to queue` when no answer field has text. Otherwise it is `Save answers and return to queue`. The button stays disabled until the server replies. |

The group note for `retry_later` says that the worker tries again when it next reaches the job. The note for every other group except `needs_answer` says that an answer does not usually fix this stop.

The function `quoted_questions` in `src/manual_jobs.py` finds the questions. It takes each quoted text that ends with a question mark. The text before the mark has 8 to 500 characters. It skips a question with elided text, which has three dots. It skips a repeat. It returns at most 25 questions. The dialog then makes one row for each question. A question with elided text never gets a row.

The page does not send a prefilled row that has a blank answer. The page does send a row that you add and leave blank. The server then skips it.

The server applies these rules to the rows:

1. The request may hold at most 25 answers.
2. Each row must be an object with only the keys `question` and `answer`.
3. Both values must be text.
4. The server joins the words of a question with single spaces.
5. The server removes spaces at the start and the end of an answer.
6. The server skips a row with a blank question and a blank answer.
7. A row with only one of the two is an error.
8. A question of more than 500 characters is an error.
9. An answer of more than 2000 characters is an error.
10. When two rows have the same question after step 4, the last row wins.

### 7.6 The confirmation checkbox

The server decides if a job has the risk `possibly_sent`. It does not trust the page for that.

1. The server reads the reservation of the job.
2. If the reservation state is `manual` or `failed`, the risk is `possibly_sent`.
3. For `possibly_sent`, the request must have `nothing_sent_confirmed` set to `true`.
4. Without it, the server returns the error code `confirmation_required` with the status 409.

The message says: check the employer account for an application, then confirm that none arrived.

### 7.7 What happens in the database

The server calls `manual_jobs.resubmit`. That function calls `campaign.requeue`. The function `campaign.requeue` is the one place where a job returns to the queue.

`campaign.requeue` does all of these actions in one transaction. The transaction starts with `BEGIN IMMEDIATE`.

1. It reads the job. If the job does not exist, it fails with `unknown_job`.
2. It checks if the system already sent an equivalent job. It also checks for an equivalent job that is active. If it finds one, it fails with `already_sent_or_reserved`.
3. It checks that the job status is `manual`, `skipped` or `failed`. Otherwise it fails with `requeue_requires_finished_non_applied_job`.
4. It inserts one row in the `applications` table with the outcome `deferred` and the run id `campaign-requeue`.
5. It deletes the rows in `campaign_reservations` for the job that have the state `manual`, `failed` or `skipped`.
6. It sets the job status to `queued`.
7. If the request has answers, it deletes the old rows in `job_answers` for the job.
8. It then inserts one row in `job_answers` for each new answer.
9. It commits the transaction.
10. It writes the event `requeued` to the activity log.

If any step fails, the transaction rolls back. A rejected request never leaves a job answer behind. A rejected request never leaves a half changed job.

The `deferred` row has this reason: `Resubmitted from the dashboard after reviewed answers.` Its evidence is a JSON object. The object has `submission_verified_absent` set to `true` and a short evidence text.

For a job without the badge, the evidence text says that the worker stopped before it reserved a submit slot. For a job with the badge `May have been sent`, the text says that the reviewer confirmed from the employer account that no application arrived.

The code does not delete older attempts. The earlier `manual` attempt stays in the table.

A request with no answers does not change `job_answers`. Earlier saved answers stay.

A job whose status is not `manual` returns the error code `not_manual` with the status 409. Two people who return the same job cause this error for the second person.

The route returns this JSON on success:

```json
{"requeued": true, "job_id": "EXAMPLE-ID", "saved_answers": 2}
```

### 7.8 The error codes

| Status | Code or message | Cause |
|---|---|---|
| 400 | `invalid_request` | The body has no `job_id` text. |
| 400 | `invalid_answers` | A row or the list breaks a rule in section 7.5. |
| 400 | `The request body is not valid JSON.` | The body does not parse. |
| 409 | `confirmation_required` | The job may have been sent and the box is not ticked. |
| 409 | `not_manual` | The job status is not `manual`. |
| 409 | `already_sent_or_reserved` | An equivalent job was sent or is active. |
| 409 | `requeue_requires_finished_non_applied_job` | The job status does not allow a requeue. |
| 400 | `unknown_job` | No job has that id. |
| 503 | A message about a busy database | SQLite reported an operational error. |
| 500 | `The job could not be resubmitted.` | Any other failure. |

The 500 reply never holds an error detail or a path.

### 7.9 How the worker gets a saved job answer

A saved job answer is data. It is not a permission.

1. At the start of each round, the supervisor calls `reviewed_answers`.
2. The function selects the job answers of jobs with the status `queued` or `applying`.
3. It groups the answers by job id.
4. The supervisor puts them in the worker prompt in a block with a heading.
5. The heading says that a person supplied the answers for exactly that job id.

A job answer for a job with another status does not reach the worker. This protects an unrelated form from an old answer.

The worker instructions in `src/CAMPAIGN_WORKER.md` set these rules:

- The worker uses an answer only for that exact job.
- The worker uses it only when a form asks that exact question.
- The worker matches the sense of the question and not a keyword.
- An answer cannot waive the consent limits.
- An answer cannot waive the duplicate protection.
- An answer cannot waive the reservation protection.
- An answer cannot waive the gate protection.
- An answer cannot waive the receipt protection.
- An answer cannot make the worker invent a fact that the question does not ask for.
- A question that no answer covers stops the job again as a manual job. The reason holds the exact words of the question.

Return to queue never submits an application. The worker still reserves a submit slot. The worker still passes the pacing gate. The system still needs a receipt before it counts an application.

New answers reach the worker at the next worker round. A round that already runs does not receive them.

## 8. What the Manual jobs tab does not do

- It does not change source code.
- It does not generalize an answer to other employers.
- It does not change the answer engine in `src/ats/answers.js`.
- It does not change `profile.json`.
- It does not submit an application.
- It does not skip the reservation, the pacing gate or the receipt check.
- It does not edit or delete an attempt.
- It does not requeue an `applied` job.
- It does not use the group to allow or block a return to the queue.
- It does not send the dismiss choice to the server.

A question that comes back often is better as a `standing answer` in `profile.json` under `answers`. That change needs a reviewed rule and a test in `src/ats/answers.js`. The README says that automatic, reviewed code updates from a return to the queue do not exist yet.

## 9. Write protection

The monitor listens on the loopback address only. A web page in your browser can still send a request to it. For this reason every route first checks the Host header. The write route has three more checks. All checks run before the code touches the database.

The server runs the checks in this order. The first failure stops the request. The Host check applies to every route. The other checks apply to the write route only.

| Order | Check | Failure status | Message |
|---|---|---|---|
| 1 | Host check | 403 | `Unexpected host` |
| 2 | Origin check | 403 | `Cross-site request refused` |
| 3 | JSON content type | 415 | `Content-Type must be application/json` |
| 4 | Token check | 403 | `Missing or invalid dashboard token` |
| 5 | Body size check | 411, 400 or 413 | See section 9.5. |

### 9.1 The Host check

The header `Host` must be one of these two values, with the port of the monitor:

- `127.0.0.1:PORT`
- `localhost:PORT`

An attack page can make your browser send a request with another host name. The name for this attack is `DNS rebinding`. The check fails for such a request on every route.

The Host check applies to the read routes too. Without it, an attack page could fetch the page `/` under its own host name. The page holds the write token. The attack page could copy the token and then send a write request. The Host check stops the first step.

The monitor answers a request with a wrong `Host` header or with no `Host` header. The status is 403. The body holds no token and no job data.

### 9.2 The Origin check

If the request has an `Origin` header, it must be `http://127.0.0.1:PORT` or `http://localhost:PORT`. This defeats a post from another site.

A request with no `Origin` header passes this check. The other checks still apply.

### 9.3 The JSON content type

The header `Content-Type` must have the media type `application/json`. A parameter such as a charset is allowed. The check ignores case.

A cross-site page cannot send this type without a CORS preflight. The monitor sends no CORS headers, so the browser blocks the request.

### 9.4 The token

The file `data/monitor-token` holds a secret for this install.

1. The monitor creates the file on its first start.
2. It creates the file with the flags that fail when the file exists. Two processes never write two different tokens.
3. It sets the file mode to 0600. Only the owner can read it.
4. The token is 32 random bytes in URL-safe text.
5. The monitor never logs the token.
6. If the file exists and is empty, the monitor stops with an error. Delete the file to create a new token.

The page holds the token in a `meta` tag named `monitor-token`. The page script reads it and sends it in the header `X-Monitor-Token` with each write request.

The server compares the header with the token in constant time. A wrong token or no token returns the status 403.

The token protects the write route. The read routes do not need a token. Every route checks the `Host` header, so a web page that uses another host name cannot read the token.

A local program that sends the header `Host: 127.0.0.1:PORT` can still fetch `/` and read the token. The Host check does not stop a local program that can open a connection to the port. Such a program can also read the file `data/monitor-token` if it runs as the same user. The token does not protect against a program that runs as the same user.

The foreground dashboard and the service dashboard use the same file, so they use the same token.

To change the token, do these steps:

1. Stop the monitor.
2. Delete `data/monitor-token`.
3. Start the monitor again.

### 9.5 The body size limit

The server reads the exact number of bytes in the header `Content-Length`.

| Case | Status |
|---|---|
| No `Content-Length`, or a value that is not a number | 411 |
| A negative value | 400 |
| A value above 65536 bytes | 413 |

The limit is 64 KiB. The server refuses an oversized body before it reads the body.

The README says that the authors attacked the guard with four kinds of request:

- forged requests
- cross-site requests
- requests with a changed host name
- oversized requests

## 10. Tests

These test files cover the dashboard:

- `test/test_manual_jobs.py` covers the requeue rules, the write guard and the links.
- `test/test_manual_groups.py` covers the group rules, the quoted questions and the group counts.
- `test/test_manual_jobs_ui.py` covers the markup and the script of the Manual jobs tab.
- `test/test_applied_jobs.py` covers the Applied jobs tab.
- `test/test_overview_ui.py` covers the markup and the script of the Overview tab.

Run all tests with this command:

```sh
python3 -m unittest discover -s test -p 'test_*.py'
```

The tests do not submit real applications. See `docs/testing.md` for what each file proves and does not prove.

## 11. What this document does not verify

- The author did not run the dashboard. The text comes from the code and the test files.
- Nobody verified the look of each tab. No screenshot exists, because the screenshot function of the browser tool timed out on every page.
- Nobody verified other browsers than headless Chromium.
- Nobody verified screen readers.
- Nobody verified the clipboard copy button for the job id.

A check in headless Chromium found no horizontal overflow at 1280 px and at 400 px on the four tabs. The check covered these items:

- The chip filters.
- A reload with a fragment.
- The dialog prefill.
- Dismiss and restore.
- The search.
- The link from the Overview to the Manual jobs tab.

The author of this document did not repeat that check.
