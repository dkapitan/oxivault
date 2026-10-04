# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- S3 storage backend (`S3Store`) with boto3, supporting conditional writes (`IfMatch`, `IfNoneMatch`) and pagination.
- Obsidian sync engine (`SyncEngine`, `oxivault sync`) with state manifest (`.oxivault/state.json`) and conflict preservation (`.conflict.md`).
- Git storage backend (`GitStore`) using dulwich in bare and working-tree modes with commit history.
- FastAPI REST API server (`create_app(vault)`, `oxivault serve`) exposing `/vault`, `/notes`, `/graph/sparql`, `/graph/edges`, `/search`, and `/reindex`.
- Note CRUD operations on `Vault` facade (`get_note`, `put_note`, `delete_note`, `list_notes`, `exists_note`) with frontmatter validation and optimistic concurrency support.
- Graph query and navigation operations on `Vault` facade (`neighbors`, `backlinks`, `search`, `search_body`, `issues`).
- Store change detection and fingerprint-based cache invalidation for derived graph and triples DataFrame.
- CLI `query` command for running SPARQL queries directly against a local vault.
- Runnable examples `graph_query_demo.py` and `server_and_backends_demo.py`.
- Configurable aggregate snapshot limit, defaulting to 256 MiB.
- Partial-publication errors identifying the failed path and already-applied paths.


### Fixed

- Snapshot publication now uses conditional writes instead of overwriting concurrent edits.
- Snapshot paths and staged symlinks are checked, reads are bounded, and full-vault byte copies are no longer retained in memory.
- Context byte limits apply per call to composed local documents and generated contexts.
- Local write failures clean up temporary files; unconditional writes skip hashing old contents.
- Language-tagged literals retain `rdf:langString` in the derived table.
- Python exports default to query-only output, matching the CLI; empty RDF document sequences are rejected.
- The CLI rejects unsupported extensions and reports conversion and filesystem errors without unhandled tracebacks.
- Corrected adapter type annotations and replaced fatal context process exits with package errors.
