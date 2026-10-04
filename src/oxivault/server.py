"""FastAPI REST API server for oxivault."""

import re
import secrets
from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Cookie, Depends, FastAPI, Form, HTTPException, Query, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel, Field
from starlette.requests import Request

from oxivault.errors import (
    ConversionError,
    ObjectConflictError,
    ObjectNotFoundError,
    StoreError,
    StoreKeyError,
)
from oxivault.store.base import _SENTINEL, validate_key
from oxivault.vault import Vault

Role = Literal["read", "editor"]


class ApiUserCredential(BaseModel):
    """Configured user credentials for token/session login."""

    password: str
    role: Role


class ApiServerConfig(BaseModel):
    """Runtime server configuration for auth and CORS."""

    auth_enabled: bool = False
    read_tokens: set[str] = Field(default_factory=set)
    editor_tokens: set[str] = Field(default_factory=set)
    users: dict[str, ApiUserCredential] = Field(default_factory=dict)
    session_cookie_name: str = "oxivault_session"
    session_cookie_secure: bool = False
    session_cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    cors_allowed_origins: list[str] = Field(default_factory=list)


class NoteUpdatePayload(BaseModel):
    """Payload for updating or creating a note."""

    frontmatter: dict[str, Any] = Field(default_factory=dict)
    body: str = ""


class SparqlQueryPayload(BaseModel):
    """SPARQL query payload."""

    query: str


class SessionLoginPayload(BaseModel):
    """Session login payload."""

    username: str
    password: str


class PresignPayload(BaseModel):
    """Payload for presigned object URL requests."""

    key: str
    expires_seconds: int = Field(default=900, gt=0)


class PublicationPayload(BaseModel):
    """Payload for publication metadata updates."""

    published: bool
    visibility: Literal["public", "private"]
    master_key: str | None = None
    public_prefix: str | None = None
    public_bucket: str | None = None


