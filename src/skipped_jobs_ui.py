"""Dashboard skipped-job cards and single-job review dialog; untrusted values use textContent."""

STYLE = """
.skipped-intro{margin-bottom:14px}.skipped-search{display:grid;gap:4px;max-width:460px;margin:12px 0}
.skipped-search label,.skipped-answer label,.skipped-review-label{font-size:.85rem;font-weight:600}
.skipped-search input,#skipped-dialog input[type=text],#skipped-dialog textarea{font:inherit;width:100%;padding:8px 10px;border:1px solid #9aaaba;border-radius:6px}
.skipped-count,.skipped-meta,.skipped-company,.skipped-note{font-size:.88rem;color:#576777}
.skipped-job{border:1px solid #d6dde5;border-radius:10px;padding:14px 16px;margin:0 0 12px;background:#fff;overflow-wrap:anywhere}
.skipped-head{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:flex-start;gap:8px 16px}
.skipped-job h3{font-size:1.02rem;margin:0}.skipped-job h3 a{color:#152536;text-decoration-color:#9aaaba;text-underline-offset:3px}
.skipped-meta{display:flex;flex-wrap:wrap;gap:6px 18px;margin:10px 0}.skipped-reason{padding:10px 12px;background:#f5f7fa;border-left:4px solid #9aaaba;white-space:pre-wrap;overflow-wrap:anywhere}
.skipped-job details{margin:10px 0}.skipped-job summary{cursor:pointer;color:#1b5e9b;font-weight:600;font-size:.9rem}
.skipped-risk{background:#fff4e0;color:#8a5200;padding:2px 9px;border-radius:999px;font-size:.78rem;font-weight:600}
.skipped-blocked{padding:8px 12px;background:#fff4e0;border-radius:6px}.skipped-links{display:flex;flex-wrap:wrap;gap:8px 16px;margin:10px 0;font-size:.9rem}
.skipped-pages{display:flex;align-items:center;flex-wrap:wrap;gap:12px;margin:14px 0}
#panel-skipped button,#skipped-dialog button{font:inherit;border:1px solid #9aaaba;border-radius:6px;background:#fff;color:#152536;padding:7px 15px;cursor:pointer}
#panel-skipped button.primary,#skipped-dialog button.primary{background:#234d75;color:#fff;border-color:#234d75}
#panel-skipped button:disabled,#skipped-dialog button:disabled{opacity:.55;cursor:not-allowed}
#panel-skipped :is(button,input,summary,a):focus-visible,#skipped-dialog :is(button,input,textarea,a):focus-visible{outline:3px solid #4584bb;outline-offset:2px}
#skipped-dialog{width:min(640px,calc(100vw - 28px));max-height:calc(100vh - 28px);padding:0;border:1px solid #9aaaba;border-radius:12px;color:#152536;overflow:hidden}
#skipped-dialog[open]{display:flex;flex-direction:column}#skipped-dialog::backdrop{background:rgba(16,25,35,.55)}
#skipped-form{display:flex;flex-direction:column;min-height:0;max-height:calc(100vh - 28px)}
.skipped-dialog-head{flex:none;padding:20px 20px 4px}.skipped-dialog-head h2{margin:0 0 6px}
.skipped-dialog-body{flex:1 1 auto;min-height:0;overflow-y:auto;padding:0 20px}
.skipped-dialog-actions{flex:none;display:flex;flex-wrap:wrap;justify-content:flex-end;gap:10px;padding:14px 20px 18px;border-top:1px solid #d6dde5;background:#fff}
#skipped-dialog textarea{min-height:75px;resize:vertical}.skipped-answer{display:grid;gap:5px;margin:14px 0}
.skipped-confirm{margin:14px 0;padding:12px;border:1px solid #d9a441;background:#fff8e8;border-radius:8px}.skipped-confirm label{display:flex;gap:9px;align-items:flex-start}
@media(max-width:520px){.skipped-job{padding:12px}.skipped-search{max-width:none}.skipped-dialog-actions button{flex:1 1 100%}.skipped-dialog-head,.skipped-dialog-body,.skipped-dialog-actions{padding-left:14px;padding-right:14px}}
"""

