# oxivault: a Vault-LD knowledge graph store on object storage

Status: proposed design with a reference-first implementation plan.
Date: 2026-09-28; updated 2026-10-02.

## Implementation baseline update: 2026-10-02

The [reference-first TDD plan](../plan/202610020723_reference-first-tdd-implementation.md) is the current implementation sequence.
Use the pinned Vault-LD reference algorithms, adapted into in-process functions, with RDFLib as the initial RDF engine.
The public conversion methods and CLI commands are `vault2rdf` and `rdf2vault`.
Initially accept Turtle and N-Triples input; other RDF formats await network-free in-process parsing.
The upstream process-wide network blocker must not be imported into the host application's behavior.
Retain ObjectStore, Polars, sync, and the API design; defer maplib and its load/cache optimizations.
The maplib investigation below records future-backend evidence, not an initial dependency or performance guarantee.
The proposed design remains subject to the decision gates listed in the implementation plan.

## 1. Purpose

oxivault is a new implementation of the Vault-LD idea, with two deliberate
changes over the reference implementation:

1. The vault's physical location is any S3-compliant object store (AWS S3,
   MinIO, Cloudflare R2, Backblaze B2, Garage) or a git repository, not
   just a local directory.
2. The RDF engine can later be replaced with maplib (Rust, Arrow/Polars, Oxigraph-derived SPARQL) behind stable conversion and graph interfaces.
   The initial implementation uses the reference's RDFLib engine to establish correctness before optimizing.

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

### 2.2 Deferred maplib backend (DataTreehouse/maplib, 0.20.26) — verified by spike on Python 3.14

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
  - maplib depends on `rdflib>=7.6` and `fastapi`.
    This does not supply the initial implementation's dependencies: declare RDFLib directly for reference conversion/query and FastAPI explicitly for the server.
    Keep engine-specific objects out of the public API so a later maplib implementation remains contained.
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
                    │        ├──► RDFLib Graph ──► SPARQL         │
                    │        └──► DataFrame queries (neighbors,    │
                    │             backlinks, search, validation)   │
                    └───────┬──────────────────────────┬──────────┘
                            │                          │
                    CLI (sync,      FastAPI server (JSON API
                    vault2rdf,       for the SvelteKit app)
                    rdf2vault,
                    query, serve)
                            │                          │
                    Obsidian (local dir)      SvelteKit web app
```

The vault's physical location is whichever backend is configured: a local
directory, an S3-compliant bucket, or a git repository. Notes and
`context.jsonld` are objects keyed by their vault-relative POSIX paths. There
are no directories in S3 (only key prefixes) and none in git (only trees);
the store abstraction hides both.
Initially the in-process reference functions operate on a bounded temporary snapshot of the store; successful ingest publishes only changed objects with conditional-write checks.
The RDFLib graph shown above can be replaced by maplib in a later increment.

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
The implementation contract must also specify bounded reads and create-only preconditions.
A caller requiring conditional publication must receive an explicit error when the backend cannot provide it, not a last-write-wins fallback.
LocalDir's conditional-write guarantee needs serialized check-and-replace within its supported writer scope; mtime and size alone are not an atomic concurrency mechanism.
Multiple object writes are not a vault-wide transaction unless a backend advertises an atomic batch capability.

### 4.2 The triple DataFrame is the canonical derived form

After parsing notes, the intermediate is a Polars DataFrame:
`subject, predicate, object, datatype, lang, note_path, layer`.
It is the canonical derived representation for graph browsing and search, alongside the reference conversion's explicit diagnostics.
The vault remains the durable source; initially the reference exporter supplies RDFLib graphs and note provenance from which this table is built.
The query graph is built from the table with RDFLib initially and maplib later.
Capture `note_path` and `layer` during reference triple emission, not by reconstructing paths from subject IRIs or optional placement triples.
Normalize plain literals to `xsd:string` so they remain distinguishable from IRI objects.
This aligns with the project rule to prefer Polars without requiring a rewrite of the reference mapping logic.

Why two engines: the web app's bread-and-butter queries (node view, graph
edges, type browse, backlinks) are Polars filters over the DataFrame, no
SPARQL involved.
SPARQL SELECT and CONSTRUCT go through the RDFLib graph initially, subject to the API's query-safety boundary.
Keeping the DataFrame primary also provides a stable boundary for the future maplib backend.

### 4.3 Initial full refresh; deferred note-level cache

Initially any note/context mutation invalidates the derived snapshot.
Before reusing it, compare store metadata to detect out-of-band note/context edits, additions, and removals.
The next graph read rebuilds through the reference exporter and publishes the table and query graph together.
Failed publication or refresh cannot leave the application serving stale data as a successful current result.
Whole-snapshot rebuilding avoids incorrect local invalidation when context definitions, duplicate names, or wiki-link targets change.

For a later maplib implementation, its lack of triple-level deletion motivates a per-note triple cache and a rebuilt Model.
Introduce that optimization only after profiling and tests for dependency-aware invalidation, not as a prerequisite to reference reuse.

### 4.4 Deferred maplib loading: N-Triples as the primary path

This section is future-backend work.
The initial implementation uses RDFLib's existing serializers and does not add a custom N-Triples serializer.

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
- Conflicts: writes use conditional `If-Match`; a store-side conflict surfaces as a 409 through the API.
  The CLI preserves both versions using a recoverable `<name>.conflict.md` copy rather than silently retrying without the precondition.
  Decide copy placement and deletion propagation before implementing sync.
  Conflict handling is note-level; it is not a vault-wide transaction.
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

FastAPI, declared as a direct server dependency, with versioned JSON endpoints (section 6).
The SvelteKit app calls the API server-side (keeping secrets and
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
- In-process conversion accepts local Turtle/N-Triples bytes through an explicit parser allowlist.
  Do not mutate global network handlers, arguments, or console state to contain an individual conversion.
  Other RDF input formats are deferred.

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
  _reference/       attributed Vault-LD code adapted into in-process functions
  triples.py        canonical triple model + RDFLib/table conversion
  graph.py          VaultGraph: triple DataFrame + RDFLib Graph, refresh
  roundtrip.py      snapshot bridge, conversion results, conditional publication
  vault.py          Vault facade: vault2rdf, rdf2vault, notes, refresh, sync
  server.py         FastAPI app (v1 endpoints)
  cli.py            oxivault CLI (init, sync, vault2rdf, rdf2vault, query, serve)
  config.py         Pydantic BaseSettings (store URI, creds, limits)
  errors.py         package-specific fatal errors and conflict reporting
```

