"""Regression coverage for conversion boundary failures."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from click.testing import CliRunner
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF, XSD

from oxivault.cli import cli
from oxivault.config import VaultConfig
from oxivault.errors import ObjectReadLimitError, OxiVaultError, RdfParseError
from oxivault.models import RdfDocument
from oxivault.store.memory import MemoryStore
from oxivault.triples import build_triple_dataframe
from oxivault.vault import Vault

CONTEXT = b'{"@context":{"@base":"https://example.org/","type":"@type"}}'
NOTE = b"---\ntype: https://example.org/Thing\n---\n"


def populated_store():
    store = MemoryStore()
    store.put("context.jsonld", CONTEXT)
    store.put("nested/item.md", NOTE)
    return store


@pytest.mark.parametrize(
    ("literal", "datatype", "language"),
    [
        (Literal("Bonjour", lang="fr"), str(RDF.langString), "fr"),
        (Literal("Hello"), str(XSD.string), None),
        (Literal(3), str(XSD.integer), None),
    ],
)
def test_literal_identity_in_table(literal, datatype, language):
    table = build_triple_dataframe([(URIRef("urn:s"), URIRef("urn:p"), literal, "n.md", "data")])
    assert table.select("datatype", "lang").row(0) == (datatype, language)


def test_default_export_omits_placement():
    export = Vault(populated_store()).vault2rdf()
    graph = Graph().parse(data=export.data_ttl, format="turtle")
    assert len(graph) == 1
    assert (URIRef("https://example.org/item"), RDF.type, URIRef("https://example.org/Thing")) in graph


def test_source_export_keeps_placement():
    export = Vault(populated_store()).vault2rdf(source=True)
    graph = Graph().parse(data=export.data_ttl, format="turtle")
    assert (
        URIRef("https://example.org/item"),
        URIRef("https://github.com/The-Knowledge-Graph-Guys/vault-ld#path"),
        Literal("nested/item.md"),
    ) in graph


def test_empty_document_sequence_is_rejected_without_writes():
    store = MemoryStore()
    with pytest.raises(RdfParseError):
        Vault(store).rdf2vault([])
    assert store.list() == []


def test_empty_graph_preserves_notes():
    store = populated_store()
    before = {key: store.get(key) for key in store.list()}
    Vault(store).rdf2vault([RdfDocument(b"", "turtle")])
    assert {key: store.get(key) for key in store.list()} == before


@pytest.mark.parametrize("operation", ["vault2rdf", "rdf2vault", "triples", "query_graph"])
def test_configured_context_limit_is_enforced(operation):
    store = populated_store()
    vault = Vault(store, VaultConfig(max_context_bytes=len(CONTEXT) - 1))
    args = ([RdfDocument(b"", "turtle")],) if operation == "rdf2vault" else ()
    with pytest.raises(OxiVaultError):
        getattr(vault, operation)(*args)
    assert store.get("context.jsonld") == CONTEXT


def test_referenced_context_limit_applies_to_arbitrary_filenames():
    store = populated_store()
    store.put("context.jsonld", b'{"@context":["vocabulary.txt"]}')
    store.put("vocabulary.txt", CONTEXT + b" " * 100)
    with pytest.raises(OxiVaultError):
        Vault(store, VaultConfig(max_context_bytes=80)).vault2rdf()


def test_context_limit_is_per_call():
    store = populated_store()
    with ThreadPoolExecutor() as pool:
        small = pool.submit(Vault(store, VaultConfig(max_context_bytes=1)).vault2rdf)
        large = pool.submit(Vault(store, VaultConfig(max_context_bytes=len(CONTEXT))).vault2rdf)
        with pytest.raises(OxiVaultError):
            small.result()
        assert large.result().data_ttl


def test_context_limit_can_be_raised_above_reference_default():
    store = populated_store()
    content = CONTEXT + b" " * (4 << 20)
    store.put("context.jsonld", content)
    export = Vault(store, VaultConfig(max_context_bytes=len(content))).vault2rdf()
    assert export.data_ttl


def test_context_synthesis_limit_fails_without_destination_writes():
    store = MemoryStore()
    with pytest.raises(ObjectReadLimitError):
        Vault(store, VaultConfig(max_context_bytes=1)).rdf2vault([RdfDocument(b"", "turtle")])
    assert store.list() == []


def test_total_snapshot_budget_is_enforced():
    store = populated_store()
    with pytest.raises(ObjectReadLimitError):
        Vault(store, VaultConfig(max_snapshot_bytes=len(CONTEXT) + len(NOTE) - 1)).vault2rdf()


@pytest.mark.parametrize("field", ["max_read_bytes", "max_context_bytes", "max_snapshot_bytes"])
def test_limits_must_be_positive(field):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        VaultConfig.model_validate({field: 0})


@pytest.mark.parametrize("content", [b"{broken", b"[]"])
@pytest.mark.parametrize("operation", ["vault2rdf", "rdf2vault"])
def test_invalid_root_context_raises_domain_error(content, operation, capsys):
    store = populated_store()
    store.put("context.jsonld", content)
    args = ([RdfDocument(b"", "turtle")],) if operation == "rdf2vault" else ()
    with pytest.raises(OxiVaultError):
        getattr(Vault(store), operation)(*args)
    assert capsys.readouterr() == ("", "")
    assert store.get("context.jsonld") == content


@pytest.mark.parametrize("suffix", [".jsonld", ".xml", ".foo", ".ntriples"])
def test_cli_refuses_unknown_extensions_before_creating_vault(tmp_path, suffix):
    source = tmp_path / ("input" + suffix)
    source.write_text("<urn:s> <urn:p> <urn:o> .", encoding="utf-8")
    destination = tmp_path / "vault"
    result = CliRunner().invoke(cli, ["rdf2vault", str(destination), str(source)])
    assert result.exit_code != 0
    assert "Unsupported" in result.output
    assert not destination.exists()


@pytest.mark.parametrize("context", [None, b"{broken"])
def test_cli_context_failure_is_clean(tmp_path, context):
    if context is not None:
        (tmp_path / "context.jsonld").write_bytes(context)
    result = CliRunner().invoke(cli, ["vault2rdf", str(tmp_path)])
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "Error:" in result.output
    assert "context" in result.output


@pytest.mark.parametrize("format_name", ["turtle", "nt"])
def test_malformed_rdf_preserves_cause(format_name):
    with pytest.raises(RdfParseError) as exc:
        Vault(MemoryStore()).rdf2vault([RdfDocument(b"not RDF", format_name)])
    assert exc.value.__cause__ is not None
