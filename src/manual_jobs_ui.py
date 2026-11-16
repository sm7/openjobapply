"""Markup, styles and script for the dashboard's Manual jobs tab.

Kept apart from campaign_monitor.py so that file stays focused on routing and the read-only tabs. Every value
shown comes from the server as data and is written with textContent, never innerHTML.

The tab remembers two things in the browser. Dismissed job ids live in localStorage, and Dismiss never calls the
server. The selected group filter lives in the URL hash as #manual/<group>, written with history.replaceState.
The tab router in campaign_monitor.py reads only the part of the hash before the first slash.
"""

STYLE = """
.manual-intro{margin-bottom:14px}.manual-empty{padding:18px 0;color:#576777}
.manual-chips{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 10px}.manual-chips[hidden],.chip[hidden]{display:none}
.chip{font:inherit;font-size:.88rem;border:1px solid #9aaaba;border-radius:999px;background:#fff;color:#152536;padding:5px 13px;cursor:pointer}
.chip[aria-pressed="true"]{background:#234d75;color:#fff;border-color:#234d75}
.chip.empty:not([aria-pressed="true"]){color:#576777;border-style:dashed}
.chip.risk:not([aria-pressed="true"]){background:#fff4e0;color:#8a5200;border-color:#d9a441}
.chip:focus-visible,.manual-search input:focus-visible,.manual-details summary:focus-visible,.manual-shown:focus-visible{outline:3px solid #4584bb;outline-offset:2px}
.manual-hint{margin:0 0 8px;padding:8px 12px;background:#edf1f6;border-radius:6px}.manual-hint[hidden]{display:none}
.manual-note{font-size:.88rem;margin:0 0 8px}
.manual-tools{display:flex;flex-wrap:wrap;gap:10px 16px;align-items:flex-end;margin:12px 0 8px}
.manual-search{display:grid;gap:4px;flex:1 1 240px;max-width:460px}.manual-search label{font-size:.85rem;font-weight:600}
.manual-search input{font:inherit;width:100%;padding:8px 10px;border:1px solid #9aaaba;border-radius:6px}
.manual-shown{font-size:.88rem;color:#576777;margin:10px 0}
.manual-group h3{font-size:1.02rem;margin:20px 0 10px;padding-bottom:4px;border-bottom:1px solid #d6dde5}
.manual-job{border:1px solid #d6dde5;border-radius:10px;padding:14px 16px;margin-bottom:12px;background:#fff}
.manual-job.dismissed{background:#f5f7fa;border-style:dashed}
.manual-head{display:flex;flex-wrap:wrap;justify-content:space-between;gap:8px 16px;align-items:flex-start}
.manual-job h4{font-size:1.02rem;margin:0;overflow-wrap:anywhere}.manual-company{font-size:.9rem;color:#576777}
.manual-headline{margin:10px 0}
.manual-reason{margin:10px 0;padding:10px 12px;background:#f5f7fa;border-left:4px solid #9aaaba;border-radius:4px;overflow-wrap:anywhere}
.manual-meta{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:.82rem;color:#576777;margin-bottom:10px}
.manual-details{margin:0 0 12px}.manual-details summary{cursor:pointer;color:#1b5e9b;font-weight:600;font-size:.9rem;border-radius:3px}
.manual-details .manual-reason{margin:8px 0}
.manual-actions{display:flex;flex-wrap:wrap;gap:8px}
.badge{display:inline-block;padding:2px 9px;border-radius:999px;font-size:.78rem;font-weight:600}
.badge.risk{background:#fff4e0;color:#8a5200}
.manual-job button,dialog button{font:inherit;border:1px solid #9aaaba;border-radius:6px;background:#fff;color:#152536;padding:7px 15px;cursor:pointer}
.manual-job button.primary,dialog button.primary{background:#234d75;color:#fff;border-color:#234d75}
.manual-job button.id-chip{padding:2px 10px;font-size:.8rem;border-radius:999px;overflow-wrap:anywhere;text-align:left}
.manual-job button:focus-visible,dialog button:focus-visible,dialog input:focus-visible,dialog textarea:focus-visible,dialog summary:focus-visible{outline:3px solid #4584bb;outline-offset:2px}
dialog button:disabled{opacity:.55;cursor:not-allowed}
dialog{width:min(640px,calc(100vw - 28px));max-height:calc(100vh - 28px);border:1px solid #9aaaba;border-radius:12px;padding:0;color:#152536;overflow:hidden}
dialog[open]{display:flex;flex-direction:column}dialog::backdrop{background:rgba(16,25,35,.55)}
#manual-form{display:flex;flex-direction:column;min-height:0;max-height:calc(100vh - 28px)}
.dialog-head{flex:none;max-height:45vh;overflow-y:auto;padding:20px 20px 4px}.dialog-head h2{margin-bottom:6px}
.dialog-head summary{cursor:pointer;color:#1b5e9b;font-weight:600;font-size:.9rem;margin-top:8px}
.dialog-body{flex:1 1 auto;min-height:0;overflow-y:auto;padding:0 20px}
.dialog-actions{flex:none;display:flex;gap:10px;justify-content:flex-end;flex-wrap:wrap;padding:14px 20px 18px;border-top:1px solid #d6dde5;background:#fff}
.answer-row{display:grid;gap:5px;margin:12px 0}.answer-row label{font-size:.85rem;font-weight:600}
dialog input[type=text],dialog textarea{font:inherit;width:100%;padding:8px 10px;border:1px solid #9aaaba;border-radius:6px}
dialog textarea{min-height:70px;resize:vertical}
.dialog-note{font-size:.85rem;color:#576777;margin:8px 0}
.dialog-group-note{margin:8px 0;padding:8px 12px;background:#edf1f6;border-radius:6px;font-size:.9rem}.dialog-group-note[hidden]{display:none}
.confirm-box{margin:14px 0;padding:12px;border:1px solid #d9a441;background:#fff8e8;border-radius:8px}
.confirm-box label{display:flex;gap:9px;align-items:flex-start}
.manual-links{display:flex;flex-wrap:wrap;gap:6px 16px;margin:0 0 10px;font-size:.9rem}
.manual-links a{color:#1b5e9b;font-weight:600;text-underline-offset:2px}
.manual-links a:focus-visible{outline:3px solid #4584bb;outline-offset:2px;border-radius:3px}
.manual-job h4 a.manual-title-link{color:#152536;text-decoration:underline;text-decoration-color:#9aaaba;text-underline-offset:3px}
.manual-job h4 a.manual-title-link:hover{color:#1b5e9b;text-decoration-color:#1b5e9b}
.manual-job h4 a.manual-title-link:focus-visible{outline:3px solid #4584bb;outline-offset:2px;border-radius:3px}
@media(max-width:520px){.manual-job{padding:12px}.manual-actions button{flex:1 1 100%}.manual-search{max-width:none}.dialog-actions button{flex:1 1 100%}.dialog-head,.dialog-actions{padding-left:14px;padding-right:14px}.dialog-body{padding:0 14px}}
"""

