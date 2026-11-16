# Configuration reference

This document lists every setting in `profile.json` and in `data/campaign.json`. It is a snapshot of the code as read on 2026-11-13. The rules come from `src/settings.py`.

All example values in this document are placeholders. They are not values from the real candidate profile.

## 1. The two configuration files

The system reads two files. Each file has its own owner of the data.

| File | Content |
|---|---|
| `profile.json` | The candidate identity, the resume path, the job preferences, the search terms, the standing answers and the permissions. |
| `data/campaign.json` | The target, the model, the pacing limits, the safety policies and the `runtime` block. |

Both files are private. The project `.gitignore` keeps them out of version control.

Both files must hold one JSON object at the top level. The reader rejects a file that holds an array, a number or text.

The reader also rejects the JSON constants `NaN`, `Infinity` and `-Infinity`.

## 2. How to read and change a setting

### 2.1 Show a file

```sh
python3 jobapp.py config show profile
python3 jobapp.py config show campaign
```

The command prints the validated file as JSON. The default section is `campaign`. For `campaign`, the output includes the defaults for keys that the file does not hold.

### 2.2 Set a value

```sh
python3 jobapp.py config set KEY VALUE
```

The `KEY` is a dotted path. It must start with `campaign.` or `profile.`. It must have at least two parts. No part can be empty.

The command reads `VALUE` as JSON. When the text is not JSON, the command stores the text as a string. Quote arrays and objects in the shell.

| Example value | Stored as |
|---|---|
| `75000` | The integer 75000. |
| `true` | The boolean true. |
| `null` | JSON null. |
| `'["a", "b"]'` | An array of two strings. |
| `provider/model-id` | The string `provider/model-id`. |

The command creates each object that the path needs and that does not exist. It stops with an error if a part of the path holds a value that is not an object.

### 2.3 Validate

```sh
python3 jobapp.py config validate
```

This command checks `data/campaign.json`. It then checks `profile.json` in ready mode. Ready mode needs all fields that the system must have before it can start. See section 4.1.

The command `config set` checks the profile in normal mode. Normal mode does not need the fields for ready mode. A successful `config set` does not prove that the profile is ready. Run `config validate` to find out.

### 2.4 Error behavior

The command validates the whole file after your change. If the check fails, the command does not write the file. The old file stays as it was.

On an error the command prints one JSON object with the key `error` to standard error. The exit code is 2.

The errors that the code raises have these forms:

| Cause | Message form |
|---|---|
| Unknown key in `campaign` | `campaign: unknown setting(s): NAME` |
| Unknown key in `profile` | `profile: unknown setting(s): NAME` |
| Unknown key in a profile section | `profile.SECTION: unknown setting(s): NAME` |
| Wrong integer | `NAME: expected an integer >= MIN` or `NAME: expected an integer MIN..MAX` |
| Wrong list | `NAME: expected a list of nonempty strings` |
| Wrong boolean | `NAME: expected true or false` |
| Safety policy off | `campaign.KEY: this safety policy cannot be disabled` |
| Credential key in profile | `Profile must not contain credentials; use the credential command` |
| Bad file | `PATH: REASON` or `PATH: expected a JSON object` |

### 2.5 How the write works

1. The command takes the lock `data/configuration.lock`.
2. It reads the file.
3. It changes the value.
4. It validates the whole file.
5. It writes a temporary file with mode 0600.
6. It replaces the real file in one step.

Two `config set` commands cannot write at the same time.

## 3. When a change takes effect

The command `config set` always prints this text: next worker round. The text is a short form. The next table is exact.

