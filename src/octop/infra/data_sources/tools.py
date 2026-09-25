"""``query_data_source`` — the single built-in NL2SQL tool (§4, §12 step 12).

The main agent calls this with a natural-language question (and, when several
sources are attached, the target id). The SQL, schema, and wide raw rows never
enter the main conversation — they live only inside :func:`run_pipeline`, which
executes synchronously in an executor thread; only the compact render digest is
returned (design §7). Large results spill to the agent workspace via the
``BackendWorkspace`` handle and come back as digest + path.

Selection is scoped to this turn's ``data_source_ids`` (stamped by
:func:`octop.infra.data_sources.default_open.stamp_turn_data_source_config`), so
an agent can only query the sources it was given.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from typing import Annotated, Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest
from langchain_core.tools import StructuredTool
from langgraph.config import get_config
from pydantic import Field

from octop.i18n.domains.data_source import error_text, no_selection_text, not_found_text
from octop.infra.data_sources.audit import make_audit_writer
from octop.infra.data_sources.pipeline import run_pipeline
from octop.infra.data_sources.service import DataSourceService
from octop.infra.db.repos.data_sources import DataSourceRow
from octop.infra.utils.ulid import new_short_id

QUERY_DATA_SOURCE_TOOL = "query_data_source"

_MAX_CATALOG_DESC_CHARS = 240

_DESC_BASE = (
    "Query one of the structured data sources attached this turn with a "
    "natural-language question. The system translates it to a read-only SQL "
    "query, runs it under a safety guard, and returns a compact result digest. "
    "Write `question` as a precise data question (entities, metric, grouping). "
    "If several sources are attached, pass the matching `data_source_id`."
)

_DESC_NONE = "No data sources are attached this turn."


def _clip(text: str, limit: int = _MAX_CATALOG_DESC_CHARS) -> str:
    stripped = (text or "").strip()
    if len(stripped) <= limit:
        return stripped
    return stripped[: limit - 3].rstrip() + "..."


def format_query_data_source_description(
    catalog: Sequence[Mapping[str, str]] | None,
) -> str:
    entries: list[str] = []
    for item in catalog or ():
        ds_id = _clip(str(item.get("id") or ""), 40)
        name = _clip(str(item.get("name") or ds_id), 80)
        if not name:
            continue
        description = _clip(str(item.get("description") or ""))
        entries.append(
            f"- {name} (id={ds_id}): {description}" if description else f"- {name} (id={ds_id})"
        )
    if not entries:
        return _DESC_NONE
    return f"{_DESC_BASE}\nAttached this turn:\n" + "\n".join(entries)


def _tool_ctx() -> tuple[int, bool, list[str], str, str, str]:
    cfg = get_config().get("configurable") or {}
    user_raw = cfg.get("user")
    if user_raw is None:
        raise ValueError("missing configurable.user")
    user_id = int(user_raw)
    is_admin = bool(cfg.get("user_is_admin"))
    raw_ids = cfg.get("data_source_ids")
    ids: list[str] = []
    if isinstance(raw_ids, list):
        ids = [str(item).strip() for item in raw_ids if str(item).strip()]
    locale = str(cfg.get("locale") or "en")
    agent_id = str(cfg.get("agent_id") or "")
    thread_id = str(cfg.get("thread_id") or "")
    return user_id, is_admin, ids, locale, agent_id, thread_id


def _select_id(ids: Sequence[str], requested: str | None) -> tuple[str | None, bool]:
    """Return ``(chosen_id, ok)`` scoped to this turn's mounted sources."""
    allowed = list(dict.fromkeys(str(i).strip() for i in ids if str(i).strip()))
    if not allowed:
        return None, False
    if requested:
        key = str(requested).strip()
        return (key, True) if key in allowed else (None, False)
    if len(allowed) == 1:
        return allowed[0], True
    return None, False


def _make_spill(workspace: Any) -> Callable[[str], str] | None:
    if workspace is None:
        return None

    def spill(csv_text: str) -> str:
        rel = f"outbound/data_source_{new_short_id(8)}.csv"
        workspace.upload_bytes(rel, csv_text.encode("utf-8"))
        return rel

    return spill


