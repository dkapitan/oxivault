"""Exceptions for oxivault."""

from __future__ import annotations


class OxiVaultError(Exception):
    """Base exception for all oxivault errors."""


class StoreError(OxiVaultError):
    """Base exception for store-related errors."""


class StoreKeyError(StoreError):
    """Raised when a store key is invalid or attempts to escape root."""


class ConversionError(OxiVaultError):
    """Raised when a vault cannot be converted."""


class PublicationError(StoreError):
    """A staged publication failed, possibly after applying some objects."""

    def __init__(self, failed_path: str, applied_paths: tuple[str, ...]) -> None:
        """Record partial progress so callers can recover without guessing."""
        self.failed_path = failed_path
        self.applied_paths = applied_paths
        super().__init__(f"Publication failed at {failed_path}; applied paths: {applied_paths}")


class RdfParseError(OxiVaultError):
    """Raised when parsing RDF document fails or uses an unsupported format."""


class ObjectNotFoundError(StoreError):
    """Raised when an object key does not exist in store."""

    def __init__(self, key: str) -> None:
        """Initialize ObjectNotFoundError."""
        super().__init__(f"Object not found: {key}")
        self.key = key


class ObjectConflictError(StoreError):
    """Raised when a conditional write fails due to ETag mismatch or create collision."""

    def __init__(self, key: str, message: str = "Conditional write failed") -> None:
        """Initialize ObjectConflictError."""
        super().__init__(f"{message}: {key}")
        self.key = key


class ObjectReadLimitError(StoreError):
    """Raised when an object read exceeds the configured size limit."""

    def __init__(self, key: str, max_bytes: int, actual_bytes: int) -> None:
        """Initialize ObjectReadLimitError."""
        super().__init__(
            f"Object '{key}' size {actual_bytes} exceeds limit of {max_bytes} bytes",
        )
        self.key = key
        self.max_bytes = max_bytes
        self.actual_bytes = actual_bytes