| Setting | When it takes effect |
|---|---|
| `campaign.worker_model` | The next worker round. The supervisor builds the worker command for each round. |
| `campaign.runtime.omp_executable` | The next worker round. |
| `campaign.runtime.worker_timeout_seconds` | The next worker round. The supervisor also uses the new value as its deadline for that round. |
| `campaign.runtime.credentials_file` | The next worker round. |
| `campaign.runtime.success_pause_seconds` | The pause between rounds reads the value again. The change applies to the pause that runs now. |
| `campaign.runtime.idle_pause_seconds` | The same as `success_pause_seconds`. |
| `profile.json` as a whole | The next worker round. The supervisor loads the profile and gives it to the worker for each round. |
| `campaign.target_total` | The controller reads `data/campaign.json` each time it needs the value. The supervisor checks it at the start of each loop. |
| `campaign.max_submissions_per_day` | The controller reads the value each time it checks the limit. |
| `campaign.max_loads_per_day` | The same as `max_submissions_per_day`. |
| `campaign.max_details_per_hour` | The same as `max_submissions_per_day`. |
| `campaign.max_submissions_per_host_per_day` | The worker code loads `data/campaign.json` when the worker process starts. The change applies to the next worker process. |
| Other `campaign` keys | The worker receives them in the prompt of each round. The change applies to the next worker round. |
| `campaign.runtime.dashboard_port` | A service restart. See section 3.1. |
| `campaign.runtime.service_name` | A service update. See section 3.1. |
| Python source code | A supervisor restart. See `docs/operations.md`. |

The supervisor never interrupts a worker to apply a change. A form that the worker already fills does not change.

### 3.1 Port and service name

1. Change the value with `config set`.
2. Run `python3 jobapp.py service install --replace`.
3. Run `python3 jobapp.py service stop`.
4. Run `python3 jobapp.py service start`.

For a new service name, stop the old services first. A changed name creates new service files. The old files stay on the computer until you remove them with `service uninstall` under the old name.

Do this only at a safe idle boundary. See `docs/operations.md`.

## 4. The `profile.json` reference

The top level of `profile.json` has seven sections. The code accepts no other top-level key.

| Section | Type | Checked by the code |
|---|---|---|
| `candidate` | object | Yes. Unknown keys are rejected. |
| `preferences` | object | Yes. Unknown keys are rejected. |
| `search` | object | Yes. Unknown keys are rejected. |
| `answers` | object | Some keys. Other keys are free. |
| `application_permissions` | object | Yes. Unknown keys are rejected. |
| `application_instructions` | list of strings | Yes. |
| `sources` | object | The code accepts the section. It does not check its content. |

The validator refuses a key with one of these names at any depth. The comparison ignores case.

- `password`
- `passwd`
- `pwd`
- `token`
- `api_key`
- `access_token`
- `refresh_token`
- `client_secret`
- `private_key`

Use the `credential` command for passwords.

Every number in the profile must be finite.

### 4.1 Ready mode

Four commands check the profile in ready mode. They are `config validate`, `doctor`, `start` and `service start`. Ready mode adds these rules:

1. Each `candidate` key in this list must hold a nonempty string:
   - `name`
   - `first_name`
   - `last_name`
   - `email`
   - `phone`
   - `country`
   - `location`
   - `work_authorization`
   - `resume_path`
2. `candidate.email` must have the form `text@text.text` with no space.
3. `candidate.requires_sponsorship` must be `true` or `false`. It must have a value.
4. `search.queries` must have at least one entry.
5. `preferences.min_salary_usd` must be an integer.
6. The file at `candidate.resume_path` must open and must start with the bytes `%PDF-`.

### 4.2 Section `candidate`

The section holds facts about the candidate. The worker takes identity and contact facts only from here and from the resume.

