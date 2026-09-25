"""NL2SQL pipeline orchestration (§4, §8.4).

Runs entirely synchronously inside the caller's executor thread:

    generate → guard → execute → render → audit

with layered retry: only *illegal-SQL* class failures (syntax / hallucinated
column / unsupported function) are retried once internally, re-feeding a trimmed
error to the model under a shared timeout budget; guard/policy blocks,
connectivity/auth/timeout/missing-table failures, and empty results are not
retried. The rephrase-and-reask loop stays with the outer agent (the tool returns
a structured failure signal, design §8.4).

All user-facing text is localized through ``i18n.domains.data_source`` and is
sanitized by owner/non-owner (§8.6); the raw driver message is kept only in the
audit record.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Any

from octop.i18n.domains.data_source import (
    error_text,
    generated_sql_note,
    guard_reason_phrase,
    render_labels,
)
from octop.infra.data_sources.connection import (
    ConnectionConfig,
    ConnectionFailure,
    ExecutionFailure,
    run_query,
)
from octop.infra.data_sources.generate import GenerationFailed, generate_sql
from octop.infra.data_sources.guard import GuardResult, guard_sql
from octop.infra.data_sources.render import render_result
from octop.infra.data_sources.types import PipelineOutcome
from octop.infra.db.repos.data_sources import AllowedTable

# Markers that indicate a fixable SQL mistake (retry once) vs an ops error.
_ILLEGAL_SQL_MARKERS = (
    "syntax error",
    "column",  # unknown/hallucinated column (paired with does-not-exist checks below)
    "unknown column",
    "operator does not exist",
    "function",
    "ambiguous",
    "group by",
    "not in group by",
    "invalid identifier",
    "1054",  # mysql: unknown column
    "1305",  # mysql: function does not exist
    "42703",  # pg: undefined column
    "42883",  # pg: undefined function
    "22032",
)
_NON_RETRYABLE_MARKERS = (
    "permission denied",
    "access denied",
    'relation "',  # pg missing table handled below too, but treat as ops
    "does not exist",  # combined with table/relation below
    "no such table",
    "1146",  # mysql: table doesn't exist
    "42p01",  # pg: undefined table
    "timeout",
    "canceling statement",
    "lock wait timeout",
    "connection",
    "could not connect",
    "authentication",
    "password",
    "ssl",
)


def _looks_like_illegal_sql(message: str) -> bool:
    """Classify an execution-failure message (design §8.4)."""
    text = (message or "").lower()
    if not text:
        return False
    # Missing table / permission / timeout are operations problems, not the
    # model's fault → do not burn a second LLM call.
    if any(
        m in text for m in ("no such table", "1146", "42p01", "permission denied", "access denied")
    ):
        return False
    if "timeout" in text or "canceling statement" in text or "connection" in text:
        return False
    if any(m in text for m in _ILLEGAL_SQL_MARKERS):
        # A "table does not exist" is ops, not illegal SQL.
        return not ("table" in text and ("does not exist" in text or "doesn't exist" in text))
    return False


def _blocked_text(reason: str, *, is_owner: bool, locale: str) -> str:
    if not is_owner:
        return error_text("blocked", locale)
    phrase, token = guard_reason_phrase(reason, locale)
    detail = f"{phrase}: {token}" if token else phrase
    return error_text("blocked_owner", locale, detail=detail)


def _invalid_sql_text(*, is_owner: bool, sql: str, locale: str) -> str:
    if is_owner:
        return error_text("invalid_sql_owner", locale, sql=sql)
    return error_text("invalid_sql_non_owner", locale)


def run_pipeline(
    question: str,
    *,
    connection_cfg: ConnectionConfig,
    engine: str,
    allowlist: Sequence[AllowedTable],
    schema_text: str,
    max_rows: int,
    timeout_ms: int,
    context_char_budget: int,
    locale: str = "en",
    is_owner: bool = True,
    chat_model: Any = None,
    spill: Callable[[str], str] | None = None,
    audit: Callable[[PipelineOutcome], None] | None = None,
    generate_fn: Callable[..., str] = generate_sql,
    guard_fn: Callable[..., GuardResult] = guard_sql,
    execute_fn: Callable[..., Any] = run_query,
    clock: Callable[[], float] = time.monotonic,
) -> PipelineOutcome:
    """Execute one natural-language question end to end against a data source."""
    started = clock()
    labels = render_labels(locale)
    dialect = connection_cfg.dialect

    def _finalize(outcome: PipelineOutcome) -> PipelineOutcome:
        outcome = replace(outcome, latency_ms=int((clock() - started) * 1000))
        if audit is not None:
            audit(outcome)
        return outcome

    if chat_model is None:
        return _finalize(
            PipelineOutcome(
                status="error",
                text=error_text("model_not_configured", locale),
                natural_query=question,
                category="model_not_configured",
                audit_error="model_not_configured",
            )
        )

    previous_error: str | None = None
    attempts = 0
    last_generated = ""
    last_execution_error = ""

    for attempt in (1, 2):
        attempts = attempt
        try:
            generated = generate_fn(
                question,
                schema_text=schema_text,
                dialect=dialect,
                chat_model=chat_model,
                previous_error=previous_error,
            )
        except GenerationFailed:
            if attempt < 2:
                previous_error = "previous reply contained no valid SQL"
                continue
            return _finalize(
                PipelineOutcome(
                    status="error",
                    text=_invalid_sql_text(is_owner=is_owner, sql=last_generated, locale=locale),
                    natural_query=question,
                    generated_sql=last_generated,
                    attempts=attempts,
                    category="invalid_sql",
                    audit_error="generation_failed",
                )
            )
        last_generated = generated

        guard = guard_fn(generated, engine=engine, allowlist=list(allowlist), max_rows=max_rows)
        if not guard.ok:
            return _finalize(
                PipelineOutcome(
                    status="blocked",
                    text=_blocked_text(guard.reason, is_owner=is_owner, locale=locale),
                    natural_query=question,
                    generated_sql=generated,
                    attempts=attempts,
                    category="blocked",
                    audit_error=guard.reason,
                )
            )
        clean_sql = guard.sql

        elapsed_ms = int((clock() - started) * 1000)
        remaining_ms = max(250, timeout_ms - elapsed_ms)
        try:
            result = execute_fn(
                connection_cfg,
                clean_sql,
                timeout_ms=remaining_ms,
                max_rows=max_rows,
            )
        except ExecutionFailure as exc:
            message = str(exc)
            last_execution_error = message
            if _looks_like_illegal_sql(message) and attempt < 2:
                # Feed a trimmed error back for one corrective attempt (§8.4).
                previous_error = message[:400]
                continue
            category = "invalid_sql" if _looks_like_illegal_sql(message) else "connection"
            text = (
                _invalid_sql_text(is_owner=is_owner, sql=clean_sql, locale=locale)
                if category == "invalid_sql"
                else error_text("connection", locale)
            )
            return _finalize(
                PipelineOutcome(
                    status="error",
                    text=text,
                    natural_query=question,
                    generated_sql=generated,
                    executed_sql=clean_sql,
                    attempts=attempts,
                    category=category,  # type: ignore[arg-type]
                    audit_error=message[:2000],
                )
            )
        except ConnectionFailure as exc:
            return _finalize(
                PipelineOutcome(
                    status="error",
                    text=error_text("connection", locale),
                    natural_query=question,
                    generated_sql=generated,
                    attempts=attempts,
                    category="connection",
                    audit_error=str(exc)[:2000],
                )
            )

        rows = result.rows
        if not rows:
            return _finalize(
                PipelineOutcome(
                    status="empty",
                    text=error_text("empty", locale),
                    natural_query=question,
                    generated_sql=generated,
                    executed_sql=clean_sql,
                    attempts=attempts,
                    category="empty",
                )
            )

        has_order_by = "order by" in clean_sql.lower()
        rendered = render_result(
            result.columns,
            rows,
            truncated=result.truncated,
            has_order_by=has_order_by,
            budget_chars=context_char_budget,
            spill=spill,
            labels=labels,
        )
        body = f"{generated_sql_note(clean_sql, locale)}\n{rendered.text}"
        return _finalize(
            PipelineOutcome(
                status="ok",
                text=body,
                natural_query=question,
                generated_sql=generated,
                executed_sql=clean_sql,
                row_count=rendered.row_count,
                attempts=attempts,
                spilled=rendered.spilled,
                file_path=rendered.file_path,
            )
        )

    # Both attempts produced illegal SQL.
    return _finalize(
        PipelineOutcome(
            status="error",
            text=_invalid_sql_text(is_owner=is_owner, sql=last_generated, locale=locale),
            natural_query=question,
            generated_sql=last_generated,
            attempts=attempts,
            category="invalid_sql",
            audit_error=last_execution_error[:2000],
        )
    )
