"""ObjectStore protocol and common types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from oxivault.errors import StoreKeyError

if TYPE_CHECKING:
    import builtins
    from datetime import datetime


@dataclass(frozen=True)
class StatInfo:
    """Metadata for an object in an ObjectStore."""

    etag: str
    size: int
    last_modified: datetime | None = None


def validate_key(key: str) -> str:
    """Validate that key is a clean POSIX-relative path within root.

    Args:
        key: Store key string.

    Returns:
        The validated clean key string.

    Raises:
        StoreKeyError: If key is empty, absolute, contains backslashes, or attempts
            to escape root with '..'.
    """
    if not key or not isinstance(key, str):
        raise StoreKeyError("Store key cannot be empty")

    if "\x00" in key:
        raise StoreKeyError("Store key cannot contain NUL")

    if "\\" in key:
        raise StoreKeyError(f"Backslashes are not permitted in store keys: {key!r}")

    if key.startswith("/"):
        raise StoreKeyError(f"Store keys must be relative, not absolute: {key!r}")

    segments = key.split("/")
    for segment in segments:
        if segment in ("", "."):
            raise StoreKeyError(f"Store key contains empty or '.' segment: {key!r}")
        if segment == "..":
            raise StoreKeyError(f"Store key attempts path traversal ('..'): {key!r}")

    return key


_SENTINEL = object()


@runtime_checkable
class ObjectStore(Protocol):
    """Protocol for pluggable object storage backends."""

    def list(self, prefix: str = "") -> builtins.list[str]:
        """List keys matching prefix in sorted order."""
        ...

    def get(self, key: str, *, max_bytes: int | None = None) -> bytes:
        """Get object bytes, optionally bounding read size."""
        ...

    def put(
        self,
        key: str,
        data: bytes,
        *,
        expected_etag: str | object | None = _SENTINEL,
    ) -> str:
        """Put object data atomically and return new ETag.

        Args:
            key: Store key.
            data: Payload bytes.
            expected_etag: Precondition for optimistic concurrency.
                - _SENTINEL (default): unconditional overwrite
                - None: create-only (fails if object exists)
                - str: fails unless current ETag matches
        """
        ...

    def delete(self, key: str) -> None:
        """Delete object. No-op if key does not exist."""
        ...

    def stat(self, key: str) -> StatInfo:
        """Return object metadata."""
        ...

    def exists(self, key: str) -> bool:
        """Check if object exists."""
        ...