| Key | Type | Allowed values | Default | Read by |
|---|---|---|---|---|
| `name` | string | Nonempty in ready mode. No NUL character. | Empty string in a new profile. | Validator. `src/ats/answers.js` derives a first and last name from it when those keys are empty. |
| `first_name` | string | Nonempty in ready mode. | Empty string in a new profile. | Validator. `src/ats/answers.js`. |
| `last_name` | string | Nonempty in ready mode. | Empty string in a new profile. | Validator. `src/ats/answers.js`. |
| `email` | string | Valid address form in ready mode. | Empty string in a new profile. | Validator. `src/ats/answers.js`. The `credential` command uses it as the default email. |
| `phone` | string | Nonempty in ready mode. | Empty string in a new profile. | Validator. `src/ats/answers.js`. |
| `location` | string | Nonempty in ready mode. | Empty string in a new profile. | Validator. The worker. |
| `country` | string | Nonempty in ready mode. | Empty string in a new profile. | Validator. The worker. |
| `street`, `city`, `state`, `zip` | string | Any text without a NUL character. | Not set. | `src/ats/answers.js` for address fields. A form field with no value stops the job. |
| `resume_path` | string | Path to a local PDF. A relative path starts at the project folder. A path that starts with `~` expands. | `data/resume.pdf` in a new profile. | Validator. `src/ats/answers.js`. The worker. |
| `work_authorization` | string | Nonempty in ready mode. | Empty string in a new profile. | Validator. The worker. |
| `requires_sponsorship` | boolean | `true` or `false`. | Not set. Ready mode needs a value. | Validator. The worker. |
| `us_citizen` | boolean | `true` or `false`. | Not set. | Validator. `src/ats/answers.js`. |
| `green_card_holder` | boolean | `true` or `false`. | Not set. | Validator. `src/ats/answers.js`. |
| `has_ead` | boolean | `true` or `false`. | Not set. | Validator. `src/ats/answers.js`. |
| `ead_type` | string | Any text. | Not set. | Accepted by the validator. No other reader found. |
| `authorization_notes` | string | Any text. | Not set. | Accepted by the validator. The worker sees it in the profile. |
| `linkedin_url` | string | Any text. | Not set. | `src/ats/answers.js`. |
| `website` | string | Any text. | Not set. | `src/ats/answers.js`. |

A string in `candidate` must not hold a NUL character. The worker never infers a fact that has no value. A fact with no value stays unknown.

### 4.3 Section `preferences`

The section holds the job filters.

| Key | Type | Allowed values | Default | Read by |
|---|---|---|---|---|
| `location_mode` | string | Any text. The code acts on the value `united_states`. | Not set. | `src/score.py`. |
| `locations` | list of strings | Nonempty strings. | `[]` in a new profile. | Validator. `src/score.py` for the city allow list. |
| `accept_remote` | boolean | `true` or `false`. | `true` in a new profile. | Validator. `src/score.py`. |
| `max_commute_miles` | integer | Zero or more. | `60` in a new profile. | Validator. `src/score.py`. |
| `allow_senior_ic` | boolean | `true` or `false`. | `false` when not set. | Validator. `src/score.py`. |
| `employment_types` | list of strings | Nonempty strings. | `["permanent_full_time"]` in a new profile. | Validator. `src/score.py` needs `permanent_full_time` in the list. |
| `min_salary_usd` | integer | Zero or more. | `75000` in a new profile. | Validator. `src/score.py`. The worker. |
| `salary_expectation_usd` | integer | Zero or more. Must equal `answers.salary_expectation_usd` when both exist. | `90000` in a new profile. | Validator. `src/ats/answers.js`. The worker. |
| `salary_range_rule` | string | Only `midpoint`. | `midpoint` when not set. | Validator. |
| `unknown_salary` | string | `allow` or `reject`. | `allow` when not set. | Validator. `src/score.py`. |
| `max_travel_percent` | integer | 0 to 100. | `10` in a new profile. | Validator. `src/ats/answers.js` reads the same key from `answers`. |
| `willing_to_relocate` | boolean | `true` or `false`. | Not set. | Validator. |
| `submit_mode` | string | Only `auto`. | `auto` when not set. | Validator. |
| `linkedin_geo_id` | string of digits | Six to twelve digits. | Not set. | Validator. `src/run.js`. The LinkedIn region id for a search outside the whole United States. |

The code rejects any other value for `salary_range_rule` and `submit_mode`. The message names the one value that the code accepts. The code does not ignore an unsupported value.

With `location_mode` set to `united_states`, the scorer considers US onsite and hybrid jobs nationwide.
The current reviewed profile uses this scope and omits `max_commute_miles`.
It has no 60-mile job-selection cap.
The candidate address stays unchanged.
An employer requirement for current residence in another state still needs a truthful answer.
The nationwide scope does not establish a relocation date or a new residence.
Foreign-only jobs remain outside scope.

For a job with an advertised salary range, `src/score.py` compares the midpoint of the range with `min_salary_usd`. It does not compare the highest value.

### 4.4 Section `search`

The section controls how the worker finds jobs and how the scorer ranks them.

