"""Tests for oxivault CLI commands."""

from __future__ import annotations

from click.testing import CliRunner

from oxivault.cli import cli


def test_cli_vault2rdf_and_rdf2vault_roundtrip(tmp_path) -> None:
    runner = CliRunner()

    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    out_dir = tmp_path / "out"

    rdf_file = tmp_path / "input.ttl"
    rdf_file.write_text(
        """@prefix cul: <https://example.org/culinary#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix data: <https://example.org/data/> .

cul:Culinary a owl:Ontology ;
    rdfs:label "Culinary Ontology" .

cul:Recipe a owl:Class ;
    rdfs:label "Recipe" .

data:hummus a cul:Recipe ;
    rdfs:label "Hummus" .
"""
    )

    # 1. Test rdf2vault
    res_ingest = runner.invoke(cli, ["rdf2vault", str(vault_dir), str(rdf_file)])
    assert res_ingest.exit_code == 0
    assert (vault_dir / "hummus.md").exists()
    assert (vault_dir / "context.jsonld").exists()

    # 2. Test vault2rdf
    res_export = runner.invoke(cli, ["vault2rdf", str(vault_dir), "--out-dir", str(out_dir), "--source"])
    assert res_export.exit_code == 0
    assert (out_dir / "schema.ttl").exists()
    assert (out_dir / "data.ttl").exists()

    # 3. Test query command
    res_query = runner.invoke(
        cli,
        [
            "query",
            str(vault_dir),
            "SELECT ?label WHERE { <https://example.org/data/hummus> <http://www.w3.org/2000/01/rdf-schema#label> ?label }",
        ],
    )
    assert res_query.exit_code == 0
    assert "Hummus" in res_query.output