PANEL = """<section id="panel-manual" class="panel" role="tabpanel" aria-labelledby="tab-manual" tabindex="0" hidden>
<h2>Manual jobs</h2>
<p class="muted manual-intro">The campaign could not finish these jobs on its own. Pick a job, add an answer if it needs one, and return it to the queue so the worker tries again. Returning a job to the queue does not submit anything by itself. The worker still checks the form first and needs the employer's confirmation.</p>
<div id="manual-error" class="banner error" role="alert" hidden></div>
<div id="manual-status" class="banner" role="status" hidden></div>
<div id="manual-chips" class="manual-chips" role="group" aria-label="Show jobs by kind of stop" hidden></div>
<p id="manual-hint" class="manual-hint" hidden></p>
<p id="manual-guess" class="muted manual-note">Grouping is a best guess from the reason text. It never limits what you can do with a job.</p>
<div class="manual-tools">
<div class="manual-search"><label for="manual-search">Search by company, role or reason</label><input type="search" id="manual-search" autocomplete="off" maxlength="200" aria-controls="manual-list"></div>
<button type="button" id="manual-show-dismissed" class="chip" aria-pressed="false" hidden>Show dismissed (0)</button>
</div>
<p id="manual-dismiss-note" class="muted manual-note">Dismiss only hides a job in this browser. It does not change the job.</p>
<p id="manual-shown" class="manual-shown" role="status" tabindex="-1"></p>
<div id="manual-list"><p class="manual-empty">Select this tab to load manual jobs.</p></div>
</section>
<dialog id="manual-dialog" aria-labelledby="manual-dialog-title"><form method="dialog" id="manual-form">
<div class="dialog-head"><h2 id="manual-dialog-title">Return job to queue</h2>
<p id="manual-dialog-job" class="muted"></p>
<p id="manual-dialog-headline"></p>
<p id="manual-dialog-note" class="dialog-group-note" hidden></p>
<div id="manual-dialog-links" class="manual-links" hidden></div>
<details id="manual-dialog-details"><summary>Show the full reason</summary><p id="manual-dialog-reason" class="manual-reason"></p></details></div>
<div class="dialog-body">
<p class="dialog-note">Enter each question exactly as the employer asks it, with your answer. Only give answers you are sure are true. A question you leave unanswered is skipped. Returning a job to the queue does not submit anything by itself.</p>
<div id="manual-answers"></div>
<button type="button" id="manual-add-answer">Add another answer</button>
<div id="manual-confirm" class="confirm-box" hidden><label><input type="checkbox" id="manual-confirm-sent"><span>This job may already have been submitted. I checked the employer account and <strong>no application was received</strong>.</span></label></div>
<p id="manual-dialog-error" class="banner error" role="alert" hidden></p>
</div>
<div class="dialog-actions"><button type="button" id="manual-cancel">Cancel</button><button type="submit" id="manual-submit" class="primary">Save and return to queue</button></div>
</form></dialog>"""

