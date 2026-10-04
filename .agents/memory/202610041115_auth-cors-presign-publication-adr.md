# Auth, CORS, presign, and publication helper handover

Implemented API authentication in `src/oxivault/server.py` using OAuth2 bearer token extraction and session-cookie login with explicit `read` and `editor` roles.
Applied role gates to all routes so read endpoints require authenticated `read` or `editor` access and write endpoints require `editor`.
Kept SPARQL write/service blocking logic unchanged and treated it as defense-in-depth behind auth.

Added CORS allow-list support through `ApiServerConfig.cors_allowed_origins` and wired middleware configuration in `create_app`.
Added `presign_put` and `presign_get` methods to the ObjectStore protocol and implemented them for S3 and in-memory store, with unsupported backends returning explicit errors.
Added `/objects/presign-put` and `/objects/presign-get` endpoints for direct upload and temporary private download flows.

Added a publication helper endpoint `/publication/notes/{path}` to update publication metadata and optionally execute copy-based publication for capable backends using source ETag verification.
Implemented `Vault.set_note_publication` to centralize publication metadata updates in note frontmatter.
Implemented `S3Store.copy_verified` to support conditional copy operations.

Documented the design in `docs/decisions/0001-publication-helper-for-master-library.md`.
Updated `README.md` and `CHANGELOG.md` with auth/CORS/presign/publication details.
Validated with `tara check` (lint, format, types, tests, security), passing with 122 tests.
