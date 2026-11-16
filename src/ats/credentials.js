// Credential vault for employer-site accounts that the candidate explicitly authorized us to create/reset
// for their job applications. Passwords are random, per-site, and stored ONLY here:
//
//   data/credentials.json   mode 0600 (owner read/write only), gitignored, never logged, never echoed.
//
// Everything else (logs/*, the database, screenshots' filenames, result objects, README examples)
// must refer to a credential by `site` + `email` only. `redact()` strips secrets from any object.
//
// Entry: { site, email, password, createdAt, updatedAt, origin: "created"|"reset", resets: n, note? }
"use strict";
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

const os = require("os");

// Outside the project tree on purpose: the campaign worker operates inside the project folder and is never
// pointed at this path. Override with ATS_CREDENTIALS_FILE (tests do). Mode 0600, never logged.
const FILE = process.env.ATS_CREDENTIALS_FILE || path.join(os.homedir(), ".omp", "openjobapply", "credentials.json");
const AUDIT = path.join(path.dirname(FILE), "credentials_audit.log");

/** Registrable-ish key: strips subdomains of known tenants so wd5/wd1 and jobs-* map to one account where sites share one. */
function siteKey(urlOrHost) {
  let host = String(urlOrHost || "").toLowerCase();
  try { host = new URL(/^[a-z]+:\/\//.test(host) ? host : `https://${host}`).hostname; } catch { /* keep as given */ }
  host = host.replace(/^www\./, "");
  // Workday and iCIMS use a separate account per EMPLOYER tenant, so keep the tenant label.
  const wd = host.match(/^([^.]+)\.(?:wd\d+\.)?(myworkdayjobs|myworkdaysite)\.com$/);
  if (wd) return `${wd[1]}.${wd[2]}.com`;
  const ic = host.match(/^(?:jobs-|careers-)?([^.]+)\.icims\.com$/);
  if (ic) return `${ic[1]}.icims.com`;
  return host.split(".").slice(-2).join(".");
}

/** 20 chars, guaranteed upper+lower+digit+symbol, avoiding characters that break forms or URLs. */
function generatePassword() {
  const sets = ["ABCDEFGHJKLMNPQRSTUVWXYZ", "abcdefghijkmnpqrstuvwxyz", "23456789", "!#$%*+-=?@^_"];
  const all = sets.join("");
  const pick = (s) => s[crypto.randomInt(s.length)];
  const chars = sets.map(pick);
  while (chars.length < 20) chars.push(pick(all));
  for (let i = chars.length - 1; i > 0; i--) { const j = crypto.randomInt(i + 1); [chars[i], chars[j]] = [chars[j], chars[i]]; }
  return chars.join("");
}

function read() {
  if (!fs.existsSync(FILE)) return { version: 1, entries: [] };
  const data = JSON.parse(fs.readFileSync(FILE, "utf8")); // corrupt file -> throw: never silently overwrite credentials
  if (!data || !Array.isArray(data.entries)) throw new Error("credentials.json is malformed; refusing to overwrite");
  return data;
}

function write(data) {
  fs.mkdirSync(path.dirname(FILE), { recursive: true });
  const tmp = `${FILE}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(data, null, 2) + "\n", { mode: 0o600 });
  fs.renameSync(tmp, FILE);
  fs.chmodSync(FILE, 0o600);
}

function audit(event, entry) {
  try {
    fs.mkdirSync(path.dirname(AUDIT), { recursive: true });
    fs.appendFileSync(AUDIT, JSON.stringify({ t: new Date().toISOString(), event, site: entry.site, email: entry.email }) + "\n");
  } catch { /* never block on audit */ }
}

function get(site, email) {
  const key = siteKey(site);
  return read().entries.find((e) => e.site === key && (!email || e.email.toLowerCase() === email.toLowerCase())) || null;
}

/** Save a NEW credential (account just created) or REPLACE after a reset. Returns the entry. */
function save({ site, email, password, origin }) {
  if (!site || !email || !password) throw new Error("site, email and password are required");
  const key = siteKey(site);
  const data = read();
  const now = new Date().toISOString();
  let e = data.entries.find((x) => x.site === key && x.email.toLowerCase() === email.toLowerCase());
  if (e) { e.password = password; e.updatedAt = now; e.origin = origin || e.origin; e.resets = (e.resets || 0) + (origin === "reset" ? 1 : 0); }
  else { e = { site: key, email, password, createdAt: now, updatedAt: now, origin: origin || "created", resets: 0 }; data.entries.push(e); }
  write(data);
  audit(origin === "reset" ? "password_reset_saved" : "credential_saved", e);
  return e;
}

/** Persist the password BEFORE submitting it to the site, so a crash can never strand an account. */
function reserveNew(site, email, origin) {
  const password = generatePassword();
  save({ site, email, password, origin });
  return password;
}

/** Remove any secret from a value before it is logged, returned or stored elsewhere. */
function redact(value) {
  const secrets = read().entries.map((e) => e.password).filter(Boolean);
  let s = typeof value === "string" ? value : JSON.stringify(value);
  for (const p of secrets) s = s.split(p).join("[REDACTED]");
  return typeof value === "string" ? s : JSON.parse(s);
}

function list() { return read().entries.map(({ password, ...rest }) => rest); } // never includes passwords

module.exports = { FILE, siteKey, generatePassword, get, save, reserveNew, redact, list };
