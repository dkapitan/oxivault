"""Tests for Note CRUD operations, cache invalidation, and graph queries on Vault."""

from __future__ import annotations

import polars as pl
import pytest

from oxivault.errors import (
    ConversionError,
    ObjectConflictError,
    ObjectNotFoundError,
    StoreKeyError,
)
from oxivault.models import RdfDocument
from oxivault.store.memory import MemoryStore
from oxivault.vault import Vault


def test_vault_note_crud_lifecycle() -> None:
    # GIVEN an empty memory vault with a context
    store = MemoryStore()
    vault = Vault(store=store)
    init_rdf = b"""@prefix cul: <https://example.org/culinary#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix data: <https://example.org/data/> .

cul:Culinary a owl:Ontology ;
    rdfs:label "Culinary Ontology" .

cul:Recipe a owl:Class ;
    rdfs:label "Recipe" .
"""
    vault.rdf2vault([RdfDocument(content=init_rdf, format="turtle")])

    # WHEN creating a note with valid frontmatter
    created = vault.put_note(
        "hummus.md",
        frontmatter={"type": "[[Recipe]]", "label": "Classic Hummus"},
        body="A creamy chickpea spread.\n",
        expected_etag=None,  # create-only
    )

    # THEN note is stored and retrieved
    assert created.path == "hummus.md"
    assert created.frontmatter == {"type": "[[Recipe]]", "label": "Classic Hummus"}
    assert created.body == "A creamy chickpea spread.\n"
    assert created.etag is not None

    got = vault.get_note("hummus.md")
    assert got.path == "hummus.md"
    assert got.frontmatter == {"type": "[[Recipe]]", "label": "Classic Hummus"}
    assert got.body == "A creamy chickpea spread.\n"
    assert got.etag == created.etag

    # AND list_notes discovers it
    notes = vault.list_notes()
    note_paths = [n.path for n in notes]
    assert "hummus.md" in note_paths

    # AND conditional update succeeds with matching ETag
    updated = vault.put_note(
        "hummus.md",
        frontmatter={"type": "[[Recipe]]", "label": "Classic Hummus Updated"},
        body="Updated body.\n",
        expected_etag=got.etag,
    )
    assert updated.etag != got.etag
    assert vault.get_note("hummus.md").frontmatter["label"] == "Classic Hummus Updated"

    # AND conditional update with stale ETag raises ObjectConflictError
    with pytest.raises(ObjectConflictError):
        vault.put_note(
            "hummus.md",
            frontmatter={"type": "[[Recipe]]", "label": "Stale Update"},
            body="",
            expected_etag="stale-etag",
        )

    # AND create-only on existing note raises ObjectConflictError
    with pytest.raises(ObjectConflictError):
        vault.put_note(
            "hummus.md",
            frontmatter={"type": "[[Recipe]]", "label": "Collision"},
            body="",
            expected_etag=None,
        )

    # AND deleting the note succeeds
    vault.delete_note("hummus.md")
    assert not vault.exists_note("hummus.md")
    with pytest.raises(ObjectNotFoundError):
        vault.get_note("hummus.md")


def test_vault_put_note_validates_frontmatter_and_keys() -> None:
    store = MemoryStore()
    vault = Vault(store=store)

    # Missing context in empty store
    with pytest.raises(ConversionError):
        vault.put_note(
            "test.md",
            frontmatter={"type": "[[Item]]"},
            body="",
        )

    # Now add context
    init_rdf = b"""@prefix cul: <https://example.org/culinary#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
cul:Culinary a owl:Ontology ; rdfs:label "Ontology" .
cul:Recipe a owl:Class ; rdfs:label "Recipe" .
"""
    vault.rdf2vault([RdfDocument(content=init_rdf, format="turtle")])

    # Key traversal or invalid extension
    with pytest.raises(StoreKeyError):
        vault.put_note("../escape.md", frontmatter={"type": "[[Recipe]]"}, body="")

    with pytest.raises(StoreKeyError):
        vault.put_note("not_markdown.txt", frontmatter={"type": "[[Recipe]]"}, body="")

    # Missing @type or type
    with pytest.raises(ConversionError):
        vault.put_note("invalid.md", frontmatter={"label": "No Type"}, body="")


