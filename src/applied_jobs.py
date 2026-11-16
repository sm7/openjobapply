"""Applied-job details for the dashboard. Read-only: this module never writes and never submits anything.

Only a job with the status 'applied' is listed. The detail comes from the job row, its newest 'applied' attempt and
its reservation. Evidence text can hold contact details, so the page never receives it whole: it receives one
bounded confirmation sentence with contact data redacted, a receipt filename and, when available, a local image URL.
"""
from pathlib import Path
import re
from urllib.parse import quote

import campaign
import db
from manual_jobs import safe_link

MAX_JOBS = 500
MAX_CONFIRMATION = 200

# The sentence of the evidence that states the employer accepted the application.
_CONFIRMATION = re.compile(
    r'your application (?:was|has been)|application (?:was|has been) (?:sent|received|submitted)|'
    r'thank you for applying|successfully (?:submitted|applied)|application submitted|applied successfully',
    re.IGNORECASE)
_CLAUSE_END = re.compile(r'[.!?;"]+(?=\s|$)')
_PHONE = re.compile(r'(?:\+?\d{1,3}[\s.-])?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}')
_RECEIPT_PATH = (r'''logs/(?P<folder>campaign/receipts|ats)/(?P<name>[A-Za-z0-9._-]{1,120}\.(?:webp|png|jpe?g))'''
                 r'''(?=$|[\s"'<>),;!]|\.(?=\s|$))''')
_RECEIPT_START = r'(?<![A-Za-z0-9_./\\:%-])'


def confirmation_sentence(evidence):
    """The clause that shows the employer's acceptance, redacted and bounded, or None when no clause shows it."""
    if not isinstance(evidence, str):
        return None
    text = ' '.join(evidence.split())
    found = _CONFIRMATION.search(text)
    if found is None:
        return None
    clause = text[found.start():]
    end = _CLAUSE_END.search(clause)
    if end is not None:
        clause = clause[:end.end()].rstrip('";')
    clause = campaign.outcome_reason(clause) or ''
    clause = _PHONE.sub('[redacted phone]', clause)
    return clause[:MAX_CONFIRMATION] or None


def _evidence_receipt(evidence):
    """(folder, name) of the receipt image the evidence names, or None.

    The path is either relative (logs/...) or prefixed with exactly the current project root; any other
    absolute prefix, traversal or longer name does not match.
    """
    if not isinstance(evidence, str):
        return None
    roots = {str(db.ROOT), str(Path(db.ROOT).resolve())}
    prefixes = '|'.join(re.escape(root + '/') for root in sorted(roots) if root.startswith('/') and len(root) > 1)
    prefix = f'(?:{prefixes})?' if prefixes else ''
    match = re.search(_RECEIPT_START + prefix + '(?i:' + _RECEIPT_PATH + ')', evidence)
    return (match.group('folder').lower(), match.group('name')) if match else None


def receipt_file(evidence):
    """The receipt screenshot file name that the evidence names, or None. The name is never a path."""
    found = _evidence_receipt(evidence)
    return found[1] if found else None


