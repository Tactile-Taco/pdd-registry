# Negotiation Minutes — user-registry v1.0.0

Date: 2026-08-05

## Scope

Single-protocol registry bootstrap. No `depends_on` edges; no cross-protocol
handshake consumption. Negotiation is therefore a sealability review, not a
multi-party reconciliation.

## Compatibility matrix

| Bundle | Version | Status | Depends on | Provides |
|---|---|---|---|---|
| user-registry | 1.0.0 | sealed | — | user-registry.create, user-registry.response |

## Conflicts

None.

## Resolutions

- Performance invariant O-005 graded `should` (project policy: provisional
  infrastructure; lenient performance gates).
- Capability invariants O-001..O-004 graded `must` (enforceable by sandboxing
  alone; no performance infrastructure required).

## Version pins

- user-registry@1.0.0 — sealed.
- Validator set: bundle-linter 1.0.0, schema-validator 1.0.0, contract-runner
  1.0.0, property-runner 1.0.0, mutation-sanity 1.0.0, import-scanner 1.0.0,
  capability-monitor 1.0.0, benchmark-runner 1.0.0.

## Rules honored

- A bundle with an open `must`-level conflict must not be sealed.
- Sealed bundles change only via explicit version events and renewed negotiation.
