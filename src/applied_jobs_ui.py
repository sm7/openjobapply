"""Markup, styles and script for the dashboard's Applied jobs tab.

The tab only reads. It shows what the ledger proves about each applied job. Every value comes from the server as data
and is written with textContent, never innerHTML. The page makes a link only from an address that passed a second
scheme check.
"""

STYLE = """
.applied-intro{margin-bottom:12px}.applied-empty{padding:18px 0;color:#576777}
.applied-tools{display:flex;flex-wrap:wrap;gap:10px 16px;align-items:center;margin:12px 0 14px}
.applied-tools label{font-size:.85rem;font-weight:600}.applied-tools input{font:inherit;padding:7px 10px;border:1px solid #9aaaba;border-radius:6px;min-width:min(320px,100%)}
.applied-facts{display:flex;flex-wrap:wrap;gap:8px 12px;margin:0 0 14px;padding:0;list-style:none}
.applied-facts li{background:#f5f7fa;border:1px solid #d6dde5;border-radius:8px;padding:8px 12px;font-size:.85rem}
.applied-facts strong{font-size:1.1rem;display:block}
.applied-job{border:1px solid #d6dde5;border-radius:10px;padding:14px 16px;margin-bottom:12px;background:#fff}
.applied-head{display:flex;flex-wrap:wrap;justify-content:space-between;gap:8px 16px;align-items:flex-start}
.applied-job h3{font-size:1.02rem;margin:0;overflow-wrap:anywhere}.applied-company{font-size:.9rem;color:#576777}
.applied-job h3 a{color:#152536;text-decoration:underline;text-decoration-color:#9aaaba;text-underline-offset:3px}
.applied-job h3 a:hover{color:#1b5e9b;text-decoration-color:#1b5e9b}
.applied-job a:focus-visible{outline:3px solid #4584bb;outline-offset:2px;border-radius:3px}
.applied-when{font-size:.88rem;margin:8px 0 0}.applied-when strong{font-weight:600}
.applied-meta{display:flex;flex-wrap:wrap;gap:4px 18px;font-size:.84rem;color:#576777;margin:6px 0 0}
.applied-confirm{margin:10px 0 0;padding:10px 12px;background:#f1f8f3;border-left:4px solid #3c9a56;border-radius:4px;overflow-wrap:anywhere}
.applied-confirm.none{background:#fff4e0;border-left-color:#c98a1e}
.applied-lines{margin:10px 0 0;font-size:.85rem}.applied-lines p{margin:3px 0}
.applied-receipt{margin:12px 0 0;max-width:640px}.applied-receipt img{display:block;max-width:100%;max-height:300px;width:auto;height:auto;border:1px solid #d6dde5;border-radius:6px;object-fit:contain;object-position:left top}
.applied-receipt figcaption{margin-top:6px;font-size:.85rem}.applied-receipt a{color:#1b5e9b;text-underline-offset:2px}
.applied-note{color:#8a5200}
.applied-links{display:flex;flex-wrap:wrap;gap:6px 16px;margin:10px 0 0;font-size:.9rem}
.applied-links a{color:#1b5e9b;font-weight:600;text-underline-offset:2px}
.applied-job details{margin-top:10px;font-size:.82rem;color:#576777}
.applied-job summary{cursor:pointer;font-weight:600}
"""

PANEL = """<section id="panel-applied" class="panel" role="tabpanel" aria-labelledby="tab-applied" tabindex="0" hidden>
<h2>Applied jobs</h2>
<p class="muted applied-intro">Every job with a confirmed application, newest first. A job counts as applied only with the employer's confirmation. This tab only reads. It never changes a job.</p>
<p id="applied-error" class="banner error" role="alert" hidden></p>
<ul id="applied-facts" class="applied-facts" aria-label="Applied jobs summary"></ul>
<div class="applied-tools"><label for="applied-search">Search by company, role or place</label><input id="applied-search" type="search" maxlength="200" autocomplete="off"><span id="applied-shown" class="muted" role="status"></span></div>
<div id="applied-list" role="list"><p class="applied-empty">Loading applied jobs.</p></div>
</section>"""

