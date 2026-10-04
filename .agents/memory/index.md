# Memory index

Freeform session notes and handover scratch. One row per doc, newest first. Name each file `YYYYMMDDHHMM_<short-descriptive-title>.md`.

| Date | File | Summary |
| ---- | ---- | ------- |
| 2026-10-04 | [202610041115_auth-cors-presign-publication-adr.md](202610041115_auth-cors-presign-publication-adr.md) | Added OAuth2/session role auth, CORS allow-list, presign endpoints, publication helper with optional verified copy, and ADR-0001 design, validated with `tara check`. |
| 2026-10-04 | [202610041740_readme-vaultld-attribution.md](202610041740_readme-vaultld-attribution.md) | Added README attribution section with links and credit to upstream Vault-LD spec and reference scripts at the pinned revision. |
| 2026-10-04 | [202610041730_readme-license-section.md](202610041730_readme-license-section.md) | Added README `License` section linking to Apache-2.0 `LICENSE`; validated with `tara check`. |
| 2026-10-04 | [202610041725_apache-license-and-metadata.md](202610041725_apache-license-and-metadata.md) | Added Apache-2.0 `LICENSE`, updated `pyproject.toml` license metadata to SPDX/PEP 639 fields, and validated with build + twine check + `tara check`. |
| 2026-10-04 | [202610041715_pypi-metadata-hardening.md](202610041715_pypi-metadata-hardening.md) | Added PyPI metadata (`keywords`, `classifiers`, project URLs), added README badges/links, and validated with build + twine check + `tara check`; license file still missing. |
| 2026-10-04 | [202610041705_readme-pypi-publication-polish.md](202610041705_readme-pypi-publication-polish.md) | Rewrote README into publication-ready PyPI format with install, quick starts, API, constraints, examples, and validated with `tara check`. |
| 2026-10-04 | [202610041430_increments-4-6-completion-handover.md](202610041430_increments-4-6-completion-handover.md) | Increments 4, 5, and 6 complete: S3 backend, Obsidian sync engine, Git backend (dulwich), and FastAPI HTTP server with CLI commands; 109 tests passing. |
| 2026-10-04 | [202610041200_increment-3-completion-handover.md](202610041200_increment-3-completion-handover.md) | Increment 3 complete: note CRUD on Vault, graph edges/backlinks, SPARQL query command, fingerprint-based cache invalidation, and runnable demo; 95 tests passing. |
| 2026-10-04 | [202610020930_increments-0-3-implementation-handover.md](202610020930_increments-0-3-implementation-handover.md) | Initial implementation plus review fixes: conditional publication, bounded snapshots, conversion/CLI errors, and 91 passing tests; full plan exit criteria remain outstanding. |
| 2026-10-02 | [202610020723_reference-first-plan-handover.md](202610020723_reference-first-plan-handover.md) | Planning handover: in-process reference reuse and Turtle/N-Triples confirmed; vault2rdf/rdf2vault and reference-first TDD plan replace the earlier maplib-first sequence; implementation not started. |
| 2026-09-28 | [202609281044_oxivault-investigation-handover.md](202609281044_oxivault-investigation-handover.md) | Investigation + design handover: vault-ld research, maplib spike results (Py3.14 OK, perf numbers, commercial-feature boundary), open questions, next steps. |