| Key | Type | Allowed values | Default | Read by |
|---|---|---|---|---|
| `queries` | list of strings | Nonempty strings. At least one in ready mode. | `[]` in a new profile. | Validator. `src/pipeline.py`. The worker rotates through them. |
| `title_include` | list of strings | Nonempty strings. | `["analyst"]` in a new profile. | Validator. `src/score.py`. |
| `title_exclude` | list of strings | Nonempty strings. | `[]` in a new profile. | Validator. `src/score.py`. |
| `skills` | list of strings | Nonempty strings. | `[]` in a new profile. | Validator. `src/score.py`. |
| `seniority_max_years_required` | integer | Zero or more. | `8` in a new profile. | Validator. `src/score.py`. |
| `core_title_words` | list of strings | Nonempty strings. | Not set. The scorer then uses `title_include`. | Validator. `src/score.py`. A title that has one of these words earns 40 points. |
| `role_words` | list of strings | Nonempty strings. | `["analyst"]`. | Validator. `src/score.py`. Words for the role family. They allow senior levels and give the generic title bonus. |
| `context_words` | list of strings | Nonempty strings. | Not set. | Validator. `src/score.py`. A role title with one of these words in the text earns 20 points. |
| `domain_words` | list of strings | Nonempty strings. | Not set. | Validator. `src/score.py`. Each word in the text earns 5 points, up to 15. |
| `title_out_of_lane` | list of strings | Nonempty strings. | Not set. | Validator. `src/score.py`. A title with one of these words is rejected. |

### 4.5 Section `answers`

The section holds reviewed answers.
Each answer applies only within its recorded scope.
Some scopes cover all employers.
An interview answer can cover only named employers.

The validator accepts any key in this section. It checks only the keys in the first table. The second table lists other keys that `src/ats/answers.js` reads.

| Key | Type | Allowed values | Default | Read by |
|---|---|---|---|---|
| `salary_expectation_usd` | integer | Zero or more. Must equal `preferences.salary_expectation_usd` when both exist. | `90000` in a new profile. | Validator. `src/ats/answers.js`. |
| `default_skill_years` | integer | Zero or more. | Not set. | Validator. `src/ats/answers.js`. |
| `years_experience` | object | Each value is a finite number of zero or more. | Not set. | Validator. `src/ats/answers.js`. |
| `reviewed_questions` | object | Each key is an exact question. Each value is true, false, text or a number. | Not set. | Validator. `src/ats/answers.js`. |

The code in `src/ats/answers.js` reads these other keys from `answers`. The validator does not check them.

| Key | Used for |
|---|---|
| `availability` | A form question about start date. |
| `currently_employed` | A form question about current work. |
| `last_employer` | A form question about the last employer. |
| `past_employers` | Work history fields. |
| `education` | Education fields. |
| `employment_gap_explanation` | A form question about a gap in work. |
| `additional_skills` | A form question about skills. |
| `willing_to_relocate` | A form question about relocation. The value must be a boolean. |
| `max_travel_percent` | A form question about travel. |
| `demographics_when_no_decline_option` | A required demographic question that has no decline choice. |
| `relationship_with_employees` | A form question about a relation to employees. |
| `secondary_employment_intended` | A form question about other work. |
| `previously_worked_at_prospective_employer` | A form question about earlier work at the employer. |
| `excluded_from_federal_health_programs` | A compliance question. |
| `spouse_or_partner_of_us_military` | A form question about military family. |
| `prior_government_employment` | A form question about government work. |
| `professional_license_disciplinary_action` | A compliance question. |
| `population_health_initiatives` | A free text field about a work topic. |
| `it_operations` | A free text field about a work topic. |

The reader does not accept a guess. When a form asks for a fact that no `standing answer` holds, the worker stops the job as a manual job with the exact question.

### Reviewed questions

The key `reviewed_questions` holds answers that you reviewed for one exact question.
Each key is the question as the employer words it.
Each value is true, false, text or a number.
The reader matches the question text exactly after it ignores case, spacing and end punctuation.
The reader never matches a keyword.
The reader never answers a consent, a background check, a legal release or an identity question this way.
A dashboard answer for one job has priority over this list.
The worker passes the job answers to the adapter in the option `jobAnswers`.

The reviewed facts from 2026-11-14 use these keys:

