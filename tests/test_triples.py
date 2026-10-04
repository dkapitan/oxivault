"""Tests for Increment 3: derived Polars triple table and query graph."""

from __future__ import annotations

import polars as pl

from oxivault.models import RdfDocument
from oxivault.store.memory import MemoryStore
from oxivault.vault import Vault


def test_vault_triples_returns_polars_dataframe_with_provenance() -> None:
    # GIVEN a vault populated with schema and data notes
    store = MemoryStore()
    vault = Vault(store=store)

    rdf = b"""@prefix cul: <https://example.org/culinary#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix data: <https://example.org/data/> .

cul:Culinary a owl:Ontology ;
    rdfs:label "Culinary Ontology" .

cul:Recipe a owl:Class ;
    rdfs:label "Recipe" .

data:hummus a cul:Recipe ;
    rdfs:label "Hummus" .
"""
    vault.rdf2vault([RdfDocument(content=rdf, format="turtle")])

    # WHEN requesting the canonical triple table
    df = vault.triples()

    # THEN the result is a Polars DataFrame with the expected columns
    assert isinstance(df, pl.DataFrame)
    expected_cols = ["subject", "predicate", "object", "datatype", "lang", "note_path", "layer"]
    assert df.columns == expected_cols

    # AND contains expected rows with note provenance and layers
    hummus_rows = df.filter(pl.col("subject") == "https://example.org/data/hummus")
    assert len(hummus_rows) >= 2
    paths = hummus_rows["note_path"].to_list()
    assert all(p == "hummus.md" for p in paths)
    layers = hummus_rows["layer"].to_list()
    assert all(lyr == "data" for lyr in layers)

    # AND plain literals have datatype normalized to xsd:string
    label_row = hummus_rows.filter(pl.col("predicate") == "http://www.w3.org/2000/01/rdf-schema#label")
    assert label_row["datatype"][0] == "http://www.w3.org/2001/XMLSchema#string"
    assert label_row["object"][0] == "Hummus"


def test_vault_query_select_and_construct() -> None:
    # GIVEN a vault with triples
    store = MemoryStore()
    vault = Vault(store=store)

    rdf = b"""@prefix cul: <https://example.org/culinary#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix data: <https://example.org/data/> .

cul:Recipe a owl:Class ;
    rdfs:label "Recipe" .

data:hummus a cul:Recipe ;
    rdfs:label "Hummus" .
"""
    vault.rdf2vault([RdfDocument(content=rdf, format="turtle")])

    # WHEN executing a SPARQL SELECT query
    res = vault.query(
        "SELECT ?label WHERE { <https://example.org/data/hummus> <http://www.w3.org/2000/01/rdf-schema#label> ?label }"
    )
    # THEN results are returned
    assert len(res) == 1
    assert isinstance(res[0], tuple)
    assert str(res[0][0]) == "Hummus"
