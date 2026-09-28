# Session handover: oxivault investigation and design

Date: 2026-09-28. Follow up: design doc in `.agents/design/202609281044_oxivault-design.md`.

## What was done

- Researched vault-ld (The-Knowledge-Graph-Guys, v0.5.x): SPEC, reference
  scripts `vault_to_rdf.py` / `rdf_to_vault.py`, example vault.
- Researched and spiked maplib 0.20.26 on Python 3.14 (macOS arm64):
  - works (cp310-abi3 wheel), imports clean;
  - verified `reads()` (ntriples/turtle), `query()` SPARQL -> polars DF,
    `writes()` turtle, `map_triples` with struct columns (field layout: `I`,
    `l`, `<datatype-IRI>`, `<langString>`);
  - perf: 45k triples load 0.06 s, 2-hop query 3 ms, full rebuild 0.06 s;
  - no triple-level deletion (whole-graph mutation only);
  - FTS, SHACL, Datalog are commercial (FTS panics: "Contact Data Treehouse").
- Wrote the design (see index above). Proposed ADRs listed in section 10.

## Confirmed decisions (developer)

- Own ObjectStore implementation with boto3 + moto (offline S3 tests), not
  fsspec, not minio SDK (minio-py 7.2.20 has no conditional writes).
- Design extended with a Git repository backend (section 4.9): dulwich
  1.2.15, bare + working-tree modes, blob-hash ETags, CAS ref updates
  (set_if_equals), commit-time LastModified. Spike verified on Py3.14.

## What's left / open questions

- Confirm design with developer; record ADRs (`tara new adr`).
- Open questions in design section 9: deployment (MinIO vs cloud), multi-
  vault, API auth, real-time updates, versioning/undo.
- Increments 0-6 in design section 11; start with 0 (skeleton + ObjectStore).
- Keep maplib pinned; keep the N-Triples load path primary (struct layout is
  an internal maplib detail).
- Note: maplib pulls rdflib + fastapi transitively; oxivault code should not
  import rdflib directly.
- Repo has no commits yet; pyproject requires-python >=3.14, no deps.