PANEL = """<section id="panel-skipped" class="panel" role="tabpanel" aria-labelledby="tab-skipped" tabindex="0" hidden>
<h2>Skipped jobs</h2>
<p class="muted skipped-intro">Review jobs that were skipped, including screening-only exclusions. Queue a job only when you have a reason to revisit it. This requests a fresh eligibility check. It does not submit an application or override requirements.</p>
<div id="skipped-error" class="banner error" role="alert" hidden></div>
<div id="skipped-status" class="banner" role="status" hidden></div>
<div class="skipped-search"><label for="skipped-search">Search by company, role, location or reason</label><input id="skipped-search" type="search" maxlength="200" autocomplete="off" aria-controls="skipped-list"></div>
<p id="skipped-count" class="skipped-count" role="status" tabindex="-1"></p>
<div id="skipped-list"><p class="muted">Select this tab to load skipped jobs.</p></div>
<nav class="skipped-pages" aria-label="Skipped jobs pages"><button id="skipped-previous" type="button" aria-label="Previous page of skipped jobs" disabled>Previous</button><span id="skipped-page" aria-live="polite"></span><button id="skipped-next" type="button" aria-label="Next page of skipped jobs" disabled>Next</button></nav>
</section>
<dialog id="skipped-dialog" aria-labelledby="skipped-dialog-title"><form id="skipped-form" method="dialog">
<div class="skipped-dialog-head"><h2 id="skipped-dialog-title">Queue for another review</h2><p id="skipped-dialog-job" class="muted"></p></div>
<div class="skipped-dialog-body">
<p class="skipped-note">This requests a fresh eligibility check. It does not submit an application or override requirements.</p>
<h3>Original skip reason</h3><p id="skipped-dialog-reason" class="skipped-reason"></p><div id="skipped-dialog-links" class="skipped-links"></div>
<label for="skipped-review-note" class="skipped-review-label">What changed or why should this job get another review? (required)</label><textarea id="skipped-review-note" maxlength="2000" required></textarea>
<p class="skipped-note">Answers are optional. Enter each question exactly as the employer asks it, and only answer when you know it is true. Leave suggested questions unanswered if you have no reviewed answer.</p>
<div id="skipped-answers"></div><button id="skipped-add-answer" type="button">Add another answer</button>
<div id="skipped-confirm" class="skipped-confirm" hidden><label><input id="skipped-confirm-sent" type="checkbox"><span>This posting may already have been submitted. I checked the employer account, including equivalent listings, and no application was received.</span></label></div>
<p id="skipped-dialog-error" class="banner error" role="alert" hidden></p>
</div>
<div class="skipped-dialog-actions"><button id="skipped-cancel" type="button">Cancel</button><button id="skipped-submit" type="submit" class="primary">Save and queue for review</button></div>
</form></dialog>"""

