"""Tests for oxivault FastAPI server."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from oxivault.models import RdfDocument
from oxivault.server import ApiServerConfig, create_app
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

    app = create_app(
        vault,
        config=ApiServerConfig(
            auth_enabled=True,
            read_tokens={"read-token"},
            editor_tokens={"editor-token"},
            users={
                "audience": {"password": "audience-password", "role": "read"},
                "librarian": {"password": "librarian-password", "role": "editor"},
            },
            cors_allowed_origins=["https://app.nalandabodhi.org"],
        ),
    )
    return TestClient(app)


def test_server_get_vault(test_client: TestClient) -> None:
    resp = test_client.get("/vault", headers={"Authorization": "Bearer read-token"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["note_count"] >= 2
    assert data["triple_count"] >= 4


def test_server_rejects_missing_auth(test_client: TestClient) -> None:
    resp = test_client.get("/vault")
    assert resp.status_code == 401


def test_server_rejects_read_role_for_editor_actions(test_client: TestClient) -> None:
    payload = {
        "frontmatter": {"type": "[[Recipe]]", "label": "Should fail"},
        "body": "Read role cannot edit\n",
    }
    resp = test_client.put("/notes/hummus.md", json=payload, headers={"Authorization": "Bearer read-token"})
    assert resp.status_code == 403


def test_server_notes_crud_and_concurrency(test_client: TestClient) -> None:
    headers = {"Authorization": "Bearer editor-token"}

    # GET list
    resp = test_client.get("/notes", headers=headers)
    assert resp.status_code == 200
    notes = resp.json()["notes"]
    paths = [n["path"] for n in notes]
    assert "hummus.md" in paths

    # GET single
    resp_note = test_client.get("/notes/hummus.md", headers=headers)
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
    resp_no_if_match = test_client.put("/notes/hummus.md", json=overwrite_payload, headers=headers)
    assert resp_no_if_match.status_code == 200
    assert resp_no_if_match.json()["frontmatter"]["label"] == "Hummus Overwrite"
    etag = resp_no_if_match.json()["etag"]

    # PUT with matching If-Match
    update_payload = {
        "frontmatter": {"type": "[[Recipe]]", "label": "Super Hummus"},
        "body": "Updated body\n",
    }
    resp_put = test_client.put(
        "/notes/hummus.md",
        json=update_payload,
        headers={"Authorization": "Bearer editor-token", "If-Match": f'"{etag}"'},
    )
    assert resp_put.status_code == 200
    assert resp_put.json()["frontmatter"]["label"] == "Super Hummus"

    # PUT with stale If-Match -> 409
    resp_stale = test_client.put(
        "/notes/hummus.md",
        json=update_payload,
        headers={"Authorization": "Bearer editor-token", "If-Match": f'"{etag}"'},
    )
    assert resp_stale.status_code == 409

    # DELETE
    resp_del = test_client.delete("/notes/hummus.md", headers=headers)
    assert resp_del.status_code == 204

    # GET deleted -> 404
    resp_missing = test_client.get("/notes/hummus.md", headers=headers)
    assert resp_missing.status_code == 404


def test_server_put_respects_if_none_match_create_only(test_client: TestClient) -> None:
    headers = {"Authorization": "Bearer editor-token"}
    payload = {
        "frontmatter": {"type": "[[Recipe]]", "label": "Create only"},
        "body": "Create only body\n",
    }
    resp_existing = test_client.put("/notes/hummus.md", json=payload, headers={**headers, "If-None-Match": "*"})
    assert resp_existing.status_code == 409

    resp_new = test_client.put("/notes/newly-created.md", json=payload, headers={**headers, "If-None-Match": "*"})
    assert resp_new.status_code == 200


def test_server_sparql_query(test_client: TestClient) -> None:
    sparql = "SELECT ?label WHERE { <https://example.org/data/chickpeas> <http://www.w3.org/2000/01/rdf-schema#label> ?label }"
    resp = test_client.post("/graph/sparql", json={"query": sparql}, headers={"Authorization": "Bearer read-token"})
    assert resp.status_code == 200
    results = resp.json()
    assert len(results["results"]) == 1
    assert "Chickpeas" in str(results["results"][0])


def test_server_sparql_allows_service_in_variable_and_iri(test_client: TestClient) -> None:
    query = "SELECT ?service WHERE { <https://example.org/data/hummus> <https://example.org/serviceName> ?service . }"
    resp = test_client.post("/graph/sparql", json={"query": query}, headers={"Authorization": "Bearer read-token"})
    assert resp.status_code == 200


def test_server_edges_and_search(test_client: TestClient) -> None:
    headers = {"Authorization": "Bearer read-token"}

    # Edges
    resp = test_client.get("/graph/edges", params={"subject": "https://example.org/data/hummus"}, headers=headers)
    assert resp.status_code == 200
    edges = resp.json()["edges"]
    assert len(edges) >= 1

    # Search
    resp_search = test_client.get("/search", params={"q": "Chickpeas"}, headers=headers)
    assert resp_search.status_code == 200
    assert len(resp_search.json()["triples"]) >= 1

    # Issues
    resp_issues = test_client.get("/graph/issues", headers=headers)
    assert resp_issues.status_code == 200
    assert "issues" in resp_issues.json()

    # Reindex requires editor role
    resp_reindex = test_client.post("/reindex", headers={"Authorization": "Bearer editor-token"})
    assert resp_reindex.status_code == 200
    assert resp_reindex.json()["status"] == "ok"


def test_server_session_auth_for_read_access(test_client: TestClient) -> None:
    login = test_client.post(
        "/auth/session/login",
        json={"username": "audience", "password": "audience-password"},
    )
    assert login.status_code == 200
    assert login.cookies.get("oxivault_session") is not None

    resp = test_client.get("/vault")
    assert resp.status_code == 200


def test_server_cors_preflight_for_allowed_origin(test_client: TestClient) -> None:
    resp = test_client.options(
        "/notes/hummus.md",
        headers={
            "Origin": "https://app.nalandabodhi.org",
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "https://app.nalandabodhi.org"


def test_server_presign_endpoints_enforce_role_and_return_url(test_client: TestClient) -> None:
    denied = test_client.post(
        "/objects/presign-put",
        json={"key": "masters/video.mp4", "expires_seconds": 300},
        headers={"Authorization": "Bearer read-token"},
    )
    assert denied.status_code == 403

    allowed = test_client.post(
        "/objects/presign-get",
        json={"key": "masters/video.mp4", "expires_seconds": 300},
        headers={"Authorization": "Bearer read-token"},
    )
    assert allowed.status_code == 200
    assert allowed.json()["url"].startswith("memory://presigned-get/masters/video.mp4")


def test_server_publication_helper_updates_note_metadata(test_client: TestClient) -> None:
    resp = test_client.post(
        "/publication/notes/hummus.md",
        json={"published": True, "visibility": "public"},
        headers={"Authorization": "Bearer editor-token"},
    )
    assert resp.status_code == 200
    publication = resp.json()["frontmatter"]["publication"]
    assert publication["published"] is True
    assert publication["visibility"] == "public"


def test_server_default_app_allows_requests_without_auth() -> None:
    store = MemoryStore()
    vault = Vault(store=store)
    rdf = b"""@prefix cul: <https://example.org/culinary#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
