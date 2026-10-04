"""In-memory ObjectStore implementation for tests."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from oxivault.errors import (
    ObjectConflictError,
    ObjectNotFoundError,
    ObjectReadLimitError,
)
from oxivault.store.base import _SENTINEL, StatInfo, validate_key

if TYPE_CHECKING:
    import builtins


@dataclass
class _MemoryEntry:
    data: bytes
    etag: str
    last_modified: datetime


class MemoryStore:
    """In-memory implementation of ObjectStore."""

    def __init__(self) -> None:
        """Initialize an empty in-memory store."""
        self._entries: dict[str, _MemoryEntry] = {}

    def list(self, prefix: str = "") -> builtins.list[str]:
        """List keys matching prefix in sorted order."""
        return sorted(k for k in self._entries if k.startswith(prefix))

    def get(self, key: str, *, max_bytes: int | None = None) -> bytes:
        """Get object bytes, optionally bounding read size."""
        clean_key = validate_key(key)
        entry = self._entries.get(clean_key)
        if entry is None:
            raise ObjectNotFoundError(clean_key)
        if max_bytes is not None and len(entry.data) > max_bytes:
            raise ObjectReadLimitError(clean_key, max_bytes, len(entry.data))
        return entry.data

    def put(
        self,
        key: str,
        data: bytes,
        *,
        expected_etag: str | object | None = _SENTINEL,
    ) -> str:
        """Put object data atomically and return new ETag."""
        clean_key = validate_key(key)
        current = self._entries.get(clean_key)

        if expected_etag is not _SENTINEL:
            if expected_etag is None:
                if current is not None:
                    raise ObjectConflictError(
                        clean_key,
                        "Object already exists (create-only precondition failed)",
                    )
            elif current is None or current.etag != expected_etag:
                raise ObjectConflictError(
                    clean_key,
                    f"ETag mismatch: expected {expected_etag}, got {current.etag if current else None}",
                )

        new_etag = hashlib.sha256(data).hexdigest()
        self._entries[clean_key] = _MemoryEntry(
            data=data,
            etag=new_etag,
            last_modified=datetime.now(UTC),
        )
        return new_etag

    def delete(self, key: str) -> None:
        """Delete object. No-op if key does not exist."""
        clean_key = validate_key(key)
        self._entries.pop(clean_key, None)

    def stat(self, key: str) -> StatInfo:
        """Return object metadata."""
        clean_key = validate_key(key)
        entry = self._entries.get(clean_key)
        if entry is None:
            raise ObjectNotFoundError(clean_key)
        return StatInfo(
            etag=entry.etag,
            size=len(entry.data),
            last_modified=entry.last_modified,
        )

    def exists(self, key: str) -> bool:
        """Check if object exists."""
        clean_key = validate_key(key)
        return clean_key in self._entries

    def presign_put(self, key: str, *, expires_seconds: int) -> str:
        """Return a deterministic in-memory upload URL for tests."""
        clean_key = validate_key(key)
        if expires_seconds <= 0:
            raise ValueError("expires_seconds must be > 0")
        return f"memory://presigned-put/{clean_key}?expires={expires_seconds}"

    def presign_get(self, key: str, *, expires_seconds: int) -> str:
        """Return a deterministic in-memory download URL for tests."""
        clean_key = validate_key(key)
        if expires_seconds <= 0:
            raise ValueError("expires_seconds must be > 0")
        return f"memory://presigned-get/{clean_key}?expires={expires_seconds}"
