# Reference-first implementation plan

Status: proposed execution plan; implementation has not started.
Date: 2026-10-02.
Audience: developers implementing the [oxivault design](../design/202609281044_oxivault-design.md).

## Agreed scope

Use the Vault-LD reference implementation now, adapted into in-process functions rather than subprocess wrappers or a new implementation of its mapping rules.
The two public conversion methods are `Vault.vault2rdf` and `Vault.rdf2vault`; use `vault2rdf` and `rdf2vault` for the corresponding CLI commands too.
Initially accept Turtle and N-Triples input.
Defer JSON-LD and other RDF input formats until network-free parsing can be established without modifying global process state.

Retain the design's ObjectStore abstraction, Local/Memory/S3/Git backends, Polars derived table, Obsidian sync, and FastAPI integration.
Use RDFLib for conversion, RDF parsing/serialization, and SPARQL during this phase.
Defer maplib, its custom N-Triples load path, struct-column optimization, and maplib-specific caching.
Do not promise the maplib spike's performance for the reference-backed implementation.

The repository currently has a Python 3.14 package scaffold and placeholder CLI, but no implementation, dependencies, or tests.
There are therefore no existing conversion methods or compatibility aliases to migrate.
This document plans implementation; it does not authorize deployment or resolve the design's outstanding product decisions.

## Reference baseline and adaptation boundary

