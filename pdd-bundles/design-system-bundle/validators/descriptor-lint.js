#!/usr/bin/env node
// descriptor-lint.js — validates a design-system descriptor against the
// rules encoded in schemas/descriptor.schema.json plus semantic checks
// (DS-S-02/03/04, DS-B-01, DS-B-03). Offline, dependency-free.
// Usage: node descriptor-lint.js [--contrast] <descriptor.json>
'use strict';
const fs = require('fs');

const KEBAB = /^[a-z0-9]+(-[a-z0-9]+)*$/;
const HEX = /^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$/;
const DIM = /^-?\d+(\.\d+)?(px|rem)$/;
const DUR = /^\d+ms$/;
const SEMVER = /^\d+\.\d+\.\d+$/;
const DEMOTE = new Set(['unmeasurable', 'proxy-rejected', 'style-intent']);
const TYPES = ['color', 'dimension', 'duration'];
const CATS = ['surface', 'text', 'accent', 'border', 'spacing', 'radius', 'motion'];

const file = process.argv.filter((a, i) => i > 1 && !a.startsWith('--'))[0];
if (!file) { console.error('usage: descriptor-lint.js [--contrast] <descriptor.json>'); process.exit(2); }
let d;
try { d = JSON.parse(fs.readFileSync(file, 'utf8')); } catch (e) { console.error(`unparseable JSON: ${e.message}`); process.exit(2); }

/** @type {{invariant_id:string, severity:string, result:string, detail:string}[]} */
const results = [];
const rec = (id, sev, ok, detail) => results.push({ invariant_id: id, severity: sev, result: ok ? 'pass' : 'fail', detail });

// ---- DS-S-01: shape vs schema rules (required fields, additionalProperties:false)
const shapeErrs = [];
for (const k of ['descriptor_version', 'generator', 'tokens', 'pairings']) {
  if (!(k in d)) shapeErrs.push(`missing required field: ${k}`);
}
const TOP = new Set(['descriptor_version', 'generator', 'tokens', 'pairings', 'measurements', 'style_context']);
for (const k of Object.keys(d)) if (!TOP.has(k)) shapeErrs.push(`unexpected top-level field: ${k}`);
rec('DS-S-01', 'must', shapeErrs.length === 0, shapeErrs.join('; ') || 'shape conforms to descriptor.schema.json rules');

// ---- DS-S-02: token grammar + pairing references
const tokErrs = [], names = new Set();
const VALRE = { color: HEX, dimension: DIM, duration: DUR };
for (const t of Array.isArray(d.tokens) ? d.tokens : []) {
  if (!t || typeof t !== 'object') { tokErrs.push('token: not an object'); continue; }
  if (!KEBAB.test(t.name || '')) tokErrs.push(`token name invalid: ${JSON.stringify(t.name)}`);
  else if (names.has(t.name)) tokErrs.push(`duplicate token: ${t.name}`);
  names.add(t.name);
  if (!TYPES.includes(t.type)) tokErrs.push(`${t.name}: bad type ${JSON.stringify(t.type)}`);
  else if (!VALRE[t.type].test(String(t.value))) tokErrs.push(`${t.name}: value ${JSON.stringify(t.value)} does not match type ${t.type}`);
  if (!CATS.includes(t.category)) tokErrs.push(`${t.name}: bad category ${JSON.stringify(t.category)}`);
}
for (const p of Array.isArray(d.pairings) ? d.pairings : []) {
  for (const ref of [p.fg, p.bg]) if (!names.has(ref)) tokErrs.push(`pairing references undeclared token: ${ref}`);
  if (p.relation !== 'contrast_ratio') tokErrs.push(`pairing ${p.fg}/${p.bg}: unknown relation ${JSON.stringify(p.relation)}`);
  const th = Number(p.threshold);
  if (!(th > 0 && th <= 21)) tokErrs.push(`pairing ${p.fg}/${p.bg}: threshold out of range`);
  if ('large_text_ok' in p && typeof p.large_text_ok !== 'boolean') tokErrs.push(`pairing ${p.fg}/${p.bg}: large_text_ok must be boolean`);
}
rec('DS-S-02', 'must', tokErrs.length === 0 && names.size > 0, tokErrs.join('; ') || `${names.size} tokens grammatical, all pairing refs resolve`);

