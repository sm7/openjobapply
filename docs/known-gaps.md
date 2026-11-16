# Known gaps

This document lists gaps in the project.
A gap is a fault or an absent feature.
A gap can also be a fact that nobody verified.
Each entry has four parts.
These are the parts:

- The gap.
- The evidence.
- The effect.
- The suggested fix.

Each entry uses these four headings.
The author read the code for each entry.
Counts change while the worker runs.
The author did not run the application or the tests.

## Gap 1: Description work now has a supervised path

### Previous gap

A job with no description stays `discovered` with the note `needs_description`.
The worker previously searched for new jobs and left this pool untouched.

### Current path

1. Run `python3 src/ingest.py --rescore` at the start of a round.
2. Finish the eligible queue before new searches.
3. Call `verifyNext({browser})` from `src/verify_pending.js` when the queue cannot proceed.
4. The helper reads one page after the campaign detail gate.
5. The helper saves observed fields through `campaign.py save-description`.
6. The controller applies the same scorer to that job.

The command `campaign.py next-verify` selects a job with no application history.
The save command refuses jobs with history or a status other than `discovered`.
Neither command records an application.
Manual jobs still require explicit review.

### Remaining limits

- A challenge stops page access under the same lockout rules.
- An empty page supplies no description.
- The worker stays stopped until the user provides answers and requests a start.
- The 30 percent goal concerns eligible jobs, not every indexed row.
- Eligibility remains provisional until the worker reads the full requirements.


## Gap 2: Automatic code updates on return to the queue are not built

### Gap

A person can add job answers and return a manual job to the queue.
The system stores the answers as data.
The system does not change the answer code in `src/ats/answers.js`.

### Evidence

- The file `README.md` says that automated reviewed code updates from a resubmission are intentionally not implemented.
- The file `README.md` says that a repeated question is better as a reviewed answer in `profile.json` under `answers`.
- The module `src/manual_jobs.py` stores answers and requeues the job. It has no code that edits source files.

### Effect

A person must read each repeated question by hand.
A person must then add an answer in `profile.json` under `answers`.
A person must also add a rule in `src/ats/answers.js`.
The same question can stop many jobs until a person does this.

### Suggested fix

Add a reviewed workflow that proposes a code change from repeated job answers.
A person must approve the change before the system applies it.
Add a test for each new rule.

## Gap 3: Linux service behavior is tested with fixtures only

### Gap

The project supports `systemd --user` on Linux.
No test or run proves this on a real Linux host.

### Evidence

- The header of `test/test_services.py` says that the tests never call native service managers.
- The class `Manager` in that file is a fixture for the transport.
- The tests build systemd files in a temporary folder.
- The author found no other test that starts a service.
- The launchd labels `com.<service_name>.applications` and `com.<service_name>.application-monitor` are the services that the project installs. The default service name is `jobapp`.

### Effect

A real systemd error can happen that the fixtures do not show.
One example is an absent user session.
Another example is a different unit option.
The `README.md` says that a functional systemd user session is required.

### Suggested fix

Run the service commands on a real Linux host.
Record the result.
Add a test that runs against a real `systemd --user` session in a disposable environment.

## Gap 4: The checker cannot verify rule 4 or rule 6

### Gap

The script `scripts/check_ste.py` checks only some of the rules for the text.
It cannot check rule 4 or rule 6.
Rule 4 says that each sentence has one idea.
Rule 6 says that each word has one sense.

### Evidence

- The header of `scripts/check_ste.py` says that it does not check rule 4 or rule 6.
- The header says that these rules need a human reader.
- The header says that a clean run is necessary but not sufficient.
- The header says that a clean run is not a certification of conformance to ASD-STE100.
- The header lists the other rules as heuristics.

### Effect

A document can pass the checker and still break rule 4 or rule 6.
A passive sentence can also pass the checker if the heuristic does not see it.
No body has certified the documents as ASD-STE100.

### Suggested fix

Ask a human reader to review each document for rule 4 and rule 6.
Compare the words with the official ASD-STE100 dictionary.

## Gap 6: An applied receipt screenshot can be absent when the receipt text exists

### Gap

An application can have a receipt text and no receipt screenshot.

### Evidence

- Step 7 in `src/CAMPAIGN_WORKER.md` tells the worker to save a screenshot to `logs/campaign/receipts` if the browser can capture it.
- The words `if the browser can capture it` make the screenshot optional.
- The result record needs real evidence text for an applied outcome.
- The record does not need a screenshot path.
- Some applications can have receipt text and no screenshot. The author did not verify a specific case in the files.

