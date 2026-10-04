"""Runnable example demonstrating bidirectional vault2rdf and rdf2vault roundtrip."""

from __future__ import annotations

from oxivault.models import RdfDocument
from oxivault.store.memory import MemoryStore
from oxivault.vault import Vault


def main() -> None:
    # 1. Start with an empty in-memory vault and ingest RDF
    store = MemoryStore()
    vault = Vault(store=store)

    initial_ttl = b"""@prefix cul: <https://example.org/culinary#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix data: <https://example.org/data/> .

cul:Culinary a owl:Ontology ;
    rdfs:label "Culinary Ontology" .

cul:Recipe a owl:Class ;
    rdfs:label "Recipe" .

data:hummus a cul:Recipe ;
    rdfs:label "Classic Hummus" .
"""
    print("Ingesting initial RDF into empty vault...")
    report1 = vault.rdf2vault([RdfDocument(content=initial_ttl, format="turtle")])
    print(f"Created notes: {report1.created_notes}")
    print(f"Contexts written: {report1.written_contexts}")

    # 2. Add local body notes in the store
    note_content = store.get("hummus.md").decode("utf-8")
    note_content += "\n## Notes\nHandcrafted recipe in the vault.\n"
    store.put("hummus.md", note_content.encode("utf-8"))
    print("Added local Markdown body to hummus.md.")

    # 3. Export to RDF via vault2rdf
    print("\nExporting vault to RDF...")
    export = vault.vault2rdf(source=True)
    print("Exported schema TTL lines:", len(export.schema_ttl.splitlines()))
    print("Exported data TTL lines:", len(export.data_ttl.splitlines()))

    # 4. Ingest back into the vault and demonstrate body preservation
    print("\nRe-ingesting data TTL back into the vault...")
    before = {key: store.stat(key) for key in store.list()}
    report2 = vault.rdf2vault([RdfDocument(content=export.data_ttl.encode("utf-8"), format="turtle")])
    print(f"Notes updated: {report2.updated_notes}, unchanged: {report2.unchanged_notes}")

    final_note = store.get("hummus.md").decode("utf-8")
    assert "Handcrafted recipe in the vault." in final_note
    assert {key: store.stat(key) for key in store.list()} == before
    print("\nSuccessfully preserved markdown body across ingest!")


if __name__ == "__main__":
    main()
