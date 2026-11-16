"""Read-only skipped-job review and explicitly audited, atomic recovery.

Queueing asks the worker to review eligibility again; it grants no submission permission.
Reasons are display data, never eligibility overrides. Unknown submission history requires
employer-side verification, including history belonging to equivalent postings.
"""
import campaign
import manual_jobs

MAX_REVIEW_NOTE = 2000
_BLOCKED = 'An application for this posting was already sent or is in progress.'
_ERRORS = {
    'unknown_job': 'Unknown job.',
    'not_skipped': 'Only a skipped job can be queued for another review.',
    'already_sent_or_reserved': _BLOCKED,
    'requeue_requires_expected_status': 'This job changed. Only a skipped job can be queued for another review.',
    'requeue_requires_finished_non_applied_job': 'Only a finished, unsubmitted job can be queued for review.',
}
_IDENTITY = '(j.id=? OR j.url=? OR (j.source=? AND j.source_id=?))'


def _identity_values(job):
    return (job['id'], job['url'], job['source'], job['source_id'])


def _documented_unsent(outcome):
    """Recognize documented pre-submit outcomes, never eligibility/blocker guesses."""
    reason = (outcome['reason'] or '').strip().lower()
    return (outcome['result'] == 'skipped' and
            (reason in ('dry_run_complete', 'no application opened. not submitted.') or
             reason.startswith('dry_run_complete:'))) or (
                reason == 'rate limited before submit; not sent' and
                outcome['result'] in ('skipped', 'deferred'))


def _assessment(con, job):
    """Return (blocked, risk, evidence) without writing or exposing private evidence."""
    reservations = manual_jobs._has_table(con, 'campaign_reservations')
    state = 'r.state' if reservations else 'NULL'
    join = 'LEFT JOIN campaign_reservations r ON r.job_id=j.id' if reservations else ''
    equivalents = con.execute(f'''SELECT j.id,j.status,j.score_notes,{state} AS reservation
        FROM jobs j {join} WHERE {_IDENTITY}''', _identity_values(job)).fetchall()
    history = con.execute(f'''SELECT a.result,a.reason FROM applications a JOIN jobs j ON j.id=a.job_id
        WHERE {_IDENTITY} AND NOT (a.result='deferred' AND a.run_id='campaign-requeue')
        ORDER BY a.id''', _identity_values(job)).fetchall()
    if reservations:
        sent, active = campaign.identity_conflicts(con, job)
        blocked = sent or active
    else:
        blocked = any(row['status'] == 'applied' for row in equivalents) or any(
            row['result'] == 'applied' for row in history)
    if blocked:
        return True, 'possibly_sent', None
    # Any finished reservation means a submit slot existed. It is never proof of absence.
    uncertain_reservation = any(row['reservation'] is not None for row in equivalents)
    uncertain_status = any(row['status'] in ('manual', 'failed', 'applying') for row in equivalents)
    if uncertain_reservation or uncertain_status:
        return False, 'possibly_sent', None
    # Preserve all attempts in the decision: a newer safe skip cannot erase an older unknown Submit.
    if history:
        if all(_documented_unsent(row) for row in history):
            return False, 'not_sent', 'Every recorded outcome for this posting and its equivalents documents a stop before final submission.'
        return False, 'possibly_sent', None
    # Scorer-only rejects have never entered the applications ledger. Status alone,
    # an empty ledger without scoring provenance, or a finished reservation is not proof.
    if (isinstance(job['score_notes'], str) and job['score_notes'].startswith('rejected:') and
            all(row['reservation'] is None for row in equivalents)):
        return False, 'not_sent', 'Scorer-only rejection with no application history or submit reservations for this posting or its equivalents; queued only for a fresh eligibility review.'
    return False, 'possibly_sent', None


def list_skipped(con):
    """JSON-safe cards for every skipped job; compatible with read-only legacy databases."""
    answers = manual_jobs._has_table(con, 'job_answers')
    rows = con.execute('''SELECT j.*,
        (SELECT a.reason FROM applications a WHERE a.job_id=j.id AND a.result<>'deferred'
         ORDER BY a.id DESC LIMIT 1) AS latest_reason,
        (SELECT a.attempted FROM applications a WHERE a.job_id=j.id AND a.result<>'deferred'
         ORDER BY a.id DESC LIMIT 1) AS attempted
        FROM jobs j WHERE j.status='skipped' ORDER BY attempted DESC,j.id''').fetchall()
    jobs = []
    for row in rows:
        blocked, risk, _ = _assessment(con, row)
        reason = campaign.outcome_reason(row['latest_reason']) or campaign.outcome_reason(row['score_notes']) or 'No reason recorded'
        saved = con.execute('SELECT COUNT(*) FROM job_answers WHERE job_id=?', (row['id'],)).fetchone()[0] if answers else 0
        jobs.append({
            'job_id': row['id'], 'company': row['company'], 'title': row['title'], 'location': row['location'],
            'posting_url': manual_jobs.safe_link(row['url']),
            'apply_url': manual_jobs.safe_link(row['apply_url']) if row['apply_url'] != row['url'] else None,
            'reason': reason, 'attempted': row['attempted'], 'questions': manual_jobs.quoted_questions(reason),
            'saved_answers': saved, 'requeue_allowed': not blocked,
            'blocked_reason': _BLOCKED if blocked else None, 'sent_risk': risk,
        })
    return jobs


def requeue_skipped(con, job_id, answers, review_note, nothing_sent_confirmed=False):
    """Save reviewed answers and an operator's explanation in the core requeue transaction."""
    if not isinstance(job_id, str) or not job_id.strip():
        raise manual_jobs.ManualJobError('unknown_job', _ERRORS['unknown_job'])
    if not isinstance(review_note, str) or not review_note.strip() or len(review_note) > MAX_REVIEW_NOTE:
        raise manual_jobs.ManualJobError('invalid_review_note', f'Explain why this job needs another review, using at most {MAX_REVIEW_NOTE} characters.')
    cleaned = manual_jobs.clean_answers(answers)
    job = con.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
    if job is None:
        raise manual_jobs.ManualJobError('unknown_job', _ERRORS['unknown_job'])
    if job['status'] != 'skipped':
        raise manual_jobs.ManualJobError('not_skipped', _ERRORS['not_skipped'])
    data = {
        'reason': 'Skipped job queued for fresh eligibility review. Reviewer note: ' + review_note.strip(),
        'evidence': '', 'submission_verified_absent': True,
    }

    def review_check(locked_con, current_job):
        blocked, risk, evidence = _assessment(locked_con, current_job)
        if blocked:
            raise manual_jobs.ManualJobError('already_sent_or_reserved', _BLOCKED)
        if risk == 'possibly_sent':
            if nothing_sent_confirmed is not True:
                raise manual_jobs.ManualJobError(
                    'confirmation_required', 'This posting may already have been submitted. Check the employer account and confirm that no application was received before queueing another review.')
            evidence = 'Reviewer confirmed from the employer account that no application was received for this posting, including equivalent listings.'
        data['evidence'] = evidence

    # Core validates evidence before entering its transaction, so seed it from the
    # same assessment, then recheck under its write lock before any audit/answer write.
    review_check(con, job)
    try:
        campaign.requeue(con, job_id, data, answers=cleaned, expected_status='skipped', review_check=review_check)
    except manual_jobs.ManualJobError:
        raise
    except ValueError as error:
        code = str(error)
        raise manual_jobs.ManualJobError(code, _ERRORS.get(code, 'The job could not be queued for review.')) from None
    return {'requeued': True, 'job_id': job_id, 'saved_answers': len(cleaned)}