Pin the reference baseline to [`The-Knowledge-Graph-Guys/vault-ld@025e71be8d810e387dc451c920e05472d1c47170`](https://github.com/The-Knowledge-Graph-Guys/vault-ld/tree/025e71be8d810e387dc451c920e05472d1c47170).
The normative source is [SPEC.md](https://github.com/The-Knowledge-Graph-Guys/vault-ld/blob/025e71be8d810e387dc451c920e05472d1c47170/SPEC.md).
The implementation sources are [vault_to_rdf.py](https://github.com/The-Knowledge-Graph-Guys/vault-ld/blob/025e71be8d810e387dc451c920e05472d1c47170/scripts/vault_to_rdf.py) and [rdf_to_vault.py](https://github.com/The-Knowledge-Graph-Guys/vault-ld/blob/025e71be8d810e387dc451c920e05472d1c47170/scripts/rdf_to_vault.py).
Its [requirements](https://github.com/The-Knowledge-Graph-Guys/vault-ld/blob/025e71be8d810e387dc451c920e05472d1c47170/scripts/requirements.txt) pin `rdflib==7.6.0` and `PyYAML==6.0.3`; start from these pins and review compatibility and advisories when introducing dependencies.

Keep adapted code under `src/oxivault/_reference/vault_ld/`, with the upstream filenames retained for traceability.
Extract the CLI `main()` bodies into callable `vault2rdf` and `rdf2vault` functions with explicit arguments and results.
Retain context composition, frontmatter parsing, identity minting, link resolution, placement, and preservation algorithms.
Do not first split or rewrite them into new context/identity/frontmatter modules.
Keep Apache-2.0 licensing and required attribution with copied code and fixtures, record the source revision and adaptations, and confirm the project's distribution license before release.

Adapt process behavior at the boundary: no `argparse`, `sys.argv` mutation, `SystemExit`, console output, or process-wide network patch in a library conversion call.
Return diagnostics explicitly, and raise package-specific exceptions for fatal errors with their causes preserved.
Upstream recoverable warnings remain observable diagnostics, not silent success or blanket exceptions.
Use explicit, locally supplied RDF bytes and an allowlist of Turtle/N-Triples parsers; reject URLs, unknown formats, and JSON-LD before parsing.
Keep upstream's local-context containment and refusal of remote context resolution.
RDF IRI values may still identify remote resources; identifying one must never fetch it.

Bridge ObjectStore to the path-based reference functions using a private, bounded temporary vault snapshot.
Preserve relative paths and local context documents, including referenced documents whose names are not `context.jsonld`.
Read bounds must apply in the store while reading, not after an unrestricted `get()` has allocated the object.
Apply per-object and total snapshot limits, validate keys and containment, and clean up temporary resources on both success and failure.
Run conversions in-process against that snapshot, never directly against the live destination.
After successful ingest, publish only changed/new notes and contexts using captured ETags and create-only preconditions.
Do not delete notes absent from the input graph or rewrite unrelated assets.
A conversion failure before publication changes nothing in the destination.
Multi-object publication is not atomic on S3 or LocalDir; report already-applied paths on a mid-publication failure and invalidate the derived graph.
Git can later provide an atomic batch commit through an optional store capability.

## Public contract

```python
class Vault:
    def vault2rdf(self, *, source: bool = False) -> RdfExport: ...

    def rdf2vault(self, rdf: Sequence[RdfDocument], *, nest: bool = False) -> IngestReport: ...
```

These are target signatures, not implemented examples.
Define typed boundary models before implementing their consumers:

| Model | Contract |
| --- | --- |
| `RdfDocument` | Local RDF bytes and explicit `turtle` or `nt` format; no URL or plugin inference. |
| `RdfExport` | Separate schema/data Turtle documents plus diagnostics; no public RDFLib graph or maplib object. |
| `IngestReport` | Created, updated, and unchanged note paths, changed contexts, and diagnostics. |
| `Issue` | Severity, message, and source path when available; fatal failures use the package exception hierarchy. |

Put store settings, base-IRI overrides, context selection, and limits in validated Pydantic configuration rather than hidden globals.
Preserve the reference default: `vault2rdf(source=False)` creates query-only output; `source=True` includes the placement metadata needed for a roundtrip.
An empty RDF document sequence is invalid; ingesting a valid empty graph must not erase existing notes.
Retain the reference's context-synthesis behavior and diagnostics when ingesting into a fresh vault, including an empty graph.
`rdf2vault` preserves existing body content, non-context frontmatter, valid explicit IDs, and existing note locations.
Do not claim lossless conversion for arbitrary RDF: blank nodes, language tags, coercion mismatches, and untyped subjects require the reference's explicit loss diagnostics.

The CLI target is `oxivault vault2rdf VAULT --out-dir BUILD [--source]` and `oxivault rdf2vault VAULT RDF_FILE... [--nest]`.
Recognize `.ttl` and `.nt` inputs only in this phase.
Keep original upstream script names only in private source paths, provenance, and test-oracle documentation, not as public command aliases.

## TDD execution rule

For each behavior below, name the production mistake the test should catch and write one independent GIVEN/WHEN/THEN test with hand-derived expectations.
Parameterize equivalent data cases, not unrelated behaviors.
Observe the intended assertion failure before writing implementation; missing imports, test setup errors, or an already-passing characterization test are not the RED step.
If necessary, add only the importable interface skeleton before reaching the behavioral failure.
Implement the smallest reference adaptation or store behavior that passes, then refactor while keeping tests green.

Use real RDFLib/Polars behavior, `tmp_path` files, real disposable dulwich repositories, and moto for offline S3.
Do not mock conversion algorithms, RDF graphs, or query results.
Keep an untouched, pinned upstream oracle and example vault in `tests/reference/vault_ld/`, separate from adapted production code.
Only the test oracle may run the original CLI in a subprocess.
Characterize the upstream fixture once, then use differential checks alongside independently specified assertions for oxivault behavior.
Never compare two calls to the adapted implementation as the sole correctness oracle.

During RED/GREEN, run the narrow test selection for the behavior.
Before completing each implementation increment, run `uv run pytest` and `tara check`; stop on a red gate and report any baseline failures by name.
Inspect `tara standards` before filling tooling gaps rather than inventing parallel configuration.
Each completed increment includes a runnable example, directly related documentation, a changelog entry for user-facing behavior, and a handover note.

## Implementation increments

### 0. Establish the store and testing contract

Dependencies: none.
Files: `pyproject.toml`, `uv.lock`, `src/oxivault/{config,errors}.py`, `src/oxivault/store/`, `tests/store/`, and a small local-store example.

RED: parameterize the shared ObjectStore contract over Memory and LocalDir.
Cover list-prefix semantics, missing objects, metadata, atomic single-object replace, delete, bounded reads, invalid keys, and stale/create-only write preconditions as separate behaviors.
Test key containment and symlink refusal before writing filesystem access.
Test configuration failures and explicit unsupported-capability errors; never silently downgrade a conditional write to last-write-wins.

GREEN: implement the six-method protocol and the two smallest backends, plus Pydantic settings and a package exception hierarchy.
Specify opaque ETags, `datetime | None` LastModified, read limits, and conditional-write semantics at the protocol boundary.
Use an actual serialized check-and-replace mechanism for LocalDir's supported writer scope, not an unlocked stat-then-write claim of CAS.
Add pytest/ruff/ty/pre-commit tooling through the existing uv/Tara conventions; add runtime dependencies only when their first consumer lands.

Exit: the example writes and reads a note, both backends satisfy the same contract, and the full gate is green.

### 1. Deliver `vault2rdf` as the first conversion slice

Depends on increment 0.
Files: `_reference/vault_ld/vault_to_rdf.py`, `roundtrip.py`, `vault.py`, conversion models, `tests/test_vault2rdf.py`, `tests/reference/`, and `examples/roundtrip.py`.

RED: a hand-written vault containing a schema definition and linked instance exports the expected triples to the correct schema/data outputs through both LocalDir and Memory.
Then add one behavior at a time from the export rows of the conformance matrix below, including query-only versus `source=True` placement.
Exercise the public method and inspect parsed RDF, not serialized prefix ordering or a mocked call.
Test that a missing/invalid root context fails explicitly and a recoverable malformed note produces its diagnostic.
Test independent concurrent calls with different vault roots/base IRIs to catch leaked process state.

GREEN: adapt the pinned exporter in-process, introduce the bounded snapshot bridge, and expose `Vault.vault2rdf`.
Keep namespace options and warnings explicit rather than changing the reference mapping rules.
Add direct RDFLib and PyYAML dependencies and source attribution.

Exit: the example emits both Turtle documents; supported cases match the pinned reference semantically and satisfy independent expected triples.

### 2. Deliver `rdf2vault` and roundtrip fidelity

Depends on increment 1.
Files: `_reference/vault_ld/rdf_to_vault.py`, `roundtrip.py`, `vault.py`, `tests/test_rdf2vault.py`, `tests/test_roundtrip.py`, and the roundtrip example.

RED: ingest known Turtle/N-Triples into a fresh store and verify note paths, frontmatter, and synthesized contexts.
Test regeneration into an existing store separately for bodies, host keys, pinned IDs, existing placement, and no-op byte/ETag preservation.
Add context extension, file collisions, scoped placement hints, hierarchy, malformed RDF, refused input formats, remote-context refusal, and loss diagnostics one behavior at a time.
Test no destination writes on conversion failure, stale ETags at publication, and a later publication failure that exposes the already-applied paths.
Verify a valid empty graph preserves existing notes and that context synthesis on a fresh destination matches the reference.

GREEN: adapt the ingester into a callable function and publish its staged diff through ObjectStore.
Use the allowlisted in-process parsers without the upstream global network blocker.
Replace printed warnings and process exits with typed results/errors without silently changing recoverable mapping behavior.

Exit: `vault2rdf(source=True) -> rdf2vault -> vault2rdf(source=True)` preserves the representable graph and placement in the conformance corpus.
The example also demonstrates that bodies survive regeneration and a second ingest makes no writes.

### 3. Add the derived graph and conversion CLI

Depends on increment 2.
Files: `triples.py`, `graph.py`, `cli.py`, `__init__.py`, `tests/test_{triples,graph,cli}.py`, README, and examples.

RED: verify literal/IRI distinction, datatypes, note provenance, schema/data layers, deduplicated RDF semantics, SELECT bindings, and CONSTRUCT graph content.
Test neighbors/backlinks, literal/body search, and issue reports against hand-authored expected results.
Test note listing/read/write/delete through the Vault facade, including frontmatter validation, missing paths, and stale write preconditions.
Test that editing/deleting a note removes stale triples and changing a context or link target invalidates dependent results, including out-of-band store edits.
Invoke `vault2rdf` and `rdf2vault` through the CLI and check files, reports, and nonzero fatal-error exit status.

GREEN: keep `subject, predicate, object, datatype, lang, note_path, layer` as the canonical Polars derived table and build the query graph with RDFLib.
Capture note provenance at the reference export emission boundary; do not infer paths from IRIs or optional `vld:path` triples.
Preserve each note's provenance when several notes contribute the same triple, but use set semantics in the RDF query graph.
Normalize plain literals to `xsd:string`; IRI objects have no datatype/language, so the table retains term identity without guessing from string contents.
Use library serializers, not a hand-written N-Triples serializer.
Keep RDFLib objects private behind `graph.py` and conversion internals.
Implement the Vault note operations here so the later HTTP layer consumes the same storage, validation, and refresh behavior.

Initially invalidate the whole snapshot on changes and rebuild it through the reference exporter before the next graph read.
Before reusing an indexed snapshot, compare store metadata for notes, contexts, additions, and removals to detect external Obsidian/store edits.
Publish a rebuilt table/query graph together; a rebuild failure is an error, never a successful response with stale data.
Use Polars for structured browsing/search and RDFLib for SPARQL.
Do not add a backend-selection/plugin framework before the second backend exists.

Exit: the renamed CLI commands and graph example work using only the reference backend, and README documents query-only versus roundtrip export.

### 4. Add S3 and Obsidian sync

Depends on increment 3.
Files: `store/s3.py`, `sync.py`, `cli.py`, `tests/store/test_s3.py`, `tests/test_sync.py`, and a sync example.

RED: reuse store contract tests with boto3+moto, then test pagination, streaming limits, opaque ETags, conditional create/update, and endpoint configuration.
Exercise conversion against an S3-backed vault, including preservation of nested contexts and unchanged objects.
Test idempotent pull/push, manifest updates only after successful writes, interrupted runs, and conflict handling that preserves both contents.
Separate deletion-policy tests from update tests; do not infer remote deletion from an incomplete listing or failed read.

GREEN: add boto3 and moto, implement S3 and the `.oxivault/state.json` sync manifest, and wire sync into the CLI.
Preserve the conditional-write failure for callers and save a recoverable conflict copy without silently overwriting the concurrent remote edit.
Finalize which side receives the conflict copy and whether deletion propagation is supported before implementing those behaviors.
Use only offline fixtures and test credentials; live S3-compatible endpoint validation needs explicit approval.

Exit: a local Obsidian-style vault and moto-backed store converge without data loss, and retries/no-op sync do not generate spurious changes.

### 5. Add Git storage

Depends on increment 4 for the sync integration; its store-only portion needs increments 0-3.
Files: `store/git.py`, optional batch/versioning protocols, Git sync integration, `tests/store/test_git.py`, and a Git example.

RED: run the shared store contract against disposable bare and working-tree repositories.
Test blob-hash ETags, commit-time metadata, conditional writes, CAS ref conflicts, bounded retries, atomic multi-file ingest, no-op ingest producing no commit, and history.
Verify that unrelated dirty working-tree content is not overwritten, staged, or included in a library-generated vault commit.
Use temporary repositories only; tests must never commit or push the oxivault development repository.

GREEN: add pinned dulwich, implement both modes and the optional batch/versioning capabilities, and connect them to conversion publication.
Serialize writers within the supported deployment scope and use CAS ref updates as the final concurrency check.
Keep remote fetch/push and merge behavior behind the unresolved Git-remotes decision rather than silently treating it as implemented.

Exit: the same conversion example works against both Git modes, a batch ingest is one atomic commit, and concurrent writes cannot clobber each other.

### 6. Expose the design's HTTP API

Depends on increments 3-5 for the full backend matrix.
Files: `server.py`, server configuration, `tests/test_server.py`, and an API example/guide.

RED: use FastAPI's in-process client with real vaults for `/vault`, note CRUD, SPARQL, edges, search, issues, and reindex.
Test stale `If-Match` returning the design's 409 response, missing notes, malformed input, conversion failures, and failed rebuilds.
Test history only when the backend advertises versioning.
Check SELECT and CONSTRUCT response shapes independently; settle and document CONSTRUCT's JSON representation before implementing it.
Verify that SPARQL cannot initiate remote `SERVICE` access or mutate the graph; test the execution boundary, not only a text filter.

GREEN: declare FastAPI/uvicorn explicitly rather than relying on maplib transitives, and wire endpoints to the same Vault/graph operations used by the CLI.
Resolve auth, allowed origins, query limits, and the server-to-server deployment boundary before enabling non-local exposure.
If safe SPARQL execution cannot be established, keep that endpoint disabled rather than substituting a permissive passthrough.

Exit: the API example exercises notes and graph queries with the chosen response contract, and API errors remain explicit and consistent across backends.

### 7. Complete the reference-backed release candidate

Depends on increments 0-6 and their recorded decision gates.
Files: regression/integration tests, runnable examples, README/guides, changelog, and handover notes.

RED: fill remaining conformance-matrix gaps, especially bounded reads, snapshot cleanup, interrupted publication, conflict recovery, and process isolation.
Adapt relevant upstream security regression cases to the library boundary without treating upstream script tests alone as oxivault coverage.
Profile a documented fixed-size vault with the reference backend and record observed limits; agree performance budgets before adding threshold assertions.
Retain full-reference rebuilds unless measured need and dependency-invalidation tests justify incremental work.

GREEN: fix only demonstrated gaps, finish documentation and diagnostics, and run the full test suite, Tara gate, and runnable examples.
Review the resulting diff, licensing/attribution, dependency lock, and staged files; the developer commits and releases.

Exit: the Definition of Done below holds without maplib installed.

## Conformance and failure matrix

Each row is a family of independent tests, not one large roundtrip assertion.
Assign the export cases to increment 1, ingest cases to increment 2, and repeat only backend-specific integration behavior later.

| Behavior | Independent expected result |
| --- | --- |
| Composed contexts and scoped bases | Later term definitions win; a referenced ontology base does not replace the root instance base. |
| Keyword aliases and native scalars | Aliased `type`/`id`, strings, numbers, booleans, dates/dateTimes, and set-valued fields produce the expected RDF terms. |
| Identity and hierarchy | File stem plus governing base determines identity; folders imply neither identity nor hierarchy; valid absolute IDs override minting. |
| Wiki-link grammar | Alias/fragment are display-only; path-qualified names select the intended subject; ambiguous/dangling names emit diagnostics. |
| Layer split and host keys | Folder location selects schema/data; bodies and unmapped editor keys are absent from RDF; explicitly mapped host keys participate. |
| Placement | `source=True` carries necessary governing-context-relative `vld:path`; ingest consumes it into location, never frontmatter. |
| Regeneration | Existing paths, body content, non-context fields, and valid IDs survive; no-op ingest preserves object bytes and ETags. |
| Foreign RDF | Fresh contexts/terms are synthesized; unsafe stems and path collisions cannot escape or overwrite another subject. |
| Unrepresentable RDF | Blank nodes, language tags, coercion mismatches, and untyped subjects emit the appropriate diagnostics; only the independently specified representable result is asserted. |
| Diagnostics | Unmapped terms and schema/type mismatches are reported; fatal context/parse failures raise errors, not empty successful graphs. |
| Input isolation | Remote contexts, URL inputs, JSON-LD/unknown formats, traversal, symlinks, YAML aliases, and oversize reads cannot bypass the boundary. |
| Publication and refresh | Conversion failure publishes nothing; stale/create-only writes conflict; partial publication is reported and invalidates graph state. |

Use graph isomorphism for RDF assertions rather than Turtle byte order, prefix labels, or a triple count alone.
Use exact file-byte comparisons for promised no-op behavior, and assert diagnostics separately from graph content.
Differential tests must list intentional deviations from upstream: typed errors/reports, no process-global side effects, and the input-format allowlist.
Other SPEC/reference discrepancies become explicit regression cases and decisions, not silently copied bugs.

## Deferred maplib work

After the reference-backed contract is stable, benchmark representative workloads and propose a separate maplib increment.
Keep `vault2rdf`, `rdf2vault`, ObjectStore, result models, CLI names, and the conformance corpus unchanged.
Run the same representable-graph and SPARQL contract tests against both engines before switching defaults.
Only then add maplib and its load adapter; prefer supported serialization APIs before considering its internal struct layout.
Per-note caching needs tests for context changes, duplicate-name resolution, renames, and deletions before replacing whole-snapshot invalidation.
SHACL, Datalog, and commercial maplib FTS remain outside this plan.

## Decisions to close before the affected increment

The conversion names, in-process adaptation, and initial RDF formats are agreed.
The rest of the design remains proposed where not already confirmed.
Before increment 4, agree sync conflict-copy placement and deletion propagation.
Before Git remote sync, agree local-only versus remote repositories and merge/auth behavior.
Before increment 6, agree deployment/auth/origins, SPARQL limits and CONSTRUCT JSON shape, and whether Git history ships immediately.
Multi-vault tenancy, push notifications, S3 undo/versioning, and additional RDF formats need separate decisions; they are not implicit v1 commitments.
Record confirmed architectural decisions with `tara new adr` before implementing their affected surfaces.

## Definition of Done

- [ ] Each new behavior was observed failing before its implementation; independent tests cover the conformance and failure contracts.
- [ ] Local, Memory, S3, and Git integrations satisfy their advertised capabilities with offline tests.
- [ ] Public conversion methods and CLI commands are `vault2rdf` and `rdf2vault`, with documented query-only and roundtrip modes.
- [ ] RDFLib is private implementation detail; no maplib dependency or implementation is required.
- [ ] The full pytest suite, `tara check`, and runnable examples pass; any limitations are documented.
- [ ] README, API/sync guidance, changelog, attribution, and the dependency lock match the delivered behavior.
- [ ] No secrets, credentials, or local configuration are staged; reviewable changes and a commit-message draft are handed to the developer.
- [ ] `.agents/memory/` and its index describe what landed, what remains, and any unresolved decisions.
