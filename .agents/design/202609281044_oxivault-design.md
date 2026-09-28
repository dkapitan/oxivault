# oxivault: a Vault-LD knowledge graph store on object storage with maplib

Status: proposed design (investigation phase). Date: 2026-09-28.

## 1. Purpose

oxivault is a new implementation of the Vault-LD idea, with two deliberate
changes over the reference implementation:

1. The vault's physical location is any S3-compliant object store (AWS S3,
   MinIO, Cloudflare R2, Backblaze B2, Garage) or a git repository, not
   just a local directory.
2. The RDF engine is maplib (Rust, Arrow/Polars, Oxigraph-derived SPARQL)
   instead of rdflib, for performance.

The product is a knowledge graph store consumed by two very different
frontends:

- **Obsidian (local)**: an Obsidian vault is a local directory. oxivault keeps
  a local working copy in step with the object store (pull/push sync).
- **SvelteKit web app**: a backend service exposes the vault as a knowledge
  graph API (notes CRUD, graph queries, search, validation) over HTTP/JSON.

The Vault-LD format itself is not invented here; it is the contract. The vault
is a tree of Markdown notes whose YAML-LD frontmatter, resolved through a
composed `context.jsonld`, projects deterministically to an RDF graph. One
note, one subject; frontmatter is the triples, body is prose (never triples);
identity mints from the file name alone against a governing `@base`; wiki
links are the edges; export and ingest are inverse roundtrip faces.

## 2. Investigation findings

### 2.1 Vault-LD reference (The-Knowledge-Graph-Guys/vault-ld, v0.5.x)

- **SPEC.md** (normative, Apache-2.0) defines the format: composed context
  resolution (JSON-LD array semantics, later entries override), keyword
  aliases (`type` -> `@type`, `id` -> `@id`), the field-naming contract
  (object properties and `@type` are wiki links, datatype properties are
  scalars, keys are bare short aliases), wiki-link grammar (path
  disambiguates, alias and fragment are display-only), name-minted identity
  with percent-encoding, `vld:path` placement roundtripping, layer split
  (schema under `Ontologies/` and `Vocabularies/`, instances elsewhere),
  host-tool keys (`tags`, `aliases`, `cssclasses`) deliberately outside the
  graph, conformance criteria.
- **Reference tooling**: `scripts/vault_to_rdf.py` (export) and
  `scripts/rdf_to_vault.py` (ingest), ~1650 lines combined, built on rdflib
  + PyYAML. Both implement the context composition, minting, link
  resolution, and placement rules by hand (no JSON-LD library).
- **The reference already does the hard parts well**: bounded reads (size
  caps bind before bytes are in memory), YAML alias refusal (billion-laughs
  protection), context composition confined to the vault tree (no SSRF, no
  network), symlink and path-escape containment, `sanitize_stem` for
  untrusted foreign IRIs on ingest, warnings instead of silent drops.
  These are ported, not reinvented.
- **Licence**: Apache-2.0. Porting logic with attribution is compatible;
  oxivault should be Apache-2.0 too.

### 2.2 maplib (DataTreehouse/maplib, 0.20.26) — verified by spike on Python 3.14

- Rust core, Apache Arrow, Polars DataFrames (zero-copy results), SPARQL via
  Oxigraph-derived engine. In-memory; the docs claim ~100M triples on 32 GB.
- **Wheels**: `cp310-abi3` for macOS arm64/x86_64 and manylinux_2_28
  aarch64/x86_64. abi3 means it installs and imports on the project's
  Python 3.14 baseline (verified: imports clean).
- **Verified in a throwaway spike** (macOS arm64, Python 3.14):
  - `Model.reads(ntriples|turtle)` parses RDF strings (battle-tested path).
  - `Model.query()` runs SPARQL SELECT and CONSTRUCT, returns Polars
    DataFrames; `return_format="json"` available for API responses.
  - `Model.writes(format="turtle")` serializes the graph.
  - `Model.map_triples(SolutionMappings)` ingests a DataFrame. Mixed
    IRI/literal objects need a struct column; the field layout is an
    internal detail (`I`, `l`, `<datatype-IRI>`, `<langString>`) that we
    reverse-engineered and verified works.
  - Performance: 45k triples load in 0.06 s, a 2-hop path query in 3 ms,
    Turtle serialization in 0.02 s, a full rebuild in 0.06 s. At vault
    scale (thousands of notes, tens of thousands of triples) the graph
    engine is not the bottleneck; YAML frontmatter parsing is.
