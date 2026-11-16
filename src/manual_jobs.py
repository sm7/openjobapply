"""Manual-job review: list blocked jobs, record reviewed answers, requeue for automatic submission.

Owns the domain rules only: no HTTP, HTML or process logic. It never submits anything. A requeue returns the
job to the queue, and the worker still reserves a slot, passes the pacing gate and needs an employer receipt.
"""
import re
from urllib.parse import urlsplit

import campaign

MAX_ANSWERS = 25
MAX_QUESTION = 500
MAX_ANSWER = 2000

# A reservation exists only after the worker finished the form and reserved a submit slot, so a manual job that
# still holds one may have been sent. Its absence means the worker stopped before the final Submit.
_UNCERTAIN_RESERVATIONS = ('manual', 'failed')

MAX_URL = 2000

_REQUEUE_ERRORS = {
    'already_sent_or_reserved': 'An application for this posting was already sent or is in progress.',
    'requeue_requires_finished_non_applied_job': 'Only a finished, unsubmitted job can be resubmitted.',
    'unknown_job': 'Unknown job.',
}


class ManualJobError(ValueError):
    """An invalid or unsafe request. ``code`` is stable; the message is safe to show a user."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


# Why a job stopped, as a best guess from the reason text. The first matching rule wins, so the order matters.
# The group only decides what the dashboard shows first. It never gates a requeue and never changes the ledger.
GROUPS = (
    ('check_employer', 'Check the employer', 'A submit slot was reserved, so an application may have reached the employer.'),
    ('needs_answer', 'Needs your answer', 'A required question has no reviewed answer.'),
    ('retry_later', 'Retry later', 'A sign-in, verification or employer site step failed.'),
    ('decision', 'Needs your decision', 'Consent, work authorization or clearance needs a policy choice.'),
    ('not_a_fit', 'Not a fit now', 'The location, residence or eligibility does not match the current profile.'),
    ('other', 'Other', 'The reason did not match a known pattern. Read it before acting.'),
)
_GROUP_RULES = (
    ('needs_answer', r'unanswered|required question|cannot be completed'),
    ('retry_later', r'account_flow_failed|workday unavailable|\botp\b|authenticat|captcha|verification code|taleo|password|'
                    r'email-only|mailbox|sign-in|login'),
    ('decision', r'consent|waiver|hold harmless|prohibited|decline control|opt-out|agreement|terms and conditions|'
                 r'work authorization|work-authorization|h-4|public trust|clearance|background|conviction|\boig\b'),
    ('not_a_fit', r'excludes (?:the )?(?:candidate.s |your |home )?state|restricts remote|reside within|residents|local candidate|relocation|'
                  r'eligible-state|choices omit|no other or generic'),
    ('needs_answer', r'unknown|unconfirmed|not established|unestablished'),
)
_GROUP_PATTERNS = tuple((key, re.compile(pattern, re.IGNORECASE)) for key, pattern in _GROUP_RULES)
_HEADLINES = {
    'check_employer': 'This job may already have been submitted. Check the employer account first.',
    'needs_answer': 'The employer asks a required question that has no reviewed answer.',
    'retry_later': 'A sign-in, verification or employer site step failed.',
    'decision': 'This needs your decision on consent, authorization or clearance.',
    'not_a_fit': 'The location or eligibility does not match the current profile.',
    'other': 'The worker stopped for a reason that needs a review.',
}
_QUOTED_QUESTION = re.compile(r'"([^"]{8,%d}\?)"' % MAX_QUESTION)


def classify(reason, risk):
    """The group key for a job. A job that may have been sent is always 'check_employer'."""
    if risk == 'possibly_sent':
        return 'check_employer'
    text = reason or ''
    for key, pattern in _GROUP_PATTERNS:
        if pattern.search(text):
            return key
    return 'other'


def quoted_questions(reason):
    """Complete questions quoted in a reason, for the answer dialog. Elided text ('...') is never a question."""
    found = []
    for match in _QUOTED_QUESTION.finditer(reason or ''):
        question = ' '.join(match.group(1).split())
        if '...' not in question and question not in found:
            found.append(question)
    return found[:MAX_ANSWERS]


def group_counts(jobs):
    """Every group in a fixed order with its job count, including groups with no job."""
    counts = {}
    for job in jobs:
        counts[job['group']] = counts.get(job['group'], 0) + 1
    return [{'key': key, 'label': label, 'hint': hint, 'count': counts.get(key, 0)} for key, label, hint in GROUPS]


def sent_risk(reservation_state):
    """'possibly_sent' only when a submit slot had been reserved, otherwise 'not_sent'."""
    return 'possibly_sent' if reservation_state in _UNCERTAIN_RESERVATIONS else 'not_sent'


def safe_link(value):
    """The URL when it is safe to put in a link, otherwise None.

    The scraper writes these values, so they are untrusted. Accept only http or https with a host. Refuse
    credentials in the URL (user:pass@host), control characters, whitespace and very long values. The page makes
    the link with createElement and sets href only from a value that passed this check.
    """
    if not isinstance(value, str) or not value or len(value) > MAX_URL:
        return None
    if any(ord(ch) < 33 or 126 < ord(ch) < 160 for ch in value):
        return None
    try:
        parts = urlsplit(value)
        host = parts.hostname
        parts.port  # raises ValueError for a malformed port
    except ValueError:
        return None
    if parts.scheme not in ('http', 'https') or not host or parts.username is not None or parts.password is not None:
        return None
    return value


def _has_table(con, name):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def list_manual(con):
    """Every manual job with its latest reason (sanitized) and whether it may have been sent.

    Safe on a read-only connection and on a database that predates job_answers or reservations.
    """
    reservations = _has_table(con, 'campaign_reservations')
    answers = _has_table(con, 'job_answers')
    reservation_sql = ('(SELECT r.state FROM campaign_reservations r WHERE r.job_id = j.id)'
                       if reservations else 'NULL')
    rows = con.execute(f'''
        SELECT j.id, j.company, j.title, j.url, j.apply_url,
               (SELECT a.reason FROM applications a WHERE a.job_id = j.id AND a.result = 'manual'
                ORDER BY a.id DESC LIMIT 1) AS reason,
               (SELECT a.attempted FROM applications a WHERE a.job_id = j.id AND a.result = 'manual'
                ORDER BY a.id DESC LIMIT 1) AS attempted,
               {reservation_sql} AS reservation
        FROM jobs j WHERE j.status = 'manual' ORDER BY attempted DESC, j.id''').fetchall()
    jobs = []
    for row in rows:
        saved = con.execute('SELECT COUNT(*) FROM job_answers WHERE job_id = ?', (row['id'],)).fetchone()[0] if answers else 0
        risk = sent_risk(row['reservation'])
        reason = campaign.outcome_reason(row['reason']) or 'No reason recorded'
        group = classify(reason, risk)
        jobs.append({
            'job_id': row['id'], 'company': row['company'], 'title': row['title'],
            'posting_url': safe_link(row['url']),
            'apply_url': safe_link(row['apply_url']) if row['apply_url'] != row['url'] else None,
            'attempted': row['attempted'],
            'reason': reason,
            'sent_risk': risk,
            'saved_answers': saved,
            'group': group,
            'headline': _HEADLINES[group],
            'questions': quoted_questions(reason),
        })
    return jobs


def clean_answers(answers):
    """Validate the dialog's rows into {question: answer}. Blank rows are untouched fields, not answers."""
    if not isinstance(answers, list) or len(answers) > MAX_ANSWERS:
        raise ManualJobError('invalid_answers', f'Provide at most {MAX_ANSWERS} answers.')
    cleaned = {}
    for item in answers:
        if not isinstance(item, dict) or set(item) - {'question', 'answer'}:
            raise ManualJobError('invalid_answers', 'Each answer needs only a question and an answer.')
        question, answer = item.get('question'), item.get('answer')
        if not isinstance(question, str) or not isinstance(answer, str):
            raise ManualJobError('invalid_answers', 'Questions and answers must be text.')
        question, answer = ' '.join(question.split()), answer.strip()
        if not question and not answer:
            continue
        if not question or not answer:
            raise ManualJobError('invalid_answers', 'Each answer needs both its question and its answer.')
        if len(question) > MAX_QUESTION or len(answer) > MAX_ANSWER:
            raise ManualJobError('invalid_answers', 'A question or answer is too long.')
        cleaned[question] = answer
    return cleaned


