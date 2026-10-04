# oxivault

Vault-LD knowledge graph store on object storage.

`oxivault` converts Obsidian-style Markdown vaults to and from RDF using the in-process Vault-LD reference algorithms and RDFLib.
Maplib remains deferred.
Not all RDF is representable in a vault; inspect conversion issues for reported losses.

## Features

- **ObjectStore Abstraction**: Backends for local directory (`LocalDirStore`), in-memory (`MemoryStore`), and upcoming S3/Git storage.
- **Conversion**:
  - `vault2rdf`: Export markdown vault notes and contexts to standard RDF Turtle documents (`schema.ttl`, `data.ttl`).
  - `rdf2vault`: Ingest RDF graph triples into markdown notes with frontmatter, preserving body content and non-LD metadata.
- **Derived Triples & SPARQL**:
  - `Vault.triples()` returns a canonical Polars DataFrame (`subject`, `predicate`, `object`, `datatype`, `lang`, `note_path`, `layer`) with note provenance.
  - `Vault.query()` provides SPARQL query capabilities across the vault.
  - `Vault.neighbors()`, `Vault.backlinks()`, `Vault.search()`, and `Vault.search_body()` provide graph exploration and text search.
  - `Vault.get_note()`, `Vault.put_note()`, `Vault.delete_note()`, and `Vault.list_notes()` provide note CRUD with optimistic concurrency and cache invalidation.
- **CLI Commands**:
  - `oxivault vault2rdf <VAULT_DIR> [--out-dir BUILD] [--source]`
  - `oxivault rdf2vault <VAULT_DIR> <RDF_FILES>... [--nest]`
  - `oxivault query <VAULT_DIR> "<SPARQL_QUERY>"`

## CLI Usage

Export a vault to RDF:

```bash
uv run oxivault vault2rdf ./my-vault --out-dir ./dist --source
```

Ingest RDF into a vault:

```bash
uv run oxivault rdf2vault ./my-vault ./graph.ttl
```

Execute a SPARQL query against a vault:

```bash
uv run oxivault query ./my-vault "SELECT ?s ?p ?o WHERE { ?s ?p ?o } LIMIT 10"
```


Only `.ttl` (Turtle) and `.nt` (N-Triples) inputs are accepted.
Errors produce a nonzero exit status with a CLI error message rather than an unhandled traceback.

## Python conversion contract

`Vault.vault2rdf()` defaults to query-only output without placement metadata, matching the CLI.
Use `Vault.vault2rdf(source=True)` or `--source` when note placement must survive re-ingest.
`Vault.rdf2vault([])` is invalid; a supplied empty RDF document is valid and does not delete notes.
Run `uv run python examples/roundtrip_demo.py` for body preservation and no-op regeneration.

```python
from oxivault.config import VaultConfig
from oxivault.store.local import LocalDirStore
from oxivault.vault import Vault

vault = Vault(
    LocalDirStore("./my-vault"),
    VaultConfig(
        max_read_bytes=10 << 20,
        max_context_bytes=4 << 20,
        max_snapshot_bytes=256 << 20,
    ),
)
export = vault.vault2rdf(source=True)
```

These positive byte limits are the defaults even when no configuration is supplied.
The context limit applies to local referenced documents regardless of filename, as well as generated contexts.
Snapshot reads enforce the aggregate budget before allocating object content; staged output is checked again before publication.
The temporary directory is not a disk quota during conversion.

Ingest publishes only changed files, using captured ETags for updates and create-only preconditions for new paths.
It rejects unsafe keys and staged symlinks, and keeps content hashes rather than a second copy of the vault in memory.
Publication is not a multi-object transaction: `PublicationError.failed_path` and `PublicationError.applied_paths` identify partial progress, while `__cause__` preserves the underlying failure.
LocalDir conditional writes serialize writers sharing the same store instance; they do not lock external editors or other processes.
No claim of an atomic whole-vault snapshot is made.
