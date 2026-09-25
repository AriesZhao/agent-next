"""HTTP API for NL2SQL structured data sources (§10).

Thin adapters only: validate HTTP, call :class:`DataSourceService` /
:func:`execute_data_source_query` in ``infra/``, and map domain exceptions to
localized :class:`OctopError` envelopes. Credentials are written-only and always
masked on read; the network-bound operations (connection test, schema
introspection, query preview) run in an executor thread so the event loop never
blocks on a driver call (design §8, AGENTS.md "no blocking I/O in async").
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from octop.api.deps import current_user, get_server, require_permission
from octop.infra.data_sources.connection import ConnectionFailure
from octop.infra.data_sources.generate import ModelNotConfigured
from octop.infra.data_sources.service import (
    MAX_SOURCES_PER_OWNER,
    MAX_TABLES_PER_SOURCE,
    DataSourceService,
)
from octop.infra.data_sources.tools import execute_data_source_query
from octop.infra.data_sources.types import PipelineOutcome
from octop.infra.db.repos.data_sources import AllowedTable, Annotation
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.server import OctopServer
from octop.infra.users.identity import User
from octop.infra.utils.locale import resolve_request_locale

router = APIRouter(prefix="/data-sources")
logger = logging.getLogger(__name__)

_ENGINES = ("postgres", "mysql")


# ---------------------------------------------------------------------------
# request bodies
# ---------------------------------------------------------------------------


class CreateSourceBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    engine: str = Field(pattern="^(postgres|mysql)$")
    description: str = Field(default="", max_length=2000)
    default_open: bool = False
    shared: bool = False
    icon_name: str = Field(default="", max_length=64)
    max_rows: int = Field(default=100, ge=1, le=5000)
    timeout_ms: int = Field(default=5000, ge=100, le=120000)
    context_char_budget: int = Field(default=4000, ge=200, le=100000)
    allow_im_export: bool = False


class UpdateSourceBody(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    default_open: bool | None = None
    shared: bool | None = None
    icon_name: str | None = Field(default=None, max_length=64)
    max_rows: int | None = Field(default=None, ge=1, le=5000)
    timeout_ms: int | None = Field(default=None, ge=100, le=120000)
    context_char_budget: int | None = Field(default=None, ge=200, le=100000)
    allow_im_export: bool | None = None


class ConnectionBody(BaseModel):
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    database_name: str = Field(min_length=1, max_length=200)
    schema_name: str = Field(default="", max_length=200)
    username: str = Field(min_length=1, max_length=200)
    password: str | None = Field(
        default=None,
        description="Write-only. Omit to keep the stored password; empty string clears it.",
    )
    ssl_mode: str = Field(default="", max_length=32)


class ProbeConnectionBody(ConnectionBody):
    engine: str = Field(pattern="^(postgres|mysql)$")


class AllowedTableBody(BaseModel):
    table_name: str = Field(min_length=1, max_length=200)
    columns: list[str] = Field(default_factory=list)


class AllowlistBody(BaseModel):
    tables: list[AllowedTableBody] = Field(default_factory=list)


class AnnotationBody(BaseModel):
    scope: str = Field(pattern="^(table|column)$")
    table_name: str = Field(min_length=1, max_length=200)
    column_name: str = Field(default="", max_length=200)
    note: str = Field(default="", max_length=2000)


class AnnotationsBody(BaseModel):
    annotations: list[AnnotationBody] = Field(default_factory=list)


class QueryBody(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class SettingsBody(BaseModel):
    enabled: bool
    model: str | None = Field(
        default=None,
        description="NL2SQL chat model ref 'provider/model'; required to enable (fail-fast, §8.1).",
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _service(server: OctopServer) -> DataSourceService:
    if server.services is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "data source services are not initialized")
    return DataSourceService(server.services)


def _is_admin(user: User) -> bool:
    return bool(user.is_admin)


def _source_payload(server: OctopServer, row: Any) -> dict[str, Any]:
    payload = _service(server).to_public_payload(row)
    owner = payload.get("owner_user_id")
    username = display = None
    if server.services is not None and owner is not None:
        user_repo = getattr(server.services, "user_repo", None)
        if user_repo is not None:
            owner_user = user_repo.get(int(owner))
            if owner_user is not None:
                username = owner_user.username
                display = owner_user.display_name or owner_user.username
    payload["data_source_id"] = row.id
    payload["owner_username"] = username
    payload["owner_display_name"] = display
    return payload


def _outcome_payload(outcome: PipelineOutcome) -> dict[str, Any]:
    return {
        "status": outcome.status,
        "text": outcome.text,
        "natural_query": outcome.natural_query,
        "generated_sql": outcome.generated_sql,
        "executed_sql": outcome.executed_sql,
        "row_count": outcome.row_count,
        "latency_ms": outcome.latency_ms,
        "attempts": outcome.attempts,
        "spilled": outcome.spilled,
        "file_path": outcome.file_path,
        "category": outcome.category,
    }


def _map_error(exc: Exception, *, locale: str) -> OctopError:
    if isinstance(exc, OctopError):
        return exc
    if isinstance(exc, (LookupError, FileNotFoundError)):
        return OctopError.localized(ErrorCode.DATA_SOURCE_NOT_FOUND, locale)
    if isinstance(exc, PermissionError):
        return OctopError.localized(ErrorCode.DATA_SOURCE_FORBIDDEN, locale)
    if isinstance(exc, ModelNotConfigured):
        return OctopError.localized(ErrorCode.DATA_SOURCE_MODEL_NOT_CONFIGURED, locale)
    if isinstance(exc, ConnectionFailure):
        # Never surface the raw driver message (§8.6); connection failure is generic.
        return OctopError.localized(ErrorCode.DATA_SOURCE_CONNECTION_FAILED, locale)
    if isinstance(exc, ValueError):
        text = str(exc).lower()
        if "unsupported engine" in text:
            return OctopError.localized(ErrorCode.DATA_SOURCE_ENGINE_UNSUPPORTED, locale)
        if "already exists" in text:
            return OctopError.localized(ErrorCode.DATA_SOURCE_NAME_TAKEN, locale)
        if "name is required" in text or "invalid data source name" in text:
            return OctopError.localized(ErrorCode.DATA_SOURCE_NAME_INVALID, locale)
        if "at most" in text and "data source" in text:
            return OctopError.localized(ErrorCode.DATA_SOURCE_LIMIT, locale)
    logger.exception("unhandled error in data source router: %s", exc)
    return OctopError.localized(ErrorCode.INTERNAL_ERROR, locale, details={"cause": str(exc)})


# ---------------------------------------------------------------------------
# instance settings (fail-fast capability gate, §8.1)
# ---------------------------------------------------------------------------


@router.get("/settings", summary="Get data-source NL2SQL settings")
async def get_settings(
    server: OctopServer = Depends(get_server),
    _user: User = Depends(current_user),
) -> dict[str, Any]:
    service = _service(server)
    return {
        "enabled": service.feature_enabled(),
        "model": service.model_ref(),
        "engines": list(_ENGINES),
        "limits": {"max_sources_per_owner": MAX_SOURCES_PER_OWNER},
    }


@router.put("/settings", summary="Enable or disable data sources and set the NL2SQL model")
async def put_settings(
    body: SettingsBody,
    request: Request,
    server: OctopServer = Depends(get_server),
    _admin: User = Depends(require_permission("data_source_settings")),
) -> dict[str, Any]:
    locale = resolve_request_locale(request)
    try:
        await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: _service(server).set_feature_enabled(enabled=body.enabled, model=body.model),
        )
    except Exception as exc:
        raise _map_error(exc, locale=locale) from exc
    service = _service(server)
    return {"enabled": service.feature_enabled(), "model": service.model_ref()}


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.get("", summary="List visible data sources")
async def list_sources(
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> list[dict[str, Any]]:
    rows = _service(server).list_visible(actor_user_id=user.id, is_admin=_is_admin(user))
    return [_source_payload(server, row) for row in rows]


@router.post("", status_code=status.HTTP_201_CREATED, summary="Create a data source")
async def create_source(
    body: CreateSourceBody,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(require_permission("data_sources")),
) -> dict[str, Any]:
    locale = resolve_request_locale(request)
    try:
        row = _service(server).create(
            owner_user_id=user.id,
            name=body.name.strip(),
            engine=body.engine,
            description=body.description.strip(),
            default_open=body.default_open,
            shared=body.shared,
            icon_name=body.icon_name.strip(),
            max_rows=body.max_rows,
            timeout_ms=body.timeout_ms,
            context_char_budget=body.context_char_budget,
            allow_im_export=body.allow_im_export,
        )
        return _source_payload(server, row)
    except Exception as exc:
        raise _map_error(exc, locale=locale) from exc


@router.get("/{data_source_id}", summary="Get a visible data source")
async def get_source(
    data_source_id: str,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    locale = resolve_request_locale(request)
    try:
        row = _service(server).get(data_source_id, actor_user_id=user.id, is_admin=_is_admin(user))
        return _source_payload(server, row)
    except Exception as exc:
        raise _map_error(exc, locale=locale) from exc


@router.patch("/{data_source_id}", summary="Update data-source metadata")
async def update_source(
    data_source_id: str,
    body: UpdateSourceBody,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(require_permission("data_sources")),
) -> dict[str, Any]:
    locale = resolve_request_locale(request)
    try:
        row = _service(server).update(
            data_source_id,
            actor_user_id=user.id,
            name=body.name.strip() if body.name is not None else None,
            description=body.description.strip() if body.description is not None else None,
            default_open=body.default_open,
            shared=body.shared,
            icon_name=body.icon_name.strip() if body.icon_name is not None else None,
            max_rows=body.max_rows,
            timeout_ms=body.timeout_ms,
            context_char_budget=body.context_char_budget,
            allow_im_export=body.allow_im_export,
            is_admin=_is_admin(user),
        )
        return _source_payload(server, row)
    except Exception as exc:
        raise _map_error(exc, locale=locale) from exc


@router.delete(
    "/{data_source_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a data source",
)
async def delete_source(
    data_source_id: str,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(require_permission("data_sources")),
) -> None:
    locale = resolve_request_locale(request)
    try:
        _service(server).delete(data_source_id, actor_user_id=user.id, is_admin=_is_admin(user))
    except Exception as exc:
        raise _map_error(exc, locale=locale) from exc


# ---------------------------------------------------------------------------
# connection (write-only password, masked read)
# ---------------------------------------------------------------------------


@router.put("/{data_source_id}/connection", summary="Configure a data-source connection")
async def configure_connection(
    data_source_id: str,
    body: ConnectionBody,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(require_permission("data_sources")),
) -> dict[str, Any]:
    locale = resolve_request_locale(request)
    try:
        _service(server).configure_connection(
            data_source_id,
            actor_user_id=user.id,
            host=body.host,
            port=body.port,
            database_name=body.database_name,
            schema_name=body.schema_name,
            username=body.username,
            password=body.password,
            ssl_mode=body.ssl_mode,
            is_admin=_is_admin(user),
        )
        return _service(server).get_connection_masked(
            data_source_id, actor_user_id=user.id, is_admin=_is_admin(user)
        )
    except Exception as exc:
        raise _map_error(exc, locale=locale) from exc


@router.get("/{data_source_id}/connection", summary="Read a data-source connection (masked)")
async def read_connection(
    data_source_id: str,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    locale = resolve_request_locale(request)
    try:
        return _service(server).get_connection_masked(
            data_source_id, actor_user_id=user.id, is_admin=_is_admin(user)
        )
    except Exception as exc:
        raise _map_error(exc, locale=locale) from exc


@router.post("/connection/probe", summary="Probe a candidate connection before saving")
async def probe_connection(
    body: ProbeConnectionBody,
    request: Request,
    server: OctopServer = Depends(get_server),
    _user: User = Depends(require_permission("data_sources")),
) -> dict[str, Any]:
    locale = resolve_request_locale(request)
    try:
        await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: _service(server).probe_new_connection(
                engine=body.engine,
                host=body.host,
                port=body.port,
                database_name=body.database_name,
                schema_name=body.schema_name,
                username=body.username,
                password=body.password or "",
                ssl_mode=body.ssl_mode,
            ),
        )
    except Exception as exc:
        raise _map_error(exc, locale=locale) from exc
    return {"ok": True}


@router.post("/{data_source_id}/connection/test", summary="Test a saved data-source connection")
async def test_connection(
    data_source_id: str,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(require_permission("data_sources")),
) -> dict[str, Any]:
    locale = resolve_request_locale(request)
    try:
        await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: _service(server).test_connection(
                data_source_id, actor_user_id=user.id, is_admin=_is_admin(user)
            ),
        )
    except Exception as exc:
        raise _map_error(exc, locale=locale) from exc
    return {"ok": True}


# ---------------------------------------------------------------------------
# schema + allowlist + annotations
# ---------------------------------------------------------------------------


@router.post("/{data_source_id}/schema/refresh", summary="Introspect and cache the schema")
async def refresh_schema(
    data_source_id: str,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(require_permission("data_sources")),
) -> dict[str, Any]:
    locale = resolve_request_locale(request)
    try:
        tables = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: _service(server).refresh_schema(
                data_source_id, actor_user_id=user.id, is_admin=_is_admin(user)
            ),
        )
    except Exception as exc:
        raise _map_error(exc, locale=locale) from exc
    return {"tables": [t.to_dict() for t in tables]}


@router.get("/{data_source_id}/schema", summary="Read the cached schema and allowlist")
async def get_schema(
    data_source_id: str,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    locale = resolve_request_locale(request)
    try:
        service = _service(server)
        service.get(data_source_id, actor_user_id=user.id, is_admin=_is_admin(user))
        tables, refreshed_at = service.get_cached_schema(data_source_id)
        allowlist = service.list_allowlist(
            data_source_id, actor_user_id=user.id, is_admin=_is_admin(user)
        )
    except Exception as exc:
        raise _map_error(exc, locale=locale) from exc
    return {
        "tables": [t.to_dict() for t in tables],
        "refreshed_at": refreshed_at,
        "allowlist": [{"table_name": a.table_name, "columns": list(a.columns)} for a in allowlist],
    }


@router.get("/{data_source_id}/allowlist", summary="List allowed tables/columns")
async def list_allowlist(
    data_source_id: str,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    locale = resolve_request_locale(request)
    try:
        items = _service(server).list_allowlist(
            data_source_id, actor_user_id=user.id, is_admin=_is_admin(user)
        )
    except Exception as exc:
        raise _map_error(exc, locale=locale) from exc
    return {
        "tables": [{"table_name": a.table_name, "columns": list(a.columns)} for a in items],
        "max_tables": MAX_TABLES_PER_SOURCE,
    }


@router.put("/{data_source_id}/allowlist", summary="Set allowed tables/columns")
async def set_allowlist(
    data_source_id: str,
    body: AllowlistBody,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(require_permission("data_sources")),
) -> dict[str, Any]:
    locale = resolve_request_locale(request)
    try:
        items = [
            AllowedTable(t.table_name.strip(), [c.strip() for c in t.columns]) for t in body.tables
        ]
        _service(server).set_allowlist(
            data_source_id, items, actor_user_id=user.id, is_admin=_is_admin(user)
        )
    except Exception as exc:
        raise _map_error(exc, locale=locale) from exc
    return {"ok": True, "count": len(body.tables)}


@router.get("/{data_source_id}/annotations", summary="List table/column annotations")
async def list_annotations(
    data_source_id: str,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    locale = resolve_request_locale(request)
    try:
        items = _service(server).list_annotations(
            data_source_id, actor_user_id=user.id, is_admin=_is_admin(user)
        )
    except Exception as exc:
        raise _map_error(exc, locale=locale) from exc
    return {
        "annotations": [
            {
                "scope": a.scope,
                "table_name": a.table_name,
                "column_name": a.column_name,
                "note": a.note,
            }
            for a in items
        ]
    }


@router.put("/{data_source_id}/annotations", summary="Replace table/column annotations")
async def set_annotations(
    data_source_id: str,
    body: AnnotationsBody,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(require_permission("data_sources")),
) -> dict[str, Any]:
    locale = resolve_request_locale(request)
    try:
        items = [
            Annotation(
                scope=a.scope,
                table_name=a.table_name.strip(),
                column_name=a.column_name.strip(),
                note=a.note.strip(),
            )
            for a in body.annotations
        ]
        _service(server).set_annotations(
            data_source_id, items, actor_user_id=user.id, is_admin=_is_admin(user)
        )
    except Exception as exc:
        raise _map_error(exc, locale=locale) from exc
    return {"ok": True, "count": len(items)}


# ---------------------------------------------------------------------------
# owner preview + audit
# ---------------------------------------------------------------------------


@router.post("/{data_source_id}/query", summary="Preview a natural-language query (owner)")
async def query_preview(
    data_source_id: str,
    body: QueryBody,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(require_permission("data_sources")),
) -> dict[str, Any]:
    locale = resolve_request_locale(request)
    try:
        if server.services is None:
            raise OctopError(ErrorCode.INTERNAL_ERROR, "data source services are not initialized")
        _service(server).require_owner(
            data_source_id, actor_user_id=user.id, is_admin=_is_admin(user)
        )
        outcome = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: execute_data_source_query(
                server.services,
                data_source_id=data_source_id,
                question=body.question,
                actor_user_id=user.id,
                is_admin=_is_admin(user),
                locale=locale,
            ),
        )
    except Exception as exc:
        raise _map_error(exc, locale=locale) from exc
    return _outcome_payload(outcome)


@router.get("/{data_source_id}/audit", summary="List SQL audit entries (owner/admin)")
async def list_audit(
    data_source_id: str,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
    limit: int = 50,
) -> dict[str, Any]:
    locale = resolve_request_locale(request)
    try:
        rows = _service(server).list_audit(
            data_source_id, actor_user_id=user.id, is_admin=_is_admin(user), limit=limit
        )
    except Exception as exc:
        raise _map_error(exc, locale=locale) from exc
    return {
        "entries": [
            {
                "id": row.id,
                "actor_user_id": row.actor_user_id,
                "thread_id": row.thread_id,
                "agent_id": row.agent_id,
                "natural_query": row.natural_query,
                "generated_sql": row.generated_sql,
                "executed_sql": row.executed_sql,
                "status": row.status,
                "row_count": row.row_count,
                "latency_ms": row.latency_ms,
                "attempt": row.attempt,
                "created_at": row.created_at,
            }
            for row in rows
        ]
    }
