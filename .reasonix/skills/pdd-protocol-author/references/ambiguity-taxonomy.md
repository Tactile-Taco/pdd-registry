# Ambiguity Taxonomy

Use this reference when converting human language into PDD constraints.

## Structural Ambiguity

Signals:

- "user object"
- "metadata"
- "payload"
- "valid request"
- "error response"

Resolution:

- Define schemas.
- Specify required fields, optional fields, nullability, enums, default values, and error variants.
- Add versioning and compatibility rules.

## Behavioral Ambiguity

Signals:

- "safe"
- "idempotent"
- "deduplicate"
- "handle errors"
- "retryable"
- "deterministic"
- "preserve order"

Resolution:

- Turn prose into predicates, examples, or property-based tests.
- Define state transitions.
- Specify what happens on invalid input, partial failure, retries, and duplicate requests.

## Operational Ambiguity

Signals:

- "fast"
- "cheap"
- "not too many calls"
- "do not overload"
- "secure"
- "no side effects"
- "best effort"

Resolution:

- Add explicit limits for latency, memory, CPU, network, disk, database calls, retries, concurrency, secrets, and dependencies.
- State whether limits are admission requirements or monitoring goals.
- **Project policy:** performance limits default to `severity: should` with generous budgets; only hard capability limits (network, disk, secrets, dependencies, background work) are `must` by default.

## Authority Ambiguity

Signals:

- "can access user data"
- "may call services"
- "uses credentials"
- "admin operation"

Resolution:

- Define allowlists and deny-by-default behavior.
- Specify scopes, roles, identity requirements, and audit logging.

## Evidence Ambiguity

Signals:

- "tested"
- "verified"
- "passed CI"
- "safe to deploy"

Resolution:

- Specify validator versions, logs, artifact hashes, dependency manifests, coverage summaries, sandbox traces, and signed attestations.

## Decision Rule

Ask a blocking question only when no conservative default is safe. Otherwise, make the default explicit in `ambiguity-log.md`.

## Reference case (CA-001): "current item" undo ambiguity

Recorded in the first sealed protocol of the pdd-repository (order-handler v1.0.0 → v1.1.0);
included here in full so this skill is self-contained. The original field case was a
typing-test engine ("current word" backspace); it is generalized here to any
component with per-item commit/undo semantics.

**The ambiguity.** A sealed behavioral invariant read: *"Undo never moves before
the start of the current item and never alters committed items."*

- **Reading A (literal):** the undo cursor may never retreat past the item it
  currently touches. (The first implementation chose this.)
- **Reading B (earliest reachable erroneous item):** the cursor may retreat
  into a committed item that contains errors, because "the item the cursor is
  currently touching" ends at the most recent *fully correct* item.

Both are defensible readings of the same English sentence, and the observable
behavior differs materially — a user or validator can tell them apart. Hence
**critical**, not cosmetic. The positional-scope noun "current" was the tell.

**Ground truth and resolution.** The reference implementation gates the undo
event: with an empty current input, undo retreats into the previous committed
item iff that item's committed input differs from its target; a fully correct
committed item is sealed. Rather than adopting Reading B's silent redefinition
of "current item" (a protocol-wide term used by cursor position, accounting, and
commit semantics), the invariant text was amended to state the behavior
explicitly:

> Undo deletes one unit within the current item's input. With an empty current
> input, undo retreats into the immediately previous committed item if and only
> if that item's committed input differs from its target (it contains an error);
> a fully correct committed item is sealed and cannot be re-entered.

**Remediation record.** Classification: protocol-gap. Version event
(v1.0.0 → v1.1.0), renewed negotiation note, implementation updated, three new
property tests (retreat-iff-error, seal-iff-correct, never-before-first-item),
full validator loop re-run, re-admission with fresh evidence.

**Lesson.** When a critical reading turns out wrong post-sealing, never patch
the sealed text silently and never "reconcile" by redefining a protocol-wide
term — issue a version event and make the text say what the behavior is.