| Key | Scope |
|---|---|
| `prior_interviews_by_company` | Only the named employers and verified affiliates. Not prior applications. |
| `restrictive_employer_agreements` | Agreements already signed. Not permission to sign a new agreement. |
| `federal_contracting_debarment_or_suspension` | Federal contracting history. Not criminal convictions. |
| `government_program_related_criminal_conviction` | Only convictions related to government-program involvement. Not general criminal history. |
| `public_trust_ever_held` | Whether Public Trust was held. Not eligibility to obtain it. |
| `education.additional_degrees` | Degrees beyond the degrees named in `education.bachelors_degree_name` and `education.masters_degree_name`. |
| `education.formal_equivalency_obtained` | Whether a formal evaluation exists. Not an invented equivalent degree. |
| `consents` | Separate grants for employment checks and future-vacancy contact. |

The value `null` for `consents.application_status_text_messages` means undecided.
It does not grant permission.
The `marketing` category and promotional texts remain forbidden.
Government or criminal-record releases remain forbidden.
Liability waivers remain forbidden.
Every obligation in a combined clause needs separate approval.

Specific experience facts override a generic skill-years default.
The 2026-11-14 facts include these keys:

| Key | Scope |
|---|---|
| `python_or_r_in_current_or_most_recent_role` | Recent-role use, not total career experience. |
| `python_libraries_used` | Each named library. A false value does not erase general Python years. |
| `databricks_advanced_sql_large_datasets` | The combined question only. Not a separate SQL proficiency answer. |
| `built_or_owned_business_kpis` | Business KPI or measurement ownership. |
| `analyses_driven_business_outcomes` | Whether analyses affected business outcomes. |
| `business_outcome_example` | The supplied example, with no invented quantities. |
| `presented_to_senior_leadership` | Presentations to senior or executive leaders. |
| `built_or_maintained_automated_data_integrations` | Automated API, database or file integrations. |
| `independently_managed_remote_deliverables` | Independent remote work and progress communication. |
| `vba_experience` | Actual VBA experience. |
| `referred_by_anyone` | A personal referral. Not the discovery source. |
| `partnered_with_client_success_or_experience_teams` | The reviewed functional partnership answer. |
| `client_team_partnership_example` | The supplied example of support for frontline care teams. |

No VBA or named-library experience means zero years for that exact tool.
The generic default does not override a specific denial.
The supplied outcome example attributes implementation to social workers.
The candidate performed the analysis and recommendations.
The latest recovery facts also use exact per-job answers in the worker prompt.
The worker must preserve the supplied team descriptions and fact scope.
These data do not authorize fabricated department names or broader criminal-history answers.



A `standing answer` for a skill never proves a licence. It never proves a certificate. It never proves a clearance. It never proves a degree. It never proves a visa. `default_skill_years` never applies to those terms.

The key `salary_expectation_usd` appears in two places. See section 6.

### 4.6 Section `application_permissions`

The section grants actions that need the permission of the candidate. All four keys default to `false`. A key that is absent means `false`.

| Key | Type | Allowed values | Default | Read by |
|---|---|---|---|---|
| `account_creation` | boolean | `true` or `false`. | `false` | Validator. The worker. The account helper `src/ats/account.js`. |
| `password_reset` | boolean | `true` or `false`. | `false` | Validator. The worker. The account helper. |
| `google_sign_in` | boolean | `true` or `false`. | `false` | Validator. The worker. |
| `mailbox_verification` | boolean | `true` or `false`. | `false` | Validator. The worker. The mailbox helpers. |

The README says the account helper joins account creation and password reset. The helper path needs both permissions.

An employer rule in `application_instructions` can still forbid an action that a permission allows. The employer rule wins.

### 4.7 Section `application_instructions`

| Key | Type | Allowed values | Default | Read by |
|---|---|---|---|---|
| `application_instructions` | list of strings | Nonempty strings. | `[]` in a new profile. | Validator. The worker. |

Each string is one reviewed instruction. Write each instruction for one exact job or one exact employer. An instruction cannot waive these protections:

- the receipt check
- the duplicate check
- the reservation
- the pacing gate
- the security rules

The worker may waive a salary floor for one exact job only. An instruction must name that job.

### 4.8 Section `sources`

The section holds data about job sources. The validator accepts the section and does not check its content. In the real profile the section has the keys `linkedin` and `indeed`. No code that this document reviewed reads those keys. The worker receives the whole profile in its prompt. This document does not verify any use of `sources`.