- **Constraints that shape the design**:
  - No triple-level deletion. Mutation is whole-graph (`truncate_graph`,
    `detach_graph`) or a fresh `Model`. Incremental updates must happen in
    our layer (per-note triple cache), with the maplib `Model` rebuilt when
    needed. Rebuilds are cheap at vault scale.
  - SHACL validation, Datalog, and full-text search are **commercial**
    (closed source; `IndexingOptions(fts=True)` panics with "Contact Data
    Treehouse to enable full text search"). oxivault v1 must not depend on
    any of them. Text search is done ourselves (Polars string search over
    literals + note bodies).
  - maplib depends on `rdflib>=7.6` (transitive; used internally for some
    serialization formats) and `fastapi` (which we reuse for the API
    server). oxivault code never touches rdflib directly; maplib replaces
    it for parsing, storage, query, and serialization.
  - maplib is young (0.20.x). The struct-column layout for mixed objects is
    internal and could change; pin the maplib version and keep the primary
    load path independent of that layout (see 4.4).

## 3. Architecture overview

```
                    ┌──────────────────────────────────────────────┐
                    │            ObjectStore (protocol)           │
                    │  list(prefix) get(key) put(key,bytes)       │
                    │  delete(key) stat(key) exists(key)          │
                    └───────┬──────────────┬──────────────┬───────┘
                    LocalDir │         S3 (boto3)    Git (dulwich)  Memory
               (Obsidian copy)│  S3/MinIO/R2/B2/Garage   repo: working  (tests)
                             │        or Cloud S3      tree or bare
                             │
                    ┌───────▼──────────────────────────────────────┐
                    │                oxivault core                 │
                    │  Vault ── context resolve ── frontmatter    │
                    │  ── identity mint ── wiki-link resolve      │
                    │        │                                     │
                    │        ▼                                     │
                    │  triple DataFrame (Polars, canonical form)   │
                    │        │                                     │
                    │        ├──► maplib Model ──► SPARQL          │
                    │        └──► DataFrame queries (neighbors,    │
                    │             backlinks, search, validation)   │
                    └───────┬──────────────────────────┬──────────┘
                            │                          │
                    CLI (sync,      FastAPI server (JSON API
                    export, import,  for the SvelteKit app)
                    query, serve)
                            │                          │
                    Obsidian (local dir)      SvelteKit web app
```

The vault's physical location is whichever backend is configured: a local
directory, an S3-compliant bucket, or a git repository. Notes and
`context.jsonld` are objects keyed by their vault-relative POSIX paths. There
are no directories in S3 (only key prefixes) and none in git (only trees);
the store abstraction hides both.

## 4. Design decisions

### 4.1 Own `ObjectStore` protocol, not fsspec/s3fs

A small protocol (`list`/`get`/`put`/`delete`/`stat`/`exists`) with four
implementations: `LocalDir` (pathlib, for the Obsidian working copy and for
tests), `S3` (boto3; `endpoint_url` for MinIO/R2/B2/Garage; paginated list;
ETag and LastModified surfaced for change detection and conditional writes),
`Git` (dulwich; section 4.9), and `Memory` (tests).

Why not fsspec/s3fs: the abstraction would hide exactly the S3 semantics we
need (ETags, conditional writes with If-Match, no directory model, list
pagination). fsspec's local and S3 filesystems behave differently in the
details, and its directory handling is quirky. boto3 is the de facto
standard, supports every S3-compliant store via `endpoint_url`, and surfaces
the primitives we need directly. The protocol is ~6 methods; the S3
implementation is the only place S3-specific concerns live.

Driver choice (verified 2026-09-28): boto3 over the minio SDK. The deciding
fact is conditional writes: boto3's `put_object(IfMatch=...)` is the
mechanism behind the API's optimistic concurrency (PUT with If-Match -> 409),
while minio-py 7.2.20 has no conditional-write support at all (no if_match /
if_none_match parameters, no extra_headers on `put_object`). Without it,
concurrent web edits silently last-write-win. The minio SDK is lighter
(~400 KB vs the boto3 stack) and maps 1:1 onto the protocol, and is the
fallback if last-write-wins is ever accepted for the web app: swapping the
S3 implementation behind the protocol is a contained change. Secondary
factors: moto gives boto3 offline tests (minio-py needs a live server), and
boto3 is the neutral AWS-standard client every S3-compatible store
documents against.

The protocol contract is deliberately small: keys are POSIX relative paths;
`put` is an atomic replace; `stat` returns `(etag, last_modified, size)`
where `last_modified` is best-effort (`datetime | None`) because git has no
file mtimes. Conditional writes (a `put` carrying the expected ETag) are
optional per backend: S3 via `If-Match`, git via blob-hash comparison,
LocalDir via mtime+size, Memory trivially. This keeps every consumer (sync,
API, roundtrip) written once against the contract, with backend-specific
behaviour confined to the implementations.

### 4.2 The triple DataFrame is the canonical derived form

After parsing notes, the intermediate is a Polars DataFrame:
`subject, predicate, object, datatype, lang, note_path, layer`. It is the
source of truth for everything oxivault does itself (neighbors, backlinks,
dangling-link and validation reports, search) and the input to the maplib
Model. This aligns with the project rule to prefer Polars and keeps maplib
as one query engine among two, not the only representation.

Why two engines: the web app's bread-and-butter queries (node view, graph
edges, type browse, backlinks) are Polars filters over the DataFrame, no
SPARQL involved. Full SPARQL (arbitrary path queries, construct) goes through
the maplib Model. Keeping the DataFrame primary also gives us a clean
incremental-update story given maplib's lack of triple deletion.

