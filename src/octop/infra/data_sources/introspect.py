"""Introspect an external database into a :class:`SchemaDoc` (§6, §8.3).

Runs controlled ``information_schema`` queries (never the guard-checked NL path)
to enumerate tables, columns, types and comments. The result is cached on
``data_source_schemas`` and later narrowed to the allowlist for prompting.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from typing import Any

from octop.infra.data_sources.connection import (
    ConnectionConfig,
    ConnectionFailure,
    apply_schema,
    default_connect,
    prepare_read_only,
)
from octop.infra.data_sources.schema import ColumnDoc, SchemaDoc, TableDoc

_POSTGRES_COLUMNS = """
SELECT c.table_name, c.column_name, c.data_type, c.is_nullable
FROM information_schema.columns c
WHERE c.table_schema = %s
ORDER BY c.table_name, c.ordinal_position
"""

_POSTGRES_TABLE_COMMENTS = """
SELECT t.relname AS table_name, obj_description(t.oid) AS table_comment
FROM pg_class t
JOIN pg_namespace n ON n.oid = t.relnamespace
WHERE n.nspname = %s AND t.relkind IN ('r', 'v', 'm')
"""

_MYSQL_COLUMNS = """
SELECT c.TABLE_NAME, c.COLUMN_NAME, c.DATA_TYPE, c.IS_NULLABLE
FROM information_schema.COLUMNS c
WHERE c.TABLE_SCHEMA = %s
ORDER BY c.TABLE_NAME, c.ORDINAL_POSITION
"""

_MYSQL_TABLE_COMMENTS = """
SELECT t.TABLE_NAME, t.TABLE_COMMENT
FROM information_schema.TABLES t
WHERE t.TABLE_SCHEMA = %s
"""


def _fetch(conn: Any, sql: str, params: tuple[Any, ...]) -> list[tuple[Any, ...]]:
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        return [tuple(r) for r in cur.fetchall()]
    finally:
        cur.close()


def _assemble(
    column_rows: list[tuple[Any, ...]],
    comment_rows: list[tuple[Any, ...]],
) -> SchemaDoc:
    table_comments = {str(r[0]): str(r[1] or "") for r in comment_rows if r and r[0] is not None}
    buckets: dict[str, list[ColumnDoc]] = {}
    order: list[str] = []
    for row in column_rows:
        table_name = str(row[0])
        col_name = str(row[1])
        data_type = str(row[2] or "")
        nullable = str(row[3] or "YES").upper() != "NO"
        if table_name not in buckets:
            buckets[table_name] = []
            order.append(table_name)
        buckets[table_name].append(
            ColumnDoc(name=col_name, data_type=data_type, comment="", nullable=nullable)
        )
    return [
        TableDoc(name=name, columns=buckets[name], comment=table_comments.get(name, ""))
        for name in order
    ]


def introspect(
    cfg: ConnectionConfig,
    *,
    timeout_ms: int = 5000,
    _connect: Callable[[ConnectionConfig, int], Any] | None = None,
) -> SchemaDoc:
    """Return the full schema (all tables) for the connection's database/schema."""
    connect = _connect or default_connect
    conn = connect(cfg, timeout_ms)
    try:
        prepare_read_only(conn, cfg, timeout_ms)
        apply_schema(conn, cfg)
        if cfg.engine == "postgres":
            ns = cfg.schema or "public"
            column_rows = _fetch(conn, _POSTGRES_COLUMNS, (ns,))
            comment_rows = _fetch(conn, _POSTGRES_TABLE_COMMENTS, (ns,))
        elif cfg.engine == "mysql":
            db = cfg.database
            column_rows = _fetch(conn, _MYSQL_COLUMNS, (db,))
            comment_rows = _fetch(conn, _MYSQL_TABLE_COMMENTS, (db,))
        else:
            raise ConnectionFailure(f"unsupported engine: {cfg.engine}")
        return _assemble(column_rows, comment_rows)
    except ConnectionFailure:
        raise
    except Exception as exc:
        raise ConnectionFailure(str(exc)) from exc
    finally:
        with suppress(Exception):
            conn.close()
