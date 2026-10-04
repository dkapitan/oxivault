# PyPI metadata hardening handover

## Scope

Follow-up publication pass after README rewrite.

## Changes made

- Updated `pyproject.toml` with publication metadata:
  - `keywords`
  - `classifiers`
  - `[project.urls]` (`Homepage`, `Repository`, `Issues`, `Changelog`)
- Updated `README.md`:
  - Added PyPI and Python-version badges
  - Replaced terminal changelog section with a project links section

## Validation

- `uv build` succeeded for both sdist and wheel.
- `uvx twine check ../dist/oxivault-0.1.0.tar.gz ../dist/oxivault-0.1.0-py3-none-any.whl` passed.
- `tara check` passed (lint, format, types, tests, security).

## Remaining publication gap

- No top-level `LICENSE` file currently exists in the repository root; add one and corresponding license metadata before first public PyPI release.
