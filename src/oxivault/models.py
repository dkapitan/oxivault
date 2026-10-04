"""Data models for RDF conversion and ingest operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class RdfDocument:
    """An RDF document to be ingested."""

    content: bytes
    format: Literal["turtle", "nt"]


@dataclass(frozen=True)
class Issue:
    """A diagnostic message emitted during conversion."""

    severity: Literal["warning", "error", "info"]
    message: str
    source_path: str | None = None


@dataclass(frozen=True)
class RdfExport:
    """The result of exporting a vault to RDF."""

    schema_ttl: str
    data_ttl: str
    issues: list[Issue] = field(default_factory=list)


@dataclass(frozen=True)
class IngestReport:
    """The result of ingesting RDF into a vault."""

    created_notes: list[str] = field(default_factory=list)
    updated_notes: list[str] = field(default_factory=list)
    unchanged_notes: list[str] = field(default_factory=list)
    written_contexts: list[str] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)


@dataclass(frozen=True)
class Note:
    """A note in the vault with its frontmatter and markdown body."""

    path: str
    frontmatter: dict[str, object]
    body: str
    etag: str | None = None
