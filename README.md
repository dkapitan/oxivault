# OxiVault

[![PyPI](https://img.shields.io/pypi/v/oxivault?cacheSeconds=300)](https://pypi.org/project/oxivault/)
[![Python](https://img.shields.io/pypi/pyversions/oxivault?cacheSeconds=300)](https://pypi.org/project/oxivault/)

Vault-LD knowledge graph store on object storage.

`oxivault` converts Obsidian-style Markdown vaults to and from RDF using in-process Vault-LD reference algorithms and RDFLib. It supports local files, in-memory stores, Git repositories, and S3-compatible object storage, with CLI, Python, and HTTP API interfaces.

## Upstream specification and reference credit

OxiVault builds on the original Vault-LD work from The Knowledge Graph Guys:

- Specification: [Vault-LD SPEC.md](https://github.com/The-Knowledge-Graph-Guys/vault-ld/blob/025e71be8d810e387dc451c920e05472d1c47170/SPEC.md)
- Reference implementation:
  - [scripts/vault_to_rdf.py](https://github.com/The-Knowledge-Graph-Guys/vault-ld/blob/025e71be8d810e387dc451c920e05472d1c47170/scripts/vault_to_rdf.py)
  - [scripts/rdf_to_vault.py](https://github.com/The-Knowledge-Graph-Guys/vault-ld/blob/025e71be8d810e387dc451c920e05472d1c47170/scripts/rdf_to_vault.py)

This project adapts those reference conversion algorithms for in-process library use under `src/oxivault/_reference/vault_ld/`.

## Features

- **Vault ↔ RDF conversion**
  - `vault2rdf`: export vault content to `schema.ttl` and `data.ttl`
  - `rdf2vault`: ingest RDF into Markdown notes with frontmatter
- **Graph access and querying**
  - Canonical triples table via `Vault.triples()` (Polars DataFrame)
  - SPARQL querying via `Vault.query()` and `oxivault query`
  - Graph exploration via `neighbors()`, `backlinks()`, `search()`, `search_body()`
- **Note CRUD with optimistic concurrency**
  - `get_note()`, `put_note()`, `delete_note()`, `list_notes()`, `exists_note()`
- **ObjectStore backends**
  - `LocalDirStore`, `MemoryStore`, `S3Store`, `GitStore`
- **Sync engine**
  - Two-way sync between local vaults and S3-compatible storage (`oxivault sync`)
- **HTTP API server (FastAPI)**
  - Endpoints for notes, graph querying, search, and reindexing

## Requirements

- Python **3.14+**
- For S3 sync/backends: credentials configured for your AWS or S3-compatible provider (through boto3/client configuration)

## Installation

```bash
pip install oxivault
```

With `uv`:

```bash
uv add oxivault
```

## CLI quick start

Export a vault to RDF:

```bash
oxivault vault2rdf ./my-vault --out-dir ./dist --source
```

Ingest RDF into a vault:

```bash
oxivault rdf2vault ./my-vault ./graph.ttl
```

Run a SPARQL query:

```bash
oxivault query ./my-vault "SELECT ?s ?p ?o WHERE { ?s ?p ?o } LIMIT 10"
```

Sync local vault with S3:

```bash
oxivault sync ./my-vault --bucket my-s3-vault --direction both
```

Start the HTTP API server:

```bash
oxivault serve ./my-vault --host 127.0.0.1 --port 8000
```

## Python quick start

```python
from oxivault.models import RdfDocument
from oxivault.store.memory import MemoryStore
from oxivault.vault import Vault

store = MemoryStore()
vault = Vault(store)

rdf = b"""@prefix ex: <https://example.org/> .
@prefix data: <https://example.org/data/> .

data:item1 a ex:Thing .
"""

vault.rdf2vault([RdfDocument(content=rdf, format="turtle")])

triples = vault.triples()
print(triples.select(["subject", "predicate", "object"]))
```

## HTTP API

Run:

```bash
oxivault serve ./my-vault --port 8000
```

Then use:

- `GET /vault`
- `GET /notes`
- `GET /notes/{path}`
- `PUT /notes/{path}`
- `DELETE /notes/{path}`
- `POST /graph/sparql`
- `GET /graph/edges?subject=...`
- `GET /graph/issues`
- `GET /search?q=...`
- `POST /reindex`

Interactive docs are available at `/docs`.

### API auth, CORS, presigned URLs, and publication helper

The FastAPI app now supports OAuth2 bearer tokens and session-cookie authentication with two roles.
Use `read` for audience and published-content access and `editor` for librarian write operations.
Read-only SPARQL filtering remains in place as defense-in-depth behind authentication and authorization.
CORS uses an explicit allow-list of front-end origins.
Presigned URLs support browser-direct uploads and temporary private downloads.
The publication helper updates note publication metadata and can optionally perform verified copy-based publication to a public bucket or prefix.
Authentication is opt-in for backward compatibility, so set `auth_enabled=True` in `ApiServerConfig` to enforce auth.

Example app wiring:

```python
from oxivault.server import ApiServerConfig, create_app

app = create_app(
    vault,
    config=ApiServerConfig(
        auth_enabled=True,
        read_tokens={"read-token"},
        editor_tokens={"editor-token"},
        users={
            "audience": {"password": "audience-password", "role": "read"},
            "librarian": {"password": "librarian-password", "role": "editor"},
        },
        cors_allowed_origins=["https://app.nalandabodhi.org"],
    ),
)
```

Additional endpoints:

- `POST /auth/token`
- `POST /auth/session/login`
- `POST /auth/session/logout`
- `POST /objects/presign-put`
- `POST /objects/presign-get`
- `POST /publication/notes/{path}`

## Conversion behavior and constraints

- Not all RDF graphs are representable as vault notes. Check reported conversion issues when ingesting/exporting.
- CLI RDF ingest accepts `.ttl` (Turtle) and `.nt` (N-Triples) files.
- `Vault.vault2rdf()` and `oxivault vault2rdf` default to query-oriented export. Use `source=True` (Python) or `--source` (CLI) to include placement metadata for roundtrip location fidelity.
- `Vault.rdf2vault([])` is invalid (empty sequence). Passing a non-empty sequence with an empty RDF document is valid.

## Examples

Runnable examples are in `examples/`:

- `examples/local_store_demo.py`
- `examples/roundtrip_demo.py`
- `examples/graph_query_demo.py`
- `examples/server_and_backends_demo.py`

Run an example:

```bash
uv run python examples/roundtrip_demo.py
```

## Development

```bash
uv sync --group dev
tara check
```

## Release and PyPI publishing

Publishing is automated with GitHub tags through `.github/workflows/publish.yml`.
Create and push an annotated SemVer tag to publish, for example:

```bash
git tag -a v0.1.0 -m "Release 0.1.0"
git push origin v0.1.0
```

The publish workflow builds source and wheel distributions and uploads them to PyPI with GitHub OIDC trusted publishing.
Configure a PyPI trusted publisher for `dkapitan/oxivault` that points to workflow `publish.yml` and environment `pypi`.

## License

Licensed under the Apache License 2.0. See [LICENSE](LICENSE).

## Project links

- Source: https://github.com/dkapitan/oxivault
- Issue tracker: https://github.com/dkapitan/oxivault/issues
- Changelog: [CHANGELOG.md](CHANGELOG.md)
