// Usage (inside omp eval, JS): const {discoverIndeed} = require('./src/discover_indeed.js')
// Returns an array of job objects; the caller persists them via src/ingest.py.
// Runs headless/managed Chromium; no login needed for discovery.

function parseSalary(s) {
  if (!s || !s.includes("$")) return {};
  const nums = [...s.replace(/,/g, "").matchAll(/\$\s*([\d.]+)\s*(k)?/gi)].map(m => {
    let v = parseFloat(m[1]);
    if (m[2]) v *= 1000;
    return v;
  });
  if (!nums.length) return {};
  const per = /hour/i.test(s) ? 2080 : /month/i.test(s) ? 12 : /week/i.test(s) ? 52 : 1;
  const ann = nums.map(n => Math.round(n * per));
  return { salary_min: Math.min(...ann), salary_max: Math.max(...ann) };
}

async function discoverIndeed({ browser, queries, location, radius = 25, days = 14, pages = 1, tabName = "indeed" }) {
  if (!location) throw new Error("discoverIndeed needs a location, for example the candidate's profile.candidate.location");
  const tab = await browser.open({ name: tabName, url: "about:blank", app: "own", headed: false });
  const out = [];
  const seen = new Set();
  for (const q of queries) {
    for (let p = 0; p < pages; p++) {
      const url = `https://www.indeed.com/jobs?q=${encodeURIComponent(q)}&l=${encodeURIComponent(location)}&radius=${radius}&fromage=${days}&sort=date&start=${p * 10}`;
      await tab.goto(url);
      await new Promise(r => setTimeout(r, 2500));
      const cards = await tab.evaluate(`(() => [...document.querySelectorAll('div.job_seen_beacon')].map(c => {
        const q = s => (c.querySelector(s)||{}).innerText;
        const a = c.querySelector('a[data-jk]');
        return { jk: a && a.getAttribute('data-jk'),
          title: q('h3.jobTitle span[title], h2.jobTitle span[title], .jobTitle span'),
          company: q('[data-testid="company-name"]'),
          location: q('[data-testid="text-location"]'),
          salary: q('[data-testid="attribute_snippet_testid"], .salary-snippet-container, .estimated-salary'),
          easy: /easily apply/i.test(c.innerText) };
      }))()`);
      for (const c of cards) {
        if (!c.jk || seen.has(c.jk) || !c.title) continue;
        seen.add(c.jk);
        const loc = (c.location || "").replace(/\s*\n.*/s, "").trim();
        out.push({
          source: "indeed", source_id: c.jk, title: c.title.trim(), company: (c.company || "").trim(),
          location: loc, remote: /remote/i.test(c.location || ""), easy_apply: !!c.easy,
          url: `https://www.indeed.com/viewjob?jk=${c.jk}`, ...parseSalary(c.salary),
        });
      }
    }
  }
  return { tab, jobs: out };
}

async function fetchDescription(tab, job) {
  await tab.goto(job.url);
  await new Promise(r => setTimeout(r, 1800));
  const d = await tab.evaluate(() => {
    const t = document.body.innerText;
    const i = t.indexOf('Full job description');
    let desc = null;
    if (i >= 0) {
      desc = t.slice(i + 'Full job description'.length);
      // Cut where similar/recommended jobs begin so their keywords don't pollute scoring.
      const stop = desc.search(/\n(Report job|Hiring Lab|Similar jobs|Jobs you may like|Explore more|Get job updates|Recommended jobs|People also viewed|More jobs)\b/i);
      if (stop > 0) desc = desc.slice(0, stop);
      // Fallback: cut at the first "<title>\n<company>\n<city, ST>\n$..." card pattern.
      const card = desc.search(/\n[^\n]{3,80}\n[^\n]{2,60}\n[A-Z][A-Za-z .]+, [A-Z]{2}[^\n]*\n(\$|Full-time|Part-time)/);
      if (card > 400) desc = desc.slice(0, card);
    }
    // Primary apply control = apply text in the job header, before the description.
    const head = t.slice(0, i > 0 ? i : 1500);
    const apply_type = /Apply on company site/i.test(head) ? 'external' : /Easily apply|Apply now/i.test(head) ? 'indeed' : 'unknown';
    return { desc, apply_type };
  });
  job.description = d.desc ? d.desc.trim() : null;
  job.apply_type = d.apply_type;
  job.apply_url = d.apply_type === 'external' ? 'external' : (d.apply_type === 'indeed' ? 'indeed-apply' : null);
  job.easy_apply = d.apply_type === 'indeed';
  return job;
}


module.exports = { discoverIndeed, fetchDescription, parseSalary };
