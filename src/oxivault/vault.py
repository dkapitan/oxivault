"""Vault facade providing the main API."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import yaml
from rdflib import Graph
from rdflib.exceptions import ParserError
from rdflib.plugins.parsers.notation3 import BadSyntax

from oxivault._reference.vault_ld.rdf_to_vault import emit_frontmatter, rdf2vault_reference
from oxivault._reference.vault_ld.vault_to_rdf import (
    FrontmatterLoader,
    vault2rdf_reference,
)
from oxivault.config import VaultConfig
from oxivault.errors import (
    ConversionError,
    RdfParseError,
    StoreKeyError,
)
from oxivault.graph import VaultGraph
from oxivault.models import IngestReport, Issue, Note, RdfExport
from oxivault.snapshot import materialize_vault_snapshot
from oxivault.store.base import _SENTINEL, validate_key

if TYPE_CHECKING:
    from collections.abc import Sequence
    from contextlib import AbstractContextManager
    from pathlib import Path

    import polars as pl
    from rdflib.query import ResultRow
    from rdflib.term import Identifier

    from oxivault.models import RdfDocument
    from oxivault.store.base import ObjectStore


_FRONTMATTER_PARTS_COUNT = 3


class Vault:
    """Vault facade for managing a Vault-LD store and its conversions."""

    def __init__(
        self,
        store: ObjectStore,
        config: VaultConfig | None = None,
    ) -> None:
        """Initialize Vault with a store backend and optional config."""
        self.store = store
        self.config = config if config is not None else VaultConfig()
        self._graph_cache: VaultGraph | None = None

    def _snapshot(self, *, sync_back: bool = False) -> AbstractContextManager[Path]:
        return materialize_vault_snapshot(
            self.store,
            max_object_bytes=self.config.max_read_bytes,
            max_snapshot_bytes=self.config.max_snapshot_bytes,
            sync_back=sync_back,
        )

    def _current_store_fingerprint(self) -> dict[str, str]:
        """Compute store fingerprint mapping relevant vault keys to their ETags."""
        keys = self.store.list()
        fingerprint: dict[str, str] = {}
        for key in keys:
            if key.endswith((".md", "context.jsonld")):
                stat = self.store.stat(key)
                fingerprint[key] = stat.etag
        return fingerprint

    def _ensure_graph(self) -> VaultGraph:
        """Get or rebuild cached VaultGraph if store has changed."""
        fingerprint = self._current_store_fingerprint()
        if self._graph_cache is not None and self._graph_cache.store_fingerprint == fingerprint:
            return self._graph_cache

        with self._snapshot() as tmp_vault:
            vg = VaultGraph.build(
                tmp_vault,
                fingerprint,
                data_ns=self.config.data_ns,
                max_context_bytes=self.config.max_context_bytes,
            )
        self._graph_cache = vg
        return vg

    def invalidate(self) -> None:
        """Explicitly invalidate the derived graph cache."""
        self._graph_cache = None

    def triples(self) -> pl.DataFrame:
        """Return canonical Polars DataFrame of all triples with note provenance."""
        vg = self._ensure_graph()
        return vg.triples_df

    def query_graph(self) -> Graph:
        """Return combined in-memory RDFLib Graph for querying."""
        vg = self._ensure_graph()
        return vg.combined_graph

    def query(self, sparql: str) -> list[ResultRow | tuple[Identifier, Identifier, Identifier] | bool]:
        """Execute a SPARQL query against the vault's RDF graph."""
        vg = self._ensure_graph()
        return vg.query(sparql)

    def neighbors(
        self,
        subject_or_iri: str,
        direction: Literal["outbound", "inbound", "both"] = "both",
    ) -> pl.DataFrame:
        """Find neighboring edges in the canonical triple DataFrame."""
        vg = self._ensure_graph()
        return vg.neighbors(subject_or_iri, direction=direction)

    def backlinks(self, iri: str) -> pl.DataFrame:
        """Find all inbound links (backlinks) pointing to an IRI."""
        vg = self._ensure_graph()
        return vg.backlinks(iri)

    def search(self, query: str) -> dict[str, pl.DataFrame]:
        """Search literal triples matching query."""
        vg = self._ensure_graph()
        return {"triples": vg.search_literals(query)}

    def search_body(self, query: str) -> list[str]:
        """Search note bodies across markdown notes."""
        matching_paths: list[str] = []
        for key in self.store.list():
            if not key.endswith(".md"):
                continue
            raw = self.store.get(key, max_bytes=self.config.max_read_bytes)
            text = raw.decode("utf-8", errors="replace")
            # Separate body from frontmatter
            if text.startswith("---"):
                parts = text.split("---", 2)
                body = parts[2] if len(parts) >= _FRONTMATTER_PARTS_COUNT else ""
            else:
                body = text
            if query in body:
                matching_paths.append(key)
        return matching_paths

    def issues(self) -> list[Issue]:
        """Return conversion/graph issues and warnings."""
        vg = self._ensure_graph()
        return vg.issues_list

    def get_note(self, path: str) -> Note:
        """Retrieve a note from the vault with parsed frontmatter and body."""
        clean_key = validate_key(path)
        if not clean_key.endswith(".md"):
            raise StoreKeyError(f"Note path must end in .md: {path}")

        raw = self.store.get(clean_key, max_bytes=self.config.max_read_bytes)
        stat = self.store.stat(clean_key)
        text = raw.decode("utf-8", errors="replace")

        fm: dict[str, object] = {}
        body = text
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= _FRONTMATTER_PARTS_COUNT:
                fm_text = parts[1]
                body = parts[2].lstrip("\r\n")
                if fm_text.strip():
                    try:
                        parsed = yaml.load(fm_text, Loader=FrontmatterLoader)  # noqa: S506
                        if isinstance(parsed, dict):
                            fm = parsed
                    except yaml.YAMLError as err:
                        raise ConversionError(f"Unparseable frontmatter in {path}: {err}") from err

        return Note(
            path=clean_key,
            frontmatter=fm,
            body=body,
            etag=stat.etag,
        )

    def exists_note(self, path: str) -> bool:
        """Check if a note exists in the vault."""
        clean_key = validate_key(path)
        return clean_key.endswith(".md") and self.store.exists(clean_key)

    def list_notes(self, prefix: str = "") -> list[Note]:
        """List all notes in the vault under prefix."""
        keys = self.store.list(prefix=prefix)
        return [self.get_note(k) for k in keys if k.endswith(".md")]

    def put_note(
        self,
        path: str,
        frontmatter: dict[str, object],
        body: str = "",
        *,
        expected_etag: str | object | None = _SENTINEL,
    ) -> Note:
        """Create or update a note with frontmatter validation against context."""
        clean_key = validate_key(path)
        if not clean_key.endswith(".md"):
            raise StoreKeyError(f"Note path must end in .md: {path}")

        if not self.store.exists("context.jsonld"):
            raise ConversionError("Cannot put note: vault has no context.jsonld")

        # Validate frontmatter has type/@type
        if "@type" not in frontmatter and "type" not in frontmatter:
            raise ConversionError(f"Frontmatter in {path} must declare '@type' or 'type'")

        # Serialize frontmatter and body
        fm_text = emit_frontmatter(frontmatter)
        content = fm_text.encode("utf-8") + body.encode("utf-8")

        new_etag = self.store.put(clean_key, content, expected_etag=expected_etag)
        self.invalidate()
        return Note(
            path=clean_key,
            frontmatter=frontmatter,
            body=body,
            etag=new_etag,
        )

    def delete_note(self, path: str) -> None:
        """Delete a note from the vault."""
        clean_key = validate_key(path)
        if not clean_key.endswith(".md"):
            raise StoreKeyError(f"Note path must end in .md: {path}")
        self.store.delete(clean_key)
        self.invalidate()

    def set_note_publication(
        self,
        path: str,
        *,
        published: bool,
        visibility: Literal["public", "private"],
        expected_etag: str | object | None = _SENTINEL,
    ) -> Note:
        """Update publication metadata in note frontmatter."""
        note = self.get_note(path)
        frontmatter = dict(note.frontmatter)
        current_publication = frontmatter.get("publication")
        publication: dict[str, object] = {}
        if isinstance(current_publication, dict):
            publication.update(current_publication)
        publication["published"] = published
        publication["visibility"] = visibility
        frontmatter["publication"] = publication
        return self.put_note(path, frontmatter=frontmatter, body=note.body, expected_etag=expected_etag)

    def vault2rdf(self, *, source: bool = False) -> RdfExport:
        """Export the vault to RDF split into schema and data Turtle strings."""
        with self._snapshot() as tmp_vault:
            schema_ttl, data_ttl, warnings, _g_schema, _g_data, _prov = vault2rdf_reference(
                tmp_vault,
                data_ns=self.config.data_ns,
                source=source,
                max_context_bytes=self.config.max_context_bytes,
            )

        issues = [Issue(severity="warning", message=w) for w in warnings]
        return RdfExport(
            schema_ttl=schema_ttl,
            data_ttl=data_ttl,
            issues=issues,
        )

    def rdf2vault(
        self,
        rdf: Sequence[RdfDocument],
        *,
        nest: bool = False,
    ) -> IngestReport:
        """Ingest RDF documents into the vault store."""
        if not rdf:
            raise RdfParseError("RDF document sequence cannot be empty")
        data_ns = self.config.data_ns or "https://example.org/data/"

        format_map = {
            "turtle": "turtle",
            "nt": "nt",
        }

        g = Graph()
        for doc in rdf:
            if doc.format not in format_map:
                raise RdfParseError(f"Unsupported RDF format: {doc.format}")
            try:
                g.parse(data=doc.content, format=format_map[doc.format])
            except (BadSyntax, ParserError, UnicodeError, ValueError) as e:
                raise RdfParseError(f"Failed to parse RDF document: {e}") from e

        with self._snapshot(sync_back=True) as tmp_vault:
            created, updated, unchanged, written_ctx, warnings = rdf2vault_reference(
                tmp_vault,
                g,
                nest=nest,
                data_ns=data_ns,
                max_context_bytes=self.config.max_context_bytes,
            )

        issues = [Issue(severity="warning", message=w) for w in warnings]
        return IngestReport(
            created_notes=created,
            updated_notes=updated,
            unchanged_notes=unchanged,
            written_contexts=written_ctx,
            issues=issues,
        )
