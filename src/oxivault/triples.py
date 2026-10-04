"""Derived Polars triple table and graph query functionality."""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl
from rdflib import Literal
from rdflib.namespace import RDF

if TYPE_CHECKING:
    from rdflib import URIRef


def build_triple_dataframe(provenance_triples: list[tuple[URIRef, URIRef, object, str, str]]) -> pl.DataFrame:
    """Build a canonical Polars DataFrame from provenance-annotated triples.

    Columns:
        subject, predicate, object, datatype, lang, note_path, layer
    """
    xsd_string = "http://www.w3.org/2001/XMLSchema#string"

    subjects: list[str] = []
    predicates: list[str] = []
    objects: list[str] = []
    datatypes: list[str | None] = []
    langs: list[str | None] = []
    note_paths: list[str] = []
    layers: list[str] = []

    for s, p, o, note_path, layer in provenance_triples:
        subjects.append(str(s))
        predicates.append(str(p))
        if isinstance(o, Literal):
            objects.append(str(o))
            dt = str(RDF.langString) if o.language else str(o.datatype) if o.datatype else xsd_string
            datatypes.append(dt)
            langs.append(o.language)
        else:
            objects.append(str(o))
            datatypes.append(None)
            langs.append(None)
        note_paths.append(note_path)
        layers.append(layer)

    schema = {
        "subject": pl.String,
        "predicate": pl.String,
        "object": pl.String,
        "datatype": pl.String,
        "lang": pl.String,
        "note_path": pl.String,
        "layer": pl.String,
    }

    if not subjects:
        return pl.DataFrame(schema=schema)

    return pl.DataFrame(
        {
            "subject": subjects,
            "predicate": predicates,
            "object": objects,
            "datatype": datatypes,
            "lang": langs,
            "note_path": note_paths,
            "layer": layers,
        },
        schema=schema,
    )
