# Increments 0-3 implementation handover

Date: 2026-10-02.
Audience: developers continuing oxivault implementation.

## What was done

1. **Increment 0 (ObjectStore Contract & Storage Backends)**:
   - Defined `ObjectStore` protocol with `StatInfo`, atomic `put` (with conditional write support), bounded `get`, `list`, `delete`, `stat`, and `exists`.
   - Implemented `MemoryStore` and `LocalDirStore` (POSIX key containment, atomic write-replace).
   - Added unit test suite in `tests/store/test_store_contract.py` (32 tests passing).

2. **Increment 1 (`vault2rdf` conversion)**:
   - Adapted pinned Vault-LD reference export in-process as `vault2rdf_reference`.
   - Created `materialize_vault_snapshot` bridging `ObjectStore` and temporary directory snapshots.
   - Exposed `Vault.vault2rdf(source=True/False)` returning `RdfExport` with schema/data Turtle documents and issues.
   - Verified with unit tests in `tests/test_vault2rdf.py`.

3. **Increment 2 (`rdf2vault` conversion & roundtrip)**:
   - Adapted pinned Vault-LD reference ingest in-process as `rdf2vault_reference`.
   - Enhanced `materialize_vault_snapshot(sync_back=True)` to track and write back added and modified notes into `ObjectStore`.
   - Exposed `Vault.rdf2vault(rdf, nest=False)` returning `IngestReport`.
   - Added roundtrip and body preservation tests in `tests/test_rdf2vault.py` and runnable `examples/roundtrip_demo.py`.

4. **Increment 3 (Canonical Polars triple table, RDF query & CLI)**:
   - Implemented `triples.py` with `build_triple_dataframe` creating a canonical Polars DataFrame with columns: `subject, predicate, object, datatype, lang, note_path, layer`.
   - Implemented `Vault.triples()` and `Vault.query()` (SPARQL queries against combined graph).
   - Created CLI (`src/oxivault/cli.py`) exposing `vault2rdf` and `rdf2vault` commands.
   - Added tests in `tests/test_triples.py` and `tests/test_cli.py`.

## Status

This recorded the initial 40-test implementation, not completion of all increment exit criteria.
The subsequent review found correctness and safety defects, addressed by the 2026-10-04 review fixes.
The wider plan still needs conformance coverage, graph/CRUD and refresh work from increment 3, and the decision gates before S3/sync.

## Review fixes: 2026-10-04

Addressed all nine review findings with regression coverage.
Snapshot publication now checks ETags and create-only preconditions, rejects unsafe paths and symlinks, retains hashes instead of full contents, and reports partial progress through `PublicationError`.
Added the approved 256 MiB aggregate snapshot budget and wired per-call context limits through the reference readers and context writers.
Corrected export defaults, empty-ingest validation, language-literal datatypes, local temporary-file cleanup, CLI failures, and adapter typing.
Also bounded LocalDir reads against file growth after stat.

Validation: 91 tests pass; `tara check --no-fail-fast` passes lint, format, types, tests, and dependency audit.
The roundtrip example preserves Markdown bodies and confirms a repeated ingest leaves object metadata unchanged.
README and changelog describe limits and the non-atomic publication contract.
No release, commit, or push was performed.
Suggested commit message: `fix: harden reference conversion and snapshot publication`.
