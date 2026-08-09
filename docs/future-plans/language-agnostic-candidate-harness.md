# Language-agnostic candidate harness (sketch)

**Context.** The protocol is language-agnostic; the *validator tooling* is
not: `validators/validate_candidate.py` runs candidates via `pytest`
(`:340`, `:422`), scans Python AST imports for O-003 (`:280-307`), imports a
`.py` entry module (`:410`), and evaluates `smoke.assert_expr` as Python
(`:243`). The candidate manifest already declares `language`/`runtime` but
the validator never reads them. Publishing a protocol needs NO candidate
(honor system, S-007); candidates are only required to run a full Validator
Loop for the registry's own dogfood.

**Options (from the analysis).**
1. **Do nothing** — third-party protocols publish without candidates.
2. **Manifest-driven execution** — add `test_command` to the manifest; the
   validator runs it in the existing docker sandbox (which already enforces
   O-001/O-002/O-004 language-agnostically). Scope O-003's AST import scan
   to python-language candidates; smoke/mutation/benchmark get a
   language-specific driver. Python candidates keep the current path.
3. **Generalized execution envelope** — the candidate contract becomes "a
   declared harness the validator runs in the sandbox, producing a standard
   result shape", instead of "an importable in-memory module".

**Why deferred.** Option 1 covers today's needs; 2 is a bounded change once
a non-Python candidate is actually wanted; 3 is over-engineering until a
specific protocol needs it.
