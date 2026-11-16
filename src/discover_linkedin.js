// LinkedIn discovery, one gated step at a time. NO bulk loops: each function does exactly one
// page load behind the shared rate limiter. Callers pick the next unit of work from the DB.
//
//   searchOne(tab, query)      -> 1 listing page load  (kind "load")
//   detailOne(tab, job, {gate})-> 1 job page load      (kind "detail"; legacy gate by default)
const rl = require("./ratelimit.js");

function parseSalary(s) {
  if (!s || !s.includes("$")) return {};
  const nums = [...s.replace(/,/g, "").matchAll(/\$\s*([\d.]+)\s*(k)?/gi)].map(m => parseFloat(m[1]) * (m[2] ? 1000 : 1));
  if (!nums.length) return {};
  const per = /\/hr|hour/i.test(s) ? 2080 : 1;
  const ann = nums.map(n => Math.round(n * per));
  return { salary_min: Math.min(...ann), salary_max: Math.max(...ann) };
}

async function guarded(tab, kind, url, gate = rl.gate) {
  await gate(kind);                          // throws if capped/locked; caller may supply campaign pacing
  await tab.goto(url);
  const page = { url: await tab.url(), title: await tab.title(), text: await tab.text() };
  if (rl.checkPage(page)) throw new rl.RateLimited("LinkedIn challenge detected; locked out", Date.now());
  return page;
}

async function searchOne(tab, query, { location = "United States", days = 14, page = 0, easyOnly = false, remoteOnly = false, geoId } = {}) {
  // LinkedIn searches by region id. Only the whole United States is built in; any other place needs its geoId (profile.preferences.linkedin_geo_id).
  if (!geoId && !/united states/i.test(location)) throw new Error(`searchOne needs a geoId for "${location}". Set profile.preferences.linkedin_geo_id.`);
  const region = geoId || "103644278";
  const params = new URLSearchParams({keywords: query, geoId: region, f_TPR: `r${days * 86400}`, sortBy: "DD", start: String(page * 25)});
  if (easyOnly) params.set("f_AL", "true");
  if (remoteOnly) params.set("f_WT", "2");
  await guarded(tab, "load", `https://www.linkedin.com/jobs/search-results/?${params}`);
  const cards = await tab.evaluate(() => {
    const modern = [...document.querySelectorAll('[role="button"][componentkey^="job-card-component-ref-"]')].map(c => {
      const lines = c.innerText.split("\n").map(s => s.trim()).filter(Boolean);
      const title = (lines[0] || "").replace(/\s*\(Verified job\)/i, "").trim();
      const repeated = (lines[1] || "").trim() === title;
      const offset = repeated ? 2 : 1;
      return {urn: c.getAttribute("componentkey").replace("job-card-component-ref-", ""), title,
        company: lines[offset] || "", loc: lines[offset + 1] || "",
        sal: lines.find(s => /^\$/.test(s)), easy: /easy apply/i.test(c.innerText), applied: /\bApplied\b/.test(c.innerText)};
    });
    if (modern.length) return modern;
    return [...document.querySelectorAll(".base-card, .job-search-card, li[data-occludable-job-id]")].map(c => ({
      urn: c.getAttribute("data-entity-urn") || c.getAttribute("data-occludable-job-id"),
      title: (c.querySelector(".base-search-card__title, .job-card-list__title") || {}).innerText?.trim(),
      company: (c.querySelector(".base-search-card__subtitle, .artdeco-entity-lockup__subtitle") || {}).innerText?.trim(),
      loc: (c.querySelector(".job-search-card__location, .artdeco-entity-lockup__caption") || {}).innerText?.trim(),
      link: ((c.querySelector("a.base-card__full-link, a.job-card-list__title") || {}).href || "").split("?")[0],
      date: (c.querySelector("time") || {}).getAttribute?.("datetime"),
      sal: (c.querySelector(".job-search-card__salary-info") || {}).innerText?.trim(),
      easy: /easy apply/i.test(c.innerText), applied: /\bApplied\b/.test(c.innerText),
    }));
  });
  const out = [], seen = new Set();
  for (const c of cards) {
    const id = (c.urn || "").split(":").pop();
    if (!id || seen.has(id) || !c.title) continue;
    seen.add(id);
    out.push({
      source: "linkedin", source_id: id, title: c.title, company: c.company || "", location: c.loc || "",
      remote: /remote/i.test(c.loc || ""), url: c.link || `https://www.linkedin.com/jobs/view/${id}`,
      posted: c.date, ...parseSalary(c.sal),
      easy_apply: !!c.easy, previously_applied: !!c.applied,
    });
  }
  return out;
}

async function detailOne(tab, job, { gate = rl.gate } = {}) {
  await guarded(tab, "detail", job.url, gate);
  const d = await tab.evaluate(() => {
    const desc = document.querySelector(".show-more-less-html__markup, .description__text, #job-details, .jobs-description__content");
    const body = document.body.innerText;
    // Only the header card: text up to the first section after it. Salaries further down belong to "similar jobs".
    const cut = body.search(/Use AI to assess|Show match details|People you can reach out to|About the job/i);
    const header = cut > 0 ? body.slice(0, cut) : body.slice(0, 1500);
    const chip = (header.match(/\$\s?[\d.,]+\s?[KkMm]?(?:\/(?:yr|hr))?\s?(?:-|–|to)\s?\$\s?[\d.,]+\s?[KkMm]?(?:\/(?:yr|hr))?/) || [null])[0];
    const label = el => ((el.getAttribute("aria-label") || "") + " " + (el.innerText || "")).trim();
    const applyEls = [...document.querySelectorAll("button, a")].filter(el => /^(easy apply|apply)\b|apply on company website/i.test(label(el)) && el.closest("main, .jobs-details, .job-view-layout, body"));
    const easyEl = applyEls.find(el => /easy apply/i.test(label(el)));
    const externalLink = document.querySelector("a[aria-label='Apply on company website']");
    let employerUrl = null;
    if (externalLink?.href) {
      try {
        let target = new URL(externalLink.href);
        if (/(^|\.)linkedin\.com$/i.test(target.hostname)) {
          const destination = target.searchParams.get("url");
          target = destination ? new URL(destination) : target;
        }
        if (["https:", "http:"].includes(target.protocol) && !/(^|\.)linkedin\.com$/i.test(target.hostname))
          employerUrl = target.href;
      } catch { /* unresolved links require manual review */ }
    }
    return {
      desc: desc ? desc.innerText : null,
      chip,
      closed: /no longer accepting applications/i.test(header),
      easy: !!easyEl,
      external: !!document.querySelector("a[aria-label='Apply on company website']"),
      employerUrl,
    };
  });
  job.description = d.desc ? d.desc.trim() : null;
  job.closed = d.closed;
  job.easy_apply = d.easy;
  job.external_apply = d.external;
  job.apply_url = d.employerUrl || null;
  if (d.chip && !job.salary_min) Object.assign(job, parseSalary(d.chip.replace(/(\d)\s?[Kk]/g, "$1k")));
  return job;
}

module.exports = { searchOne, detailOne, parseSalary };