cul:Culinary a owl:Ontology .
"""
    vault.rdf2vault([RdfDocument(content=rdf, format="turtle")])
    client = TestClient(create_app(vault))
    resp = client.get("/vault")
    assert resp.status_code == 200


def test_server_publication_helper_does_not_mutate_on_invalid_copy_request(test_client: TestClient) -> None:
    before = test_client.get("/notes/hummus.md", headers={"Authorization": "Bearer editor-token"})
    assert before.status_code == 200
    assert "publication" not in before.json()["frontmatter"]

    publish = test_client.post(
        "/publication/notes/hummus.md",
        json={"published": True, "visibility": "public", "master_key": "masters/video.mp4"},
        headers={"Authorization": "Bearer editor-token"},
    )
    assert publish.status_code == 400

    after = test_client.get("/notes/hummus.md", headers={"Authorization": "Bearer editor-token"})
    assert after.status_code == 200
    assert "publication" not in after.json()["frontmatter"]


def test_server_publication_helper_returns_not_found_for_missing_master_object(test_client: TestClient) -> None:
    publish = test_client.post(
        "/publication/notes/hummus.md",
        json={
            "published": True,
            "visibility": "public",
            "master_key": "masters/missing.mp4",
            "public_prefix": "public",
        },
        headers={"Authorization": "Bearer editor-token"},
    )
    assert publish.status_code == 404


def test_server_session_logout_revokes_cookie_token(test_client: TestClient) -> None:
    login = test_client.post(
        "/auth/session/login",
        json={"username": "audience", "password": "audience-password"},
    )
    assert login.status_code == 200
    session_cookie = login.cookies.get("oxivault_session")
    assert session_cookie is not None

    logout = test_client.post("/auth/session/logout")
    assert logout.status_code == 200

    replay = test_client.get("/vault", headers={"Cookie": f"oxivault_session={session_cookie}"})
    assert replay.status_code == 401
