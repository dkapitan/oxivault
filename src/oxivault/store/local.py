"""Local filesystem ObjectStore implementation."""

from __future__ import annotations

import hashlib
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from oxivault.errors import (
    ObjectConflictError,
    ObjectNotFoundError,
    ObjectReadLimitError,
    StoreError,
    StoreKeyError,
)
from oxivault.store.base import _SENTINEL, StatInfo, validate_key

if TYPE_CHECKING:
    import builtins


class LocalDirStore:
    """Local directory implementation of ObjectStore with serialized writes."""

    def __init__(self, root_dir: Path | str) -> None:
        """Initialize LocalDirStore with a root directory path."""
        self._root = Path(root_dir).resolve()
        self._lock = threading.Lock()

    def _resolve_path(self, key: str) -> Path:
        clean_key = validate_key(key)
        target = (self._root / clean_key).resolve()
        if not target.is_relative_to(self._root):
            raise StoreKeyError(f"Key resolves outside store root: {key!r}")
        return target

    def _compute_etag(self, path: Path) -> str:
        # Content hash as reliable ETag
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()

    def list(self, prefix: str = "") -> builtins.list[str]:
        """List keys matching prefix in sorted order."""
        if not self._root.exists():
            return []

        results: list[str] = []
        for file_path in self._root.rglob("*"):
            if file_path.is_file() and not file_path.is_symlink():
                rel_path = file_path.relative_to(self._root).as_posix()
                if rel_path.startswith(prefix):
                    results.append(rel_path)
        return sorted(results)

    def get(self, key: str, *, max_bytes: int | None = None) -> bytes:
        """Get object bytes, optionally bounding read size."""
        target = self._resolve_path(key)
        if not target.is_file() or target.is_symlink():
            raise ObjectNotFoundError(key)

        stat = target.stat()
        if max_bytes is not None and stat.st_size > max_bytes:
            raise ObjectReadLimitError(key, max_bytes, stat.st_size)

        with target.open("rb") as stream:
            content = stream.read() if max_bytes is None else stream.read(max_bytes + 1)
        if max_bytes is not None and len(content) > max_bytes:
            raise ObjectReadLimitError(key, max_bytes, len(content))
        return content

    def put(
        self,
        key: str,
        data: bytes,
        *,
        expected_etag: str | object | None = _SENTINEL,
    ) -> str:
        """Put object data atomically and return new ETag."""
        target = self._resolve_path(key)

        with self._lock:
            exists = target.is_file() and not target.is_symlink()
            if expected_etag is not _SENTINEL:
                if expected_etag is None:
                    if exists:
                        raise ObjectConflictError(
                            key,
                            "Object already exists (create-only precondition failed)",
                        )
                else:
                    current_etag = self._compute_etag(target) if exists else None
                    if current_etag != expected_etag:
                        raise ObjectConflictError(
                            key,
                            f"ETag mismatch: expected {expected_etag}, got {current_etag}",
                        )

            target.parent.mkdir(parents=True, exist_ok=True)
            # Atomic write via tempfile in the same directory
            tmp_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as tmp:
                    tmp_path = Path(tmp.name)
                    tmp.write(data)
                tmp_path.replace(target)
            finally:
                if tmp_path is not None:
                    tmp_path.unlink(missing_ok=True)
            return hashlib.sha256(data).hexdigest()

    def delete(self, key: str) -> None:
        """Delete object. No-op if key does not exist."""
        target = self._resolve_path(key)
        with self._lock:
            if target.is_file() and not target.is_symlink():
                target.unlink()

    def stat(self, key: str) -> StatInfo:
        """Return object metadata."""
        target = self._resolve_path(key)
        if not target.is_file() or target.is_symlink():
            raise ObjectNotFoundError(key)

        st = target.stat()
        mtime = datetime.fromtimestamp(st.st_mtime, tz=UTC)
        return StatInfo(
            etag=self._compute_etag(target),
            size=st.st_size,
            last_modified=mtime,
        )

    def exists(self, key: str) -> bool:
        """Check if object exists."""
        target = self._resolve_path(key)
        return target.is_file() and not target.is_symlink()

    def presign_put(self, key: str, *, expires_seconds: int) -> str:
        """Local filesystem backend does not support presigned URLs."""
        _ = (key, expires_seconds)
        raise StoreError("Presigned URLs are not supported by LocalDirStore")

    def presign_get(self, key: str, *, expires_seconds: int) -> str:
        """Local filesystem backend does not support presigned URLs."""
        _ = (key, expires_seconds)
        raise StoreError("Presigned URLs are not supported by LocalDirStore")
