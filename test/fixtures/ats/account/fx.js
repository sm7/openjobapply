// Shared fixture helpers. window.__log mirrors what the page saw (never password values, only field names).
window.__log = [];
const PERSIST = ['FORBIDDEN_CLICK', 'CHOSE', 'SOCIAL_CLICKED', 'GOOGLE_CLICKED', 'SIGNED_IN'];
const L = (k, v) => { window.__log.push({ k, v }); if (PERSIST.includes(k)) fetch('/api/log', { method: 'POST', keepalive: true, body: JSON.stringify({ k, v }) }); };
const $ = (id) => document.getElementById(id);
const post = (url, body) => fetch(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) }).then((r) => r.json());
const param = (k) => new URLSearchParams(location.search).get(k);
document.addEventListener("input", (e) => { if (e.target && e.target.type === "password") L("TYPED", e.target.id); });
document.addEventListener("click", (e) => {
  const t = e.target && e.target.closest ? e.target.closest("button, a") : null;
  if (t && /with google/i.test(t.textContent) && !window.ALLOW_GOOGLE) L("FORBIDDEN_CLICK", t.textContent.trim());
  if (t) L("click", (t.textContent || "").trim());
});