SCRIPT = r"""
let appliedJobs = [], appliedSummary = null, appliedPending = false, appliedLastText = '';
const ROUTE_WORDS = {linkedin_easy_apply: 'LinkedIn Easy Apply', employer_site: 'Employer site', unknown: 'Route not recorded'};
function appliedLink(url, text, hint) {
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
function appliedMoney(job) {
  const money = value => '$' + Number(value).toLocaleString('en-US');
  if (Number.isFinite(job.salary_min) && Number.isFinite(job.salary_max) && job.salary_max !== job.salary_min) return money(job.salary_min) + ' to ' + money(job.salary_max);
  const single = Number.isFinite(job.salary_min) ? job.salary_min : job.salary_max;
  return Number.isFinite(single) ? money(single) : null;
}
function appliedLine(text, className) {
  const p = document.createElement('p');
  if (className) p.className = className;
  p.textContent = text;
  return p;
}
function appliedReceiptUrl(job) {
  if (typeof job.receipt_url !== 'string' || !job.receipt_url.startsWith('/api/applied/')) return null;
  try {
    const url = new URL(job.receipt_url, window.location.origin);
    const match = url.pathname.match(/^\/api\/applied\/([^/]+)\/receipt$/);
    if (url.origin !== window.location.origin || url.search || url.hash || !match || decodeURIComponent(match[1]) !== String(job.job_id)) return null;
    return url.href;
  } catch (error) { return null; }
}
function appliedCard(job) {
  const card = document.createElement('article');
  card.className = 'applied-job'; card.setAttribute('role', 'listitem');
  const head = document.createElement('div'); head.className = 'applied-head';
  const titles = document.createElement('div');
  const title = document.createElement('h3');
  const titleText = job.title || 'Untitled role';
  const titleLink = job.posting_url ? appliedLink(job.posting_url, titleText, 'Open the job posting for ' + titleText) : null;
  if (titleLink) title.append(titleLink); else title.textContent = titleText;
  const company = document.createElement('div'); company.className = 'applied-company'; company.textContent = job.company || 'Company not recorded';
  titles.append(title, company);
  const badge = document.createElement('span'); badge.className = 'badge safe'; badge.textContent = 'Applied';
  head.append(titles, badge);
  const when = document.createElement('p'); when.className = 'applied-when';
  const strong = document.createElement('strong');
  const seconds = Number.isFinite(job.applied_at) ? job.applied_at : null;
  strong.textContent = seconds == null ? 'Time not recorded' : new Date(seconds * 1000).toLocaleString();
  when.append('Applied ', strong);
  if (seconds != null) { const age = document.createElement('span'); age.className = 'applied-age'; age.dataset.at = String(seconds); age.textContent = ' (' + duration(Date.now() / 1000 - seconds) + ' ago)'; when.append(age); }
  const meta = document.createElement('div'); meta.className = 'applied-meta';
  for (const text of [job.location, appliedMoney(job), job.employment_type, ROUTE_WORDS[job.route] || ROUTE_WORDS.unknown]) {
    if (!text) continue;
    const item = document.createElement('span'); item.textContent = text; meta.append(item);
  }
  const confirm = document.createElement('p');
  confirm.className = 'applied-confirm' + (job.confirmation ? '' : ' none');
  const label = document.createElement('strong'); label.textContent = 'Employer confirmation: ';
  confirm.append(label, job.confirmation || 'No confirmation sentence is recorded for this job.');
  const lines = document.createElement('div'); lines.className = 'applied-lines';
  const receiptUrl = appliedReceiptUrl(job);
  const missingReceipt = () => appliedLine('No screenshot file is available.', 'applied-note');
  if (receiptUrl) {
    const figure = document.createElement('figure'); figure.className = 'applied-receipt';
    const image = document.createElement('img');
    image.loading = 'lazy'; image.decoding = 'async';
    image.alt = 'Application receipt screenshot for ' + titleText + ' at ' + (job.company || 'the employer');
    image.addEventListener('error', () => figure.replaceWith(missingReceipt()), {once: true});
    image.src = receiptUrl;
    const caption = document.createElement('figcaption');
    const fullImage = appliedLink(receiptUrl, 'Open full screenshot', 'Open the full application receipt screenshot');
    caption.append(fullImage);
    figure.append(image, caption); lines.append(figure);
  } else lines.append(missingReceipt());
  if (!job.controller_checked) lines.append(appliedLine('This application has no applied reservation. It was recorded outside the campaign controller. It does have employer confirmation text.', 'applied-note'));
  const links = document.createElement('div'); links.className = 'applied-links';
  const employer = job.apply_url ? appliedLink(job.apply_url, 'Open the employer application page', 'Open the employer application page') : null;
  if (employer) links.append(employer);
  const details = document.createElement('details');
  const summary = document.createElement('summary'); summary.textContent = 'More detail';
  details.append(summary, appliedLine('Job ' + job.job_id + '. The ledger holds ' + job.attempts + ' attempt' + (job.attempts === 1 ? '' : 's') + ' for this job, including the confirmed one.'));
  if (job.receipt_file) details.append(appliedLine('Recorded screenshot filename: ' + job.receipt_file));
  card.append(head, when, meta, confirm, lines);
  if (links.childNodes.length) card.append(links);
  card.append(details);
  return card;
}
function appliedMatches(job, query) {
  if (!query) return true;
  return [job.company, job.title, job.location].some(value => typeof value === 'string' && value.toLowerCase().includes(query));
}
function renderAppliedFacts() {
  const facts = $('applied-facts'); facts.replaceChildren();
  if (!appliedSummary) return;
  const items = [
    [appliedSummary.count, 'applied jobs'],
    [appliedSummary.with_confirmation, 'with confirmation text'],
    [appliedSummary.with_receipt_file, 'with a saved receipt screenshot'],
    [appliedSummary.without_controller_check, 'recorded outside the controller'],
  ];
  for (const [value, text] of items) {
    const li = document.createElement('li'); const number = document.createElement('strong'); number.textContent = String(value);
    li.append(number, text); facts.append(li);
  }
}
function renderApplied() {
  const query = $('applied-search').value.trim().toLowerCase();
  const shown = appliedJobs.filter(job => appliedMatches(job, query));
  const list = $('applied-list');
  if (!shown.length) {
    const empty = document.createElement('p'); empty.className = 'applied-empty';
    empty.textContent = appliedJobs.length ? 'No applied job matches the search.' : 'No application is confirmed yet.';
    list.replaceChildren(empty);
  } else list.replaceChildren(...shown.map(appliedCard));
  $('applied-shown').textContent = appliedJobs.length ? 'Showing ' + shown.length + ' of ' + appliedJobs.length : '';
}
function updateAppliedAges() {
  for (const el of document.querySelectorAll('.applied-age')) el.textContent = ' (' + duration(Date.now() / 1000 - Number(el.dataset.at)) + ' ago)';
}
function appliedMessage(text) { const el = $('applied-error'); el.hidden = !text; el.textContent = text || ''; }
async function refreshApplied() {
  if (activeTab !== 'applied' || appliedPending) return;
  appliedPending = true;
  const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 6000);
  try {
    const response = await fetch('/api/applied', {cache: 'no-store', credentials: 'omit', signal: controller.signal});
    if (!response.ok) throw new Error('request failed');
    const text = await response.text();
    const data = JSON.parse(text);
    if (!data || !Array.isArray(data.jobs) || !data.summary) throw new Error('invalid response');
    if (text !== appliedLastText) { appliedJobs = data.jobs; appliedSummary = data.summary; appliedLastText = text; renderAppliedFacts(); renderApplied(); } else updateAppliedAges();
    appliedMessage('');
  } catch (error) {
    appliedMessage('Applied jobs could not be refreshed' + (error.name === 'AbortError' ? ' (timed out)' : '') + '. The list below is the last data received.');
  } finally { clearTimeout(timer); appliedPending = false; }
}
$('applied-search').addEventListener('input', renderApplied);
setInterval(refreshApplied, 10000);
setInterval(updateAppliedAges, 30000);
"""
