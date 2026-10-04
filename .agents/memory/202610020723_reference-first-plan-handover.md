# Reference-first implementation planning handover

Date: 2026-10-02.

## Completed

Created the [TDD implementation plan](../plan/202610020723_reference-first-tdd-implementation.md) and aligned the [proposed design](../design/202609281044_oxivault-design.md).
The developer selected in-process adaptation of the reference code rather than isolated production subprocesses.
The developer also confirmed Turtle and N-Triples as the initial RDF input formats.
Public conversion methods and CLI commands are `vault2rdf` and `rdf2vault`.
RDFLib is now the initial engine; maplib and its load/cache optimizations are deferred.
The older investigation handover's maplib-first instructions are superseded by this plan.

Verified the upstream scripts, SPEC conformance section, dependency pins, and example-fixture paths at revision `025e71be8d810e387dc451c920e05472d1c47170`.
The scripts expose CLI `main()` functions, and ingest's network guard mutates process-global urllib handlers.
The planned adaptation must replace process behavior with explicit arguments, diagnostics/errors, and the agreed parser allowlist.
It must retain mapping/preservation rules and compare against a separate untouched reference oracle.

## Next

Review the proposed implementation sequence, then begin increment 0 with failing store-contract tests.
No runtime code, dependencies, tests, or release artifacts were changed in this planning increment; implementation/build/runtime-test/release phases do not apply yet.
The repository remains a scaffold with an empty README and no test suite.
Implementation increments require the full pytest suite, `tara check`, runnable examples, and related documentation before handoff.

Open gates include sync conflict-copy/deletion behavior, Git remotes, auth/deployment, SPARQL safety and result shape, history timing, and distribution licensing.
Do not claim arbitrary-RDF losslessness, vault-wide S3 transactions, or reference-backend performance based on the maplib spike.

Existing `.tara/skills.toml` and `.tara/skills.lock` worktree changes were left untouched.
Only planning/design/handover documents and their indexes belong to this increment.
Suggested developer commit message: `docs: plan reference-first TDD implementation`.
No commit or push was made.
