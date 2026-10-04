"""Snapshot bridge between ObjectStore and path-based reference functions."""

from __future__ import annotations

import hashlib
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from oxivault.errors import (
    ObjectConflictError,
    ObjectReadLimitError,
    PublicationError,
    StoreError,
    StoreKeyError,
)
from oxivault.store.base import validate_key

if TYPE_CHECKING:
    from collections.abc import Generator

    from oxivault.store.base import ObjectStore


def _snapshot_files(root: Path, max_object_bytes: int | None, max_snapshot_bytes: int) -> list[Path]:
    files: list[Path] = []
    total = 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise StoreKeyError(f"Unsafe snapshot path: {path.relative_to(root)}")
        if path.is_file():
            key = validate_key(path.relative_to(root).as_posix())
            size = path.stat().st_size
            if max_object_bytes is not None and size > max_object_bytes:
                raise ObjectReadLimitError(key, max_object_bytes, size)
            total += size
            if total > max_snapshot_bytes:
                raise ObjectReadLimitError("snapshot", max_snapshot_bytes, total)
            files.append(path)
    return files


def _publish(
    store: ObjectStore,
    root: Path,
    files: list[Path],
    initial: dict[str, tuple[str, str]],
) -> None:
    applied: list[str] = []
    for path in files:
        key = path.relative_to(root).as_posix()
        try:
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            previous = initial.get(key)
            if previous is not None and previous[1] == digest:
                continue
            store.put(key, path.read_bytes(), expected_etag=previous[0] if previous else None)
        except (StoreError, OSError) as err:
            raise PublicationError(key, tuple(applied)) from err
        applied.append(key)


@contextmanager
def materialize_vault_snapshot(
    store: ObjectStore,
    *,
    prefix: str = "",
    max_object_bytes: int | None = 10 << 20,
    max_snapshot_bytes: int = 256 << 20,
    sync_back: bool = False,
) -> Generator[Path, None, None]:
    """Materialize a temporary directory snapshot of the vault from store.

    Reads are bounded per object and across the snapshot.
    Successful conversions publish changed files using captured ETags and
    create-only preconditions; unchanged objects and deletions are not published.

    Yields:
        Path to the temporary directory containing the materialized files.

    Raises:
        ObjectReadLimitError: An object or the snapshot exceeds its byte budget.
        StoreKeyError: A key escapes the snapshot or a staged path is a symlink.
        PublicationError: Publication failed; includes paths already applied.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_root = Path(tmp_dir)
        keys = store.list(prefix)
        initial: dict[str, tuple[str, str]] = {}
        total = 0
        for key in keys:
            validate_key(key)
            before = store.stat(key)
            remaining = max_snapshot_bytes - total
            limit = min(max_object_bytes, remaining) if max_object_bytes is not None else remaining
            content = store.get(key, max_bytes=limit)
            if len(content) > limit:
                raise ObjectReadLimitError(key, limit, len(content))
            if store.stat(key).etag != before.etag:
                raise ObjectConflictError(key, "Object changed during snapshot read")
            total += len(content)
            initial[key] = (before.etag, hashlib.sha256(content).hexdigest())
            dest = tmp_root / key
            if not dest.resolve().is_relative_to(tmp_root.resolve()):
                raise StoreKeyError(f"Key escapes snapshot: {key!r}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content)
            del content

        yield tmp_root

        if sync_back:
            files = _snapshot_files(tmp_root, max_object_bytes, max_snapshot_bytes)
            _publish(store, tmp_root, files, initial)
