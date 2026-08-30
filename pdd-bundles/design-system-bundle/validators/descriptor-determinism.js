#!/usr/bin/env node
// descriptor-determinism.js — DS-B-02: two independent lint processes over the
// same descriptor must produce identical normalized reports.
'use strict';
const { execFileSync } = require('child_process');
const path = require('path');
const file = process.argv[2];
if (!file) { console.error('usage: descriptor-determinism.js <descriptor.json>'); process.exit(2); }
const lint = path.join(__dirname, 'descriptor-lint.js');
const norm = (out) => out.replace(/"timestamp": "[^"]*"/, '"timestamp": "<elided>"');
const a = execFileSync('node', [lint, '--contrast', file], { encoding: 'utf8' });
const b = execFileSync('node', [lint, '--contrast', file], { encoding: 'utf8' });
const same = norm(a) === norm(b);
console.log(JSON.stringify({
  invariant_id: 'DS-B-02', severity: 'must',
  result: same ? 'pass' : 'fail',
  detail: same ? 'two-run reports byte-identical after timestamp elision' : 'reports diverge between runs — nondeterministic lint',
}));
process.exit(same ? 0 : 1);