### 4.3 Incremental updates: note-level triple cache, Model rebuilt

maplib cannot delete individual triples. oxivault therefore caches the
triples of each note (DataFrame keyed by `note_path`), and:

- a note change re-parses one note, recomputes its triples, and updates the
  DataFrame by subject (set-based);
- the maplib Model is rebuilt from the DataFrame when a SPARQL query runs
  after changes (a full rebuild of 45k triples took 0.06 s in the spike),
  or lazily invalidated by a dirty flag.

This keeps the graph correct without depending on maplib mutation APIs.

### 4.4 Loading triples into maplib: N-Triples as the primary path

Two verified options:

1. **Primary**: serialize our triple DataFrame to N-Triples (a ~30-line
   serializer we own: IRI escaping, literal escaping, `^^<datatype>`, `@lang`)
   and call `Model.reads(ntriples)`. Robust, uses maplib's tested parser,
   and does not couple us to maplib internals.
2. **Optimization (documented, version-pinned)**: `map_triples` with the
   struct object column (`I`, `l`, `<datatype-IRI>`, ...). Zero-copy, but
   the field layout is internal to maplib; guard with a pinned-version test.

Start with 1; adopt 2 only if profiling shows the serialization matters.

### 4.5 Sync for the Obsidian consumer

Obsidian edits a local directory. The object store is the physical vault.
`oxivault sync` keeps them in step:

- `pull`: materialize the store into a local working copy (idempotent).
- `push`: upload locally changed notes; detect changes by comparing ETags
  against a local manifest (`.oxivault/state.json` in the working copy).
- Conflicts: writes use conditional `If-Match`; a store-side conflict
  surfaces as a 409, and the CLI saves the incoming version as
  `<name>.conflict.md` rather than destroying either side (last-write-wins
  plus a recoverable copy). Note-level granularity only; vault files are
  independent, so this is safe.
- S3 (AWS since Dec 2020, MinIO, R2) is strongly consistent; no
  read-after-write staleness to work around.
- Alternative for users who prefer it: Obsidian's Remotely Save plugin syncs
  a local vault directly to S3; then oxivault's S3 store is the common
  ground and the `sync` CLI is only needed for the web-app side. Both work;
  the store abstraction is what makes it a non-decision at the engine level.
- With a git-backed vault, sync is git: the Obsidian directory is the
  repository's working tree, `oxivault sync` commits local changes and
  pushes (and pulls and merges), and conflicts are ordinary git merge
  conflicts surfaced the way obsidian-git reports them. The manifest/ETag
  machinery above is the S3 path; git tracks changes itself.

### 4.6 API server for the SvelteKit app

FastAPI (already a maplib dependency) with versioned JSON endpoints (section
6). The SvelteKit app calls the API server-side (keeping secrets and
credentials out of the browser); CORS is configured for the app's origin.
SPARQL passthrough gives the app full graph power; structured endpoints cover
the common cases without SPARQL.

### 4.7 Search without maplib FTS

v1 search: Polars string matching over literal objects in the triple
DataFrame plus a simple full-text pass over note bodies (bounded reads).
At vault scale this is fine. maplib's FTS is commercial and is not used. If
body search needs to scale later, add Tantivy or SQLite FTS behind the same
`search()` interface.

### 4.8 Security posture (ported from the reference implementation)

- Bounded reads: size caps bind before bytes enter memory (context docs,
  frontmatter prefixes, note bodies for search).
- YAML alias refusal in frontmatter (billion-laughs).
- Context composition confined to the vault tree; remote context references
  refused; no network I/O during export.
- Symlink and path-escape containment on the local store; key validation on
  S3 (no absolute keys, no `..` segments, no backslashes).
