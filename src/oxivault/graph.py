"""Graph state and query operations over a Vault."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import polars as pl
from rdflib import Graph

from oxivault._reference.vault_ld.vault_to_rdf import vault2rdf_reference
from oxivault.models import Issue
from oxivault.triples import build_triple_dataframe

if TYPE_CHECKING:
    from pathlib import Path

    from rdflib.query import ResultRow
    from rdflib.term import Identifier


class VaultGraph:
    """Manages derived RDFLib graph and Polars triple DataFrame with invalidation."""

    def __init__(
        self,
        schema_graph: Graph,
        data_graph: Graph,
        triples_df: pl.DataFrame,
        issues: list[Issue],
        store_fingerprint: dict[str, str],
    ) -> None:
        """Initialize VaultGraph with schema/data graphs and triple DataFrame."""
        self.schema_graph = schema_graph
        self.data_graph = data_graph
        self.triples_df = triples_df
        self.issues_list = issues
        self.store_fingerprint = store_fingerprint

        combined = Graph()
        for s, p, o in schema_graph:
            combined.add((s, p, o))
        for s, p, o in data_graph:
            combined.add((s, p, o))
        self.combined_graph = combined

    @classmethod
    def build(
        cls,
        snapshot_path: Path,
        store_fingerprint: dict[str, str],
        *,
        data_ns: str | None = None,
        max_context_bytes: int = 4 << 20,
    ) -> VaultGraph:
        """Build VaultGraph from a temporary snapshot directory."""
        _schema_ttl, _data_ttl, warnings, g_schema, g_data, prov = vault2rdf_reference(
            snapshot_path,
            data_ns=data_ns,
            source=True,
            max_context_bytes=max_context_bytes,
        )
        df = build_triple_dataframe(prov)
        issues = [Issue(severity="warning", message=w) for w in warnings]
        return cls(
            schema_graph=g_schema,
            data_graph=g_data,
            triples_df=df,
            issues=issues,
            store_fingerprint=store_fingerprint,
        )

    def query(self, sparql: str) -> list[ResultRow | tuple[Identifier, Identifier, Identifier] | bool]:
        """Execute a SPARQL query on the combined graph."""
        return list(self.combined_graph.query(sparql))

    def neighbors(
        self, subject_or_iri: str, direction: Literal["outbound", "inbound", "both"] = "both"
    ) -> pl.DataFrame:
        """Find neighboring edges in the canonical triple DataFrame."""
        if self.triples_df.is_empty():
            return self.triples_df

        if direction == "outbound":
            return self.triples_df.filter(pl.col("subject") == subject_or_iri)
        if direction == "inbound":
            return self.triples_df.filter(pl.col("object") == subject_or_iri)
        return self.triples_df.filter((pl.col("subject") == subject_or_iri) | (pl.col("object") == subject_or_iri))

    def backlinks(self, iri: str) -> pl.DataFrame:
        """Find all inbound links (backlinks) pointing to an IRI."""
        return self.neighbors(iri, direction="inbound")

    def search_literals(self, query: str) -> pl.DataFrame:
        """Search for literal objects containing query string."""
        if self.triples_df.is_empty():
            return self.triples_df
        return self.triples_df.filter(
            (pl.col("datatype").is_not_null()) & (pl.col("object").str.contains(query, literal=True))
        )