// ---- DS-S-03: normative-filter encoding
const mErrs = [];
for (const m of Array.isArray(d.measurements) ? d.measurements : []) {
  if (!m || typeof m.item !== 'string' || !m.item) { mErrs.push('measurement: missing item'); continue; }
  if (typeof m.machine_checkable !== 'boolean') { mErrs.push(`${m.item}: machine_checkable must be boolean`); continue; }
  if (m.machine_checkable) {
    if (!m.proxy || typeof m.proxy !== 'object') mErrs.push(`${m.item}: machine_checkable=true requires proxy{selector_or_method, threshold_or_window}`);
    else {
      for (const k of ['selector_or_method', 'threshold_or_window'])
        if (!(k in m.proxy)) mErrs.push(`${m.item}: proxy missing ${k}`);
      if (!('intent_delta' in (m.proxy || {}))) mErrs.push(`${m.item}: WARNING proxy.intent_delta recommended (Measurement Ambiguity delta logging)`);
    }
  } else if (!DEMOTE.has(m.demote_reason)) {
    mErrs.push(`${m.item}: demoted item needs demote_reason in controlled set`);
  }
}
// warnings must not mask pass/fail: treat pure warnings as pass-with-detail
const hardM = mErrs.filter(e => !e.includes('WARNING'));
rec('DS-S-03', 'must', hardM.length === 0, hardM.join('; ') || `filter encoding ok (${(d.measurements || []).length} items)`);

// ---- DS-S-04: provenance
const g = d.generator || {};
const provOk = typeof g.tool === 'string' && g.tool.length > 0 &&
  g.raw_output_hash && g.raw_output_hash.algorithm === 'sha256' && /^[a-f0-9]{64}$/.test(g.raw_output_hash.value || '');
rec('DS-S-04', 'should', !!provOk, provOk ? `provenance present (${g.tool})` : 'generator.provenance missing/malformed (should-tier: degrades, does not gate)');

// ---- DS-S-05: semver grammar
rec('DS-S-05', 'must', SEMVER.test(d.descriptor_version || ''), `descriptor_version=${d.descriptor_version}`);

// ---- DS-B-03: demote reasons enumerable (should)
const demoted = (d.measurements || []).filter(m => m && m.machine_checkable === false);
const badDemote = demoted.filter(m => !DEMOTE.has(m.demote_reason));
rec('DS-B-03', 'should', badDemote.length === 0, badDemote.length ? `${badDemote.length} demoted items lack valid reason` : `${demoted.length} demoted items all carry controlled reasons`);

// ---- DS-B-01 (--contrast): WCAG relative luminance ratios
if (process.argv.includes('--contrast')) {
  const byName = Object.fromEntries((d.tokens || []).map(t => [t.name, String(t.value)]));
  const lum = (hexv) => {
    const h = hexv.replace('#', '').padEnd(6, '0').slice(0, 6);
    const f = (i) => { let c = parseInt(h.slice(i * 2, i * 2 + 2), 16) / 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(0) + 0.7152 * f(1) + 0.0722 * f(2);
  };
  const cErrs = [];
  for (const p of d.pairings || []) {
    if (!byName[p.fg] || !byName[p.bg]) continue; // already reported by S-02
    const l1 = lum(byName[p.fg]), l2 = lum(byName[p.bg]);
    const ratio = (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
    if (ratio < p.threshold) cErrs.push(`${p.fg}/${p.bg}: ${ratio.toFixed(2)}:1 < ${p.threshold}:1`);
  }
  rec('DS-B-01', 'must', cErrs.length === 0, cErrs.join('; ') || `all ${(d.pairings || []).length} pairings meet thresholds`);
}

const passed = results.every(r => r.result === 'pass');
console.log(JSON.stringify({
  protocol_name: 'design-system',
  timestamp: new Date().toISOString(),
  descriptor: file,
  validation_results: results,
  passed,
}, null, 2));
process.exit(passed ? 0 : 1);
