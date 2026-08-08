# PDD Bundle Spec

Use this reference when writing or reviewing a PDD bundle.

## Minimum Files

```text
pdd-bundles/<protocol-name>/
├── protocol.yaml
├── schemas/
│   ├── request.schema.json
│   └── response.schema.json
├── capability-manifest.yaml
├── invariants/
│   ├── structural.yaml
│   ├── behavioral.yaml
│   └── operational.yaml
├── validators/
│   ├── validation-plan.yaml
│   └── validator-set.yaml
├── ambiguity-log.md
└── evidence-requirements.yaml
```

## protocol.yaml

Required fields:

- `protocol.name`: stable slug (matches the bundle directory name).
- `protocol.version`: semantic version.
- `protocol.status`: `draft`, `review`, `sealed`, or `deprecated`.
- `namespace`: kebab-case owner/scope slug (Docker-Hub owner / npm scope
  analogy, e.g. `pdd`, `user`, `typing`). Uniqueness: bundle names are unique
  WITHIN a namespace, not globally — two projects may each own a
  `typing-test-engine` under different namespaces (S-004).
- `tags`: list of kebab-case tags from the controlled catalog vocabulary
  (seeds: `engine`, `input`, `stats`, `data-catalog`, `ui`, `auth`,
  `server`); at most 8, no duplicates (S-005).
- `purpose`: one paragraph.
- `boundary.in_scope`: what the protocol governs.
- `boundary.out_of_scope`: what it does not govern.
- `handshakes`: schema references.
- `invariants`: references to invariant files.
- `capabilities`: reference to capability manifest.
- `validators`: reference to validation plan.
- `evidence`: reference to evidence requirements.

Display addressing is `namespace/name`; the on-disk layout stays
`pdd-bundles/<name>` and evidence stays name-keyed — a backwards-compatible
bridge, not a directory reorganization.

## Invariant Files

Each invariant should include:

- `id`: stable machine-readable identifier.
- `statement`: human-readable rule.
- `severity`: `must`, `should`, or `may`.
- `rationale`: why the invariant exists.
- `validation`: one or more validation mechanisms.

Use `must` for admission requirements. Use `should` only when failure does not reject admission.

**Project policy:** performance/resource invariants (latency, memory, CPU, throughput)
default to `severity: should` with generous budgets; upgrade to `must` only with an
explicit budget rationale. Hard capability invariants (network, filesystem, secrets,
dependencies, background work) stay `must` — they are enforceable by sandboxing alone.

Each operational invariant may declare `infra_assumption`: what the validator needs
(sandbox, docker, CI runner, deployment/RVL). When the assumed infra is absent, the
invariant is validated in harness mode and excess is recorded as an observation, not
an admission failure (unless it is a hard capability `must`).

## Capability Manifest

Operational authority should be explicit. Include:

- network access and allowed destinations.
- disk I/O and allowed paths.
- database operation limits.
- external service dependencies.
- secret/environment variable access.
- latency, memory, CPU, and concurrency budgets.
- logging and telemetry boundaries.

## Ambiguity Log

Maintain two sections:

- `Resolved assumptions`: decisions made to produce the bundle.
- `Open questions`: questions that may require a protocol revision.

Blocking ambiguity prevents sealing. Non-blocking ambiguity can be recorded as an assumption.

## Evidence Requirements

Evidence should be sufficient to answer:

- Which protocol version governed admission?
- Which artifact was validated?
- Which validators ran?
- What results were observed?
- What dependencies and environment were used?
- Can the decision be replayed or audited?
