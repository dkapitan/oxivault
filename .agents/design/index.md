# Design index

Design docs; finalized ADRs live in docs/decisions/. One row per doc, newest first. Name each file `YYYYMMDDHHMM_<short-descriptive-title>.md`.

| Date | File | Summary |
| ---- | ---- | ------- |
| 2026-09-28 | [202609281044_oxivault-design.md](202609281044_oxivault-design.md) | oxivault design: vault-ld format on any S3-compliant store or git repo, maplib (not rdflib) as RDF engine, own ObjectStore protocol (boto3+moto for S3, dulwich for git), triple-DataFrame canonical form, FastAPI server for SvelteKit, sync for Obsidian. maplib spike on Py3.14 verified (45k triples in 60ms; FTS/SHACL/Datalog commercial); dulwich spike verified (bare-repo mode, CAS refs). |
