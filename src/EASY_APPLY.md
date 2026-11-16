# Easy Apply

`apply_easy.js` exports `applyEasy(browser, job, profile, {dryRun:true})`. Run in omp Eval with Chrome relay connected. Default is dry-run; live submission requires explicit `dryRun:false`.

The native LinkedIn dialog is handled page by page. Unknown required questions and unverified prefilled answers stop for human input. Reviewed skill-year answers match exact skill names only. Citizenship/green-card answers are separate from work authorization. An EAD establishes current authorization, not citizenship or future sponsorship; use only the candidate's explicit sponsorship answer.

Resume-selection pages require a selected resume; the final review page has no resume-selection radio. Final submission unticks Follow and Top Choice. A success requires explicit application-sent or application-submitted text, not the word 'applied' in applicant statistics.

The complete automated final-submit path needs a live run before you rely on it. If a submission times out, inspect the page before any retry to avoid duplicates.

Rate limits are local conservative budgets, not LinkedIn-published limits or a guarantee that automation is permitted. No scheduled continuous submissions are enabled.
