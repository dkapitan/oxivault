# README publication polish handover

## What I changed

- Rewrote `README.md` to a publication-ready structure for PyPI consumption.
- Added clear sections for:
  - package summary and feature set
  - requirements (Python 3.14+)
  - installation (`pip` and `uv`)
  - CLI quick start
  - Python quick start
  - HTTP API endpoints
  - conversion constraints and behavior
  - runnable examples
  - development checks and changelog link

## Validation

- Ran `tara check` from repo root.
- Result: all checks passed.

## Notes / follow-up

- README is now publication-oriented, but package metadata in `pyproject.toml` can still be strengthened before public release (for example: `license`, `classifiers`, and project URLs).
