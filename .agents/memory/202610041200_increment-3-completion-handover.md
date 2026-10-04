# Increment 3 Completion Handover: Graph Queries, Invalidation & Note CRUD

Date: 2026-10-04.

## Completed Work

1. **Increment 3 Completion**:
   - `src/oxivault/graph.py`: Added `VaultGraph` encapsulating derived RDFLib `Graph` and Polars triple `DataFrame`. Implemented `neighbors` (outbound/inbound), `backlinks`, and literal search.
   - `src/oxivault/models.py`: Added `Note` dataclass holding path, frontmatter dict, body, and ETag.
   - `src/oxivault/vault.py`:
     - Note CRUD methods: `get_note`, `put_note` (with `@type`/`type` validation and optimistic concurrency preconditions), `delete_note`, `list_notes`, `exists_note`.
     - Graph queries: `neighbors`, `backlinks`, `search`, `search_body`, `issues`.
     - Invalidation: Fingerprint-based store change detection on `.md` and `context.jsonld` ETags; auto-rebuilds and cache invalidation on mutations or external store edits.
   - `src/oxivault/cli.py`: Added `oxivault query <VAULT_DIR> "<SPARQL_QUERY>"` command with clean error handling.
   - `examples/graph_query_demo.py`: Created runnable example demonstrating Polars triples table, SPARQL queries, backlinks/edges, and Note CRUD.
   - `tests/test_graph.py` and `tests/test_cli.py`: Full suite covering note CRUD lifecycle, frontmatter validation, cache invalidation, SPARQL execution, and search.
   - `README.md` & `CHANGELOG.md`: Updated with all new Increment 3 capabilities.
   - Tara check gate: 95 tests pass, lint/format/types/security audits clean.

## Next Steps

- **Increment 4**: Add S3 storage backend (`store/s3.py`) with `boto3` and `moto`, and Obsidian sync manifest integration (`.oxivault/state.json`).
- **Increment 5**: Add Git storage backend (`store/git.py`) with `dulwich`.
