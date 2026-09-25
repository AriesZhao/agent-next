"""Audit sink: every pipeline outcome is persisted (§15.5, step 10).

:func:`make_audit_writer` binds the request context and returns a callback the
pipeline invokes with the final :class:`PipelineOutcome`. The raw driver text is
stored only here (``error_message``), never echoed to non-owners (§8.6).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from octop.infra.data_sources.types import PipelineOutcome


def make_audit_writer(
    repo: Any,
    *,
    data_source_id: str,
    actor_user_id: int | None = None,
    thread_id: str | None = None,
    agent_id: str | None = None,
) -> Callable[[PipelineOutcome], int]:
    def write(outcome: PipelineOutcome) -> int:
        return int(
            repo.add_audit(
                data_source_id=data_source_id,
                status=outcome.status,
                actor_user_id=actor_user_id,
                thread_id=thread_id,
                agent_id=agent_id,
                natural_query=outcome.natural_query,
                generated_sql=outcome.generated_sql,
                executed_sql=outcome.executed_sql,
                row_count=outcome.row_count,
                latency_ms=outcome.latency_ms,
                error_message=outcome.audit_error,
                attempt=outcome.attempts,
            )
        )

    return write
