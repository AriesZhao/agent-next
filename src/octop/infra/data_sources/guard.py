"""SQL guard: sqlglot-backed validation and sanitization (design §8.2).

This is one layer of defense-in-depth, not the only one — a read-only DB
account remains the real backstop (§15.7). The guard enforces:

* exactly one statement, and it must be a ``SELECT`` (CTEs / subqueries / UNION ok)
* every table reference ⊆ the allowlist
* every explicit column reference ⊆ the allowlist (``SELECT *`` is permitted)
* system schemas are never reachable (``information_schema`` / ``pg_catalog`` /
  ``mysql.*`` / ``performance_schema``)
* dangerous functions and file/export clauses are rejected
* a row ``LIMIT`` is present — injected when missing, clamped when too large

Rejected queries map to a ``blocked`` outcome with a machine reason; the caller
localizes the user-facing text (design §8.6).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import sqlglot
from sqlglot import exp

from octop.infra.db.repos.data_sources import AllowedTable

_DIALECT = {"postgres": "postgres", "mysql": "mysql"}

# Bare (unqualified) schema names that must never be queried.
_SYSTEM_SCHEMAS = {
    "information_schema",
    "pg_catalog",
    "mysql",
    "performance_schema",
    "sys",
    "pg_toast",
}

# Function names (lowercased) blocked regardless of dialect.
_DANGEROUS_FUNCTIONS = {
    "pg_sleep",
    "pg_sleep_for",
    "pg_sleep_until",
    "pg_read_file",
    "pg_read_binary_file",
    "pg_ls_dir",
    "pg_stat_file",
    "lo_import",
    "lo_export",
    "load_file",
    "benchmark",
    "sleep",
    "grant",
    "revoke",
}

# Non-query statement keys we reject even if they somehow parse.
_FORBIDDEN_STATEMENT_KEYS = {
    "insert",
    "update",
    "delete",
    "drop",
    "create",
    "alter",
    "grant",
    "revoke",
    "truncate",
    "command",
    "copy",
    "call",
    "set",
    "use",
}


@dataclass(frozen=True)
class GuardResult:
    ok: bool
    sql: str
    reason: str = ""


def _allowed_columns(allowlist: list[AllowedTable]) -> set[str]:
    """Union of explicit column names across all allowlisted tables.

    A table whose column list is empty means "all columns allowed", so column
    checking is skipped entirely for that case.
    """
    cols: set[str] = set()
    for item in allowlist:
        for col in item.columns:
            cols.add(col.lower())
    return cols


def _has_open_table(allowlist: list[AllowedTable]) -> bool:
    return any(not item.columns for item in allowlist)


def _text_contains_export(sql: str) -> str | None:
    lowered = " ".join(sql.lower().split())
    for clause in ("into outfile", "into dumpfile"):
        if clause in lowered:
            return clause
    return None


def guard_sql(
    sql: str,
    *,
    engine: str,
    allowlist: list[AllowedTable],
    max_rows: int,
) -> GuardResult:
    """Validate and sanitize *sql*; return a ``GuardResult``.

    ``engine`` is ``postgres`` | ``mysql``. Never raises for adversarial input —
    parse failures become ``ok=False`` with reason ``unparseable``.
    """
    candidate = (sql or "").strip().rstrip(";").strip()
    if not candidate:
        return GuardResult(False, "", "empty")

    dialect = _DIALECT.get(engine, engine)
    export = _text_contains_export(candidate)
    if export:
        return GuardResult(False, candidate, f"forbidden_export:{export}")

    try:
        statements = sqlglot.parse(candidate, read=dialect)
    except Exception:
        return GuardResult(False, candidate, "unparseable")

    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        return GuardResult(False, candidate, "multi_statement")

    parsed = statements[0]
    assert parsed is not None
    if parsed.key in _FORBIDDEN_STATEMENT_KEYS or not isinstance(
        parsed, (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.Values)
    ):
        return GuardResult(False, candidate, "not_select")

    # With-CTE aliases are local names, not allowlist tables.
    cte_names = {c.alias_or_name.lower() for c in parsed.find_all(exp.CTE)}

    allowed_tables = {item.table_name.lower() for item in allowlist}
    referenced_tables: set[str] = set()
    for table in parsed.find_all(exp.Table):
        schema_name = (table.db or "").lower()
        catalog_name = (table.catalog or "").lower()
        name = table.name.lower()
        if (
            schema_name in _SYSTEM_SCHEMAS
            or catalog_name in _SYSTEM_SCHEMAS
            or name in _SYSTEM_SCHEMAS
        ):
            return GuardResult(False, candidate, f"system_schema:{schema_name or name}")
        if not schema_name and name in cte_names:
            continue  # reference to a local CTE
        referenced_tables.add(name)
        if name not in allowed_tables:
            return GuardResult(False, candidate, f"table_not_allowed:{name}")

    if not referenced_tables:
        # e.g. ``SELECT 1`` with no table — nothing to expose, but reject as useless.
        return GuardResult(False, candidate, "no_allowed_table")

    # Column scope: enforce explicit columns ⊆ allowlist unless a table is fully open.
    if not _has_open_table(allowlist):
        allowed_cols = _allowed_columns(allowlist)
        for col in parsed.find_all(exp.Column):
            col_name = col.name.lower()
            if col_name and col_name != "*" and col_name not in allowed_cols:
                return GuardResult(False, candidate, f"column_not_allowed:{col_name}")

    # Dangerous functions (named + anonymous).
    for func in parsed.find_all(exp.Func, exp.Anonymous):
        fname = (getattr(func, "name", "") or func.key or "").lower()
        if fname in _DANGEROUS_FUNCTIONS:
            return GuardResult(False, candidate, f"dangerous_function:{fname}")

    limited = _apply_limit(parsed, max_rows=_safe_max_rows(max_rows))
    try:
        clean = limited.sql(dialect=dialect)
    except Exception:
        return GuardResult(False, candidate, "unrenderable")
    return GuardResult(True, clean, "")


def _safe_max_rows(max_rows: int) -> int:
    """Coerce non-positive / non-integer row caps to a safe default."""
    try:
        value = int(max_rows)
    except (TypeError, ValueError):
        return 100
    return value if value > 0 else 100


def _apply_limit(parsed: exp.Expression, *, max_rows: int) -> exp.Expression:
    """Inject ``LIMIT max_rows`` when missing; clamp when present but too large."""

    def _with_limit(node: exp.Expression) -> exp.Expression:
        return cast(exp.Expression, node.limit(max_rows, copy=False))  # type: ignore[attr-defined]

    limit = parsed.args.get("limit")
    if limit is not None:
        expr = limit.expression
        try:
            current = int(expr.this if isinstance(expr, exp.Literal) else expr.name)
        except (TypeError, ValueError, AttributeError):
            current = None
        if current is None or current > max_rows:
            return _with_limit(parsed)
        return parsed
    return _with_limit(parsed)
