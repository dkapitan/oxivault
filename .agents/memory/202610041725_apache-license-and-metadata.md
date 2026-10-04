# Apache-2.0 license + metadata update

## What was changed

- Added top-level `LICENSE` file with Apache License 2.0 text.
- Updated `pyproject.toml` metadata to declare license using PEP 639 style:
  - `license = "Apache-2.0"`
  - `license-files = ["LICENSE"]`
- Removed deprecated/ambiguous license Trove classifier to avoid backend warnings.

## Validation

- `uv build` passed (sdist and wheel).
- `uvx twine check ../dist/oxivault-0.1.0.tar.gz ../dist/oxivault-0.1.0-py3-none-any.whl` passed.
- `tara check` passed (lint, format, types, tests, security).

## Notes

- Build warning about license classifiers is now gone after moving to SPDX license expression + license files.