def execute_data_source_query(
    services: Any,
    *,
    workspace: Any = None,
    data_source_id: str,
    question: str,
    actor_user_id: int,
    is_admin: bool = False,
    locale: str = "en",
    agent_id: str = "",
    thread_id: str = "",
    pipeline_run: Any = run_pipeline,
) -> Any:
    """Assemble one data source's context and run the NL2SQL pipeline (sync)."""
    service = DataSourceService(services)
    row: DataSourceRow = service.get_readable(
        data_source_id, actor_user_id=actor_user_id, is_admin=is_admin
    )
    schema_text, allowlist = service.build_scoped_prompt(data_source_id)
    connection_cfg = service.build_connection_config(data_source_id)
    chat_model = service.build_chat_model()
    audit = make_audit_writer(
        services.data_source_repo,
        data_source_id=data_source_id,
        actor_user_id=actor_user_id,
        thread_id=thread_id or None,
        agent_id=agent_id or None,
    )
    return pipeline_run(
        question,
        connection_cfg=connection_cfg,
        engine=row.engine,
        allowlist=allowlist,
        schema_text=schema_text,
        max_rows=row.max_rows,
        timeout_ms=row.timeout_ms,
        context_char_budget=row.context_char_budget,
        locale=locale,
        is_owner=(row.owner_user_id == actor_user_id or is_admin),
        chat_model=chat_model,
        spill=_make_spill(workspace),
        audit=audit,
    )


def build_data_source_tools(services: Any, *, workspace: Any = None) -> list[StructuredTool]:
    """Return the built-in ``query_data_source`` tool (wired via agent config.tools)."""

    async def query_data_source(
        question: Annotated[
            str,
            Field(
                description=(
                    "A precise natural-language data question for the attached "
                    "data source (entities, metric, grouping/filters)."
                ),
            ),
        ],
        data_source_id: Annotated[
            str,
            Field(
                description=(
                    "Which attached data source to query. Required when more than "
                    "one is attached this turn; omit when exactly one is present."
                ),
            ),
        ] = "",
    ) -> str:
        try:
            user_id, is_admin, ids, locale, agent_id, thread_id = _tool_ctx()
            if not ids:
                return no_selection_text(locale)
            chosen, ok = _select_id(ids, data_source_id or None)
            if not ok or not chosen:
                return not_found_text(locale)
            loop = asyncio.get_running_loop()
            outcome = await loop.run_in_executor(
                None,
                lambda: execute_data_source_query(
                    services,
                    workspace=workspace,
                    data_source_id=chosen,
                    question=question,
                    actor_user_id=user_id,
                    is_admin=is_admin,
                    locale=locale,
                    agent_id=agent_id,
                    thread_id=thread_id,
                ),
            )
            return str(outcome.text)
        except LookupError:
            return not_found_text(_safe_locale())
        except PermissionError:
            return not_found_text(_safe_locale())
        except Exception:  # never leak driver/stack detail to the agent
            return error_text("connection", _safe_locale())

    return [
        StructuredTool.from_function(
            coroutine=query_data_source,
            name=QUERY_DATA_SOURCE_TOOL,
            # Static fallback; middleware rewrites this with this turn's catalog.
            description=format_query_data_source_description([]),
        )
    ]


def _safe_locale() -> str:
    try:
        return str((get_config().get("configurable") or {}).get("locale") or "en")
    except Exception:
        return "en"


def _catalog_from_config() -> list[dict[str, str]]:
    cfg = get_config().get("configurable") or {}
    raw = cfg.get("data_source_catalog")
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        ds_id = str(item.get("id") or "").strip()
        name = str(item.get("name") or "").strip() or ds_id
        description = str(item.get("description") or "").strip()
        if not name:
            continue
        out.append({"id": ds_id or name, "name": name, "description": description})
    return out


def _with_enriched_tool_description(request: ModelRequest[Any]) -> ModelRequest[Any]:
    catalog = _catalog_from_config()
    tools_in = list(request.tools or [])
    if not tools_in:
        return request
    if not catalog:
        filtered = [
            tool
            for tool in tools_in
            if not (isinstance(tool, StructuredTool) and tool.name == QUERY_DATA_SOURCE_TOOL)
        ]
        if len(filtered) == len(tools_in):
            return request
        return request.override(tools=filtered)
    description = format_query_data_source_description(catalog)
    tools_out: list[Any] = []
    changed = False
    for tool in tools_in:
        if isinstance(tool, StructuredTool) and tool.name == QUERY_DATA_SOURCE_TOOL:
            if tool.description == description:
                tools_out.append(tool)
            else:
                tools_out.append(tool.model_copy(update={"description": description}))
                changed = True
            continue
        tools_out.append(tool)
    if not changed:
        return request
    return request.override(tools=tools_out)


class DataSourceQueryHintMiddleware(AgentMiddleware[Any, Any]):
    """Rewrite ``query_data_source`` with this turn's catalog; hide it when empty."""

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Any,
    ) -> Any:
        return handler(_with_enriched_tool_description(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Any,
    ) -> Any:
        return await handler(_with_enriched_tool_description(request))


__all__ = [
    "DataSourceQueryHintMiddleware",
    "QUERY_DATA_SOURCE_TOOL",
    "build_data_source_tools",
    "execute_data_source_query",
    "format_query_data_source_description",
]