### Effect

An audit of the receipt files finds no image for some applications.
The text in the `applications` table is the only proof for these applications.

### Suggested fix

Make the worker retry the screenshot once after a confirmed receipt.
The Applied jobs tab now shows the state of the screenshot for each applied job. See gap 13 and gap 15.

## Gap 8: The supervisor keeps old code until it restarts

### Gap

A live supervisor does not load new Python source code.

### Evidence

- The file `README.md` says that an update of Python source needs a supervisor restart.
- The file `README.md` says to restart at an idle boundary and never during an uncertain Submit.
- The same file says that an upgraded supervisor reloads some settings at each round.
- The settings are the model and the executable.
- The settings also cover the deadlines.
- The settings also cover the profile and the credential paths.
- The reload covers settings and not code.

### Effect

A code fix does not take effect until a restart.
The worker can keep the old behavior for many rounds.
A restart at a bad time can leave a submission uncertain.

### Suggested fix

Add a command that waits for an idle boundary and then restarts the supervisor.
Show the code version of the live supervisor in the status output.

## Gap 9: The scorer rejects jobs without a worker review and its keyword rules are coarse

### Gap

The scorer sets the status `skipped` for a job that fails a rule.
No worker reads these jobs.
The keyword rules are simple text matches.

### Evidence

- The function `score_row` in `src/ingest.py` sets the status to `skipped` and the note to `rejected:` plus the reason.
- The function writes a score of 0 in this case.
- The function `evaluate` in `src/score.py` tests the words in `profile.search.title_out_of_lane` against the padded title.
- The function `evaluate` rejects a title that has no word from `title_include` in the profile.
- The function `evaluate` rejects a job when a clause that describes required experience asks for more years than `seniority_max_years_required`.
- The command `python3 src/ingest.py --rescore` revisits jobs that have a note that starts with `rejected:`.
- The command `python3 src/ingest.py --report` lists the count of each rejection reason.
- The command `python3 src/ingest.py --why JOB_ID` shows the text that caused one rejection.

### Effect

The scorer can reject a good job that has an unusual title.
The scorer can reject a job for a word that has another sense in context.
A years number in unrelated text can reject a job.
No person sees the rejected job.

### Suggested fix

Sample the rejected jobs and review them.
Record each wrong rejection as a test for the scorer.
Change the rules or add a review step for borderline cases.

## Gap 10: The `_US_METROS` allowlist needs manual extension

### Gap

The scorer accepts a LinkedIn metro name only if the name is in the list `_US_METROS`.
A person must add a new metro by hand.

### Evidence

- The comment in `src/score.py` says to extend the list when a real US metro appears.
- The comment says that a metro shape alone is not proof of a US city.
- The comment gives `Greater London Area` and `Greater Sydney Area` as look-alike names.
- The comment says that a closed result is the safe direction.
- The tests in `test/test_score_location.py` check the accepted and the rejected names.

### Effect

A job in a real US metro that is not in the list gets rejected for location.
No alert tells a person about the absent metro.

### Suggested fix

Log each location that fails only because of the metro list.
Review the log at a fixed interval.
Add the real US metros to the list and add a test for each.

## Gap 11: The `doctor` command does not test model authentication

### Gap

The command `python3 jobapp.py doctor` does not test the sign in of the model provider.

### Evidence

- The function `doctor` in `jobapp.py` adds the check `model_authentication` with the status `warn`.
- The detail text says that `doctor` does not test it.
- The detail text says that the check made no paid model request.
- The `doctor` function checks the flags of the executable `omp`.
- The flags `--print` and `--wait-async` are required.
- The flags `--max-time` and `--auto-approve` are also required.

### Effect

The `doctor` report can say that all is fine while the model sign in has expired.
The failure shows only when the worker starts.
The `README.md` says that the system reports provider authentication failures and does not count them as submitted applications.

### Suggested fix

Add an optional flag that sends one small model request.
Keep the default free of cost.
Tell the person before the paid request.

## Gap 12: The dashboard token is not encryption

### Gap

The dashboard token protects the write route.
It does not encrypt any data.

### Evidence