- `sanitize_stem`-style neutralisation of untrusted foreign IRIs on ingest.
- S3 credentials from environment/roles only, never from the vault.

### 4.9 Git repository as a storage backend

The vault can also live in a git repository. The Git backend implements the
same `ObjectStore` protocol, so nothing above the store changes: context
resolution, graph projection, roundtrip, sync, and the API are store-agnostic
by construction.

**Two modes.** Working-tree mode: the vault is the repository's working tree,
which is exactly the Obsidian directory; reads come from the tree, writes
stage and commit. Bare mode: no working tree at all; writes are plumbing
(new blobs, rebuilt trees, a commit, a ref update), which suits the web-app
backend or CI. Verified in a spike on Python 3.14 (dulwich 1.2.15): bare-repo
init, put/get/list/delete via tree surgery and commits, ETag change on
rewrite, history preserved.

**Protocol mapping.**

- `get(key)` / `list(prefix)` — tree walk of HEAD, blobs read from the object
  store; a full vault scan is one tree walk plus batched blob reads.
- `put(key, bytes)` — new blob, tree rebuilt along the path, commit, ref
  update. A batch of puts (one API request writing several notes) is one
  commit, hence atomic.
- `delete(key)` — tree rebuilt without the path, committed; the deleted
  object stays in history.
- `stat(key)` — ETag is the blob hash: content-addressed and deterministic,
  ideal as the conditional-write key. LastModified is the committer time of
  the last commit touching the path; git has no file mtime, hence the
  best-effort `last_modified` in the protocol contract.
- Conditional writes — the current blob hash is the If-Match value: a `put`
  whose expected ETag differs from the current blob is refused with a
  conflict, same as the S3 path.

**Concurrency.** Every write moves HEAD. dulwich ref updates are
compare-and-swap (`refs.set_if_equals(old, new)`), so two concurrent commits
cannot silently clobber each other: the loser's CAS fails, it re-reads HEAD,
rebuilds its trees, and retries. The API server still takes a per-repo write
lock (in-process or `fcntl` across processes) to keep retries rare; S3 needs
no such lock. Git-backed deployments prefer a single writer.

**Why git wins where S3 does not.** Versioning is inherent: every write is a
commit, so undo and history are `git log`/`git show` for free, with no S3
object versioning needed (open question 5). A batch of changes is one atomic,
reviewable commit. And for Obsidian the vault is itself a real git repo,
diffable and pushable to GitHub/GitLab, matching the vault-ld README's own
pitch.

**Costs.** No file mtimes (commit time only). Binary blobs bloat the repo,
but vault files are small Markdown; git LFS is out of scope. Library choice:
dulwich (pure Python, object-level access, Rust-accelerated wheel, no
subprocess) over GitPython (spawns `git` per call, slow for bulk reads and
writes) and pygit2 (fastest but a native libgit2 dependency). Pin dulwich.

## 5. Module layout

```
src/oxivault/
  store/            ObjectStore protocol; Local, S3, Git, Memory implementations
  context.py        composed @context resolution (port of reference logic)
  frontmatter.py    bounded YAML-LD frontmatter parse (port)
  identity.py       name minting, wiki-link resolution, vld:path (port)
  triples.py        canonical triple model + N-Triples serializer
  graph.py          VaultGraph: triple DataFrame + maplib Model, refresh
  roundtrip.py      export to Turtle, ingest from RDF (maplib reads)
  vault.py          Vault facade: notes, snapshot, change detection, sync
  server.py         FastAPI app (v1 endpoints)
  cli.py            oxivault CLI (init, sync, export, import, query, serve)
  config.py         Pydantic BaseSettings (store URI, creds, limits)
```

Ports stay close to the reference logic (context merge, minting, link
resolution, placement, fidelity rules) but emit/consume the triple DataFrame
instead of an rdflib Graph. Conformance criteria from SPEC section 6 become
tests, run against the reference example vault.

## 6. API surface (v1)

- `GET  /vault` — context, base IRIs, note and triple counts.
- `GET  /notes?prefix=` — list notes (path, minted IRI, ETag, layer).
- `GET  /notes/{path}` — note: frontmatter, body, minted IRI, its triples,
  inbound links (backlinks).
- `PUT  /notes/{path}` with `If-Match` — create/update; validates frontmatter
  against the composed context; reindexes the note.
- `DELETE /notes/{path}` — delete note and its triples.
- `GET  /notes/{path}/history` — (versioned backends only: git) commit
  history for a note; exposed via an optional `VersionedStore` extension of
  the protocol, so a backend without versioning (S3 without object
  versioning) simply does not advertise it.
