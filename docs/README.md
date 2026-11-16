# Documentation index

This folder holds the technical documents for the job application project.
The documents describe the code as it exists on 2026-11-13.
The dashboard parts describe the code as it exists on 2026-11-15.
Read `architecture.md` first. Then read `modules.md`.

## Documents

| Document | Purpose |
|---|---|
| [README](README.md) | This index. |
| [architecture](architecture.md) | The whole system: processes, data flow, tables, files, lifecycle and pacing. |
| [modules](modules.md) | One section for each source file, script and adapter. |
| [operations](operations.md) | How to operate the system. |
| [configuration](configuration.md) | The configuration files and settings. |
| [dashboard](dashboard.md) | The dashboard and its four tabs: Overview, Applied jobs, Manual jobs and Live logs. |
| [safety-model](safety-model.md) | The safety rules of the system. |
| [decisions](decisions.md) | The design decisions. |
| [changelog](changelog.md) | The history of changes. |
| [testing](testing.md) | The tests. |
| [known-gaps](known-gaps.md) | The known gaps. |
| [glossary-and-faq](glossary-and-faq.md) | The glossary and the frequent questions. |

## Writing standard

The documents follow nine rules. The rules come from ASD-STE100 Simplified Technical English.

1. Use active voice only.
2. Write procedural sentences with 20 words or fewer.
3. Write descriptive sentences with 25 words or fewer.
4. Put one instruction or one idea in each sentence.
5. Use the simple present, simple past and simple future only. Do not use words that end in "ing".
6. Give each word one sense. Use the same noun for the same thing.
7. Write commands in the imperative.
8. Use vertical lists for steps and for groups of three or more items.
9. Do not use semicolons, parentheses or dashes as an aside.

## Limits of the standard

The checker verifies only the mechanical rules. Rules 4 and 6 are not mechanical.
The checker does not verify rule 4. The checker does not verify rule 6.
A person must check rules 4 and 6.
The checker is a heuristic. It can report a false violation. It can also miss a real violation.
This work is not a certification of ASD-STE100 conformance.
The documents do not use the official STE dictionary.

The checker ignores literal text. Literal text has five kinds.

- Code blocks.
- Inline code.
- Table rows.
- Headings.
- Link targets.

## How to run the checker

Run the command from the project root.

```
python3 scripts/check_ste.py docs/README.md
```

Run the checker for all documents with this command.

```
python3 scripts/check_ste.py docs/*.md
```

Add the option `--summary` to print only the count of violations for each file.

```
python3 scripts/check_ste.py --summary docs/*.md
```

The checker prints each violation with the file name, the line number and the sentence.
The last line shows the total count of violations.
The exit status is 0 when the count is 0. The exit status is 1 when the count is more than 0.

## Glossary

| Term | Meaning |
|---|---|
| worker | The AI process that opens the browser and fills forms. |
| supervisor | The Python process that starts, watches and stops the worker. |
| controller | The commands in `src/campaign.py` that the worker calls: `reserve`, `gate`, `result`, `requeue`, `reconcile`, `activity` and `status`. The controller is not the supervisor. The supervisor is the process. The controller is the set of commands. Both live in the same file. |
| monitor | The dashboard web server. |
| dashboard | The web page. |
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
| submit | Click the final button on an employer form. The worker does this. |
| apply | Use only for the noun "application" or the job title. Do not use it for the click. |
| status | The current state of a job, one word in the `jobs` table. Examples are `queued` and `manual`. |
| result | The word that the worker passes to the `result` command, or the field `result` in the `applications` table. |
| outcome | The same as the result of one attempt, when the sentence talks about the meaning and not the field. |
| job answer | A row in `job_answers` that applies to one job. |
| return to queue | The dashboard action that saves job answers and puts one manual job back in the queue. The route and the function keep the name `resubmit`. |
