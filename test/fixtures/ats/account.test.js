// Fixture test for src/ats/account.js + src/ats/mailbox_account.js. Needs the omp `browser` global (managed headless
// tabs only, never the Chrome relay), so run it from an omp eval cell:
//
//   const run = require("/abs/path/test/fixtures/ats/account.test.js"); await run(browser);
//
// Serves test/fixtures/ats/account over 127.0.0.1 with a tiny in-memory "employer site" (register / lookup / login /
// reset / verify endpoints). Credentials go to a TEMP vault (ATS_CREDENTIALS_FILE); the mailbox is a FAKE object (and
// a fake Gmail tab for the provider's gates). No live site, no real mailbox, no real credentials are touched.
"use strict";
const fs = require("fs");
const os = require("os");
const path = require("path");

module.exports = async function run(browser) {
  const ROOT = path.resolve(__dirname, "../../..");
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "ats-account-"));
  const credFile = path.join(tmp, "vault", "credentials.json");
  const ENV = { ATS_CREDENTIALS_FILE: credFile, ATS_CONSENT_LOG: path.join(tmp, "consents.log"), ATS_EVIDENCE_DIR: path.join(tmp, "evidence"), ATS_MAILBOX_ACCOUNT_LOG: path.join(tmp, "mailbox_account.log") };
  const prevEnv = Object.fromEntries(Object.keys(ENV).map((k) => [k, process.env[k]]));
  Object.assign(process.env, ENV);
  const MODS = ["lib", "form", "answers", "otp", "mailbox_otp", "mailbox_account", "credentials", "account"].map((m) => path.join(ROOT, "src/ats", `${m}.js`));
  const unload = () => { for (const m of MODS) delete require.cache[m]; };
  unload(); // modules read their env-configured paths at load time: load them fresh against the temp paths
  const A = require(path.join(ROOT, "src/ats/account.js"));
  const credentials = require(path.join(ROOT, "src/ats/credentials.js"));
  const MB = require(path.join(ROOT, "src/ats/mailbox_account.js"));
  const { deriveProfile } = require(path.join(ROOT, "src/ats/answers.js"));
  const { Blocked } = require(path.join(ROOT, "src/ats/lib.js"));
  const profile = JSON.parse(fs.readFileSync(path.resolve(__dirname, "../profile.fixture.json"), "utf8"));
  const EMAIL = "jane.doe@example.com";

  // ---- the fake employer site -------------------------------------------------------------------------
  const dir = path.join(__dirname, "account");
  const S = { accounts: new Map(), cfg: {}, events: [], verifyTokens: new Map(), resetTokens: new Map(), lastVerify: null, lastReset: null, seen: new Set() };
  const resetSite = (cfg) => { S.accounts = new Map(); S.cfg = cfg || {}; S.events = []; S.verifyTokens = new Map(); S.resetTokens = new Map(); S.lastVerify = S.lastReset = null; try { fs.rmSync(credFile); } catch { /* absent */ } };
  const vaultEntry = (email) => { try { return JSON.parse(fs.readFileSync(credFile, "utf8")).entries.find((e) => e.email.toLowerCase() === String(email).toLowerCase()) || null; } catch { return null; } };
  const ev = (o) => S.events.push(o);
  let tokenN = 0;
  const server = Bun.serve({
    port: 0,
    async fetch(req) {
      const u = new URL(req.url);
      if (req.method === "POST") {
        const b = await req.json();
        const email = String(b.email || "").toLowerCase();
        if (b.password) S.seen.add(b.password);
        switch (u.pathname) {
          case "/api/register": {
            if (S.accounts.has(email)) { ev({ k: "register", exists: true }); return Response.json({ error: "exists" }); }
            const ent = vaultEntry(email);
            const persisted = !!(ent && ent.password === b.password && ent.origin === "created");
            S.accounts.set(email, { password: b.password, verified: !S.cfg.requireVerify });
            const tok = `v${++tokenN}`; S.verifyTokens.set(tok, email); S.lastVerify = tok;
            ev({ k: "register", persisted, first: b.first, last: b.last, email, terms: b.terms, marketing: b.marketing, pwLen: String(b.password).length });
            return Response.json({ ok: true, verify: !!S.cfg.requireVerify });
          }
          case "/api/lookup": ev({ k: "lookup", exists: S.accounts.has(email) }); return Response.json({ exists: S.accounts.has(email) });
          case "/api/login": {
            const a = S.accounts.get(email);
            if (S.cfg.locked) { ev({ k: "login", ok: false, locked: true }); return Response.json({ locked: true }); }
            const ok = !S.cfg.rejectLogin && !!a && a.password === b.password && a.verified;
            ev({ k: "login", ok });
            return Response.json({ ok, mfa: ok && !!S.cfg.mfa });
          }
          case "/api/reset-request": {
            ev({ k: "reset-request", email, known: S.accounts.has(email) });
            if (S.cfg.noAccount || !S.accounts.has(email)) return Response.json({ notFound: true });
            const tok = `r${++tokenN}`; S.resetTokens.set(tok, email); S.lastReset = tok;
            return Response.json({ ok: true });
          }
          case "/api/reset": {
            const who = S.resetTokens.get(b.token);
            if (!who) { ev({ k: "reset", ok: false }); return Response.json({ ok: false }); }
            const ent = vaultEntry(who);
            const persisted = !!(ent && ent.password === b.password && ent.origin === "reset");
            S.accounts.get(who).password = b.password;
            ev({ k: "reset", ok: true, persisted, pwLen: String(b.password).length });
            return Response.json({ ok: true });
          }
          case "/api/log": ev({ k: b.k, v: b.v }); return Response.json({ ok: true });
          default: return new Response("nf", { status: 404 });
        }
      }
      if (u.pathname === "/verify") {
        const who = S.verifyTokens.get(u.searchParams.get("token"));
        if (who) { S.accounts.get(who).verified = true; ev({ k: "verified" }); }
        return new Response(null, { status: 302, headers: { location: "/signin.html?both=1&verified=1" } });
      }
      if (u.pathname === "/reset") return new Response(Bun.file(path.join(dir, "reset.html")), { headers: { "content-type": "text/html" } });
      const rel = path.normalize(u.pathname).replace(/^(\.\.[/\\])+/, "");
      const f = Bun.file(path.join(dir, rel));
      return f.size ? new Response(f, { headers: { "content-type": rel.endsWith(".js") ? "text/javascript" : "text/html" } }) : new Response("nf", { status: 404 });
    },
  });
  const base = `http://127.0.0.1:${server.port}`;

  let pass = 0, fail = 0;
  const check = (name, cond, extra) => { if (cond) { pass++; console.log("  ok  ", name); } else { fail++; console.log("  FAIL", name, extra === undefined ? "" : JSON.stringify(extra)); } };
  const names = [];
  const open = async (name, file) => { names.push(name); return browser.open({ name, url: `${base}/${file}`, app: "own" }); };
  const logOf = (tab) => tab.evaluate("window.__log");
  const count = (arr, k) => arr.filter((e) => e.k === k).length;
  const returned = []; // every object/string an account flow handed back or logged: scanned for secrets at the end
  const logs = [];
  const ownedPasswords = new Set();
  const harvest = () => { const e = vaultEntry(EMAIL); if (e) ownedPasswords.add(e.password); };

  let n = 0;
  const mkCtx = (tab, o = {}) => ({
    browser, tab, tabName: "fx", job: { id: `acct-${++n}`, source: "linkedin" }, profile, P: deriveProfile(profile),
    opts: { allowAccountCreation: o.allow !== false, mailbox: o.mailbox, allowLoopbackHttp: o.loopback !== false },
    dryRun: "dryRun" in o ? o.dryRun : false, log: (e, x) => logs.push([e, x]),
    site: "127.0.0.1", url: `${base}/`, token: `tok${n}`, consents: [], filled: [], skipped: [], prefilled: [], submitted: false,
  });
  const fakeMailbox = (o = {}) => {
    const calls = [];
    return {
      calls,
      markTriggered(req, kind) { calls.push({ k: "mark", kind, at: S.events.length }); },
      async getVerificationLink() { calls.push({ k: "verify" }); return o.verifyLink ? o.verifyLink() : `${base}/verify?token=${S.lastVerify}`; },
      async getResetLink() { calls.push({ k: "reset" }); return o.resetLink ? o.resetLink() : `${base}/reset?token=${S.lastReset}`; },
      async getCode() { calls.push({ k: "code" }); return null; },
    };
  };
  const go = async (ctx, o) => { const r = await A.ensureAccount(ctx, { adapterName: "fixture", ...(o || {}) }); returned.push(r); harvest(); return r; };
  const seedExisting = (pw = "Old#Pass1-fixture") => S.accounts.set(EMAIL, { password: pw, verified: true });
  const existsCreds = () => fs.existsSync(credFile);

  try {
    // ======================================================================= pure units
    console.log("# classifier (pure)");
    const info = (o) => ({ inputs: [], controls: [], heads: [], text: "", captcha: null, ...o });
    const inp = (type, label, extra) => ({ type, label, name: "", ac: "", max: 0, ...extra });
    check("classify: email + Continue = signin_email", A.classifyAccountPage(info({ inputs: [inp("email", "Email")], controls: [{ name: "Continue", role: "button", tag: "button" }] })) === "signin_email");
    check("classify: pw + Sign In + forgot = signin_password", A.classifyAccountPage(info({ inputs: [inp("email", "Email"), inp("password", "Password")], controls: [{ name: "Sign In", role: "button", tag: "button" }, { name: "Forgot password?", role: "link", tag: "a" }] })) === "signin_password");
    check("classify: two password fields = create", A.classifyAccountPage(info({ inputs: [inp("text", "Email"), inp("password", "Password"), inp("password", "Confirm Password")], controls: [{ name: "Create Account", role: "button", tag: "button" }] })) === "create");
    check("classify: captcha wins", A.classifyAccountPage(info({ captcha: "recaptcha", inputs: [inp("password", "Password")] })) === "captcha");
    check("classify: Google + email chooser with no inputs = choose_method", A.classifyAccountPage(info({ controls: [{ name: "Sign in with Google", role: "button", tag: "button" }, { name: "Sign in with email", role: "button", tag: "button" }] })) === "choose_method");
    check("classify: Google-only page is NOT choose_method (never offers the social button)", A.classifyAccountPage(info({ controls: [{ name: "Sign in with Google", role: "button", tag: "button" }, { name: "Sign in with LinkedIn", role: "button", tag: "button" }] })) === "none");
    check("classify: lookalike 'Sign in with Gmail' / 'emailer' are not the email method", A.classifyAccountPage(info({ controls: [{ name: "Sign in with Gmail", role: "button", tag: "button" }, { name: "Sign in with emailer", role: "button", tag: "button" }] })) === "none");
    check("classify: chooser with 'Log in with email' wording", A.classifyAccountPage(info({ controls: [{ name: "Log in with your email", role: "button", tag: "button" }] })) === "choose_method");
    check("classify: sign-in with a Create Account link is NOT create", A.classifyAccountPage(info({ inputs: [inp("email", "Email"), inp("password", "Password")], controls: [{ name: "Sign In", role: "button", tag: "button" }, { name: "Create Account", role: "button", tag: "a" }] })) === "signin_password");
    check("classify: sms code page = mfa", A.classifyAccountPage(info({ inputs: [inp("text", "Verification code")], text: "We sent a text message with a code to the phone ending in 44" })) === "mfa");
    check("classify: marketing SMS consent on a create form is not mfa", A.classifyAccountPage(info({ inputs: [inp("password", "Password"), inp("password", "Confirm")], text: "I agree to receive text messages. Enter the code" })) === "create");
    check("notices: exists / bad / locked / notFound", (() => { const x = A.noticesOf("An account with this email address already exists. Already have an account? Sign in"); const y = A.noticesOf("Incorrect email or password."); const z = A.noticesOf("Your account is locked after too many failed attempts"); const w = A.noticesOf("We couldn't find an account with that email"); return x.exists && y.bad && z.locked && w.notFound && !A.noticesOf("Already have an account? Sign in").exists; })());

    console.log("# link validation (pure)");
    const V = (u, site, o) => MB.validateLink(u, site, o);
    check("https same host accepted", V("https://careers.acme.com/reset?token=a", "careers.acme.com") === "https://careers.acme.com/reset?token=a");
    check("https same family (sibling subdomain) accepted", !!V("https://jobs.acme.com/verify?k=1", "careers.acme.com"));
    check("vendor family accepted (workday)", !!V("https://acme.wd5.myworkdayjobs.com/x/activate/1", "acme.wd5.myworkdayjobs.com"));
    check("http refused", V("http://careers.acme.com/reset?token=a", "careers.acme.com") === null);
    check("cross-site refused", V("https://evil.example.net/reset?token=a", "careers.acme.com") === null);
    check("lookalike host refused (acme.com.evil.net)", V("https://acme.com.evil.net/reset", "careers.acme.com") === null);
    check("userinfo trick refused", V("https://careers.acme.com@evil.invalid/reset", "careers.acme.com") === null);
    check("custom port refused", V("https://careers.acme.com:8443/reset", "careers.acme.com") === null);
    check("garbage refused", V("javascript:alert(1)", "careers.acme.com") === null && V("", "careers.acme.com") === null);
    check("loopback http only with the flag AND exact site host", V("http://127.0.0.1:9/x", "127.0.0.1") === null && !!V("http://127.0.0.1:9/x", "127.0.0.1", { allowLoopbackHttp: true }) && V("http://127.0.0.1:9/x", "careers.acme.com", { allowLoopbackHttp: true }) === null);
    check("pickLink: unsubscribe/privacy/offsite ignored, one action link", MB.pickLink(["https://careers.acme.com/unsubscribe?t=1", "https://acme.com/privacy", "https://evil.net/reset?token=1", "https://careers.acme.com/reset?token=ok", "https://careers.acme.com/reset?token=ok"], "careers.acme.com", "reset") === "https://careers.acme.com/reset?token=ok");
    check("pickLink: two different action links = null", MB.pickLink(["https://careers.acme.com/reset?token=1", "https://careers.acme.com/reset?token=2"], "careers.acme.com", "reset") === null);

    // ======================================================================= MailboxLinkProvider gates (fake Gmail)
    console.log("# MailboxLinkProvider (fake Gmail tab)");
    const fakeGmail = (rows, hrefs) => {
      const st = { opens: 0, closes: [], clicks: [], gotos: [] };
      const tab = {
        goto: async (u) => { st.gotos.push(u); },
        evaluate: async (code) => {
          if (code.startsWith("/*rows*/")) return rows.map((r, i) => ({ i, domain: r.domain, subject: r.subject, when: r.when }));
          if (code.startsWith("/*open*/")) { st.clicks.push(Number(code.match(/\[(\d+)\]/)[1])); return null; }
          if (code.startsWith("/*links*/")) return hrefs[st.clicks[st.clicks.length - 1]] || [];
          throw new Error("unexpected evaluate");
        },
      };
      return { st, browser: { open: async () => { st.opens++; return tab; }, close: async (o) => { st.closes.push(o.name); } } };
    };
    const prov = (g, o) => new MB.MailboxLinkProvider({ browser: g.browser, waitMs: 30, pollMs: 5, settleMs: 0, openMs: 0, ...o });
    const req = { token: "t1", site: "careers.acme.com", jobId: "j1" };
    const now = Date.now();
    const iso = (ms) => new Date(ms).toISOString();
    const ACT = "https://careers.acme.com/reset?token=abc123";

    let g = fakeGmail([], {});
    let p = prov(g);
    check("no markTriggered => null and Gmail never opened", (await p.getResetLink(req)) === null && g.st.opens === 0);

    fs.rmSync(ENV.ATS_MAILBOX_ACCOUNT_LOG, { force: true });
    g = fakeGmail(
      [{ domain: "evil.com", subject: "Reset your password", when: iso(now + 5000) }, { domain: "acme.com", subject: "Reset your password", when: iso(now) }],
      { 1: [ACT, "https://acme.com/unsubscribe?x=1", "https://evil.net/reset?token=zz", "http://careers.acme.com/reset?token=q"] },
    );
    p = prov(g);
    p.markTriggered(req, "reset");
    let link = await p.getResetLink(req);
    check("reset link returned (right sender/subject/fresh, https same-site)", link === ACT, link);
    check("only the single qualifying message was opened; wrong-sender newer one ignored", g.st.clicks.length === 1 && g.st.clicks[0] === 1, g.st.clicks);
    check("mailbox tab closed in finally", g.st.closes.length === 1);
    const aud = fs.readFileSync(ENV.ATS_MAILBOX_ACCOUNT_LOG, "utf8");
    check("audit = event+site+kind+time only (no link/token)", /"event":"link_read"/.test(aud) && /"site":"careers.acme.com"/.test(aud) && /"kind":"reset"/.test(aud) && !/abc123|token|https?:/.test(aud), aud);
    check("trigger consumed: second read without new markTriggered = null, not reopened", (await p.getResetLink(req)) === null && g.st.opens === 1);

    g = fakeGmail([{ domain: "evil.com", subject: "Reset your password", when: iso(now) }], {});
    p = prov(g); p.markTriggered(req, "reset");
    check("wrong sender domain => null, no message opened", (await p.getResetLink(req)) === null && g.st.clicks.length === 0);

    g = fakeGmail([{ domain: "acme.com", subject: "Reset your password", when: iso(now - 2 * 3600e3) }], { 0: [ACT] });
    p = prov(g); p.markTriggered(req, "reset");
    check("stale message (before trigger) => null, not opened", (await p.getResetLink(req)) === null && g.st.clicks.length === 0);

    g = fakeGmail([{ domain: "acme.com", subject: "Reset your password", when: "not a date" }], { 0: [ACT] });
    p = prov(g); p.markTriggered(req, "reset");
    check("unparseable time => refused", (await p.getResetLink(req)) === null && g.st.clicks.length === 0);

    g = fakeGmail([{ domain: "acme.com", subject: "Confirm your account", when: iso(now) }], { 0: ["https://careers.acme.com/verify?token=1"] });
    p = prov(g); p.markTriggered(req, "reset");
    check("subject of the other kind => null (reset read never takes a verification mail)", (await p.getResetLink(req)) === null && g.st.clicks.length === 0);

    g = fakeGmail([{ domain: "acme.com", subject: "Reset your password", when: iso(now) }], { 0: ["http://careers.acme.com/reset?token=q"] });
    p = prov(g); p.markTriggered(req, "reset");
    check("non-https link => null", (await p.getResetLink(req)) === null);

    g = fakeGmail([{ domain: "acme.com", subject: "Reset your password", when: iso(now) }], { 0: ["https://evil.example.net/reset?token=q", "https://acme.com.evil.net/reset?token=q"] });
    p = prov(g); p.markTriggered(req, "reset");
    check("cross-site / lookalike links => null", (await p.getResetLink(req)) === null);

    g = fakeGmail([{ domain: "acme.com", subject: "Reset your password", when: iso(now) }], { 0: ["https://careers.acme.com/reset?token=1", "https://careers.acme.com/reset?token=2"] });
    p = prov(g); p.markTriggered(req, "reset");
    check("two different action links => null (never guesses)", (await p.getResetLink(req)) === null);

    g = fakeGmail([{ domain: "acme.com", subject: "Reset your password", when: iso(now - 30e3) }, { domain: "acme.com", subject: "Password reset", when: iso(now) }], { 0: ["https://careers.acme.com/reset?token=old"], 1: ["https://careers.acme.com/reset?token=new"] });
    p = prov(g); p.markTriggered(req, "reset");
    check("two qualifying messages: only the NEWEST is opened", (await p.getResetLink(req)) === "https://careers.acme.com/reset?token=new" && g.st.clicks.join() === "1");

    g = fakeGmail([{ domain: "acme.com", subject: "Please verify your email", when: iso(now) }], { 0: ["https://jobs.acme.com/verify?token=v1"] });
    p = prov(g); p.markTriggered(req, "verify");
    check("verification link returned", (await p.getVerificationLink(req)) === "https://jobs.acme.com/verify?token=v1");

    g = fakeGmail([{ domain: "myworkday.com", subject: "Activate your account", when: iso(now) }], { 0: ["https://acme.wd5.myworkdayjobs.com/en-US/c/activate/abc"] });
    p = prov(g); const wreq = { token: "w1", site: "acme.wd5.myworkdayjobs.com", jobId: "j" }; p.markTriggered(wreq, "verify");
    check("vendor sender family (myworkday.com) accepted for a workday tenant", (await p.getVerificationLink(wreq)) === "https://acme.wd5.myworkdayjobs.com/en-US/c/activate/abc");

    g = fakeGmail([], {});
    p = prov(g);
    check("getCode inherited: null and nothing opened unless a code was triggered", typeof p.getCode === "function" && (await p.getCode(req)) === null && g.st.opens === 0);
    const ipReq = { token: "ip", site: "127.0.0.1", jobId: "j" };
    p.markTriggered(ipReq, "reset");
    check("IP/loopback sites never go to Gmail", (await p.getResetLink(ipReq)) === null && g.st.opens === 0);

    // ======================================================================= fillCredential
    console.log("# fillCredential");
    let tab = await open("fx-acct-fill", "create.html");
    let ctx = mkCtx(tab);
    const secret = "Zz9#fixture-secret-pw!";
    check("types with key events and verifies", (await A.fillCredential(ctx, "#pw", secret)) === true && (await tab.evaluate("document.getElementById('pw').value.length")) === secret.length);
    check("page saw typing (input events) on the password field", (await logOf(tab)).some((e) => e.k === "TYPED" && e.v === "pw"));
    await tab.evaluate("document.getElementById('pw2').maxLength = 4");
    let err = null;
    try { await A.fillCredential(ctx, { selector: "#pw2" }, secret); } catch (e) { err = e; }
    check("field that does not take the value => Blocked, message without the secret", err instanceof Blocked && !String(err.message + err.evidence).includes(secret), err && err.message);
    const obs = (await tab.observe()).elements.find((e) => e.role === "textbox" && /^Password/.test(e.name));
    check("accepts an observe() element target", (await A.fillCredential(ctx, { id: obs.id }, secret)) === true);
    check("redactResult strips password keys and vault secrets", (() => { credentials.save({ site: "127.0.0.1", email: EMAIL, password: "Vault#Secret-123", origin: "created" }); const r = A.redactResult({ status: "manual", password: "x", evidence: "oops Vault#Secret-123 here", nested: { Password: "y", ok: 1 } }); return !JSON.stringify(r).includes("Vault#Secret-123") && r.password === undefined && r.nested.Password === undefined && r.nested.ok === 1; })());
    resetSite(); await tab.close().catch(() => {});

    // ======================================================================= dry run / not authorized
    console.log("# dry run and unauthorized: nothing is done");
    resetSite({ requireVerify: true });
    let mb = fakeMailbox();
    tab = await open("fx-acct-dry1", "create.html");
    let r = await go(mkCtx(tab, { dryRun: true, mailbox: mb }));
    check("create page, dry run => manual/account_required + wouldCreateAccount", r.ready === false && r.result.status === "manual" && r.result.reason === "account_required" && r.result.wouldCreateAccount === true && /would be created/.test(r.result.evidence), r);
    check("nothing typed, nothing sent, mailbox untouched, no vault", (await logOf(tab)).filter((e) => e.k === "TYPED" || e.k === "SUBMIT").length === 0 && (await tab.evaluate("document.getElementById('fn').value + document.getElementById('em').value")) === "" && S.events.length === 0 && mb.calls.length === 0 && !existsCreds());
    await tab.goto(`${base}/signin.html?both=1`);
    r = await go(mkCtx(tab, { dryRun: true, mailbox: mb }));
    check("sign-in page, no saved password, dry run => wouldResetPassword", r.result.reason === "account_required" && r.result.wouldResetPassword === true && !r.result.wouldCreateAccount, r);
    check("dry run on sign-in typed/clicked nothing", (await logOf(tab)).filter((e) => e.k === "TYPED" || e.k === "SUBMIT" || e.k === "click").length === 0 && S.events.length === 0 && mb.calls.length === 0);
    credentials.save({ site: "127.0.0.1", email: EMAIL, password: "Saved#Dry-run-1", origin: "created" }); harvest();
    r = await go(mkCtx(tab, { dryRun: true, mailbox: mb }));
    check("saved credential, dry run => wouldLogin, still nothing typed", r.result.wouldLogin === true && (await logOf(tab)).filter((e) => e.k === "TYPED").length === 0);
    r = await go(mkCtx(tab, { dryRun: undefined, mailbox: mb }));
    check("dryRun undefined (not === false) is a dry run", r.ready === false && r.result.reason === "account_required" && S.events.length === 0);
    resetSite({});
    await tab.goto(`${base}/signin.html?both=1`);
    r = await go(mkCtx(tab, { dryRun: false, allow: false, mailbox: mb }));
    check("live without opts.allowAccountCreation => account_required, nothing done", r.ready === false && r.result.reason === "account_required" && /allowAccountCreation/.test(r.result.evidence) && S.events.length === 0 && mb.calls.length === 0 && !existsCreds());
    await tab.close().catch(() => {});

    // ======================================================================= new account + email verification
    console.log("# new account: create, verify by mailbox link, sign in");
    resetSite({ requireVerify: true });
    mb = fakeMailbox();
    tab = await open("fx-acct-create", "create.html?mkt=1");
    ctx = mkCtx(tab, { mailbox: mb });
    r = await go(ctx);
    check("ready, action created, account extras", r.ready === true && r.status === "continue" && r.action === "created" && r.account.email === EMAIL && r.extras.account.action === "created", r);
    const reg = S.events.find((e) => e.k === "register");
    check("register request carried allowlisted name/email, terms ticked, marketing UNticked (was pre-ticked)", reg && reg.first === "Jane" && reg.last === "Doe" && reg.email === EMAIL && reg.terms === true && reg.marketing === false, reg);
    check("password was in the vault BEFORE the site received it (origin created)", reg && reg.persisted === true && reg.pwLen === 20, reg);
    check("mailbox trigger recorded BEFORE the submit; one verification wait", mb.calls.find((c) => c.k === "mark" && c.kind === "verify").at <= S.events.indexOf(reg) && count(mb.calls, "verify") === 1 && count(mb.calls, "reset") === 0, mb.calls);
    check("verified via link, then exactly one login, landed on the application", count(S.events, "verified") === 1 && S.events.filter((e) => e.k === "login").length === 1 && S.events.find((e) => e.k === "login").ok && /app\.html$/.test(await tab.url()), S.events);
    const ent = vaultEntry(EMAIL);
    check("vault entry: origin created, matches the site's password", ent && ent.origin === "created" && ent.password === S.accounts.get(EMAIL).password);
    check("credentials file mode 0600", (fs.statSync(credFile).mode & 0o777) === 0o600, (fs.statSync(credFile).mode & 0o777).toString(8));
    check("no forbidden click (Sign up with Google)", count(S.events, "FORBIDDEN_CLICK") === 0);
    check("terms consent logged, marketing never accepted", /Terms of Use/.test(fs.readFileSync(ENV.ATS_CONSENT_LOG, "utf8")) && !/marketing/i.test(fs.readFileSync(ENV.ATS_CONSENT_LOG, "utf8")));
    await tab.close().catch(() => {});

    // ======================================================================= provider chooser (seen live on a Workday tenant)
    console.log("# provider chooser: Google + email buttons, no inputs");
    resetSite({}); seedExisting(); mb = fakeMailbox();
    tab = await open("fx-acct-chooser-dry", "chooser.html");
    r = await go(mkCtx(tab, { dryRun: true, mailbox: mb }));
    check("dry run on chooser => account_required + wouldResetPassword (page is recognized)", r.ready === false && r.result.reason === "account_required" && r.result.wouldResetPassword === true && !/not recognized/.test(r.result.evidence), r);
    check("dry run on chooser pressed nothing, mailbox untouched", (await logOf(tab)).filter((e) => e.k === "click" || e.k === "CHOSE" || e.k === "SOCIAL_CLICKED").length === 0 && mb.calls.length === 0 && S.events.length === 0 && !existsCreds());
    await tab.close().catch(() => {});

    resetSite({}); seedExisting(); mb = fakeMailbox();
    tab = await open("fx-acct-chooser", "chooser.html");
    r = await go(mkCtx(tab, { mailbox: mb }));
    check("live chooser: chooses email, resets password, signs in, lands on the application", r.ready === true && r.action === "reset" && /app\.html$/.test(await tab.url()), r);
    check("email option chosen exactly once (server-recorded, survives navigation)", count(S.events, "CHOSE") === 1, S.events.map((e) => e.k).join());
    check("Google NEVER clicked: no social event on the page or recorded by the plugin", count(S.events, "SOCIAL_CLICKED") === 0 && count(S.events, "FORBIDDEN_CLICK") === 0);
    check("chooser flow: one reset request and one login, as capped", count(S.events, "reset-request") === 1 && S.events.filter((e) => e.k === "login").length === 1);
    await tab.close().catch(() => {});

    resetSite({}); mb = fakeMailbox();
    tab = await open("fx-acct-chooser-noemail", "chooser.html");
    await tab.evaluate("document.getElementById('viaemail').remove()");
    r = await go(mkCtx(tab, { mailbox: mb }));
    check("chooser with ONLY a Google button => manual, nothing pressed, nothing created", r.ready === false && r.result.status === "manual" && count(S.events, "SOCIAL_CLICKED") === 0 && (await logOf(tab)).filter((e) => e.k === "SOCIAL_CLICKED").length === 0 && !existsCreds() && mb.calls.length === 0 && S.events.length === 0, r);
    await tab.close().catch(() => {});

    // ======================================================================= Google sign-in (her own account, authorized)
    console.log("# Google sign-in: her own account only, never a password, never broad scopes");
    const gHost = `localhost:${server.port}`;
    const gOpen = async (name, scn, extra = {}) => { names.push(name); return browser.open({ name, url: `${base}/google_employer.html?gh=${encodeURIComponent(gHost)}&scn=${scn}`, app: "own" }); };
    const gCtx = (tab, o = {}) => { const c = mkCtx(tab, { mailbox: fakeMailbox(), ...o }); c.opts.allowGoogleSignIn = o.google !== false; c.opts.googleFixtureHost = "localhost"; c.url = `${base}/google_employer.html`; return c; };
    const gEv = () => ({ clicked: count(S.events, "GOOGLE_CLICKED"), tile: S.events.filter((e) => e.k === "GOOGLE_TILE").map((e) => e.v), consent: count(S.events, "GOOGLE_CONSENT"), returned: count(S.events, "GOOGLE_RETURNED"), social: count(S.events, "SOCIAL_CLICKED"), signed: count(S.events, "SIGNED_IN") });

    resetSite({}); let gt = await gOpen("fx-acct-g-ok", "ok");
    r = await go(gCtx(gt));
    let ge = gEv();
    check("Google: chooser -> her tile -> basic consent -> signed in (action google)", r.ready === true && r.action === "google" && r.account.action === "google", r);
    check("Google: pressed once, picked ONLY her tile, consent once, returned once", ge.clicked === 1 && ge.tile.join() === "jane.doe@example.com" && ge.consent === 1 && ge.returned === 1, ge);
    check("Google: LinkedIn never pressed", ge.social === 0 && count(S.events, "FORBIDDEN_CLICK") === 0, ge);
    check("Google: NO account created, NO password reserved, no vault file, mailbox untouched", !existsCreds() && count(S.events, "register") === 0 && count(S.events, "login") === 0, S.events.map((e) => e.k).join());
    check("Google: landed on the signed-in employer page", /signed=1/.test(await gt.url()));
    await gt.close().catch(() => {});

    resetSite({}); gt = await gOpen("fx-acct-g-nochooser", "ok_nochooser");
    r = await go(gCtx(gt)); ge = gEv();
    check("Google: straight to basic consent also works", r.ready === true && r.action === "google" && ge.consent === 1 && ge.tile.length === 0, [r, ge]);
    await gt.close().catch(() => {});

    for (const [scn, label, reasonRx] of [["wrong", "her account not offered", /candidate Google account is not offered/i], ["dupe", "two tiles for her account", /more than one tile matches/i], ["password", "Google asks for a password", /asking for a password/i], ["twostep", "2-Step Verification", /2-step verification|sign-in is not allowed/i], ["broad", "consent asks for Gmail/Drive", /beyond basic/i]]) {
      resetSite({}); gt = await gOpen(`fx-acct-g-${scn}`, scn);
      r = await go(gCtx(gt)); ge = gEv();
      const typed = await gt.evaluate("window.__log ? window.__log.filter((e) => e.k === 'TYPED').length : 0").catch(() => 0);
      // Refusal while still on Google must preserve the actual reason, not search that page for employer email controls.
      check(`Google refused: ${label}: reports the actual Google blocker`, r.ready === false && reasonRx.test(r.result.evidence) && !/no first-party email option/.test(r.result.evidence), r);
      check(`Google refused: ${label}`, ge.consent === 0 && ge.returned === 0 && ge.signed === 0 && typed === 0, { r: r.ready, reason: r.result && r.result.reason, ge });
      check(`Google refused: ${label}: never reached the application via Google`, !(r.ready === true && r.action === "google"), r);
      await gt.close().catch(() => {});
    }

    resetSite({}); gt = await gOpen("fx-acct-g-off", "ok");
    r = await go(gCtx(gt, { google: false })); ge = gEv();
    check("Google disabled (default): the Google button is NEVER pressed", ge.clicked === 0 && ge.returned === 0 && ge.signed === 0, ge);
    await gt.close().catch(() => {});

    resetSite({}); gt = await gOpen("fx-acct-g-dry", "ok");
    r = await go(gCtx(gt, { dryRun: true })); ge = gEv();
    check("Google: dry run presses nothing", ge.clicked === 0 && ge.signed === 0 && r.ready === false, [r.ready, ge]);
    await gt.close().catch(() => {});

    resetSite({}); gt = await gOpen("fx-acct-g-noallow", "ok");
    r = await go(gCtx(gt, { allow: false })); ge = gEv();
    check("Google: without allowAccountCreation presses nothing", ge.clicked === 0 && ge.signed === 0 && r.ready === false, [r.ready, ge]);
    await gt.close().catch(() => {});

    resetSite({}); gt = await gOpen("fx-acct-g-prod-host", "ok");
    { const c = gCtx(gt); c.opts.googleFixtureHost = "evil.localhost"; r = await go(c); ge = gEv(); }
    check("Google: a non-Google host is not trusted even in fixtures when the fixture host does not match", ge.consent === 0 && ge.returned === 0 && !(r.ready === true && r.action === "google"), [r.ready, ge]);
    await gt.close().catch(() => {});

    // ======================================================================= existing account: password reset
    console.log("# existing account: forgot password -> mailbox link -> new password -> login");
    resetSite({}); seedExisting(); mb = fakeMailbox();
    tab = await open("fx-acct-reset", "signin.html");
    r = await go(mkCtx(tab, { mailbox: mb }));
    check("email-only sign-in, no saved password => reset, ready", r.ready === true && r.action === "reset" && r.extras.account.action === "reset", r);
    const rq = S.events.filter((e) => e.k === "reset-request");
    const rs = S.events.find((e) => e.k === "reset");
    check("exactly one reset request, trigger recorded before it", rq.length === 1 && mb.calls.find((c) => c.k === "mark" && c.kind === "reset").at <= S.events.indexOf(rq[0]) && count(mb.calls, "reset") === 1);
    check("new password persisted (origin reset) BEFORE the site got it", rs && rs.ok && rs.persisted === true, rs);
    check("one login with the new password, then application page", S.events.filter((e) => e.k === "login").length === 1 && S.events.find((e) => e.k === "login").ok && /app\.html$/.test(await tab.url()));
    const e2 = vaultEntry(EMAIL);
    check("vault: origin reset, site password equals vault and differs from the old one", e2 && e2.origin === "reset" && S.accounts.get(EMAIL).password === e2.password && e2.password !== "Old#Pass1-fixture", e2 && { o: e2.origin, r: e2.resets });
    await tab.close().catch(() => {});

    console.log("# existing account: saved credential logs in without any reset");
    resetSite({}); seedExisting("Known#Pw-777-fixture"); credentials.save({ site: "127.0.0.1", email: EMAIL, password: "Known#Pw-777-fixture", origin: "created" }); harvest(); mb = fakeMailbox();
    tab = await open("fx-acct-saved", "signin.html?both=1");
    r = await go(mkCtx(tab, { mailbox: mb }));
    check("action login, no reset request, no mailbox use", r.ready && r.action === "login" && count(S.events, "reset-request") === 0 && mb.calls.length === 0 && S.events.filter((e) => e.k === "login").length === 1);
    await tab.close().catch(() => {});

    console.log("# existing account: saved credential is wrong -> reset (2 logins total)");
    resetSite({}); seedExisting("Real#Pw-888-fixture"); credentials.save({ site: "127.0.0.1", email: EMAIL, password: "Stale#Pw-000-fixture", origin: "created" }); harvest(); mb = fakeMailbox();
    tab = await open("fx-acct-stale", "signin.html?both=1");
    r = await go(mkCtx(tab, { mailbox: mb }));
    const lg = S.events.filter((e) => e.k === "login");
    check("failed login, one reset, successful login", r.ready && r.action === "reset" && lg.length === 2 && !lg[0].ok && lg[1].ok && count(S.events, "reset-request") === 1, S.events);
    await tab.close().catch(() => {});

    console.log("# existing account: a password the USER stored is rejected -> stop, never reset, never overwrite");
    resetSite({}); seedExisting("Real#Pw-888-fixture"); credentials.save({ site: "127.0.0.1", email: EMAIL, password: "UserTypo#Pw-111-fixture", origin: "user" }); harvest(); mb = fakeMailbox();
    tab = await open("fx-acct-userpw", "signin.html?both=1");
    r = await go(mkCtx(tab, { mailbox: mb }));
    const stillStored = credentials.get("127.0.0.1", EMAIL);
    check("user-stored password rejected => manual/account_flow_failed, no reset request, mailbox untouched, stored password unchanged",
      r.ready === false && r.result.reason === "account_flow_failed" && /not requesting a reset/.test(r.result.evidence) &&
      count(S.events, "reset-request") === 0 && mb.calls.length === 0 && stillStored && stillStored.password === "UserTypo#Pw-111-fixture" && stillStored.origin === "user", r);
    await tab.close().catch(() => {});

    // ======================================================================= hard caps
    console.log("# hard caps: one reset, two logins, never loops");
    resetSite({ rejectLogin: true }); seedExisting(); mb = fakeMailbox();
    tab = await open("fx-acct-caps", "signin.html?both=1");
    ctx = mkCtx(tab, { mailbox: mb });
    r = await go(ctx);
    check("login after reset rejected => manual/account_flow_failed", r.ready === false && r.result.status === "manual" && r.result.reason === "account_flow_failed", r);
    await tab.goto(`${base}/signin.html?both=1`);
    r = await go(ctx);
    check("second call (same run): no second reset request", count(S.events, "reset-request") === 1 && r.ready === false && r.result.reason === "account_flow_failed" && /reset/.test(r.result.evidence), r);
    await tab.goto(`${base}/signin.html?both=1`);
    r = await go(ctx);
    check("third call: login cap (2) holds, still one reset", S.events.filter((e) => e.k === "login").length === 2 && count(S.events, "reset-request") === 1 && r.ready === false, S.events.map((e) => e.k).join());
    await tab.close().catch(() => {});

    console.log("# lockout and no-account notices");
    resetSite({ locked: true }); seedExisting(); credentials.save({ site: "127.0.0.1", email: EMAIL, password: "Lock#Me-123-fixture", origin: "created" }); harvest();
    mb = fakeMailbox(); tab = await open("fx-acct-lock", "signin.html?both=1");
    r = await go(mkCtx(tab, { mailbox: mb }));
    check("lockout => manual/account_flow_failed, no reset attempted", r.ready === false && r.result.reason === "account_flow_failed" && /lockout/.test(r.result.evidence) && count(S.events, "reset-request") === 0, r);
    await tab.close().catch(() => {});

    // ======================================================================= captcha / MFA
    console.log("# captcha and SMS MFA hand off to a human");
    resetSite({ requireVerify: true }); mb = fakeMailbox();
    tab = await open("fx-acct-cap1", "create.html?captcha=1");
    r = await go(mkCtx(tab, { mailbox: mb }));
    check("captcha on create => manual/captcha_required", r.ready === false && r.result.reason === "captcha_required", r);
    check("nothing typed/clicked, nothing persisted, mailbox untouched", (await logOf(tab)).filter((e) => e.k === "TYPED" || e.k === "click").length === 0 && (await tab.evaluate("document.getElementById('fn').value")) === "" && !existsCreds() && S.events.length === 0 && mb.calls.length === 0);
    await tab.goto(`${base}/create.html?captcha=late`);
    r = await go(mkCtx(tab, { mailbox: mb }));
    check("captcha appearing after submit => captcha_required", r.ready === false && r.result.reason === "captcha_required", r);
    check("Create Account pressed exactly once; the form was not posted", (await logOf(tab)).filter((e) => e.k === "click" && /Create Account/.test(e.v)).length === 1 && count(S.events, "register") === 0);
    await tab.close().catch(() => {});

    resetSite({ mfa: true }); seedExisting("Mfa#Pw-999-fixture"); credentials.save({ site: "127.0.0.1", email: EMAIL, password: "Mfa#Pw-999-fixture", origin: "created" }); harvest(); mb = fakeMailbox();
    tab = await open("fx-acct-mfa", "signin.html?both=1");
    r = await go(mkCtx(tab, { mailbox: mb }));
    check("login lands on SMS MFA => manual/mfa_required", r.ready === false && r.result.reason === "mfa_required", r);
    check("MFA page: code never typed, nothing requested, no reset", (await logOf(tab)).filter((e) => e.k === "TYPED").length === 0 && /mfa\.html$/.test(await tab.url()) && count(S.events, "reset-request") === 0 && mb.calls.length === 0);
    r = await go(mkCtx(tab, { mailbox: mb }));
    check("arriving on the MFA page directly => mfa_required", r.ready === false && r.result.reason === "mfa_required");
    await tab.close().catch(() => {});

    // ======================================================================= link refusal in the live flow
    console.log("# untrusted links from the mailbox are refused and never opened");
    const refuse = async (label, mbOpts, ctxOpts) => {
      resetSite({}); seedExisting(); const m = fakeMailbox(mbOpts);
      const t = await open(`fx-acct-link-${label}`, "signin.html");
      const rr = await go(mkCtx(t, { mailbox: m, ...ctxOpts }));
      const url = await t.url();
      check(`${label}: manual/account_flow_failed untrusted_link`, rr.ready === false && rr.result.reason === "account_flow_failed" && /untrusted_link/.test(rr.result.evidence), rr);
      check(`${label}: tab not navigated, no password reserved, no login`, /forgot\.html$/.test(url) && !existsCreds() && count(S.events, "login") === 0 && count(S.events, "reset") === 0, { url, ev: S.events.map((e) => e.k) });
      await t.close().catch(() => {});
    };
    await refuse("http", { resetLink: () => `${base}/reset?token=${S.lastReset}` }, { loopback: false });
    await refuse("cross-site", { resetLink: () => "https://evil.example.net/reset?token=1" });
    await refuse("wrong-port-https", { resetLink: () => "https://127.0.0.1:65000/reset?token=1" });
    resetSite({}); seedExisting(); mb = fakeMailbox({ resetLink: () => null });
    tab = await open("fx-acct-nolink", "signin.html");
    r = await go(mkCtx(tab, { mailbox: mb }));
    check("no email arrived (helper returned null) => manual/verification_timeout, nothing reserved", r.ready === false && r.result.reason === "verification_timeout" && !existsCreds() && count(S.events, "reset-request") === 1, r);
    await tab.close().catch(() => {});

    // ======================================================================= unknown required field
    console.log("# unknown required identity field stops before any password exists");
    resetSite({ requireVerify: true }); mb = fakeMailbox();
    tab = await open("fx-acct-ssn", "create.html?extra=1");
    r = await go(mkCtx(tab, { mailbox: mb }));
    check("SSN field => manual/account_flow_failed naming the field", r.ready === false && r.result.reason === "account_flow_failed" && /Social Security/i.test(r.result.evidence), r);
    check("no vault entry, no password typed, nothing posted, no mailbox trigger", !existsCreds() && (await logOf(tab)).filter((e) => e.k === "TYPED").length === 0 && S.events.length === 0 && mb.calls.length === 0);
    await tab.close().catch(() => {});

    // ======================================================================= adapter hooks
    console.log("# adapter hooks (signInFlow / detect) are honoured");
    resetSite({}); seedExisting(); mb = fakeMailbox();
    tab = await open("fx-acct-hooks", "signin.html?both=1");
    const hit = { clickForgot: 0, fillPassword: 0, submitNewPassword: 0, submit: [], detect: 0 };
    ctx = mkCtx(tab, { mailbox: mb });
    r = await go(ctx, {
      detect: async () => { hit.detect++; return "none"; },
      signInFlow: {
        clickForgot: async (c) => { hit.clickForgot++; await c.tab.click("#forgot"); },
        fillPassword: async (c, pw) => { hit.fillPassword++; await A.fillCredential(c, "#pw", pw); },
        submitNewPassword: async (c, pw) => { hit.submitNewPassword++; await A.fillCredential(c, "#pw", pw); await A.fillCredential(c, "#pw2", pw); await c.tab.click("#go"); },
        submit: async (c, kind) => { hit.submit.push(kind); if (kind === "signin") { await c.tab.click("#go"); } else if (kind === "reset") { await c.tab.click("#go"); } else throw new Error(`unexpected submit kind ${kind}`); },
      },
    });
    check("reset completed through the adapter's hooks", r.ready && r.action === "reset" && hit.clickForgot === 1 && hit.submitNewPassword === 1 && hit.fillPassword === 1 && hit.submit.join() === "reset,signin" && hit.detect > 0, { r, hit });
    await tab.close().catch(() => {});

    // ======================================================================= secrets
    console.log("# the password never appears in any result, log, evidence, audit or page log");
    ownedPasswords.forEach((x) => S.seen.add(x));
    const secrets = [...new Set([...S.seen, ...ownedPasswords])].filter((x) => x && x.length >= 12);
    const blob = [JSON.stringify(returned), JSON.stringify(logs), JSON.stringify(S.events), ...["consents.log", "mailbox_account.log"].map((f) => (fs.existsSync(path.join(tmp, f)) ? fs.readFileSync(path.join(tmp, f), "utf8") : "")),
      fs.existsSync(path.join(tmp, "vault", "credentials_audit.log")) ? fs.readFileSync(path.join(tmp, "vault", "credentials_audit.log"), "utf8") : "",
      ...(fs.existsSync(path.join(tmp, "evidence")) ? fs.readdirSync(path.join(tmp, "evidence")) : [])].join("\n");
    check(`checked ${secrets.length} distinct passwords the site/vault ever held`, secrets.length >= 6, secrets.length);
    check("no password in results, ctx logs, events, audit logs, consent log, evidence names", secrets.every((x) => !blob.includes(x)), secrets.filter((x) => blob.includes(x)).length);
    const aud2 = path.join(tmp, "vault", "credentials_audit.log");
    check("credentials audit exists beside the vault and holds site+email+event only", fs.existsSync(aud2) && fs.readFileSync(aud2, "utf8").trim().split("\n").every((l) => { const j = JSON.parse(l); return Object.keys(j).sort().join() === "email,event,site,t"; }));
  } finally {
    for (const nm of names) { try { await browser.close({ name: nm }); } catch { /* already closed */ } }
    server.stop(true);
    unload();
    for (const [k, v] of Object.entries(prevEnv)) { if (v === undefined) delete process.env[k]; else process.env[k] = v; }
    try { fs.rmSync(tmp, { recursive: true, force: true }); } catch { /* temp */ }
  }
  console.log(`\n${pass} passed, ${fail} failed`);
  return { pass, fail };
};
