// Read-only Phase 1 enrichment: one selection, one campaign-gated detail load, one save.
// Eval JS: const {verifyNext} = require(ROOT + '/src/verify_pending.js');
//          const result = await verifyNext({browser});
// Do not gate separately: this helper blocks on campaign.py gate detail itself.
const path = require("path");
const { execFileSync } = require("child_process");
const { detailOne } = require("./discover_linkedin.js");
const rl = require("./ratelimit.js");
const ROOT = path.resolve(__dirname, "..");
let sequence = 0;

function campaign(command, argument, payload) {
  const args = [path.join(ROOT, "src", "campaign.py"), command];
  if (argument !== undefined) args.push(String(argument));
  let text;
  try {
    text = execFileSync("python3", args, {
      cwd: ROOT,
      encoding: "utf8",
      input: payload === undefined ? undefined : JSON.stringify(payload),
      stdio: ["pipe", "pipe", "pipe"],
    });
  } catch (error) {
    let reason;
    try { reason = JSON.parse(String(error.stderr)).error; } catch {}
    throw new Error(reason || error.message);
  }
  const result = JSON.parse(text);
  if (result.error) throw new Error(result.error);
  return result;
}

async function verifyNext({ browser } = {}) {
  let job, name, result;
  let phase = "select";
  try {
    ({ job } = campaign("next-verify"));
    if (!job) return { status: "no-job", job: null };
    if (!browser) throw new Error("browser is required");

    phase = "open";
    name = `verify-pending-${process.pid}-${Date.now()}-${++sequence}`;
    const tab = await browser.open({ name, url: "about:blank", app: "relay", timeout: 60 });
    const gate = kind => {
      phase = "gate";
      const decision = campaign("gate", kind);
      if (decision.allowed !== true) {
        throw Object.assign(new Error(decision.reason || "campaign_gate_denied"), {
          retry_at: decision.retry_at ?? null,
        });
      }
      phase = "detail";
    };
    const observed = await detailOne(tab, { ...job }, { gate });
    phase = "description";
    if (!observed.description && observed.closed !== true) {
      throw new Error("empty_description");
    }
    const payload = {
      description: observed.description,
      closed: observed.closed,
      easy_apply: observed.easy_apply,
      external_apply: observed.external_apply,
      apply_url: observed.apply_url,
    };
    for (const field of ["salary_min", "salary_max"]) {
      if (observed[field] !== undefined) payload[field] = observed[field];
    }
    phase = "save";
    const saved = campaign("save-description", job.id, payload);
    if (saved.verified !== true) throw new Error("campaign_did_not_confirm_verification");
    result = {
      status: "verified",
      job_id: job.id,
      closed: observed.closed,
      description_length: observed.description?.length || 0,
      saved,
    };
  } catch (error) {
    result = {
      status: "blocked",
      job_id: job?.id ?? null,
      phase,
      reason: error.message || String(error),
    };
    if (error.retry_at !== undefined) result.retry_at = error.retry_at;
    // detailOne retains checkPage/trip, including the persistent six-hour lockout.
    // Report the persisted deadline, not the older exception's detection timestamp.
    if (error instanceof rl.RateLimited) {
      try { result.lockout = rl.status().locked; } catch {}
    }
  } finally {
    if (name) {
      try { await browser.close({ name }); }
      catch (error) { result.cleanup_error = error.message || String(error); }
    }
  }
  return result;
}

module.exports = { verifyNext };
