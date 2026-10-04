"""Demonstrate S3, Git, and FastAPI server in OxiVault."""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from oxivault.models import RdfDocument
from oxivault.server import create_app
from oxivault.store.git import GitStore
from oxivault.vault import Vault


def main() -> None:
    print("=== OxiVault Git Backend & HTTP API Demo ===")

    with tempfile.TemporaryDirectory() as tmp_dir:
        git_dir = Path(tmp_dir) / "vault.git"
        print(f"Initializing bare Git vault at {git_dir}...")
        store = GitStore.init_bare(git_dir)
        vault = Vault(store=store)

        # 1. Ingest initial RDF
        rdf = b"""@prefix cul: <https://example.org/culinary#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix data: <https://example.org/data/> .

cul:Culinary a owl:Ontology ; rdfs:label "Culinary Ontology" .
cul:Recipe a owl:Class ; rdfs:label "Recipe" .
data:hummus a cul:Recipe ; rdfs:label "Classic Hummus" .
"""
        vault.rdf2vault([RdfDocument(content=rdf, format="turtle")])
        print("Ingested RDF into Git store.")

        # 2. Check Git history
        hist = store.history("hummus.md")
        print(f"Git commit history for hummus.md: {len(hist)} commit(s)")

        # 3. Create FastAPI app and exercise endpoints via client
        app = create_app(vault)
        client = TestClient(app)

        vault_info = client.get("/vault").json()
        print("GET /vault response:", vault_info)

        notes = client.get("/notes").json()
        print("GET /notes response:", notes)

        sparql_res = client.post(
            "/graph/sparql",
            json={
                "query": "SELECT ?label WHERE { <https://example.org/data/hummus> <http://www.w3.org/2000/01/rdf-schema#label> ?label }"
            },
        ).json()
        print("POST /graph/sparql response:", sparql_res)


if __name__ == "__main__":
    main()