## 5. The `data/campaign.json` reference

The code rejects any key that is not in the next tables.

### 5.1 Target, model and pacing

| Key | Type | Allowed values | Default | Read by |
|---|---|---|---|---|
| `target_total` | integer or null | Zero or more, or `null` for continuous operation with no total cap. Zero closes the campaign. | `50` | `src/campaign.py` in `reserve`, `status` and the loop. The dashboard. |
| `worker_model` | string or null | A nonempty model name, or `null`. | `null` | The supervisor passes it to the worker as `--model`. |
| `priority_job_ids` | list of strings | Nonempty strings. | `[]` | The worker prompt only. Not read by a controller counter. |
| `max_submissions_per_run` | integer or null | Zero or more, or `null`. | `null` | The worker prompt only. `src/campaign.py` does not read it. |
| `max_submissions_per_day` | integer or null | Zero or more, or `null`. | `null` | `src/campaign.py` in the pacing gate, the budget and `reserve`. |
| `max_submissions_per_host_per_day` | integer or null | Zero or more, or `null`. | `null` | `src/ats/pacing.js`. |
| `max_details_per_hour` | integer or null | Zero or more, or `null`. | `null` | `src/campaign.py` in the pacing gate and the budget. |
| `max_loads_per_day` | integer or null | Zero or more, or `null`. | `null` | `src/campaign.py` in the pacing gate and the budget. |

For a `max_` key, `null` means no limit. The value `0` disables the action. A limit of zero for `max_details_per_hour` blocks detail loads with no retry time.

When a count reaches a positive limit, the controller computes a retry time. The supervisor then waits in the state `waiting_platform`.

The README says that some host and run rules belong to the worker or to the ATS code. They are not extra global counters in the controller.

### 5.2 Safety policies and other keys

| Key | Type | Allowed values | Default | Read by |
|---|---|---|---|---|
| `skip_unknown_questions` | boolean | Only `true`. | `true` | Validator. The worker prompt. |
| `skip_captcha` | boolean | Only `true`. | `true` | Validator. The worker prompt. |
| `skip_sensitive_identity_fields` | boolean | Only `true`. | `true` | Validator. The worker prompt. |
| `skip_separate_binding_legal_agreements` | boolean | Only `true`. | `true` | Validator. The worker prompt. |
| `local_resume_only` | boolean | Only `true`. | `true` | Validator. The worker prompt. |
| `no_further_questions` | boolean | Only `true`. | `true` | Validator. The worker prompt. |
| `standard_application_privacy_and_accuracy_attestation` | boolean | `true` or `false`. | `false` | Validator. The worker prompt. The worker may accept a standard privacy and accuracy attestation only when this key is `true`. |
| `skip_new_otp` | boolean | `true` or `false`. | Not set. | Validator. The worker prompt. |
| `willing_to_relocate` | boolean | `true` or `false`. | Not set. | Validator. The worker prompt. |
| `relocation_geography` | any | Not checked. | Not set. | The worker prompt. |
| `employment_types` | list of strings | Nonempty strings. | Not set. | Validator. The worker prompt. |
| `notes` | any | Not checked. | Not set. | The worker prompt. |
| `baseline_confirmed` | integer | Zero or more. | Not set. | Validator. The worker prompt. The controller does not use it to count. |
| `manual_recovery_job_ids` | list of strings | Nonempty strings. | Not set. | Validator. The worker prompt. |
| `manual_recovery_requested_at` | number | Finite, zero or more. A Unix time. | Not set. | Validator. The worker prompt. |
| `runtime` | object | See section 5.3. | The defaults in section 5.3. | The supervisor and `jobapp.py`. |

The campaign target counts unique jobs with an `applied` attempt. The controller uses the count `COUNT(DISTINCT job_id)` over the `applications` table with the outcome `applied`.

### 5.3 The `runtime` block

The block holds settings for the local process. The worker prompt does not receive the block. The validator rejects unknown keys in the block.

