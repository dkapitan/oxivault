# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- API authentication with OAuth2 bearer token flow and session-cookie login, with `read` and `editor` roles.
- CORS allow-list configuration for front-end origins via `ApiServerConfig.cors_allowed_origins`.
- Presigned URL operations on object stores (`presign_put`, `presign_get`) with S3-compatible implementation in `S3Store`.
- API endpoints for presigned upload and download URL issuance (`/objects/presign-put`, `/objects/presign-get`).
- Publication helper endpoint (`/publication/notes/{path}`) that updates publication metadata and supports optional verified copy-based publication for compatible backends.
- ADR `docs/decisions/0001-publication-helper-for-master-library.md` documenting the publication helper design and rollout.

### Fixed

- Restored backward-compatible default server behavior by keeping auth disabled unless explicitly enabled in `ApiServerConfig`.
- Publication helper now avoids persisting publication metadata before copy-mode validation and copy execution succeed.
- Publication helper now maps missing/invalid source object errors to explicit 4xx responses instead of uncaught server errors.
- Session logout now revokes the current in-memory session token in addition to clearing the cookie.

## [0.1.0] - 2026-10-04

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
