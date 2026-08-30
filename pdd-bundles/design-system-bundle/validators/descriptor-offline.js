#!/usr/bin/env node
// descriptor-offline.js — DS-O-01: prove the lint path makes no network use.
// Mechanism (computed, not self-attested): static egress-surface scan of the
// validator sources (http/https/net/dns/fetch/WebSocket requires or calls)
// plus a clean subprocess run. Any egress capability = fail.
'use strict';
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
const file = process.argv[2];
if (!file) { console.error('usage: descriptor-offline.js <descriptor.json>'); process.exit(2); }
const EGRESS = /\brequire\s*\(\s*['"](https?|node:https?|net|dns|tls|http2)['"]\)|\bfetch\s*\(|WebSocket|XMLHttpRequest|\baxios\b|\bnode-fetch\b/;
let ok = true; const detail = [];
for (const src of ['descriptor-lint.js', 'descriptor-determinism.js']) {
  const text = fs.readFileSync(path.join(__dirname, src), 'utf8');
  const hit = text.match(EGRESS);
  if (hit) { ok = false; detail.push(`${src}: egress surface ${hit[0].trim()}`); }
}
try { execFileSync('node', [path.join(__dirname, 'descriptor-lint.js'), '--contrast', file], { encoding: 'utf8' }); }
catch (e) { /* lint result irrelevant here; only crash-free execution matters */ if (e.status === undefined || e.status === null || e.status > 2) { ok = false; detail.push(`lint crashed: ${e.message}`); } }
console.log(JSON.stringify({
  invariant_id: 'DS-O-01', severity: 'must',
  result: ok ? 'pass' : 'fail',
  detail: ok ? 'no egress surface in validator sources; subprocess run clean' : detail.join('; '),
}));
process.exit(ok ? 0 : 1);
