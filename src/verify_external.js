// Read-only verification of a direct employer posting imported from an external source.
// No form filling or submission. Uses the same conservative serial navigation budget.
const rl = require('./ratelimit.js');

function isLinkedIn(url) {
  try { const h = new URL(url).hostname.toLowerCase(); return h === 'linkedin.com' || h.endsWith('.linkedin.com'); }
  catch { return false; }
}

async function verifyExternal(tab, job) {
  const url = job.apply_url || job.url;
  if (isLinkedIn(url)) throw new Error('LinkedIn URLs must use detailOne');
  await rl.gate('detail');
  await tab.goto(url);
  const actual = await tab.url();
  if (isLinkedIn(actual)) throw new Error('Employer URL redirected to LinkedIn; manual review required');
  const page = await tab.evaluate(() => {
    const body = document.body?.innerText || '';
    const main = document.querySelector('main, [role=main]');
    return {text: (main?.innerText || body).slice(0, 100000), title: document.title};
  });
  if (/captcha|security verification|additional verification required|just a moment|access denied/i.test(page.title + '\n' + page.text.slice(0, 1000))) {
    throw new Error('Employer verification requires human review; no challenge bypass attempted');
  }
  const words = String(job.title || '').toLowerCase().match(/[a-z0-9]+/g) || [];
  const text = page.text.toLowerCase();
  // An empty/loading page or unrelated redirect cannot count as a verified posting.
  if (!words.length || !words.every(word => text.includes(word))) throw new Error('Posting title not found on employer page; manual review required');
  const closed = /no longer accepting applications|position (?:has been )?(?:filled|closed)|posting has expired/i.test(page.text);
  return {...job, description: page.text, apply_url: actual, easy_apply: false, external_apply: true, closed};
}

module.exports = {isLinkedIn, verifyExternal};