def test_vault_cache_invalidation_on_mutation_and_out_of_band_edit() -> None:
    store = MemoryStore()
    vault = Vault(store=store)
    init_rdf = b"""@prefix cul: <https://example.org/culinary#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix data: <https://example.org/data/> .

cul:Culinary a owl:Ontology ; rdfs:label "Ontology" .
cul:Recipe a owl:Class ; rdfs:label "Recipe" .
data:falafel a cul:Recipe ; rdfs:label "Falafel" .
"""
    vault.rdf2vault([RdfDocument(content=init_rdf, format="turtle")])

    # Initial graph query
    t1 = vault.triples()
    assert len(t1.filter(pl.col("subject") == "https://example.org/data/falafel")) >= 2

    # Put a new note via vault facade
    vault.put_note(
        "tahini.md",
        frontmatter={"type": "[[Recipe]]", "label": "Tahini Dip"},
        body="Sesame paste.\n",
    )

    # Re-querying triples should include tahini
    t2 = vault.triples()
    assert len(t2.filter(pl.col("subject") == "https://example.org/data/tahini")) >= 2

    # Out-of-band edit directly into store
    raw_tahini = store.get("tahini.md").decode("utf-8")
    raw_tahini_updated = raw_tahini.replace("Tahini Dip", "Sesame Tahini")
    store.put("tahini.md", raw_tahini_updated.encode("utf-8"))

    # Vault detects out-of-band edit via store stat/list comparison and invalidates
    t3 = vault.triples()
    row = t3.filter(
        (pl.col("subject") == "https://example.org/data/tahini")
        & (pl.col("predicate") == "http://www.w3.org/2000/01/rdf-schema#label")
    )
    assert row["object"][0] == "Sesame Tahini"

    # Delete note via vault facade
    vault.delete_note("tahini.md")
    t4 = vault.triples()
    assert len(t4.filter(pl.col("subject") == "https://example.org/data/tahini")) == 0


def test_vault_graph_neighbors_backlinks_and_search() -> None:
    store = MemoryStore()
    vault = Vault(store=store)
    init_rdf = b"""@prefix cul: <https://example.org/culinary#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix data: <https://example.org/data/> .

cul:Culinary a owl:Ontology ; rdfs:label "Ontology" .
cul:Recipe a owl:Class ; rdfs:label "Recipe" .
cul:Ingredient a owl:Class ; rdfs:label "Ingredient" .

data:chickpeas a cul:Ingredient ;
    rdfs:label "Chickpeas" .

data:hummus a cul:Recipe ;
    rdfs:label "Hummus" ;
    rdfs:seeAlso data:chickpeas .
"""
    vault.rdf2vault([RdfDocument(content=init_rdf, format="turtle")])

    # Outbound edges / neighbors
    outbound = vault.neighbors("https://example.org/data/hummus", direction="outbound")
    preds = outbound["predicate"].to_list()
    assert "http://www.w3.org/2000/01/rdf-schema#seeAlso" in preds

    # Inbound edges / backlinks
    backlinks = vault.backlinks("https://example.org/data/chickpeas")
    assert len(backlinks) >= 1
    assert backlinks["subject"][0] == "https://example.org/data/hummus"

    # Literal search in triples
    search_res = vault.search("Chickpeas")
    assert len(search_res["triples"]) >= 1
    assert search_res["triples"]["object"][0] == "Chickpeas"

    # Body search across notes
    hummus_note = store.get("hummus.md").decode("utf-8")
    hummus_note += "\nSecret ingredient: roasted garlic.\n"
    store.put("hummus.md", hummus_note.encode("utf-8"))

    body_hits = vault.search_body("roasted garlic")
    assert "hummus.md" in body_hits

    # Issues report
    issues = vault.issues()
    assert isinstance(issues, list)
