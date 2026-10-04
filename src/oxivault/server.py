"""FastAPI REST API server for oxivault."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, FastAPI, Header, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from oxivault.errors import (
    ConversionError,
    ObjectConflictError,
    ObjectNotFoundError,
    StoreKeyError,
)
from oxivault.store.base import _SENTINEL

if TYPE_CHECKING:
    from oxivault.vault import Vault


class NoteUpdatePayload(BaseModel):
    """Payload for updating or creating a note."""

    frontmatter: dict[str, Any] = Field(default_factory=dict)
    body: str = ""


class SparqlQueryPayload(BaseModel):
    """SPARQL query payload."""

    query: str


def _build_notes_router(vault: Vault) -> APIRouter:  # noqa: C901
    router = APIRouter(prefix="/notes", tags=["notes"])

    @router.get("")
    def list_notes(prefix: str = "") -> dict[str, Any]:
        """List notes in the vault."""
        notes = vault.list_notes(prefix=prefix)
        return {
            "notes": [
                {
                    "path": n.path,
                    "etag": n.etag,
                    "frontmatter": n.frontmatter,
                }
                for n in notes
            ]
        }

    @router.get("/{path:path}")
    def get_note(path: str) -> dict[str, Any]:
        """Retrieve a single note."""
        try:
            note = vault.get_note(path)
        except ObjectNotFoundError as err:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err)) from err
        except StoreKeyError as err:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err
        else:
            return {
                "path": note.path,
                "etag": note.etag,
                "frontmatter": note.frontmatter,
                "body": note.body,
            }

    @router.put("/{path:path}")
    def put_note(
        path: str,
        payload: NoteUpdatePayload,
        if_match: Annotated[str | None, Header()] = None,
        if_none_match: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """Create or update a note with optimistic concurrency."""
        expected_etag: str | object | None = _SENTINEL
        if if_match is not None:
            expected_etag = if_match.strip('"')
        elif if_none_match == "*":
            expected_etag = None

        try:
            updated = vault.put_note(
                path,
                frontmatter=payload.frontmatter,
                body=payload.body,
                expected_etag=expected_etag,
            )
        except ObjectConflictError as err:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(err)) from err
        except (ConversionError, StoreKeyError) as err:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err
        else:
            return {
                "path": updated.path,
                "etag": updated.etag,
                "frontmatter": updated.frontmatter,
                "body": updated.body,
            }

    @router.delete("/{path:path}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_note(path: str) -> Response:
        """Delete a note from the vault."""
        try:
            vault.delete_note(path)
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        except StoreKeyError as err:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err

    return router


def _build_graph_router(vault: Vault) -> APIRouter:  # noqa: C901
    router = APIRouter(tags=["graph"])

    @router.get("/vault")
    def get_vault_info() -> dict[str, Any]:
        """Return basic vault summary information."""
        triples_df = vault.triples()
        notes = vault.list_notes()
        return {
            "note_count": len(notes),
            "triple_count": len(triples_df),
        }

    @router.post("/graph/sparql")
    def run_sparql(payload: SparqlQueryPayload) -> dict[str, Any]:
        """Run read-only SPARQL query on the vault graph."""
        query_text = payload.query.strip()
        first_word = query_text.split(None, 1)[0].upper() if query_text else ""
        has_service_clause = (
            re.search(r"(?:^|[\s{.;])SERVICE\s+(?:SILENT\s+)?(?:<|\?)", query_text, flags=re.IGNORECASE) is not None
        )
        if first_word in ("INSERT", "DELETE", "DROP", "CLEAR", "LOAD", "CREATE") or has_service_clause:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Modifying SPARQL queries or remote SERVICE access are not permitted",
            )

        try:
            results = vault.query(query_text)
            serialized = []
            for row in results:
                if isinstance(row, (tuple, list)):
                    serialized.append([str(v) for v in row])
                else:
                    serialized.append(str(row))
        except Exception as err:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"SPARQL error: {err}") from err
        else:
            return {"results": serialized}

    @router.get("/graph/edges")
    def get_edges(subject: str) -> dict[str, Any]:
        """Get graph edges for a subject."""
        df = vault.neighbors(subject, direction="outbound")
        return {"edges": df.to_dicts()}

    @router.get("/search")
    def search_vault(q: Annotated[str, Query()]) -> dict[str, Any]:
        """Search literal triples and note bodies."""
        search_res = vault.search(q)
        body_matches = vault.search_body(q)
        return {
            "triples": search_res["triples"].to_dicts(),
            "body_notes": body_matches,
        }

    @router.get("/graph/issues")
    def get_issues() -> dict[str, Any]:
        """Get diagnostic issues from graph conversion."""
        issues = vault.issues()
        return {
            "issues": [
                {
                    "severity": iss.severity,
                    "message": iss.message,
                    "source_path": iss.source_path,
                }
                for iss in issues
            ]
        }

    @router.post("/reindex")
    def reindex_vault() -> dict[str, str]:
        """Force reindex and graph cache invalidation."""
        vault.invalidate()
        vault.triples()
        return {"status": "ok"}

    return router


def create_app(vault: Vault) -> FastAPI:
    """Create and configure FastAPI application for a vault."""
    app = FastAPI(title="OxiVault API", version="0.1.0")
    app.include_router(_build_notes_router(vault))
    app.include_router(_build_graph_router(vault))
    return app
