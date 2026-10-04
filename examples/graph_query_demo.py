"""Demonstrate graph queries, SPARQL, and note operations with OxiVault."""

from __future__ import annotations

import polars as pl

from oxivault.models import RdfDocument
from oxivault.store.memory import MemoryStore
from oxivault.vault import Vault


def main() -> None:
    # 1. Initialize an in-memory vault and seed with culinary graph
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

cul:Ingredient a owl:Class ;
    rdfs:label "Ingredient" .

data:chickpeas a cul:Ingredient ;
    rdfs:label "Chickpeas" .

data:tahini a cul:Ingredient ;
    rdfs:label "Tahini" .

data:hummus a cul:Recipe ;
    rdfs:label "Classic Hummus" ;
    rdfs:seeAlso data:chickpeas, data:tahini .
"""
    print("Ingesting initial RDF graph into vault...")
    vault.rdf2vault([RdfDocument(content=rdf, format="turtle")])

    # 2. Access canonical triple table via Polars
    print("\n--- Canonical Triples Table (Polars) ---")
    triples_df = vault.triples()
    print(triples_df.select(["subject", "predicate", "object", "datatype", "note_path"]))

    # 3. Query graph with SPARQL
    print("\n--- SPARQL Query ---")
    query = """
    PREFIX cul: <https://example.org/culinary#>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    SELECT ?recipe ?label WHERE {
        ?recipe a cul:Recipe ;
                rdfs:label ?label .
    }
    """
    for row in vault.query(query):
        if isinstance(row, (tuple, list)):
            print(f"Found recipe: {row[1]} <{row[0]}>")

    # 4. Outbound neighbors and backlinks
    print("\n--- Graph Edges & Backlinks ---")
    hummus_out = vault.neighbors("https://example.org/data/hummus", direction="outbound")
    print(f"Hummus outbound edges:\n{hummus_out.select(['predicate', 'object'])}")

    chickpea_backlinks = vault.backlinks("https://example.org/data/chickpeas")
    print(f"Chickpeas backlinks:\n{chickpea_backlinks.select(['subject', 'predicate'])}")

    # 5. Note CRUD operations through Vault facade
    print("\n--- Note CRUD Operations ---")
    note = vault.put_note(
        "falafel.md",
        frontmatter={"type": "[[Recipe]]", "label": "Crispy Falafel"},
        body="Deep-fried chickpea patties with herbs.\n",
    )
    print(f"Created note: {note.path} (ETag: {note.etag})")

    # 6. Verify cache invalidation & search
    search_hits = vault.search("Crispy Falafel")
    print(f"Search found literal: {search_hits['triples']['object'].to_list()}")

    body_hits = vault.search_body("Deep-fried")
    print(f"Body search found notes: {body_hits}")


if __name__ == "__main__":
    main()
