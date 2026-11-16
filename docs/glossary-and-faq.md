# Glossary and FAQ

This document defines the terms of the project.
It then answers common questions.
Any count in this document is an example.
Counts change while the worker runs.

## Glossary

Each term has one sense in all documents.

### Processes and pages

| Term | Meaning |
|---|---|
| worker | The AI process that opens the browser and fills forms. |
| supervisor | The Python process that starts, watches, and stops the worker. |
| monitor | The dashboard web server. The code is in `src/campaign_monitor.py`. |
| dashboard | The web page. It listens on `127.0.0.1` at port 8765 by default. |
| controller | The module `src/campaign.py`. It reserves slots, records outcomes, and enforces the target. |
| relay | The omp browser relay. It connects the worker to the signed-in Chrome. It listens on port 9224. |

### Jobs and applications

| Term | Meaning |
|---|---|
| job | One row in the `jobs` table. |
| application | One submitted employer application. |
| attempt | One row in the `applications` table. |
| outcome | The result of an attempt. |
| applied job | A job with the status `applied`. The Applied jobs tab lists it. |
| receipt | The confirmation text of the employer. |
| manual job | A job with the status `manual`. The worker could not finish it. |
| skipped job | A job with the status `skipped`. |
| queued job | A job with the status `queued`. It waits for the worker. |
| discovered job | A job with the status `discovered`. The scorer has not queued it. |
| target | The number of confirmed applications that the campaign wants. The target is 50. |
| possibly sent | A label in the dashboard. The badge `May have been sent` marks a manual job that holds a reservation in the `manual` or `failed` state. |

### Reservations and answers

| Term | Meaning |
|---|---|
| reservation | One row in the `campaign_reservations` table. It holds a submit slot for one job. |
| standing answer | An answer in `profile.json` under `answers`. It applies to every employer. |
| job answer | One row in the `job_answers` table. It applies to one job. |
| `needs_description` | A note in `score_notes`. It means that the job has no description. |

### Tables

The database file is `data/jobs.db`.

| Table | Purpose |
|---|---|
| `jobs` | One row for each job. |
| `applications` | One row for each attempt. |
| `campaign_reservations` | One row for each reservation. |
| `job_answers` | Answers for one job. |
| `search_log` | The search queries that the pipeline ran, with the last run time. |

### Job statuses

The author found these values in the code.
The list is not a complete list from a schema constraint.

| Status | Where the code uses it |
|---|---|
| `discovered` | The default in `src/db.py`. The scorer also uses it for `needs_description`. |
| `scored` | The scorer sets it when the score is below 50. |
| `queued` | The scorer sets it when the score is 50 or more. A requeue sets it too. |
| `applying` | The controller reads it in a deferred outcome. |
| `applied` | An application that has a receipt. |
| `manual` | The worker could not finish the job. |
| `failed` | An attempt failed. |
| `skipped` | The scorer or the worker skipped the job. |

### Attempt results

The comment in `src/db.py` lists four results.
They are `applied`, `failed`, `manual`, and `skipped`.
The controller also writes the result `deferred` for a reviewed requeue.

### Reservation states

The controller writes these states in the `campaign_reservations` table.

| State | Meaning |
|---|---|
| `reserved` | The worker holds a submit slot. |
| `applied` | The application has a receipt. |
| `manual` | The worker stopped. |
| `failed` | The attempt failed. |
| `skipped` | The worker skipped the job. |

### Runtime terms

| Term | Meaning |
|---|---|
| heartbeat | A time stamp that the supervisor writes every 5 seconds. |
| stale | A heartbeat older than 20 seconds. |
| activity | A report from the worker about its stage and its job. |
| round | One start of the worker. The supervisor pauses between rounds. |
| run id | A name for one worker run. |
| singleton lock | A file lock that allows only one worker. |
| requeue | A reviewed change of a job back to the queued status. |
| reconcile | A reviewed record of a real receipt for an earlier attempt. |
| return to queue | The dashboard action that saves job answers and requeues a manual job. The route and the function keep the name `resubmit`. |
| dry run | An adapter run that stops before the final Submit. |
| adapter | A JavaScript file in `src/ats/adapters/` for one employer system. |

### Run ids in the history

| Run id | Meaning |
|---|---|
| `campaign-50` | Applications that the controller recorded. |
| `campaign-requeue` | The audit row for a reviewed requeue. |
| `campaign-reconcile` | The row for a reconciled receipt. |
| `adp-submit-confirmed` | One early ADP live test. It has no applied reservation. |
| `run-test-001` | The first test run. It has no applied reservation. |

## Questions and answers

### Why is a job manual?

The worker stopped before it finished the job.
The worker records the reason in the attempt.
Each manual job has a reason in the dashboard.
One reason is a question that the profile does not answer.
Another reason is a CAPTCHA.
Another reason is a phone code.
Another reason is an account that the profile does not allow the worker to make.
The worker instructions in `src/CAMPAIGN_WORKER.md` name these cases.
On 2026-11-15, 59 jobs have the status `manual`.

### What does manual mean?

Manual means that the worker did not send the application.
It also means that a person must look at the job.
A manual job is not a failure of the whole campaign.
The worker moves to the next job and does not retry the manual job.

### Why does return to queue not submit?

The return to queue action does not send an application.
It saves job answers and sets the job status to `queued`.
The code is the function `resubmit` in `src/manual_jobs.py`.
The worker must still reserve a slot.
The worker must still pass the pacing gate.
The worker must still get a receipt from the employer.
The intro of the Manual jobs tab and the dashboard footer say the same.

