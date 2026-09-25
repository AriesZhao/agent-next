"""Shared result types for the NL2SQL pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Status = Literal["ok", "empty", "blocked", "error"]

# Machine-readable failure categories (drive retry policy + audit + tests).
Category = Literal[
    "",
    "model_not_configured",
    "generation_failed",
    "blocked",
    "connection",
    "invalid_sql",
    "empty",
]


@dataclass(frozen=True)
class PipelineOutcome:
    """One end-to-end ``query_data_source`` result, ready for injection."""

    status: Status
    text: str
    natural_query: str = ""
    generated_sql: str = ""
    executed_sql: str = ""
    row_count: int = 0
    latency_ms: int = 0
    attempts: int = 1
    spilled: bool = False
    file_path: str = ""
    category: Category = ""
    # Raw (sanitized) driver/error text — stored only in the audit row, never
    # returned to non-owners or the main agent context (design §8.6).
    audit_error: str = ""
    # Structured result data for frontend chart rendering (octop_ui envelope).
    columns: tuple[str, ...] = ()
    rows: tuple[tuple[Any, ...], ...] = ()

    @property
    def ok(self) -> bool:
        return self.status == "ok"
