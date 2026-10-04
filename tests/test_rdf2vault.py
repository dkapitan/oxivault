"""Tests for rdf2vault and roundtrip fidelity."""

from __future__ import annotations

from oxivault.models import RdfDocument
from oxivault.store.memory import MemoryStore
from oxivault.vault import Vault


def test_rdf2vault_ingests_turtle_into_fresh_store() -> None:
    # GIVEN turtle RDF defining an ontology class and an instance
    turtle_data = b"""@prefix cul: <https://example.org/culinary#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix data: <https://example.org/> .

cul:Culinary a owl:Ontology ;
    rdfs:label "Culinary Ontology" .

cul:Recipe a owl:Class ;
    rdfs:label "Recipe" .

data:hummus a cul:Recipe ;
    rdfs:label "Hummus" .
"""

    store = MemoryStore()
    vault = Vault(store=store)

    # WHEN ingesting rdf via rdf2vault
    doc = RdfDocument(content=turtle_data, format="turtle")
    report = vault.rdf2vault([doc])

    # THEN notes and contexts are created in the store
    assert len(report.created_notes) >= 2
    assert store.exists("context.jsonld")
    assert store.exists("Recipes/hummus.md") or store.exists("hummus.md")


def test_rdf2vault_preserves_existing_body_and_frontmatter_on_update() -> None:
    # GIVEN a vault with an existing note having custom frontmatter and markdown body
    store = MemoryStore()
    vault = Vault(store=store)

    doc1 = RdfDocument(
        content=b"""@prefix cul: <https://example.org/culinary#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix data: <https://example.org/data/> .

cul:Recipe a owl:Class ;
    rdfs:label "Recipe" .

data:hummus a cul:Recipe ;
    rdfs:label "Hummus" .
""",
        format="turtle",
    )
    report1 = vault.rdf2vault([doc1])
    assert "hummus.md" in report1.created_notes

    # Add extra body content and custom non-LD frontmatter to hummus.md
    existing = store.get("hummus.md").decode("utf-8")
    modified = existing + "\n## Preparation\nBlend chickpeas with tahini.\n"
    store.put("hummus.md", modified.encode("utf-8"))

    # WHEN ingesting updated RDF adding a new property
    doc2 = RdfDocument(
        content=b"""@prefix cul: <https://example.org/culinary#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix data: <https://example.org/data/> .

data:hummus a cul:Recipe ;
    rdfs:label "Classic Hummus" .
""",
        format="turtle",
    )
    report2 = vault.rdf2vault([doc2])

    # THEN the note is updated and body is preserved
    assert "hummus.md" in report2.updated_notes
    final_content = store.get("hummus.md").decode("utf-8")
    assert "## Preparation" in final_content
    assert "Blend chickpeas with tahini." in final_content
    assert "Classic Hummus" in final_content


def test_roundtrip_vault2rdf_and_rdf2vault() -> None:
    # GIVEN a populated vault
    store1 = MemoryStore()
    vault1 = Vault(store=store1)

    initial_rdf = b"""@prefix cul: <https://example.org/culinary#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix data: <https://example.org/data/> .

cul:Culinary a owl:Ontology ;
    rdfs:label "Culinary" .

cul:Recipe a owl:Class ;
    rdfs:label "Recipe" .

data:hummus a cul:Recipe ;
    rdfs:label "Hummus" .
"""
    vault1.rdf2vault([RdfDocument(content=initial_rdf, format="turtle")])

    # WHEN exporting vault1 to RDF
    export = vault1.vault2rdf()
    assert export.schema_ttl or export.data_ttl

    # AND importing into a fresh vault2
    store2 = MemoryStore()
    vault2 = Vault(store=store2)
    docs = []
    if export.schema_ttl:
        docs.append(RdfDocument(content=export.schema_ttl.encode("utf-8"), format="turtle"))
    if export.data_ttl:
        docs.append(RdfDocument(content=export.data_ttl.encode("utf-8"), format="turtle"))

    report2 = vault2.rdf2vault(docs)
    assert len(report2.created_notes) >= 1

    # THEN vault2 has the same notes
    assert store2.exists("hummus.md")
    assert store2.exists("context.jsonld")


def test_rdf2vault_rejects_unsupported_format_or_malformed_rdf() -> None:
    # GIVEN a vault and an invalid/unsupported RDF document
    store = MemoryStore()
    vault = Vault(store=store)

    import pytest

    from oxivault.errors import RdfParseError

    with pytest.raises(RdfParseError, match="Unsupported RDF format"):
        vault.rdf2vault([RdfDocument(content=b"{}", format="json-ld")])  # ty: ignore[invalid-argument-type]

    with pytest.raises(RdfParseError, match="Failed to parse RDF"):
        vault.rdf2vault([RdfDocument(content=b"this is not valid turtle rdf", format="turtle")])