### Why can a job be possibly sent?

A reservation exists only after the worker finished the form and reserved a submit slot.
The worker could have pressed Submit before it stopped.
So the worker could have sent a manual job that holds a reservation in the `manual` state or the `failed` state.
No receipt is not proof that no application went out.
The dashboard asks a person to check the employer account.
The person must tick a box.
The box states that the employer has no application.
Only then does the dashboard requeue the job.
The dashboard shows the badge `May have been sent` on such a job.

### Why is a count different after a restart?

The database keeps the permanent counts.
The function `confirmed` counts distinct jobs that have an applied attempt.
This count does not change after a restart.
Other values belong to one run.
The activity report belongs to one worker run.
The code clears it in three cases.
The first case is the death of the worker.
The second case is the start of a new run.
The third case is a terminal state.
The job counts by status also change while the worker runs.
The worker adds jobs and the scorer changes statuses.
So a count that you read earlier can differ from a count that you read later.

### What is a heartbeat and does it prove an application?

A heartbeat is a time stamp.
The supervisor writes it every 5 seconds.
The status is stale when the heartbeat is older than 20 seconds.
A heartbeat shows that the supervisor is alive.
It does not prove that the worker did any work.
It does not prove that an application went out.
The `README.md` says that liveness is not proof of a submission.
The dashboard shows worker activity and the heartbeat as two separate items.

### Why does the worker not answer a question it does not know?

A wrong answer on an employer form can be false and can have legal effect.
The worker instructions say to record a manual outcome for an unknown required factual or legal question.
The worker does not ask the person and does not guess.
The file `src/ats/answers.js` says in its header that nothing in it guesses or infers experience.
The code raises `NeedsHuman` with the text of the question when it has no confirmed answer.
A person can add a job answer for that question.

### Why are location rules strict?

A wrong location wastes an application and can be a false statement.
The scorer accepts a place only when the profile or a rule names it.
The comment in `src/score.py` says that a metro shape alone is not evidence of a US city.
For example `Greater London Area` has the same shape as a US metro name.
So the scorer accepts only the named metros in `_US_METROS`.
The comment says that a closed result is the safe direction.
A person must add a new real metro to the list by hand.

### Why is the salary compared by midpoint?

The scorer takes the midpoint of an advertised range.
The code is in the function `evaluate` in `src/score.py`.
The scorer rejects the job when the midpoint is below `min_salary_usd`.
A range that touches the floor only at its top is not a fit.
The `README.md` says that known ranges use the midpoint and not the maximum.
The salary rule for an unknown salary follows the setting `preferences.unknown_salary`.
The values are `allow` and `reject`.

### How do I add a standing answer?

Follow these steps.

1. Choose a fact that applies to every employer.
2. Run `python3 jobapp.py config set profile.answers.KEY VALUE` with the name of the key and the value.
3. Run `python3 jobapp.py config validate`.
4. Check that `src/ats/answers.js` has a rule that reads the key.
5. Add a rule and a test in `test/fixtures/ats/answers_standing.test.js` if the rule is absent.

The `README.md` says that a repeated question needs a reviewed answer in `profile.json` and a rule in `src/ats/answers.js`.
A key without a rule does nothing.
The command rejects an invalid value and leaves the file unchanged.
The command is `config set` in `jobapp.py`.
Its key form is `profile.preferences.min_salary_usd` in the help text.

### How do I add a job answer?

Follow these steps.

1. Open the dashboard at `http://127.0.0.1:8765`.
2. Open the Manual jobs tab.
3. Press the chip `Needs your answer`.
4. Press `Add answers and return to queue` on the card of the job.
5. Check the question rows. The dialog fills one row for each complete question that the reason quotes.
6. Enter each question as the employer words it.
7. Enter your answer next to each question.
8. Tick the confirmation box if the dashboard asks for it.
9. Press `Save answers and return to queue`.

The dashboard allows at most 25 answers for one job.
A question can have at most 500 characters.
An answer can have at most 2000 characters.
The save replaces the earlier answers of that job.
The worker uses an answer only for that job.
The worker uses it only when a form asks that exact question.
The worker gets the answers only while the job has the status `queued` or `applying`.

### Where do I see the details of an applied job?

Open the dashboard at `http://127.0.0.1:8765`.
Open the Applied jobs tab.
Each card shows one clause of the employer confirmation text and the name of the receipt screenshot file.
The card says if the file exists in `logs/campaign/receipts`.
The tab never shows the whole evidence text, because evidence can hold contact data.
The tab does not show the screenshot image.
See `docs/dashboard.md` for the description of the tab.

### How do I know an application really went through?

Check these items.

1. Read the attempt in the `applications` table. It must have the result `applied`.
2. Read the evidence column. It must hold the receipt text of the employer.
3. Read the reservation. It must be in the `applied` state.
4. Look for a screenshot in `logs/campaign/receipts`. The Applied jobs tab says if the file exists. A screenshot can be absent.
5. Check the employer account or the email for the application.

The controller refuses to record an applied outcome without evidence.
It also refuses without an active reservation.
Two applied jobs have no applied reservation because they ran before the controller.
Both have real employer receipts.
See `docs/known-gaps.md` for details.

## Related documents

See `docs/testing.md` for the tests.
See `docs/known-gaps.md` for gaps.