def _read_receipt(evidence, receipts_dir=None):
    """Read an evidence-linked image from the campaign receipts or ATS evidence directory only."""
    found = _evidence_receipt(evidence)
    if not found:
        return None
    folder, name = found
    configured_root = (Path(receipts_dir) if receipts_dir is not None and folder == 'campaign/receipts'
                       else db.ROOT.resolve() / 'logs' / folder)
    try:
        root = configured_root.resolve(strict=True)
        if (receipts_dir is None or folder == 'ats') and root != configured_root:
            return None
        path = (root / name).resolve(strict=True)
        path.relative_to(root)
        if not path.is_file():
            return None
        data = path.read_bytes()
    except (OSError, RuntimeError, ValueError):
        return None
    suffix = Path(name).suffix.lower()
    if (suffix == '.png' and len(data) >= 45 and data.startswith(b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR')
          and data[-12:] == b'\x00\x00\x00\x00IEND\xaeB`\x82'):
        content_type = 'image/png'
    elif suffix in ('.jpg', '.jpeg') and data.startswith(b'\xff\xd8\xff') and data.endswith(b'\xff\xd9'):
        content_type = 'image/jpeg'
    elif (suffix == '.webp' and len(data) >= 20 and data[:4] == b'RIFF' and data[8:12] == b'WEBP'
          and int.from_bytes(data[4:8], 'little') == len(data) - 8
          and data[12:16] in (b'VP8 ', b'VP8L', b'VP8X')):
        content_type = 'image/webp'
    else:
        return None
    return data, content_type


def receipt_image(con, job_id):
    """The latest applied attempt's local screenshot bytes and MIME type, or None; never accept a file path."""
    row = con.execute('''
        SELECT a.evidence FROM jobs j
        JOIN applications a ON a.id = (SELECT MAX(b.id) FROM applications b
                                       WHERE b.job_id = j.id AND b.result = 'applied')
        WHERE j.id = ? AND j.status = 'applied'
    ''', (job_id,)).fetchone()
    return _read_receipt(row['evidence']) if row else None


def _has_table(con, name):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def list_applied(con, receipts_dir=None):
    """Every applied job, newest application first, with what the ledger proves about it.

    ``controller_checked`` is true when the job also holds an 'applied' reservation. Safe on a read-only
    connection and on a database that predates the reservations table.
    """
    reservation_sql = ('(SELECT r.state FROM campaign_reservations r WHERE r.job_id = j.id)'
                       if _has_table(con, 'campaign_reservations') else 'NULL')
    # apply_method is added by a later migration, so a database that never ran it has no such column.
    columns = {row[1] for row in con.execute('PRAGMA table_info(jobs)')}
    apply_method_sql = 'j.apply_method' if 'apply_method' in columns else 'NULL'
    rows = con.execute(f'''
        SELECT j.id, j.company, j.title, j.location, j.url, j.apply_url, j.salary_min, j.salary_max,
               j.employment_type, j.source, {apply_method_sql} AS apply_method, j.easy_apply,
               a.attempted AS applied_at, a.evidence AS evidence,
               (SELECT COUNT(*) FROM applications x WHERE x.job_id = j.id) AS attempts,
               {reservation_sql} AS reservation
        FROM jobs j
        JOIN applications a ON a.id = (SELECT MAX(b.id) FROM applications b
                                       WHERE b.job_id = j.id AND b.result = 'applied')
        WHERE j.status = 'applied'
        ORDER BY a.attempted DESC, j.id LIMIT ?''', (MAX_JOBS,)).fetchall()
    jobs = []
    for row in rows:
        name = receipt_file(row['evidence'])
        available = _read_receipt(row['evidence'], receipts_dir) is not None
        jobs.append({
            'job_id': row['id'], 'company': row['company'], 'title': row['title'],
            'location': row['location'],
            'salary_min': row['salary_min'], 'salary_max': row['salary_max'],
            'employment_type': row['employment_type'],
            'route': 'linkedin_easy_apply' if row['easy_apply'] or row['apply_method'] in ('easy', 'easy_apply')
                     else ('employer_site' if row['apply_method'] in ('external', 'ats') or row['apply_url']
                           else 'unknown'),
            'posting_url': safe_link(row['url']),
            'apply_url': safe_link(row['apply_url']) if row['apply_url'] != row['url'] else None,
            'applied_at': row['applied_at'],
            'confirmation': confirmation_sentence(row['evidence']),
            'receipt_file': name,
            'receipt_file_saved': available,
            'receipt_url': '/api/applied/' + quote(str(row['id']), safe='') + '/receipt' if available else None,
            'controller_checked': row['reservation'] == 'applied',
            'attempts': row['attempts'],
        })
    return jobs


def summarize(jobs):
    """Counts the page shows above the list."""
    return {
        'count': len(jobs),
        'with_confirmation': sum(1 for j in jobs if j['confirmation']),
        'with_receipt_file': sum(1 for j in jobs if j['receipt_file_saved']),
        'without_controller_check': sum(1 for j in jobs if not j['controller_checked']),
    }
