"""Tests for oxivault FastAPI server."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from oxivault.models import RdfDocument
from oxivault.server import create_app
from oxivault.store.memory import MemoryStore
from oxivault.vault import Vault


@pytest.fixture
def test_client() -> TestClient:
    store = MemoryStore()
    vault = Vault(store=store)

    rdf = b"""@prefix cul: <https://example.org/culinary#> .
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
    vault.rdf2vault([RdfDocument(content=rdf, format="turtle")])

    app = create_app(vault)
    return TestClient(app)


def test_server_get_vault(test_client: TestClient) -> None:
    resp = test_client.get("/vault")
    assert resp.status_code == 200
    data = resp.json()
    assert data["note_count"] >= 2
    assert data["triple_count"] >= 4


def test_server_notes_crud_and_concurrency(test_client: TestClient) -> None:
    # GET list
    resp = test_client.get("/notes")
    assert resp.status_code == 200
    notes = resp.json()["notes"]
    paths = [n["path"] for n in notes]
    assert "hummus.md" in paths

    # GET single
    resp_note = test_client.get("/notes/hummus.md")
    assert resp_note.status_code == 200
    note_data = resp_note.json()
    assert note_data["path"] == "hummus.md"
    etag = note_data["etag"]
    assert etag is not None

    # PUT without If-Match must allow normal overwrite
    overwrite_payload = {
        "frontmatter": {"type": "[[Recipe]]", "label": "Hummus Overwrite"},
        "body": "Overwrite body\n",
    }
    resp_no_if_match = test_client.put("/notes/hummus.md", json=overwrite_payload)
    assert resp_no_if_match.status_code == 200
    assert resp_no_if_match.json()["frontmatter"]["label"] == "Hummus Overwrite"
    etag = resp_no_if_match.json()["etag"]

    # PUT with matching If-Match
    update_payload = {
        "frontmatter": {"type": "[[Recipe]]", "label": "Super Hummus"},
        "body": "Updated body\n",
    }
    resp_put = test_client.put("/notes/hummus.md", json=update_payload, headers={"If-Match": f'"{etag}"'})
    assert resp_put.status_code == 200
    assert resp_put.json()["frontmatter"]["label"] == "Super Hummus"

    # PUT with stale If-Match -> 409
    resp_stale = test_client.put("/notes/hummus.md", json=update_payload, headers={"If-Match": f'"{etag}"'})
    assert resp_stale.status_code == 409

    # DELETE
    resp_del = test_client.delete("/notes/hummus.md")
    assert resp_del.status_code == 204

    # GET deleted -> 404
    resp_missing = test_client.get("/notes/hummus.md")
    assert resp_missing.status_code == 404


def test_server_put_respects_if_none_match_create_only(test_client: TestClient) -> None:
    payload = {
        "frontmatter": {"type": "[[Recipe]]", "label": "Create only"},
        "body": "Create only body\n",
    }
    resp_existing = test_client.put("/notes/hummus.md", json=payload, headers={"If-None-Match": "*"})
    assert resp_existing.status_code == 409

    resp_new = test_client.put("/notes/newly-created.md", json=payload, headers={"If-None-Match": "*"})
    assert resp_new.status_code == 200


def test_server_sparql_query(test_client: TestClient) -> None:
    sparql = "SELECT ?label WHERE { <https://example.org/data/chickpeas> <http://www.w3.org/2000/01/rdf-schema#label> ?label }"
    resp = test_client.post("/graph/sparql", json={"query": sparql})
    assert resp.status_code == 200
    results = resp.json()
    assert len(results["results"]) == 1
    assert "Chickpeas" in str(results["results"][0])


def test_server_sparql_allows_service_in_variable_and_iri(test_client: TestClient) -> None:
    query = "SELECT ?service WHERE { <https://example.org/data/hummus> <https://example.org/serviceName> ?service . }"
    resp = test_client.post("/graph/sparql", json={"query": query})
    assert resp.status_code == 200


def test_server_edges_and_search(test_client: TestClient) -> None:
    # Edges
    resp = test_client.get("/graph/edges", params={"subject": "https://example.org/data/hummus"})
    assert resp.status_code == 200
    edges = resp.json()["edges"]
    assert len(edges) >= 1

    # Search
    resp_search = test_client.get("/search", params={"q": "Chickpeas"})
    assert resp_search.status_code == 200
    assert len(resp_search.json()["triples"]) >= 1

    # Issues
    resp_issues = test_client.get("/graph/issues")
    assert resp_issues.status_code == 200
    assert "issues" in resp_issues.json()

    # Reindex
    resp_reindex = test_client.post("/reindex")
    assert resp_reindex.status_code == 200
    assert resp_reindex.json()["status"] == "ok"