- The function `check_host` in `src/monitor_security.py` checks the Host header. The method `do_GET` in `src/campaign_monitor.py` calls it for every read route.
- The function `check_write` calls `check_host` and then checks the Origin header when one exists.
- The function compares the token in the request with the token on disk. It uses `hmac.compare_digest`.
- The token file is `data/monitor-token`.
- The header of `src/monitor_security.py` says that the checks stop DNS rebind attacks.
- On 2026-11-13, a version of the monitor served the page `/` under any Host header. The page holds the token. A DNS rebind page could read the token. The author fixed this the same day. The test `test_a_page_requested_under_a_foreign_host_never_receives_the_token_or_any_data` covers it.
- The server in `src/campaign_monitor.py` listens on `127.0.0.1` with plain HTTP.
- The `README.md` says that the page embeds the token.

### Effect

The token stops other web pages from a write.
The Host check stops a DNS rebind page. That page cannot read the token.
The token stops a local program that does not have the token.
A local program that sends the header `Host: 127.0.0.1:PORT` can open the page and read the token.
A local program that runs as the same user can also read `data/monitor-token`.
The token does not protect against a program that runs as the same user.
The loopback connection does not encrypt data.

### Suggested fix

Keep the token file at mode 0600.
Do not expose the port to a network.
Add a user session cookie only if the dashboard must serve more than one local user.

## Gap 13: Applied jobs can name a receipt screenshot that is absent from the disk

### Gap

The Applied jobs tab can find applied jobs whose evidence names a receipt screenshot file that does not exist in `logs/campaign/receipts`.
The tab shows the text `the file is missing` for each of them.
The author did not find the cause.

### Evidence

- The function `receipt_file` in `src/applied_jobs.py` reads the file name from the evidence.
- The function `list_applied` checks if a file with that name exists in `logs/campaign/receipts`.
- The author of this document did not test any cause. This document does not name one.

### Effect

The text in the `applications` table is the only proof for these jobs.
The tab cannot show an image for them.
An audit of the folder finds fewer files than the evidence names.

### Suggested fix

Find out why the files are absent.
Check the worker step that saves the file and the path that it writes to.
Make the worker check that the file exists before it records the name.

## Gap 14: The group of a manual job is a keyword guess

### Gap

The Manual jobs tab gives each job a group from keyword rules on the reason text.
A reason with new text can land in the wrong group.

### Evidence

- The tuple `_GROUP_RULES` and the function `classify` in `src/manual_jobs.py` hold the rules. The first rule that matches wins.
- The tests in `test/test_manual_groups.py` use sample reasons. They cannot cover a reason that nobody recorded yet.
- The group never allows or blocks a return to the queue.

### Effect

A job can show under a wrong group, a wrong headline and a wrong button label.
A wrong headline can mislead a reviewer.
The reviewer can still open the dialog for every job and read the full reason.

### Suggested fix

Review the jobs in the group `other` at a fixed interval.
Add each new reason text to the rules and to the sample reasons in `test/test_manual_groups.py`.
Keep the full reason one click away on every card.

## Gap 15: The Applied jobs tab does not show receipt screenshots

### Gap

The Applied jobs tab shows the file name of a receipt screenshot.
It does not show the image.

### Evidence

- The method `do_GET` in `src/campaign_monitor.py` has no branch that reads a file from `logs/campaign/receipts`.
- The script in `src/applied_jobs_ui.py` writes only the file name and the folder as text.

### Effect

A reviewer must open the folder by hand to see a screenshot.

### Suggested fix

Add a read-only route that serves one file by name from the receipts folder.
The route must accept only a name that the function `receipt_file` accepts.
The route must keep the Host check.
A test must prove that a path never works.

## Gap 16: The confirmation clause comes from a phrase list

### Gap

The Applied jobs tab finds the confirmation clause with a fixed list of phrases.
A job with unusual receipt text shows `No confirmation sentence is recorded for this job.`

### Evidence

- The pattern `_CONFIRMATION` in `src/applied_jobs.py` holds the phrases.
- The function `confirmation_sentence` returns `None` when no phrase matches.
- The test `test_the_confirmation_is_one_bounded_clause_or_none` covers a text with no phrase.

### Effect

A real application can show with no confirmation clause.
The job still has the status `applied`, because the list of applied jobs does not depend on the clause.
A reviewer can read the full evidence in the database.

### Suggested fix

Add a phrase to the list when a new receipt text appears.
Add a test for each new phrase.

## Related documents

See `docs/testing.md` for what the tests prove.
See `docs/glossary-and-faq.md` for terms and answers.