| Key | Type | Allowed values | Default | Read by |
|---|---|---|---|---|
| `omp_executable` | string | A nonempty command name or path. No NUL character. A relative path starts at the project folder. | `omp` | `jobapp.py`, `src/services.py` and the supervisor. |
| `dashboard_port` | integer | 1 to 65535. | `8765` | `jobapp.py start` and `src/services.py`. |
| `worker_timeout_seconds` | integer | 1 or more. | `1200` | The supervisor. It is both the worker time limit and the supervisor deadline. |
| `success_pause_seconds` | integer | Zero or more. | `60` | The supervisor. |
| `idle_pause_seconds` | integer | Zero or more. | `300` | The supervisor. |
| `service_name` | string | One lowercase letter, then up to 47 lowercase letters, digits, `_` or `-`. | `jobapp` | `src/services.py`. |
| `credentials_file` | string | A nonempty path. No NUL character. | `~/.omp/job-applications/credentials.json` | The supervisor passes it to the worker as the variable `ATS_CREDENTIALS_FILE`. `scripts/store_credential.py` writes to it. |

The setup command writes a different `service_name` and `credentials_file` into a new `data/campaign.json`. The `service_name` is `jobapp-` and eight characters of a hash of the project path. The `credentials_file` is `~/.omp/job-applications/<service_name>/credentials.json`.

Never put the content of the credentials file in any document or chat message.

## 6. The salary fields stay equal

Two profile fields hold the salary expectation:

- `preferences.salary_expectation_usd`
- `answers.salary_expectation_usd`

The scorer and the form filler read different fields. A difference between them would give a wrong answer on a form.

The validator rejects a profile in which both fields exist and differ. The message is `profile: preferences and answers salary_expectation_usd must agree`.

The command `config set` keeps the fields equal. When you set either key, the command writes the same value to both. Use `config set` and not a text editor to change the salary.

```sh
python3 jobapp.py config set profile.preferences.salary_expectation_usd 90000
```

The value above is a placeholder.

The field `preferences.min_salary_usd` is a separate floor. It does not need a partner field.

## 7. Safety settings that cannot be turned off

The campaign file has six policies that the validator forces to `true`. A file with `false` for one of them fails validation. The command `config set` does not write such a file.

| Key | What it protects |
|---|---|
| `skip_unknown_questions` | The worker never guesses an answer to a required question. It records a manual job. |
| `skip_captcha` | The worker never solves a CAPTCHA. |
| `skip_sensitive_identity_fields` | The worker never fills sensitive identity fields. |
| `skip_separate_binding_legal_agreements` | The worker never accepts a separate binding legal agreement. |
| `local_resume_only` | The worker uses only the local PDF in `candidate.resume_path`. |
| `no_further_questions` | The worker does not stop to ask a person. |

These protections have no setting at all:

- the check for a receipt before the system counts an application
- the duplicate check across all history
- the reservation before a final Submit
- the pause between browser actions
- the platform lockouts
- the ban on a CAPTCHA solver, a proxy or an undocumented API

A `max_` key set to `null` does not remove the reservation or the duplicate check. The worker instructions say so in plain words.

Only automatic submit mode exists. The value `auto` for `preferences.submit_mode` is the only accepted value.

## 8. Example commands

The values are placeholders. Replace each with a value that the candidate approves.

```sh
python3 jobapp.py config set profile.search.queries '["query one", "query two"]'
python3 jobapp.py config set profile.preferences.min_salary_usd 70000
python3 jobapp.py config set profile.preferences.unknown_salary reject
python3 jobapp.py config set profile.application_permissions.account_creation true
python3 jobapp.py config set campaign.target_total 10
python3 jobapp.py config set campaign.max_submissions_per_day 3
python3 jobapp.py config set campaign.runtime.idle_pause_seconds 600
python3 jobapp.py config validate
```

## 9. Command-line options that are not in the files

| Option | Command | Effect |
|---|---|---|
| `--profile PATH` | `setup` | Import a reviewed profile into a new project. |
| `--resume PATH` | `setup` | Copy a PDF to `data/resume.pdf`. |
| `--no-dashboard` | `start` | Run the supervisor without the dashboard. |
| `--offline` | `doctor` | Skip the relay check. |
| `--json` | `doctor` and `status` | Print JSON. |
| `--replace` | `service` | Replace a service file that this project owns. |
| `--email ADDRESS` | `credential` | Use this email instead of `candidate.email`. |
| `--port N` | `src/campaign_monitor.py` | Set the port when you start the monitor by hand. |
