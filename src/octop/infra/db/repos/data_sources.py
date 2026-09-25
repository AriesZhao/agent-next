"""NL2SQL data-source persistence — one repo over the six v16 tables.

Pure SQL only (see AGENTS.md §5): no orchestration, no harness imports.
The main ``data_sources`` table follows the resource-table convention
(integer ``id`` surrogate PK + public ``data_source_id`` string); child rows
reference the public string id. ``data_source_connections`` /
``data_source_schemas`` are 1:1 extensions keyed by ``data_source_id``;
``data_source_sql_audit`` is an append-only log (integer id, no public string).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, bool_int, map_rows, now_ts, partial_updates
from octop.infra.utils.ulid import new_short_id


@dataclass(frozen=True)
class DataSourceRow:
    id: str
    pk: int
    owner_user_id: int
    name: str
    description: str
    engine: str
    default_open: bool
    shared: bool
    icon_name: str
    max_rows: int
    timeout_ms: int
    context_char_budget: int
    allow_im_export: bool
    created_at: int
    updated_at: int

    @classmethod
    def from_row(cls, r: DbRow) -> DataSourceRow:
        return cls(
            id=str(r["data_source_id"]),
            pk=int(r["id"]),
            owner_user_id=int(r["owner_user_id"]),
            name=str(r["name"]),
            description=str(r["description"] or ""),
            engine=str(r["engine"]),
            default_open=bool(r["default_open"]),
            shared=bool(r["shared"]),
            icon_name=str(r["icon_name"] or ""),
            max_rows=int(r["max_rows"]),
            timeout_ms=int(r["timeout_ms"]),
            context_char_budget=int(r["context_char_budget"]),
            allow_im_export=bool(r["allow_im_export"]),
            created_at=int(r["created_at"]),
            updated_at=int(r["updated_at"]),
        )


@dataclass(frozen=True)
class DataSourceConnectionRow:
    data_source_id: str
    host: str
    port: int
    database_name: str
    schema_name: str
    username: str
    password_blob: bytes | None
    ssl_mode: str
    last_tested_at: int | None

    @classmethod
    def from_row(cls, r: DbRow) -> DataSourceConnectionRow:
        blob = r["password_blob"]
        return cls(
            data_source_id=str(r["data_source_id"]),
            host=str(r["host"] or ""),
            port=int(r["port"] or 0),
            database_name=str(r["database_name"] or ""),
            schema_name=str(r["schema_name"] or ""),
            username=str(r["username"] or ""),
            password_blob=bytes(blob) if blob is not None else None,
            ssl_mode=str(r["ssl_mode"] or ""),
            last_tested_at=(int(r["last_tested_at"]) if r["last_tested_at"] is not None else None),
        )

    @property
    def has_password(self) -> bool:
        return bool(self.password_blob)


@dataclass(frozen=True)
class AllowedTable:
    table_name: str
    columns: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Annotation:
    scope: str
    table_name: str
    column_name: str
    note: str


@dataclass(frozen=True)
class SqlAuditRow:
    id: int
    data_source_id: str
    actor_user_id: int | None
    thread_id: str | None
    agent_id: str | None
    natural_query: str
    generated_sql: str
    executed_sql: str
    status: str
    row_count: int
    latency_ms: int
    error_message: str
    attempt: int
    created_at: int

    @classmethod
    def from_row(cls, r: DbRow) -> SqlAuditRow:
        return cls(
            id=int(r["id"]),
            data_source_id=str(r["data_source_id"]),
            actor_user_id=(int(r["actor_user_id"]) if r["actor_user_id"] is not None else None),
            thread_id=(str(r["thread_id"]) if r["thread_id"] is not None else None),
            agent_id=(str(r["agent_id"]) if r["agent_id"] is not None else None),
            natural_query=str(r["natural_query"] or ""),
            generated_sql=str(r["generated_sql"] or ""),
            executed_sql=str(r["executed_sql"] or ""),
            status=str(r["status"]),
            row_count=int(r["row_count"] or 0),
            latency_ms=int(r["latency_ms"] or 0),
            error_message=str(r["error_message"] or ""),
            attempt=int(r["attempt"] or 1),
            created_at=int(r["created_at"]),
        )


class DataSourceRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    # --- data_sources ---------------------------------------------------

    def _allocate_id(self) -> str:
        for _ in range(16):
            candidate = f"ds{new_short_id(6)}"
            if self.get(candidate) is None:
                return candidate
        raise RuntimeError("failed to allocate unique data source id")

    def create(
        self,
        *,
        owner_user_id: int,
        name: str,
        engine: str,
        description: str = "",
        default_open: bool = False,
        shared: bool = False,
        icon_name: str = "",
        max_rows: int = 100,
        timeout_ms: int = 5000,
        context_char_budget: int = 4000,
        allow_im_export: bool = False,
    ) -> DataSourceRow:
        ds_id = self._allocate_id()
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO data_sources("
                "data_source_id, owner_user_id, name, description, engine, default_open, shared, "
                "icon_name, max_rows, timeout_ms, context_char_budget, allow_im_export, "
                "created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ds_id,
                    owner_user_id,
                    name,
                    description,
                    engine,
                    bool_int(default_open),
                    bool_int(shared),
                    icon_name,
                    max_rows,
                    timeout_ms,
                    context_char_budget,
                    bool_int(allow_im_export),
                    ts,
                    ts,
                ),
            )
        row = self.get(ds_id)
        if row is None:
            raise RuntimeError(f"data source insert failed: {ds_id}")
        return row

    def get(self, data_source_id: str) -> DataSourceRow | None:
        with self._db.connect() as conn:
            r = conn.execute(
                "SELECT * FROM data_sources WHERE data_source_id = ?",
                (data_source_id,),
            ).fetchone()
        return DataSourceRow.from_row(r) if r else None

    def get_by_owner_name(self, owner_user_id: int, name: str) -> DataSourceRow | None:
        with self._db.connect() as conn:
            r = conn.execute(
                "SELECT * FROM data_sources WHERE owner_user_id = ? AND name = ?",
                (owner_user_id, name),
            ).fetchone()
        return DataSourceRow.from_row(r) if r else None

    def list_visible(self, user_id: int) -> list[DataSourceRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM data_sources WHERE owner_user_id = ? OR shared = 1 ORDER BY name",
                (user_id,),
            ).fetchall()
        return map_rows(rows, DataSourceRow)

    def list_all(self) -> list[DataSourceRow]:
        with self._db.connect() as conn:
            rows = conn.execute("SELECT * FROM data_sources ORDER BY name").fetchall()
        return map_rows(rows, DataSourceRow)

    def list_default_open(self) -> list[DataSourceRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM data_sources WHERE default_open = 1 ORDER BY name"
            ).fetchall()
        return map_rows(rows, DataSourceRow)

    def count_for_owner(self, owner_user_id: int) -> int:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM data_sources WHERE owner_user_id = ?",
                (owner_user_id,),
            ).fetchone()
        return int(row["c"]) if row else 0

    def update(
        self,
        data_source_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        default_open: bool | None = None,
        shared: bool | None = None,
        icon_name: str | None = None,
        max_rows: int | None = None,
        timeout_ms: int | None = None,
        context_char_budget: int | None = None,
        allow_im_export: bool | None = None,
    ) -> None:
        fields, params = partial_updates(
            [
                ("name", name),
                ("description", description),
                ("default_open", bool_int(default_open) if default_open is not None else None),
                ("shared", bool_int(shared) if shared is not None else None),
                ("icon_name", icon_name),
                ("max_rows", max_rows),
                ("timeout_ms", timeout_ms),
                ("context_char_budget", context_char_budget),
                (
                    "allow_im_export",
                    bool_int(allow_im_export) if allow_im_export is not None else None,
                ),
            ]
        )
        if not fields:
            return
        fields.append("updated_at = ?")
        params.append(now_ts())
        params.append(data_source_id)
        with self._db.transaction() as conn:
            conn.execute(
                f"UPDATE data_sources SET {', '.join(fields)} WHERE data_source_id = ?",
                params,
            )

    def delete(self, data_source_id: str) -> None:
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM data_sources WHERE data_source_id = ?", (data_source_id,))

    # --- connections (1:1) ---------------------------------------------

    def upsert_connection(
        self,
        data_source_id: str,
        *,
        host: str,
        port: int,
        database_name: str,
        schema_name: str,
        username: str,
        password_blob: bytes | None,
        ssl_mode: str = "",
    ) -> None:
        with self._db.transaction() as conn:
            existing = conn.execute(
                "SELECT data_source_id FROM data_source_connections WHERE data_source_id = ?",
                (data_source_id,),
            ).fetchone()
            if existing:
                # Preserve an existing secret when the caller omits a new one.
                if password_blob is None:
                    conn.execute(
                        "UPDATE data_source_connections SET host = ?, port = ?, "
                        "database_name = ?, schema_name = ?, username = ?, ssl_mode = ? "
                        "WHERE data_source_id = ?",
                        (
                            host,
                            port,
                            database_name,
                            schema_name,
                            username,
                            ssl_mode,
                            data_source_id,
                        ),
                    )
                else:
                    conn.execute(
                        "UPDATE data_source_connections SET host = ?, port = ?, "
                        "database_name = ?, schema_name = ?, username = ?, password_blob = ?, "
                        "ssl_mode = ? WHERE data_source_id = ?",
                        (
                            host,
                            port,
                            database_name,
                            schema_name,
                            username,
                            password_blob,
                            ssl_mode,
                            data_source_id,
                        ),
                    )
            else:
                conn.execute(
                    "INSERT INTO data_source_connections("
                    "data_source_id, host, port, database_name, schema_name, username, "
                    "password_blob, ssl_mode) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        data_source_id,
                        host,
                        port,
                        database_name,
                        schema_name,
                        username,
                        password_blob,
                        ssl_mode,
                    ),
                )

    def get_connection(self, data_source_id: str) -> DataSourceConnectionRow | None:
        with self._db.connect() as conn:
            r = conn.execute(
                "SELECT * FROM data_source_connections WHERE data_source_id = ?",
                (data_source_id,),
            ).fetchone()
        return DataSourceConnectionRow.from_row(r) if r else None

    def mark_connection_tested(self, data_source_id: str, *, tested_at: int | None = None) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE data_source_connections SET last_tested_at = ? WHERE data_source_id = ?",
                (tested_at if tested_at is not None else now_ts(), data_source_id),
            )

    # --- allowlist ------------------------------------------------------

    def replace_allowlist(self, data_source_id: str, items: list[AllowedTable]) -> None:
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "DELETE FROM data_source_allowed_tables WHERE data_source_id = ?",
                (data_source_id,),
            )
            for item in items:
                conn.execute(
                    "INSERT INTO data_source_allowed_tables("
                    "data_source_id, table_name, columns_json) VALUES (?, ?, ?)",
                    (data_source_id, item.table_name, json.dumps(item.columns, ensure_ascii=False)),
                )
            conn.execute(
                "UPDATE data_sources SET updated_at = ? WHERE data_source_id = ?",
                (ts, data_source_id),
            )

    def list_allowlist(self, data_source_id: str) -> list[AllowedTable]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT table_name, columns_json FROM data_source_allowed_tables "
                "WHERE data_source_id = ? ORDER BY table_name",
                (data_source_id,),
            ).fetchall()
        out: list[AllowedTable] = []
        for r in rows:
            try:
                cols = json.loads(r["columns_json"] or "[]")
            except (json.JSONDecodeError, TypeError):
                cols = []
            out.append(AllowedTable(str(r["table_name"]), [str(c) for c in cols]))
        return out

    # --- annotations ----------------------------------------------------

    def replace_annotations(self, data_source_id: str, items: list[Annotation]) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "DELETE FROM data_source_annotations WHERE data_source_id = ?",
                (data_source_id,),
            )
            for item in items:
                conn.execute(
                    "INSERT INTO data_source_annotations("
                    "data_source_id, scope, table_name, column_name, note) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        data_source_id,
                        item.scope,
                        item.table_name,
                        item.column_name,
                        item.note,
                    ),
                )

    def list_annotations(self, data_source_id: str) -> list[Annotation]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT scope, table_name, column_name, note FROM data_source_annotations "
                "WHERE data_source_id = ? ORDER BY table_name, column_name",
                (data_source_id,),
            ).fetchall()
        return [
            Annotation(
                scope=str(r["scope"]),
                table_name=str(r["table_name"]),
                column_name=str(r["column_name"] or ""),
                note=str(r["note"] or ""),
            )
            for r in rows
        ]

    # --- schema cache ---------------------------------------------------

    def upsert_schema(self, data_source_id: str, tables_json: str) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO data_source_schemas(data_source_id, tables_json, refreshed_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(data_source_id) DO UPDATE SET tables_json = excluded.tables_json, "
                "refreshed_at = excluded.refreshed_at",
                (data_source_id, tables_json, now_ts()),
            )

    def get_schema(self, data_source_id: str) -> tuple[str, int] | None:
        with self._db.connect() as conn:
            r = conn.execute(
                "SELECT tables_json, refreshed_at FROM data_source_schemas WHERE data_source_id = ?",
                (data_source_id,),
            ).fetchone()
        if not r:
            return None
        return str(r["tables_json"] or "[]"), int(r["refreshed_at"] or 0)

    # --- audit ----------------------------------------------------------

    def add_audit(
        self,
        *,
        data_source_id: str,
        status: str,
        actor_user_id: int | None = None,
        thread_id: str | None = None,
        agent_id: str | None = None,
        natural_query: str = "",
        generated_sql: str = "",
        executed_sql: str = "",
        row_count: int = 0,
        latency_ms: int = 0,
        error_message: str = "",
        attempt: int = 1,
    ) -> int:
        from octop.infra.db.repos._base import insert_returning_id

        with self._db.transaction() as conn:
            return insert_returning_id(
                conn,
                "INSERT INTO data_source_sql_audit("
                "data_source_id, actor_user_id, thread_id, agent_id, natural_query, "
                "generated_sql, executed_sql, status, row_count, latency_ms, error_message, "
                "attempt, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    data_source_id,
                    actor_user_id,
                    thread_id,
                    agent_id,
                    natural_query,
                    generated_sql,
                    executed_sql,
                    status,
                    row_count,
                    latency_ms,
                    error_message,
                    attempt,
                    now_ts(),
                ),
            )

    def list_audit(self, data_source_id: str, *, limit: int = 50) -> list[SqlAuditRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM data_source_sql_audit WHERE data_source_id = ? "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (data_source_id, limit),
            ).fetchall()
        return map_rows(rows, SqlAuditRow)
