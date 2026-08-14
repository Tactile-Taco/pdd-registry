// PDD Runtime Verification Layer - Express middleware template.
// Observes the monitorable projection of a protocol and appends to the Dynamic Evidence Ledger.
// Isolation property: verdicts are computed OUTSIDE the generated implementation's code paths.
import { appendFileSync, readFileSync, existsSync } from "node:fs";
import { createHash, createHmac } from "node:crypto";

// Fail closed: never sign ledger blocks with a public fallback key.
const KEY = process.env.PDD_EVIDENCE_KEY;
if (!KEY) throw new Error("PDD_EVIDENCE_KEY is not set (or is empty); refusing to sign ledger blocks (fail closed)");
const sha = (s) => "sha256:" + createHash("sha256").update(s).digest("hex");
// Canonical JSON matching pdd.evidence (pdd-cli package, byte-compatible with the old evidence_chain.py): sorted keys recursively, compact (no spaces),
// so digests computed here verify identically on the Python side.
const canonical = (o) => {
  if (Array.isArray(o)) return "[" + o.map(canonical).join(",") + "]";
  if (o !== null && typeof o === "object") {
    return "{" + Object.keys(o).sort().map((k) => JSON.stringify(k) + ":" + canonical(o[k])).join(",") + "}";
  }
  return JSON.stringify(o);
};

const sign = (d) => "hmac-sha256:" + createHmac("sha256", KEY).update(d).digest("hex");

export function makeRvl({ protocol, implVersion, ledgerPath, checks, onViolation }) {
  const append = (observations, decision) => {
    let prev = "sha256:" + "0".repeat(64);
    if (existsSync(ledgerPath)) {
      const lines = readFileSync(ledgerPath, "utf8").trim().split("\n").filter(Boolean);
      if (lines.length) prev = JSON.parse(lines.at(-1)).digest;
    }
    const block = { previous: prev, protocol, implementation_version: implVersion,
                    observations, decision, time: new Date().toISOString() };
    block.digest = sha(canonical(block));
    block.signature = sign(block.digest);
    appendFileSync(ledgerPath, JSON.stringify(block) + "\n");
    return block;
  };
  return function rvl(req, res, next) {
    const start = process.hrtime.bigint();
    const violations = [];
    for (const c of checks.request || []) { const v = c(req); if (v) violations.push(v); }
    const origJson = res.json.bind(res);
    res.json = (body) => {
      const ms = Number(process.hrtime.bigint() - start) / 1e6;
      for (const c of checks.response || []) { const v = c(req, res, body, ms); if (v) violations.push(v); }
      if (violations.length) {
        const block = append({ route: req.path, violations, latency_ms: ms }, "attest-violation");
        onViolation?.(block, req, res);
      }
      return origJson(body);
    };
    next();
  };
}