SCRIPT = r"""
const SKIPPED_PAGE_SIZE = 25, SKIPPED_MAX_ROWS = 25;
const SKIPPED_TOKEN = document.querySelector('meta[name="monitor-token"]')?.content || '';
let skippedJobs = [], skippedPage = 0, skippedLastText = null, skippedPending = false, skippedDialogJob = null, skippedSubmitting = false;
const skippedOpenReasons = new Set();
function skippedText(tag, className, text) {
  const el = document.createElement(tag); if (className) el.className = className; el.textContent = text; return el;
}
function skippedMessage(id, text) { const el = $(id); el.textContent = text || ''; el.hidden = !text; }
function skippedLink(url, text) {
  if (typeof url !== 'string' || !url) return null;
  let parsed; try { parsed = new URL(url); } catch (error) { return null; }
  if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password) return null;
  const link = skippedText('a', '', text); link.href = parsed.href; link.target = '_blank'; link.rel = 'noopener noreferrer';
  link.setAttribute('aria-label', text + ', opens in a new tab'); return link;
}
function skippedRisky(job) { return job.sent_risk === 'possibly_sent'; }
function skippedAge(attempted) {
  return typeof attempted === 'number' && Number.isFinite(attempted) ? duration(Date.now() / 1000 - attempted) + ' ago' : 'No application attempt recorded';
}
function skippedCard(job) {
  const card = skippedText('article', 'skipped-job', ''); card.setAttribute('role', 'listitem');
  const head = skippedText('div', 'skipped-head', ''), titles = document.createElement('div'), title = document.createElement('h3');
  const name = job.title || 'Untitled role', link = skippedLink(job.posting_url, name);
  if (link) title.append(link); else title.textContent = name;
  titles.append(title, skippedText('div', 'skipped-company', job.company || 'Company not recorded'), skippedText('div', 'skipped-company', job.location || 'Location not recorded'));
  head.append(titles); if (skippedRisky(job)) head.append(skippedText('span', 'skipped-risk', 'May have been sent'));
  const meta = skippedText('div', 'skipped-meta', '');
  const attempted = skippedText('span', '', skippedAge(job.attempted));
  if (typeof job.attempted === 'number') attempted.dataset.skippedAttempted = String(job.attempted);
  meta.append(attempted, skippedText('span', '', job.saved_answers + ' saved answer(s)'));
  const details = document.createElement('details'); details.open = skippedOpenReasons.has(job.job_id);
  details.addEventListener('toggle', () => { if (details.open) skippedOpenReasons.add(job.job_id); else skippedOpenReasons.delete(job.job_id); });
  details.append(skippedText('summary', '', 'Show latest reason'), skippedText('p', 'skipped-reason', job.reason), skippedText('p', 'skipped-note', 'Job ' + job.job_id));
  card.append(head, meta, details);
  const employer = skippedLink(job.apply_url, 'Open the employer application page');
  if (employer) { const links = skippedText('div', 'skipped-links', ''); links.append(employer); card.append(links); }
  if (!job.requeue_allowed) card.append(skippedText('p', 'skipped-blocked', job.blocked_reason || 'This posting cannot safely be queued.'));
  const button = skippedText('button', 'primary', 'Review and queue'); button.type = 'button'; button.disabled = !job.requeue_allowed;
  button.dataset.skippedJobId = job.job_id; button.setAttribute('aria-label', 'Review and queue ' + name + ' at ' + (job.company || 'company not recorded'));
  button.addEventListener('click', () => openSkippedDialog(job)); card.append(button); return card;
}
function renderSkipped(keepScroll) {
  const scrollY = window.scrollY, focusedJob = document.activeElement?.dataset?.skippedJobId;
  const query = $('skipped-search').value.trim().toLowerCase();
  const matches = skippedJobs.filter(job => !query || [job.company, job.title, job.location, job.reason, job.job_id].some(value => typeof value === 'string' && value.toLowerCase().includes(query)));
  const pages = Math.max(1, Math.ceil(matches.length / SKIPPED_PAGE_SIZE)); skippedPage = Math.min(skippedPage, pages - 1);
  const start = skippedPage * SKIPPED_PAGE_SIZE, shown = matches.slice(start, start + SKIPPED_PAGE_SIZE), list = $('skipped-list');
  list.replaceChildren(); list.setAttribute('role', 'list');
  if (!shown.length) list.append(skippedText('p', 'muted', skippedJobs.length ? 'No skipped jobs match this search.' : 'No skipped jobs.'));
  else for (const job of shown) list.append(skippedCard(job));
  $('skipped-count').textContent = 'Showing ' + (shown.length ? (start + 1) + '–' + (start + shown.length) : '0') + ' of ' + matches.length + ' matching jobs (' + skippedJobs.length + ' skipped total).';
  $('skipped-page').textContent = 'Page ' + (skippedPage + 1) + ' of ' + pages;
  $('skipped-previous').disabled = skippedPage === 0; $('skipped-next').disabled = skippedPage + 1 >= pages;
  if (focusedJob) {
    const replacement = [...list.querySelectorAll('button')].find(button => button.dataset.skippedJobId === focusedJob);
    (replacement || $('skipped-count')).focus({preventScroll: true});
  }
  if (keepScroll) window.scrollTo(0, scrollY);
}
async function refreshSkipped() {
  if (activeTab !== 'skipped' || skippedPending || $('skipped-dialog').open) return;
  skippedPending = true;
  const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 6000);
  try {
    const response = await fetch('/api/skipped', {cache: 'no-store', credentials: 'omit', signal: controller.signal});
    if (!response.ok) throw new Error('request failed');
    const text = await response.text(), data = JSON.parse(text);
    if (!data || !Array.isArray(data.jobs)) throw new Error('invalid response');
    if ($('skipped-dialog').open) return;
    if (text !== skippedLastText) { skippedJobs = data.jobs; renderSkipped(true); skippedLastText = text; }
    else for (const el of document.querySelectorAll('#skipped-list [data-skipped-attempted]')) el.textContent = skippedAge(Number(el.dataset.skippedAttempted));
    skippedMessage('skipped-error', '');
  } catch (error) {
    skippedMessage('skipped-error', 'Skipped jobs could not be refreshed' + (error.name === 'AbortError' ? ' (timed out)' : '') + '. The list below is the last data received.');
  } finally { clearTimeout(timer); skippedPending = false; }
}
function skippedAnswerRow(question) {
  const n = $('skipped-answers').children.length + 1, row = skippedText('div', 'skipped-answer', '');
  if (question) row.dataset.suggested = '1';
  const qLabel = skippedText('label', '', 'Exact question ' + n), q = document.createElement('input');
  q.type = 'text'; q.id = 'skipped-question-' + n; q.maxLength = 500; q.value = question || ''; qLabel.htmlFor = q.id;
  const aLabel = skippedText('label', '', 'Answer ' + n), a = document.createElement('textarea');
  a.id = 'skipped-answer-' + n; a.maxLength = 2000; aLabel.htmlFor = a.id;
  row.append(qLabel, q, aLabel, a); return row;
}
function openSkippedDialog(job) {
  if (!job.requeue_allowed) return;
  skippedDialogJob = job;
  $('skipped-dialog-job').textContent = (job.company || 'Company not recorded') + ' · ' + (job.title || 'Role') + ' · ' + (job.location || 'Location not recorded') + ' · Job ' + job.job_id;
  $('skipped-dialog-reason').textContent = job.reason;
  const links = $('skipped-dialog-links'); links.replaceChildren();
  for (const [url, label] of [[job.posting_url, 'Open the job posting'], [job.apply_url, 'Open the employer application page']]) { const link = skippedLink(url, label); if (link) links.append(link); }
  links.hidden = !links.children.length;
  $('skipped-review-note').value = ''; $('skipped-confirm-sent').checked = false; $('skipped-confirm').hidden = !skippedRisky(job);
  const rows = $('skipped-answers'); rows.replaceChildren();
  const questions = (Array.isArray(job.questions) ? job.questions : []).filter(q => typeof q === 'string' && q).slice(0, SKIPPED_MAX_ROWS);
  for (const question of questions) rows.append(skippedAnswerRow(question));
  if (!questions.length) rows.append(skippedAnswerRow());
  $('skipped-add-answer').disabled = rows.children.length >= SKIPPED_MAX_ROWS;
  $('skipped-submit').disabled = false; $('skipped-cancel').disabled = false; skippedMessage('skipped-dialog-error', '');
  $('skipped-dialog').showModal(); $('skipped-review-note').focus();
}
function skippedCollectAnswers() {
  return [...$('skipped-answers').children]
    .filter(row => !(row.dataset.suggested && !row.querySelector('textarea').value.trim()))
    .map(row => ({question: row.querySelector('input').value, answer: row.querySelector('textarea').value}));
}
async function submitSkipped(event) {
  event.preventDefault();
  if (!skippedDialogJob || skippedSubmitting) return;
  const job = skippedDialogJob, reviewNote = $('skipped-review-note').value.trim(), risky = skippedRisky(job);
  if (!reviewNote) { skippedMessage('skipped-dialog-error', 'Explain why this job should get another review.'); $('skipped-review-note').focus(); return; }
  if (risky && !$('skipped-confirm-sent').checked) { skippedMessage('skipped-dialog-error', 'Check the employer account and confirm that no application was received first.'); $('skipped-confirm-sent').focus(); return; }
  skippedSubmitting = true; $('skipped-submit').disabled = true; $('skipped-cancel').disabled = true; skippedMessage('skipped-dialog-error', '');
  try {
    const response = await fetch('/api/skipped/requeue', {
      method: 'POST', cache: 'no-store', credentials: 'omit', headers: {'Content-Type': 'application/json', 'X-Monitor-Token': SKIPPED_TOKEN},
      body: JSON.stringify({job_id: job.job_id, answers: skippedCollectAnswers(), review_note: reviewNote, nothing_sent_confirmed: risky && $('skipped-confirm-sent').checked}),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'The request was refused.');
    $('skipped-dialog').close(); skippedDialogJob = null;
    const status = $('skipped-status'), link = skippedText('a', '', 'Job ' + job.job_id + ' — queued for review'); link.href = '#overview';
    status.replaceChildren(link, document.createTextNode((data.saved_answers ? ' with ' + data.saved_answers + ' saved answer(s)' : '') + '. Not applied. This requests a fresh eligibility check; it does not submit an application or override requirements.'));
    status.hidden = false; refreshSkipped();
  } catch (error) { skippedMessage('skipped-dialog-error', error.message); }
  finally { skippedSubmitting = false; $('skipped-submit').disabled = false; $('skipped-cancel').disabled = false; }
}
$('skipped-form').addEventListener('submit', submitSkipped);
$('skipped-cancel').addEventListener('click', () => { if (!skippedSubmitting) { $('skipped-dialog').close(); skippedDialogJob = null; } });
$('skipped-dialog').addEventListener('cancel', event => { if (skippedSubmitting) event.preventDefault(); else skippedDialogJob = null; });
$('skipped-add-answer').addEventListener('click', () => {
  const rows = $('skipped-answers'); if (rows.children.length >= SKIPPED_MAX_ROWS) return;
  const row = skippedAnswerRow(); rows.append(row); $('skipped-add-answer').disabled = rows.children.length >= SKIPPED_MAX_ROWS; row.querySelector('input').focus();
});
$('skipped-search').addEventListener('input', () => { skippedPage = 0; renderSkipped(false); });
$('skipped-previous').addEventListener('click', () => { if (skippedPage > 0) { skippedPage--; renderSkipped(false); $('skipped-count').focus(); } });
$('skipped-next').addEventListener('click', () => { skippedPage++; renderSkipped(false); $('skipped-count').focus(); });
setInterval(refreshSkipped, 5000);
"""
