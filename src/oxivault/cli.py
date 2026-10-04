"""CLI interface for oxivault."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import click

from oxivault.errors import OxiVaultError
from oxivault.models import RdfDocument
from oxivault.store.local import LocalDirStore
from oxivault.vault import Vault

if TYPE_CHECKING:
    from collections.abc import Iterator


@contextmanager
def _cli_errors() -> Iterator[None]:
    try:
        yield
    except (OxiVaultError, OSError) as err:
        raise click.ClickException(str(err)) from err


@click.group()
def cli() -> None:
    """OxiVault CLI: Vault-LD knowledge graph store on object storage."""


@cli.command("vault2rdf")
@click.argument("vault", type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path))
@click.option("--out-dir", type=click.Path(file_okay=False, dir_okay=True, path_type=Path), default=Path())
@click.option("--source", is_flag=True, default=False, help="Include vld:path placement triples for roundtrip.")
def vault2rdf_cmd(vault: Path, out_dir: Path, source: bool) -> None:
    """Export a Vault-LD vault to RDF files (schema.ttl and data.ttl)."""
    with _cli_errors():
        store = LocalDirStore(root_dir=vault)
        v = Vault(store=store)
        export = v.vault2rdf(source=source)

        out_dir.mkdir(parents=True, exist_ok=True)
        schema_file = out_dir / "schema.ttl"
        data_file = out_dir / "data.ttl"

        schema_file.write_text(export.schema_ttl, encoding="utf-8")
        data_file.write_text(export.data_ttl, encoding="utf-8")

    click.echo(f"Exported schema layer to {schema_file}")
    click.echo(f"Exported data layer to {data_file}")
    for issue in export.issues:
        click.echo(f"[{issue.severity.upper()}] {issue.message}", err=True)


@cli.command("rdf2vault")
@click.argument("vault", type=click.Path(file_okay=False, dir_okay=True, path_type=Path))
@click.argument(
    "rdf_files", nargs=-1, type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path), required=True
)
@click.option(
    "--nest", is_flag=True, default=False, help="Organise newly created schema files into hierarchy-derived subfolders."
)
def rdf2vault_cmd(vault: Path, rdf_files: tuple[Path, ...], nest: bool) -> None:
    """Ingest RDF files into a Vault-LD vault."""
    for f in rdf_files:
        if f.suffix.lower() not in {".ttl", ".nt"}:
            raise click.ClickException(f"Unsupported RDF extension: {f.suffix}; use .ttl or .nt")

    with _cli_errors():
        store = LocalDirStore(root_dir=vault)
        v = Vault(store=store)
        docs: list[RdfDocument] = []
        for f in rdf_files:
            fmt = "nt" if f.suffix.lower() == ".nt" else "turtle"
            docs.append(RdfDocument(content=f.read_bytes(), format=fmt))
        report = v.rdf2vault(docs, nest=nest)
    click.echo(
        f"Notes created: {len(report.created_notes)}, updated: {len(report.updated_notes)}, unchanged: {len(report.unchanged_notes)}"
    )
    for ctx in report.written_contexts:
        click.echo(f"Context written: {ctx}")
    for issue in report.issues:
        click.echo(f"[{issue.severity.upper()}] {issue.message}", err=True)


@cli.command("query")
@click.argument("vault", type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path))
@click.argument("sparql", type=str)
def query_cmd(vault: Path, sparql: str) -> None:
    """Execute a SPARQL query against a Vault-LD vault."""
    with _cli_errors():
        store = LocalDirStore(root_dir=vault)
        v = Vault(store=store)
        results = v.query(sparql)
        for row in results:
            click.echo(row)