def resubmit(con, job_id, answers, nothing_sent_confirmed=False):
    """Save reviewed answers and requeue the job in one transaction.

    A job that may have been sent is requeued only when the reviewer confirms, from the employer's own
    records, that no application was received. Missing evidence is never treated as absence.
    """
    cleaned = clean_answers(answers)
    row = con.execute('SELECT status FROM jobs WHERE id = ?', (job_id,)).fetchone()
    if row is None:
        raise ManualJobError('unknown_job', _REQUEUE_ERRORS['unknown_job'])
    if row['status'] != 'manual':
        raise ManualJobError('not_manual', 'Only a manual job can be resubmitted.')
    reservation = con.execute('SELECT state FROM campaign_reservations WHERE job_id = ?', (job_id,)).fetchone()
    risk = sent_risk(reservation['state'] if reservation else None)
    if risk == 'possibly_sent' and nothing_sent_confirmed is not True:
        raise ManualJobError(
            'confirmation_required',
            'This job may already have been submitted. Check the employer account for an application, '
            'then confirm that none was received.')
    evidence = ('The worker stopped before reserving a submit slot, so no final Submit was attempted.'
                if risk == 'not_sent' else
                'Reviewer confirmed from the employer account that no application was received.')
    try:
        campaign.requeue(con, job_id, {
            'reason': 'Resubmitted from the dashboard after reviewed answers.',
            'evidence': evidence, 'submission_verified_absent': True,
        }, answers=cleaned)
    except ValueError as error:
        code = str(error)
        raise ManualJobError(code, _REQUEUE_ERRORS.get(code, 'The job could not be resubmitted.')) from None
    return {'requeued': True, 'job_id': job_id, 'saved_answers': len(cleaned)}