- `POST /graph/sparql` — SPARQL SELECT/CONSTRUCT passthrough, JSON result.
- `GET  /graph/edges?subject=` — outbound and inbound edges (DataFrame path).
- `GET  /search?q=` — literal and body search.
- `GET  /graph/issues` — dangling links, unmapped fields, ambiguity warnings,
  schema-folder type mismatches (the reference tool's warning surface,
  exposed as an API).
- `POST /reindex` — rebuild the triple DataFrame and maplib Model.

## 7. Dependencies to add

maplib (pinned, e.g. `==0.20.26`), polars, boto3, dulwich (pinned, e.g.
`==1.2.15`), fastapi (transitive via maplib; declare explicitly for the
server), uvicorn, pydantic-settings. Dev: pytest, ruff, ty, moto (offline
S3 tests). PyYAML stays (frontmatter parsing). rdflib is not a direct
dependency.

## 8. Risks

- **maplib is young and partially commercial.** Pin the version; keep the
  load path off its internal layouts; never rely on SHACL/Datalog/FTS.
- **maplib is in-memory.** Vault-scale is fine; a pathological multi-million-
  note vault would need a different engine. Not a v1 concern.
- **Porting subtlety.** Context composition and minting edge cases are easy
  to get subtly wrong. Mitigation: differential tests against the reference
  scripts on the example vault, plus roundtrip-fidelity tests.
- **Sync conflicts.** Mitigated by conditional writes and conflict copies.
- **Git backend concurrency.** Writes move HEAD; mitigated by compare-and-swap
  ref updates plus a per-repo write lock; git-backed deployments prefer a
  single writer.
- **dulwich maturity.** Pure-Python git (Rust-accelerated wheel), pinned;
  keep the plumbing in one module so swapping to pygit2 is contained if it
  ever matters.
- **No mtimes in git.** LastModified is commit time; consumers must not rely
  on file mtimes for ordering.
- **YAML-LD nuances** (keyword aliases, composed bases, scoped `@base` per
  ontology): handled by porting the reference logic verbatim rather than
  re-deriving it.

## 9. Open questions for the developer

1. Deployment target: self-hosted MinIO, or a cloud S3? Drives credential
   handling and whether bucket notifications (SQS) are available for
   change events.
2. Multi-vault: one bucket + prefix per vault? Per user (multi-tenancy for
   the web app)? v1 can assume one vault per store root; the protocol does
   not preclude prefix-scoped vaults.
3. Auth for the API: v1 behind the SvelteKit app (server-to-server), or
   direct browser access with its own auth?
4. Real-time updates for the web app: polling on ETags, or bucket
   notifications -> queue -> API push (WebSocket/SSE)?
5. Undo/history: free for git-backed vaults (every write is a commit); S3
   object versioning is the option for S3-backed vaults. Ship the history
   endpoints in v1 or later?
6. Git remotes: for git-backed vaults, does the web backend write to a repo
   on its own disk (bare mode) or clone from and push to a remote
   (GitHub/GitLab)? The former is simpler; the latter adds push/pull and
   auth to the backend.

## 10. Proposed ADRs (record once the design is confirmed)

1. Object storage abstraction: own `ObjectStore` protocol over fsspec.
2. maplib as the graph engine; triple DataFrame as canonical derived form.
3. N-Triples as the primary maplib load path; struct path version-pinned.
4. Rebuild-not-patch update strategy (maplib has no triple deletion).
5. FastAPI server as the SvelteKit integration surface.
6. Search via Polars in v1, no maplib FTS (commercial).
7. boto3 + moto as the S3 driver and offline test harness (If-Match
   conditional writes; the minio SDK lacks them).
8. Git repository as a storage backend behind the ObjectStore protocol,
   implemented with dulwich (working-tree and bare modes).

## 11. Increments

- **0**: package skeleton, `ObjectStore` protocol + Local/Memory, config,
  tests.
- **1**: port context resolution, frontmatter parse, identity, wiki-link
  resolution; differential tests against reference scripts + example vault.
- **2**: triple DataFrame + N-Triples serializer + maplib Model + SPARQL
  query + Turtle export.
- **3**: ingest (foreign RDF -> vault notes, maplib `reads`), validation
  report surface.
- **4**: S3 store + sync CLI (pull/push/conflict) + change detection.
- **5**: Git repository backend (dulwich): working-tree and bare modes, CAS
  commits, conditional writes, `VersionedStore` extension + history
  endpoints.
- **6**: FastAPI server, API v1 endpoints, docs for the SvelteKit app.
- **7**: incremental reindex, search polish, hardening review, README.