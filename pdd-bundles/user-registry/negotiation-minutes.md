# Negotiation Minutes — user-registry v1.1.0

Date: 2026-08-05

## v1.1.0 version event (namespace/tags)

- Additive metadata only, per S-003 (minor version): `namespace: user`,
  `tags: [server]` in protocol.yaml. No behavior change to the create/get
  handshake; the user-registry candidate is unchanged except for its
  candidate-manifest protocol version pin.
- Addressing: display address becomes `user/user-registry`; directory and
  evidence stay name-keyed.
- Grammar + cross-bundle uniqueness enforced by `bundle-linter` and the
  pdd-registry S-004/S-005 invariants.

## Scope

Single-protocol registry bootstrap. No `depends_on` edges; no cross-protocol
handshake consumption. Negotiation is therefore a sealability review, not a
multi-party reconciliation.

## Compatibility matrix

| Bundle | Version | Status | Depends on | Provides |
|---|---|---|---|---|
| user-registry | 1.1.0 (1.0.0 in git history) | sealed | — | user-registry.create, user-registry.response |

## Conflicts

None.

## Resolutions

- Performance invariant O-005 graded `should` (project policy: provisional
  infrastructure; lenient performance gates).
- Capability invariants O-001..O-004 graded `must` (enforceable by sandboxing
  alone; no performance infrastructure required).

## Version pins

- user-registry@1.1.0 — sealed (v1.1.0 version event; 1.0.0 remains in git history).
- Validator set: bundle-linter 1.0.0, schema-validator 1.0.0, contract-runner
  1.0.0, property-runner 1.0.0, mutation-sanity 1.0.0, import-scanner 1.0.0,
  capability-monitor 1.0.0, benchmark-runner 1.0.0.

## Rules honored

- A bundle with an open `must`-level conflict must not be sealed.
- Sealed bundles change only via explicit version events and renewed negotiation.
