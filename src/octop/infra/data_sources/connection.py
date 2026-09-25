"""Synchronous read-only connections to external PostgreSQL / MySQL (§8.3).

Everything here is deliberately sync and short-lived: the NL2SQL pipeline runs
inside an executor thread, so it uses the blocking drivers (``psycopg`` /
``PyMySQL``) directly rather than an async pool. Credentials are materialized
per call and never cached. Every connection is placed in a read-only
transaction with a statement timeout before the generated SQL runs, and the
query fetches ``max_rows + 1`` rows so the caller can detect truncation.

The ``_connect`` seam is injectable so unit tests can drive a fake DB-API
without a live server (see design §13).
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

ENGINES = ("postgres", "mysql")


class ConnectionFailure(Exception):
    """Raised for connectivity / auth / timeout problems (non-retryable)."""


class ExecutionFailure(ConnectionFailure):
    """The connection worked but statement execution failed.

    The pipeline classifies these into retryable (illegal SQL) vs non-retryable
    (missing table / permission) using the raw driver message (§8.4).
    """


@dataclass(frozen=True)
class ConnectionConfig:
    engine: str
    host: str
    port: int
    database: str
    schema: str
    username: str
    password: str
    ssl_mode: str = ""

    @property
    def dialect(self) -> str:
        return "postgres" if self.engine == "postgres" else "mysql"


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool


ConnectFactory = Callable[[ConnectionConfig, int], Any]


def _connect_postgres(cfg: ConnectionConfig, timeout_ms: int) -> Any:
    import psycopg

    sslmode = cfg.ssl_mode or "prefer"
    try:
        conn = psycopg.connect(
            host=cfg.host,
            port=cfg.port,
            dbname=cfg.database,
            user=cfg.username,
            password=cfg.password,
            sslmode=sslmode,
            connect_timeout=max(1, timeout_ms // 1000),
            autocommit=True,
            application_name="octop-nl2sql",
        )
    except Exception as exc:  # driver-specific → classify uniformly
        raise ConnectionFailure(str(exc)) from exc
    return conn


def _connect_mysql(cfg: ConnectionConfig, timeout_ms: int) -> Any:
    import pymysql

    connect_timeout_s = max(1, timeout_ms // 1000)
    read_timeout_s = max(1, timeout_ms // 1000)
    ssl = None
    if cfg.ssl_mode and cfg.ssl_mode not in {"disabled", "none"}:
        ssl = {"ca": None} if cfg.ssl_mode in {"require", "verify-ca", "verify-full"} else None
    try:
        conn = pymysql.connect(
            host=cfg.host,
            port=cfg.port,
            database=cfg.database,
            user=cfg.username,
            password=cfg.password,
            connect_timeout=connect_timeout_s,
            read_timeout=read_timeout_s,
            write_timeout=read_timeout_s,
            autocommit=False,
            ssl=ssl,
        )
    except Exception as exc:
        raise ConnectionFailure(str(exc)) from exc
    return conn


def default_connect(cfg: ConnectionConfig, timeout_ms: int) -> Any:
    if cfg.engine == "postgres":
        return _connect_postgres(cfg, timeout_ms)
    if cfg.engine == "mysql":
        return _connect_mysql(cfg, timeout_ms)
    raise ConnectionFailure(f"unsupported engine: {cfg.engine}")


def prepare_read_only(conn: Any, cfg: ConnectionConfig, timeout_ms: int) -> None:
    """Enter a read-only transaction and set a server-side statement timeout."""
    cur = conn.cursor()
    try:
        if cfg.engine == "postgres":
            from psycopg import sql as psql

            cur.execute(
                psql.SQL("SET statement_timeout = {}").format(
                    psql.Literal(int(timeout_ms))
                )
            )
            cur.execute("BEGIN READ ONLY")
        else:
            cur.execute(f"SET SESSION MAX_EXECUTION_TIME = {int(timeout_ms)}")
            cur.execute("START TRANSACTION READ ONLY")
    finally:
        cur.close()


def apply_schema(conn: Any, cfg: ConnectionConfig) -> None:
    if cfg.engine == "postgres" and cfg.schema:
        from psycopg import sql as psql

        cur = conn.cursor()
        try:
            cur.execute(
                psql.SQL("SET search_path = {}").format(
                    psql.Identifier(cfg.schema)
                )
            )
        finally:
            cur.close()


def run_query(
    cfg: ConnectionConfig,
    sql: str,
    *,
    timeout_ms: int,
    max_rows: int,
    _connect: ConnectFactory | None = None,
) -> QueryResult:
    """Execute ``sql`` (already guard-validated) and return at most ``max_rows``.

    Fetches ``max_rows + 1`` rows to report ``truncated``. Never retries — the
    caller classifies failures (design §8.4).
    """
    connect = _connect or default_connect
    conn = connect(cfg, timeout_ms)
    try:
        prepare_read_only(conn, cfg, timeout_ms)
        apply_schema(conn, cfg)
    except Exception as exc:  # setup / statement-timeout configuration failed
        _rollback(conn)
        with suppress(Exception):
            conn.close()
        raise ConnectionFailure(str(exc)) from exc
    try:
        cur = conn.cursor()
        try:
            cur.execute(sql)
            description = cur.description or []
            columns = [str(d[0]) for d in description]
            fetched = cur.fetchmany(max_rows + 1)
        finally:
            cur.close()
        rows = [list(r) for r in fetched]
        truncated = len(rows) > max_rows
        if truncated:
            rows = rows[:max_rows]
        return QueryResult(columns=columns, rows=rows, truncated=truncated)
    except Exception as exc:  # query execution / per-statement timeout
        _rollback(conn)
        raise ExecutionFailure(str(exc)) from exc
    finally:
        with suppress(Exception):
            conn.close()


def _rollback(conn: Any) -> None:
    with suppress(Exception):
        conn.rollback()


def probe_connectivity(cfg: ConnectionConfig, *, timeout_ms: int = 5000) -> None:
    """Open, run a trivial read-only probe, close. Raises ``ConnectionFailure``."""
    run_query(
        cfg,
        "SELECT 1",
        timeout_ms=timeout_ms,
        max_rows=1,
    )