def _build_notes_router(  # noqa: C901
    vault: Vault,
    *,
    require_read: Callable[..., Any],
    require_editor: Callable[..., Any],
) -> APIRouter:
    router = APIRouter(prefix="/notes", tags=["notes"], dependencies=[Depends(require_read)])

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

    @router.put("/{path:path}", dependencies=[Depends(require_editor)])
    def put_note(
        path: str,
        payload: NoteUpdatePayload,
        request: Request,
    ) -> dict[str, Any]:
        """Create or update a note with optimistic concurrency."""
        if_match = request.headers.get("if-match")
        if_none_match = request.headers.get("if-none-match")
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

    @router.delete("/{path:path}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_editor)])
    def delete_note(path: str) -> Response:
        """Delete a note from the vault."""
        try:
            vault.delete_note(path)
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        except StoreKeyError as err:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err

    return router


def _build_graph_router(  # noqa: C901
    vault: Vault,
    *,
    require_read: Callable[..., Any],
    require_editor: Callable[..., Any],
) -> APIRouter:
    router = APIRouter(tags=["graph"], dependencies=[Depends(require_read)])

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
    def search_vault(
        q: Annotated[str, Query()],
    ) -> dict[str, Any]:
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

    @router.post("/reindex", dependencies=[Depends(require_editor)])
    def reindex_vault() -> dict[str, str]:
        """Force reindex and graph cache invalidation."""
        vault.invalidate()
        vault.triples()
        return {"status": "ok"}

    return router


def create_app(vault: Vault, config: ApiServerConfig | None = None) -> FastAPI:  # noqa: C901, PLR0915
    """Create and configure FastAPI application for a vault."""
    cfg = config if config is not None else ApiServerConfig()
    app = FastAPI(title="OxiVault API", version="0.1.0")
    if cfg.cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cfg.cors_allowed_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    oauth2 = OAuth2PasswordBearer(tokenUrl="/auth/token", auto_error=False)
    issued_tokens: dict[str, Role] = {}

    def _issue_token(role: Role) -> str:
        token = secrets.token_urlsafe(32)
        issued_tokens[token] = role
        return token

    def _resolve_role_from_token(token: str | None) -> Role | None:
        if token is None:
            return None
        if token in cfg.editor_tokens:
            return "editor"
        if token in cfg.read_tokens:
            return "read"
        return issued_tokens.get(token)

    async def _authenticated_role(
        bearer_token: str | None = Depends(oauth2),
        session_token: str | None = Cookie(default=None, alias=cfg.session_cookie_name),
    ) -> str:
        if not cfg.auth_enabled:
            return "editor"

        role = _resolve_role_from_token(bearer_token) or _resolve_role_from_token(session_token)
        if role is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return role

    async def require_read(role: str = Depends(_authenticated_role)) -> str:
        return role

    async def require_editor(role: str = Depends(_authenticated_role)) -> str:
        if role != "editor":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Editor role required")
        return role

    @app.post("/auth/token")
    async def issue_access_token(
        username: Annotated[str, Form()],
        password: Annotated[str, Form()],
    ) -> dict[str, str]:
        """Issue bearer token for configured users using OAuth2 password flow."""
        user = cfg.users.get(username)
        if user is None or password != user.password:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password",
                headers={"WWW-Authenticate": "Bearer"},
            )
        token = _issue_token(user.role)
        return {"access_token": token, "token_type": "bearer", "role": user.role}

    @app.post("/auth/session/login")
    async def login_session(payload: SessionLoginPayload, response: Response) -> dict[str, str]:
        """Create cookie-backed session for browser flows."""
        user = cfg.users.get(payload.username)
        if user is None or payload.password != user.password:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")
        token = _issue_token(user.role)
        response.set_cookie(
            key=cfg.session_cookie_name,
            value=token,
            httponly=True,
            secure=cfg.session_cookie_secure,
            samesite=cfg.session_cookie_samesite,
        )
        return {"status": "ok", "role": user.role}

    @app.post("/auth/session/logout")
    async def logout_session(
        response: Response,
        session_token: Annotated[str | None, Cookie(alias=cfg.session_cookie_name)] = None,
    ) -> dict[str, str]:
        """Clear browser session cookie."""
        if session_token is not None:
            issued_tokens.pop(session_token, None)
        response.delete_cookie(cfg.session_cookie_name)
        return {"status": "ok"}

    @app.post("/objects/presign-put", dependencies=[Depends(require_editor)])
    async def presign_put(
        payload: PresignPayload,
    ) -> dict[str, str | int]:
        """Issue direct-upload presigned URL."""
        try:
            return {
                "key": payload.key,
                "expires_seconds": payload.expires_seconds,
                "url": vault.store.presign_put(payload.key, expires_seconds=payload.expires_seconds),
            }
        except (StoreError, StoreKeyError, ValueError) as err:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err

    @app.post("/objects/presign-get", dependencies=[Depends(require_read)])
    async def presign_get(
        payload: PresignPayload,
    ) -> dict[str, str | int]:
        """Issue private playback/download presigned URL."""
        try:
            return {
                "key": payload.key,
                "expires_seconds": payload.expires_seconds,
                "url": vault.store.presign_get(payload.key, expires_seconds=payload.expires_seconds),
            }
        except (StoreError, StoreKeyError, ValueError) as err:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err

    @app.post("/publication/notes/{path:path}", dependencies=[Depends(require_editor)])
    async def publish_note(  # noqa: C901, PLR0912
        path: str,
        payload: PublicationPayload,
    ) -> dict[str, Any]:
        """Update publication metadata and optionally copy a verified master object."""
        # Validate the note target before any optional copy side effects.
        try:
            vault.get_note(path)
        except StoreKeyError as err:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err
        except ObjectNotFoundError as err:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err)) from err

        try:
            copied_master: dict[str, str] | None = None
            copy_requested = any(
                value is not None for value in (payload.master_key, payload.public_prefix, payload.public_bucket)
            )
            if copy_requested:
                if payload.master_key is None or payload.public_prefix is None:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="master_key and public_prefix are required when copy-based publication is requested",
                    )
                if not payload.published:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="copy-based publication requires published=true",
                    )

                source_key = validate_key(payload.master_key)
                target_key = f"{payload.public_prefix.strip('/')}/{source_key}"
                stat = vault.store.stat(source_key)
                copy_verified = getattr(vault.store, "copy_verified", None)
                if not callable(copy_verified):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Configured store backend does not support copy-based publication",
                    )

                copied_etag = copy_verified(
                    source_key,
                    target_key,
                    expected_etag=stat.etag,
                    target_bucket=payload.public_bucket,
                )

                copied_master = {
                    "source_key": source_key,
                    "target_key": target_key,
                    "etag": copied_etag,
                }
                if payload.public_bucket is not None:
                    copied_master["target_bucket"] = payload.public_bucket
        except StoreKeyError as err:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err
        except ObjectNotFoundError as err:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err)) from err
        except ObjectConflictError as err:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(err)) from err
        except StoreError as err:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err

        try:
            updated = vault.set_note_publication(
                path,
                published=payload.published,
                visibility=payload.visibility,
            )
        except (StoreKeyError, ConversionError) as err:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err
        except ObjectNotFoundError as err:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err)) from err
        else:
            response: dict[str, Any] = {
                "path": updated.path,
                "etag": updated.etag,
                "frontmatter": updated.frontmatter,
                "body": updated.body,
            }
            if copied_master is not None:
                response["copied_master"] = copied_master
            return response

    app.include_router(_build_notes_router(vault, require_read=require_read, require_editor=require_editor))
    app.include_router(_build_graph_router(vault, require_read=require_read, require_editor=require_editor))
    return app