SCRIPT = r"""
const TOKEN = document.querySelector('meta[name="monitor-token"]')?.content || '';
const MANUAL_MAX_ROWS = 25;
const DISMISSED_KEY = 'manual-dismissed';
const SENT_FILTER = 'sent';
const RETURN_LABELS = {needs_answer: 'Add answers and return to queue', check_employer: 'Check, then return to queue', retry_later: 'Return to queue'};
const RETURN_ANYWAY = 'Return to queue anyway';
const SENT_HINT = 'The worker may have reached the final step for these jobs. Check the employer account before you return one to the queue.';
const NOT_ANSWER_NOTE = 'This stop is not usually fixed by an answer. Add answers only if you have a real one.';
const DIALOG_NOTES = {retry_later: 'The worker tries again when it next reaches this job.'};
let manualJobs = [], manualGroups = [], manualLastText = null, manualPending = false, manualFailed = false, dialogJob = null;
let manualFilter = manualHashFilter() || 'all', manualShowDismissed = false;
const manualOpenReasons = new Set();
const manualDismissed = readDismissed();
function manualMessage(id, text) { const el = $(id); el.hidden = !text; el.textContent = text || ''; }
function manualHashFilter() {
  const [tab, group] = location.hash.slice(1).split('/');
  if (tab !== 'manual') return null;
  return /^[a-z_]+$/.test(group || '') ? group : 'all';
}
function manualKnownFilter(key) { return key === 'all' || key === SENT_FILTER || manualGroups.some(group => group.key === key); }
function syncManualHash() {
  if (activeTab !== 'manual') return;
  const wanted = '#manual' + (manualFilter === 'all' ? '' : '/' + manualFilter);
  if (location.hash === wanted) return;
  try { history.replaceState(null, '', wanted); } catch (error) { /* the filter still works without the address */ }
}
function readDismissed() {
  try {
    const value = JSON.parse(localStorage.getItem(DISMISSED_KEY) || '[]');
    return new Set(Array.isArray(value) ? value.filter(id => typeof id === 'string') : []);
  } catch (error) { return new Set(); }
}
function writeDismissed() {
  try { localStorage.setItem(DISMISSED_KEY, JSON.stringify([...manualDismissed])); } catch (error) { /* private mode: the choice lasts until the page closes */ }
}
function setDismissed(jobId, dismissed) {
  if (dismissed) manualDismissed.add(jobId); else manualDismissed.delete(jobId);
  writeDismissed(); renderManual(true);
}
const LINK_LABELS = [['posting_url', 'Open the job posting'], ['apply_url', 'Open the employer application page']];
function manualLink(url, text, hint) {
  let parsed;
  try { parsed = new URL(url); } catch (error) { return null; }
  if (parsed.protocol !== 'https:' && parsed.protocol !== 'http:') return null;
  const link = document.createElement('a');
  link.href = parsed.href; link.target = '_blank'; link.rel = 'noopener noreferrer';
  link.textContent = text;
  link.title = hint + ' (opens in a new tab)';
  link.setAttribute('aria-label', hint + ', opens in a new tab');
  return link;
}
function manualLinks(job) {
  const links = [];
  for (const [field, label] of LINK_LABELS) { const link = job[field] ? manualLink(job[field], label, label) : null; if (link) links.push(link); }
  return links;
}
function manualRisky(job) { return job.sent_risk === 'possibly_sent'; }
function manualGroupOf(job) { return manualGroups.some(group => group.key === job.group) ? job.group : 'other'; }
function manualPlain(tag, className, text) {
  const el = document.createElement(tag); if (className) el.className = className; el.textContent = text; return el;
}
async function copyJobId(button, jobId) {
  try { await navigator.clipboard.writeText(jobId); } catch (error) { return; }
  button.textContent = 'Copied'; button.setAttribute('aria-label', 'Job ID copied');
  setTimeout(() => { button.textContent = 'Job ' + jobId; button.setAttribute('aria-label', 'Copy job ID ' + jobId); }, 1500);
}
function manualCard(job, dismissed) {
  const card = document.createElement('article'); card.className = 'manual-job' + (dismissed ? ' dismissed' : ''); card.setAttribute('role', 'listitem');
  const head = document.createElement('div'); head.className = 'manual-head';
  const titles = document.createElement('div');
  const title = document.createElement('h4');
  const titleText = job.title || 'Untitled role';
  const titleLink = job.posting_url ? manualLink(job.posting_url, titleText, 'Open the job posting for ' + titleText) : null;
  if (titleLink) { titleLink.className = 'manual-title-link'; title.append(titleLink); } else title.textContent = titleText;
  const company = document.createElement('div'); company.className = 'manual-company'; company.textContent = job.company || 'Company not recorded';
  titles.append(title, company);
  head.append(titles);
  if (manualRisky(job)) head.append(manualPlain('span', 'badge risk', 'May have been sent'));
  const headline = manualPlain('p', 'manual-headline', job.headline || 'This job needs a look.');
  const meta = document.createElement('div'); meta.className = 'manual-meta';
  const stopped = manualPlain('span', '', 'Stopped ' + age(job.attempted)); stopped.dataset.attempted = String(job.attempted);
  meta.append(stopped, manualPlain('span', '', job.saved_answers ? job.saved_answers + ' saved answer(s)' : 'No saved answers'));
  if (dismissed) meta.append(manualPlain('span', '', 'Dismissed in this browser'));
  const details = document.createElement('details'); details.className = 'manual-details';
  details.open = manualOpenReasons.has(job.job_id);
  details.addEventListener('toggle', () => { if (details.open) manualOpenReasons.add(job.job_id); else manualOpenReasons.delete(job.job_id); });
  const idChip = manualPlain('button', 'id-chip', 'Job ' + job.job_id); idChip.type = 'button';
  idChip.title = 'Copy the job ID'; idChip.setAttribute('aria-label', 'Copy job ID ' + job.job_id);
  idChip.addEventListener('click', () => copyJobId(idChip, job.job_id));
  details.append(manualPlain('summary', '', 'Show the full reason'), manualPlain('p', 'manual-reason', job.reason), idChip);
  const actions = document.createElement('div'); actions.className = 'manual-actions';
  const button = document.createElement('button'); button.type = 'button';
  button.className = job.group === 'needs_answer' || job.group === 'retry_later' ? 'primary' : '';
  button.textContent = RETURN_LABELS[job.group] || RETURN_ANYWAY;
  button.dataset.jobId = job.job_id; button.dataset.action = 'open';
  button.addEventListener('click', () => openManualDialog(job));
  const dismiss = manualPlain('button', '', dismissed ? 'Restore' : 'Dismiss'); dismiss.type = 'button';
  dismiss.title = dismissed ? 'Show this job in the list again' : 'Hide this job in this browser only';
  dismiss.dataset.jobId = job.job_id; dismiss.dataset.action = 'dismiss-toggle';
  dismiss.addEventListener('click', () => setDismissed(job.job_id, !dismissed));
  actions.append(button, dismiss);
  const employer = job.apply_url ? manualLink(job.apply_url, 'Open the employer application page', 'Open the employer application page') : null;
  if (employer) { const row = document.createElement('div'); row.className = 'manual-links'; row.append(employer); card.append(head, headline, row, meta, details, actions); }
  else card.append(head, headline, meta, details, actions);
  return card;
}
function manualFocusState() {
  const el = document.activeElement;
  if (!el || !el.dataset) return null;
  if (el.dataset.filter) return {filter: el.dataset.filter};
  if (el.dataset.jobId && $('manual-list').contains(el)) return {job: el.dataset.jobId, action: el.dataset.action};
  return null;
}
function restoreManualFocus(state) {
  if (!state) return;
  const found = [...document.querySelectorAll('#panel-manual button')].find(b => state.filter ? b.dataset.filter === state.filter : b.dataset.jobId === state.job && b.dataset.action === state.action);
  (found || $('manual-shown')).focus();
}
function manualChip(key, text, className) {
  const chip = manualPlain('button', 'chip' + (className ? ' ' + className : ''), text); chip.type = 'button';
  chip.dataset.filter = key; chip.setAttribute('aria-pressed', String(manualFilter === key));
  chip.addEventListener('click', () => { manualFilter = key; syncManualHash(); renderManual(false); restoreManualFocus({filter: key}); });
  return chip;
}
function manualChips(base) {
  const counts = {}, target = $('manual-chips');
  for (const job of base) { const key = manualGroupOf(job); counts[key] = (counts[key] || 0) + 1; }
  const chips = [manualChip('all', 'All (' + base.length + ')', '')];
  for (const group of manualGroups) { const count = counts[group.key] || 0; chips.push(manualChip(group.key, group.label + ' (' + count + ')', count ? '' : 'empty')); }
  const sent = base.filter(manualRisky).length;
  chips.push(manualChip(SENT_FILTER, 'May have been sent (' + sent + ')', 'risk' + (sent ? '' : ' empty')));
  target.replaceChildren(...chips); target.hidden = !base.length;
  const hint = $('manual-hint');
  const selected = manualGroups.find(group => group.key === manualFilter);
  const text = manualFilter === SENT_FILTER ? SENT_HINT : selected ? selected.hint : '';
  hint.hidden = !text || !base.length; hint.textContent = text || '';
}
function manualMatches(job, query) {
  if (manualFilter === SENT_FILTER) { if (!manualRisky(job)) return false; }
  else if (manualFilter !== 'all' && manualGroupOf(job) !== manualFilter) return false;
  if (!query) return true;
  return [job.company, job.title, job.reason].some(value => typeof value === 'string' && value.toLowerCase().includes(query));
}
function renderManual(keepScroll) {
  const focus = manualFocusState(), scrollY = window.scrollY;
  const dismissedCount = manualJobs.filter(job => manualDismissed.has(job.job_id)).length;
  if (!dismissedCount) manualShowDismissed = false;
  const toggle = $('manual-show-dismissed');
  toggle.hidden = !dismissedCount; toggle.textContent = 'Show dismissed (' + dismissedCount + ')';
  toggle.setAttribute('aria-pressed', String(manualShowDismissed));
  const base = manualJobs.filter(job => manualShowDismissed || !manualDismissed.has(job.job_id));
  manualChips(base);
  const query = $('manual-search').value.trim().toLowerCase();
  const shown = base.filter(job => manualMatches(job, query));
  const target = $('manual-list'); target.replaceChildren();
  set('manual-shown', manualJobs.length ? 'Showing ' + shown.length + ' of ' + base.length + ' job(s).' : '');
  if (!manualJobs.length) {
    target.append(manualPlain('p', 'manual-empty', 'No manual jobs. Everything the campaign touched has either been submitted or is queued.'));
  } else if (!shown.length) {
    target.append(manualPlain('p', 'manual-empty', base.length ? 'No jobs match. Clear the search or pick another group.' : 'Every job is dismissed in this browser. Use Show dismissed to see them again.'));
  } else {
    for (const group of manualGroups) {
      const items = shown.filter(job => manualGroupOf(job) === group.key);
      if (!items.length) continue;
      const section = document.createElement('section'); section.className = 'manual-group';
      const heading = manualPlain('h3', '', group.label + ' (' + items.length + ')'); heading.id = 'manual-group-' + group.key;
      section.setAttribute('aria-labelledby', heading.id);
      const list = document.createElement('div'); list.setAttribute('role', 'list');
      for (const job of items) list.append(manualCard(job, manualDismissed.has(job.job_id)));
      section.append(heading, list); target.append(section);
    }
  }
  if (keepScroll) window.scrollTo(0, scrollY);
  restoreManualFocus(focus);
}
function updateManualAges() {
  for (const el of document.querySelectorAll('#manual-list [data-attempted]')) el.textContent = 'Stopped ' + age(Number(el.dataset.attempted));
}
function applyManualData(data) {
  manualJobs = data.jobs;
  manualGroups = data.groups.filter(group => group && typeof group.key === 'string');
  if (!manualGroups.some(group => group.key === 'other')) manualGroups.push({key: 'other', label: 'Other', hint: '', count: 0});
  const ids = new Set(manualJobs.map(job => job.job_id));
  let pruned = false;
  for (const id of [...manualDismissed]) if (!ids.has(id)) { manualDismissed.delete(id); pruned = true; }
  if (pruned) writeDismissed();
  if (manualFilter !== 'all' && manualFilter !== SENT_FILTER && !manualGroups.some(group => group.key === manualFilter)) manualFilter = 'all';
  renderManual(true);
}
async function refreshManual() {
  if (activeTab !== 'manual' || manualPending || $('manual-dialog').open) return;
  manualPending = true;
  const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 6000);
  try {
    const response = await fetch('/api/manual', {cache: 'no-store', credentials: 'omit', signal: controller.signal});
    if (!response.ok) throw new Error('request failed');
    const text = await response.text();
    const data = JSON.parse(text);
    if (!data || !Array.isArray(data.jobs) || !Array.isArray(data.groups)) throw new Error('invalid response');
    if (text !== manualLastText) { applyManualData(data); manualLastText = text; } else updateManualAges();
    manualFailed = false; manualMessage('manual-error', ''); syncManualHash();
  } catch (error) {
    manualFailed = true;
    manualMessage('manual-error', 'Manual jobs could not be refreshed' + (error.name === 'AbortError' ? ' (timed out)' : '') + '. The list below is the last data received.');
  } finally { clearTimeout(timer); manualPending = false; }
}
function answerRow(question, answer, suggested) {
  const n = $('manual-answers').children.length + 1;
  const row = document.createElement('div'); row.className = 'answer-row';
  if (suggested) row.dataset.suggested = '1';
  const qLabel = document.createElement('label'); qLabel.textContent = 'Question ' + n; qLabel.htmlFor = 'manual-question-' + n;
  const q = document.createElement('input'); q.type = 'text'; q.maxLength = 500; q.value = question || '';
  q.id = 'manual-question-' + n; q.setAttribute('aria-label', 'Question ' + n);
  const aLabel = document.createElement('label'); aLabel.textContent = 'Answer ' + n; aLabel.htmlFor = 'manual-answer-' + n;
  const a = document.createElement('textarea'); a.maxLength = 2000; a.value = answer || '';
  a.id = 'manual-answer-' + n; a.setAttribute('aria-label', 'Answer ' + n);
  row.append(qLabel, q, aLabel, a);
  return row;
}
function updateDialogControls() {
  const rows = [...$('manual-answers').children];
  const filled = rows.some(row => row.querySelector('textarea').value.trim());
  $('manual-submit').textContent = filled ? 'Save answers and return to queue' : 'Save and return to queue';
  $('manual-add-answer').disabled = rows.length >= MANUAL_MAX_ROWS;
}
function openManualDialog(job) {
  dialogJob = job;
  set('manual-dialog-job', (job.company || 'Company') + ' · ' + (job.title || 'Role') + ' · Job ' + job.job_id);
  set('manual-dialog-headline', job.headline || '');
  const note = job.group === 'needs_answer' ? '' : DIALOG_NOTES[job.group] || NOT_ANSWER_NOTE;
  manualMessage('manual-dialog-note', note);
  set('manual-dialog-reason', job.reason);
  const questions = (Array.isArray(job.questions) ? job.questions : []).filter(q => typeof q === 'string' && q).slice(0, MANUAL_MAX_ROWS);
  $('manual-dialog-details').open = !questions.length;
  const linkRow = $('manual-dialog-links'), dialogLinks = manualLinks(job);
  linkRow.replaceChildren(...dialogLinks); linkRow.hidden = !dialogLinks.length;
  const rows = $('manual-answers'); rows.replaceChildren();
  for (const question of questions) rows.append(answerRow(question, '', true));
  if (!questions.length) rows.append(answerRow());
  updateDialogControls();
  $('manual-confirm').hidden = !manualRisky(job);
  $('manual-confirm-sent').checked = false;
  manualMessage('manual-dialog-error', '');
  $('manual-submit').disabled = false;
  $('manual-dialog').showModal();
  const blank = [...rows.querySelectorAll('input, textarea')].find(el => !el.value);
  if (blank) blank.focus();
}
function collectAnswers() {
  return [...$('manual-answers').children]
    .filter(row => !(row.dataset.suggested && !row.querySelector('textarea').value.trim()))
    .map(row => ({question: row.querySelector('input').value, answer: row.querySelector('textarea').value}));
}
async function submitManual(event) {
  event.preventDefault();
  if (!dialogJob || $('manual-submit').disabled) return;
  const risky = manualRisky(dialogJob);
  if (risky && !$('manual-confirm-sent').checked) { manualMessage('manual-dialog-error', 'Confirm that no application was received before returning this job to the queue.'); return; }
  $('manual-submit').disabled = true; manualMessage('manual-dialog-error', '');
  try {
    const response = await fetch('/api/manual/resubmit', {
      method: 'POST', credentials: 'omit', cache: 'no-store',
      headers: {'Content-Type': 'application/json', 'X-Monitor-Token': TOKEN},
      body: JSON.stringify({job_id: dialogJob.job_id, answers: collectAnswers(), nothing_sent_confirmed: risky && $('manual-confirm-sent').checked}),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'The request was refused.');
    $('manual-dialog').close();
    manualMessage('manual-status', (dialogJob.title || 'Job') + ' was returned to the queue' + (data.saved_answers ? ' with ' + data.saved_answers + ' saved answer(s)' : '') + '. Nothing has been submitted yet. The worker still checks the form and needs the employer\'s confirmation.');
    dialogJob = null; refreshManual();
  } catch (error) {
    manualMessage('manual-dialog-error', error.message); $('manual-submit').disabled = false;
  }
}
$('manual-form').addEventListener('submit', submitManual);
$('manual-cancel').addEventListener('click', () => { $('manual-dialog').close(); dialogJob = null; });
$('manual-add-answer').addEventListener('click', () => {
  const rows = $('manual-answers');
  if (rows.children.length >= MANUAL_MAX_ROWS) return;
  const row = answerRow(); rows.append(row); updateDialogControls(); row.querySelector('input').focus();
});
$('manual-answers').addEventListener('input', updateDialogControls);
$('manual-search').addEventListener('input', () => renderManual(false));
$('manual-show-dismissed').addEventListener('click', () => { manualShowDismissed = !manualShowDismissed; renderManual(false); });
window.addEventListener('hashchange', () => {
  const wanted = manualHashFilter();
  if (wanted == null || wanted === manualFilter) return;
  manualFilter = manualGroups.length && !manualKnownFilter(wanted) ? 'all' : wanted;
  if (manualGroups.length) renderManual(false);
});
setInterval(refreshManual, 5000);
"""
