"""Tests for vault2rdf conversion."""

from __future__ import annotations

from rdflib import Graph, URIRef
from rdflib.namespace import RDF

from oxivault.models import RdfExport
from oxivault.store.memory import MemoryStore
from oxivault.vault import Vault


def test_vault2rdf_basic_schema_and_instance() -> None:
    # GIVEN a vault with root context, culinary ontology class, and recipe note
    store = MemoryStore()

    # Root context with standard prefixes and keyword aliases
    store.put(
        "context.jsonld",
        b"""{
  "@context": [
    {
      "@base": "https://example.org/",
      "type": "@type",
      "id": "@id",
      "owl": "http://www.w3.org/2002/07/owl#",
      "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
      "label": "rdfs:label"
    },
    "Ontologies/Culinary/context.jsonld"
  ]
}""",
    )

    # Culinary context
    store.put(
        "Ontologies/Culinary/context.jsonld",
        b"""{
  "@context": {
    "@base": "https://example.org/culinary#",
    "cul": "https://example.org/culinary#",
    "Recipe": "cul:Recipe",
    "prepTime": {
      "@id": "cul:prepTimeMinutes",
      "@type": "http://www.w3.org/2001/XMLSchema#integer"
    }
  }
}""",
    )

    # Culinary Class note
    store.put(
        "Ontologies/Culinary/Classes/Recipe.md",
        b"""---
type: owl:Class
label: Recipe
---
A recipe note.
""",
    )

    # Instance note
    store.put(
        "Recipes/hummus.md",
        b"""---
type: cul:Recipe
prepTime: 25
---
A delicious hummus recipe.
""",
    )

    vault = Vault(store=store)

    # WHEN exporting with vault2rdf
    export = vault.vault2rdf(source=False)

    # THEN export contains schema and data turtle with expected triples
    assert isinstance(export, RdfExport)

    g_schema = Graph()
    g_schema.parse(data=export.schema_ttl, format="turtle")

    g_data = Graph()
    g_data.parse(data=export.data_ttl, format="turtle")

    recipe_class = URIRef("https://example.org/culinary#Recipe")
    owl_class = URIRef("http://www.w3.org/2002/07/owl#Class")
    assert (recipe_class, RDF.type, owl_class) in g_schema

    hummus_inst = URIRef("https://example.org/hummus")
    assert (hummus_inst, RDF.type, recipe_class) in g_data
