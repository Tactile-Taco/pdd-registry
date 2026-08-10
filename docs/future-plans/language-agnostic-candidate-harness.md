# Language-agnostic candidate harness — LANDED (option 2, 2026-08-10)

**Context.** The protocol is language-agnostic; the *validator tooling* was
not: `validators/validate_candidate.py` ran candidates via `pytest`, scanned
Python AST imports for O-003, imported a `.py` entry module, and evaluated
`smoke.assert_expr` as Python. Publishing a protocol needs NO candidate
(honor system, S-007); candidates are only required to run a full Validator
Loop for the registry's own dogfood.

**What shipped (option 2, manifest-driven execution).**
- `candidate-manifest.json` gains `test_command` (argv list, 1..8 strings,
  each 1..200 chars, no newlines): the behavioral layer runs it from the
  temp copy with scrubbed env — the same containment as the pytest path.
- Language gate: candidates with `language != python` skip the Python-shaped
  layers with honest reasons — O-001..O-004 static AST scans, sandbox smoke,
  O-005 benchmark, and the mutant harness. No pass claim is ever made for a
  harness that did not run.
- Candidate digest is manifest-driven: hashes the manifest `files` list
  (any language); falls back to the python entry module (back-compat).
- Demo candidate: `implementations/taxonomy-web-service/shell-stdlib/`
  (language `shell`, `test_command: ["sh", "tests/run.sh"]`) — validated,
  verdict admit (5 passed, honest skips for the python-shaped layers).

**Remaining (documented boundary).** `test_command` runs on the validator
host (same trust level as pytest); the docker sandbox smoke remains a
python-image (`python:3.12-slim`) so a Node/Rust candidate's runtime must
exist on the validator host or the sandbox image must gain runtimes. Option
3 (generalized execution envelope with a standard result shape) remains
unneeded until a specific protocol requires it.

**Options considered (original sketch).**
1. **Do nothing** — third-party protocols publish without candidates.
2. **Manifest-driven execution** — LANDED as described above.
3. **Generalized execution envelope** — the candidate contract becomes "a
   declared harness the validator runs in the sandbox, producing a standard
   result shape", instead of "an importable in-memory module". Deferred.