Keep the reference's context, frontmatter, identity, and placement helpers together initially rather than extracting new modules before behavior is covered.
The adapted reference functions use RDFLib internally; public conversion results contain serialized RDF documents and typed reports.
Conformance criteria from SPEC section 6 become independent tests, supplemented by comparisons with an untouched pinned reference and its example vault.

The public methods are `Vault.vault2rdf(*, source=False)` and `Vault.rdf2vault(rdf, *, nest=False)`.
The first returns schema/data Turtle documents and diagnostics; the second accepts explicitly typed Turtle/N-Triples documents and reports note/context changes and diagnostics.
`source=True` opts into placement-preserving export, matching the reference's `--source` behavior.
See the implementation plan for target models and detailed failure contracts.

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
- `POST /graph/sparql` — SPARQL SELECT/CONSTRUCT with JSON results and an explicit network-free, read-only execution boundary.
  Agree CONSTRUCT's JSON representation before implementing the endpoint.
- `GET  /graph/edges?subject=` — outbound and inbound edges (DataFrame path).
- `GET  /search?q=` — literal and body search.
- `GET  /graph/issues` — dangling links, unmapped fields, ambiguity warnings,
  schema-folder type mismatches (the reference tool's warning surface,
  exposed as an API).
- `POST /reindex` — rebuild the triple DataFrame and initial RDFLib query graph.

## 7. Dependencies to add

Add dependencies when their implementation increment first needs them.
Initially use direct RDFLib and PyYAML dependencies, starting from the pinned reference requirements (`rdflib==7.6.0`, `PyYAML==6.0.3`) and reviewing them before installation.
Add polars, boto3, pinned dulwich, explicit fastapi/uvicorn server dependencies, and pydantic-settings at their respective increments.
Dev dependencies include pytest, ruff, ty, pre-commit, and moto for offline S3 tests.
Do not add maplib until its deferred implementation is approved.

## 8. Risks

- **Future maplib is young and partially commercial.** Pin the version; keep the
  load path off its internal layouts; never rely on SHACL/Datalog/FTS.
- **Future maplib is in-memory.** Vault-scale is fine; a pathological multi-million-
  note vault would need a different engine. Not a v1 concern.
- **Reference adaptation subtlety.** Context composition and minting edge cases are easy to change accidentally.
  Mitigation: independent SPEC assertions, differential tests against the pinned reference, and roundtrip-fidelity tests.
- **Reference runtime behavior.** The scripts use CLI exits, printed diagnostics, and a process-wide network blocker.
  Adapt these into typed in-process boundaries; do not copy them into the API host unchanged.
- **Reference performance and snapshot costs.** Full conversion includes temporary storage and Python parsing.
  Measure it separately; the maplib graph-loading spike did not measure this path.
- **Multi-object publication.** S3/LocalDir can partially apply a successful conversion before an I/O failure.
  Surface the applied paths and invalidate the graph; only an advertised batch capability supplies vault-wide atomicity.
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
2. In-process Vault-LD reference reuse with RDFLib initially, a canonical derived triple DataFrame, and stable `vault2rdf`/`rdf2vault` interfaces.
3. Deferred maplib implementation and its supported load path; struct optimization only after profiling.
4. Whole-snapshot invalidation initially; dependency-aware note caching deferred.
5. FastAPI server as the SvelteKit integration surface.
6. Search via Polars in v1, no maplib FTS (commercial).
7. boto3 + moto as the S3 driver and offline test harness (If-Match
   conditional writes; the minio SDK lacks them).
8. Git repository as a storage backend behind the ObjectStore protocol,
   implemented with dulwich (working-tree and bare modes).

## 11. Increments

The [implementation plan](../plan/202610020723_reference-first-tdd-implementation.md) supplies RED/GREEN steps, file scopes, dependencies, conformance tests, and exit criteria.

- **0**: package/tooling baseline, ObjectStore contract, Local/Memory, configuration, and offline tests.
- **1**: in-process reference `vault2rdf`, typed diagnostics, and bounded store snapshots.
- **2**: in-process reference `rdf2vault`, conditional publication, preservation, and roundtrip fidelity.
- **3**: canonical triple DataFrame, RDFLib SPARQL, structured graph/search operations, and renamed conversion CLI commands.
- **4**: S3 store, Obsidian sync, conflicts, and change detection.
- **5**: Git bare/working-tree storage, CAS commits, atomic ingest batches, and optional history.
- **6**: FastAPI endpoints and SvelteKit integration documentation, gated on auth and query-safety decisions.
- **7**: remaining regressions, performance characterization, hardening, examples, and release documentation.

Maplib is a separate later increment, not a dependency of any step above.