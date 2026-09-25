"""Connection tests via a fake DB-API (drivers fully mocked, §13)."""

from __future__ import annotations

import pytest

from octop.infra.data_sources import connection as conn_mod
from octop.infra.data_sources.connection import (
    ConnectionConfig,
    ConnectionFailure,
    probe_connectivity,
    run_query,
)

_PG = ConnectionConfig(
    engine="postgres",
    host="h",
    port=5432,
    database="db",
    schema="public",
    username="u",
    password="p",
)
_MY = ConnectionConfig(
    engine="mysql",
    host="h",
    port=3306,
    database="db",
    schema="",
    username="u",
    password="p",
)


class FakeCursor:
    def __init__(self, conn):
        self._conn = conn
        self.description = None

    def execute(self, sql, params=None):
        self._conn.statements.append((sql, params))
        if self._conn.fail_on and self._conn.fail_on in sql:
            raise RuntimeError("boom: " + sql)
        # Only the actual data query sets a description.
        if sql.strip().upper().startswith("SELECT") and "statement_timeout" not in sql:
            self.description = [(c,) for c in self._conn.columns]
        else:
            self.description = None

    def fetchmany(self, n):
        return self._conn.rows[:n]

    def fetchall(self):
        return self._conn.rows

    def close(self):
        self._conn.closed_cursors += 1


class FakeConn:
    def __init__(self, columns, rows, *, fail_on=None):
        self.columns = columns
        self.rows = rows
        self.statements = []
        self.closed = False
        self.rolled_back = False
        self.closed_cursors = 0
        self.fail_on = fail_on

    def cursor(self):
        return FakeCursor(self)

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def _fake_connect(factory):
    def _connect(cfg, timeout_ms):
        return factory

    return _connect


def test_run_query_read_only_txn_and_timeout_pg():
    fake = FakeConn(["id", "amount"], [[1, 10], [2, 20]])
    res = run_query(
        _PG,
        "SELECT id, amount FROM orders LIMIT 100",
        timeout_ms=3000,
        max_rows=100,
        _connect=_fake_connect(fake),
    )
    assert res.columns == ["id", "amount"]
    assert res.rows == [[1, 10], [2, 20]]
    assert res.truncated is False
    stmts = " || ".join(s for s, _ in fake.statements)
    assert "statement_timeout" in stmts
    assert "BEGIN READ ONLY" in stmts
    assert fake.closed


def test_run_query_read_only_txn_mysql():
    fake = FakeConn(["id"], [[1]])
    run_query(
        _MY,
        "SELECT id FROM orders LIMIT 10",
        timeout_ms=2000,
        max_rows=10,
        _connect=_fake_connect(fake),
    )
    stmts = " || ".join(s for s, _ in fake.statements)
    assert "MAX_EXECUTION_TIME" in stmts
    assert "START TRANSACTION READ ONLY" in stmts


def test_run_query_detects_truncation():
    # max_rows=2 but fake has 3 rows -> fetchmany(3) returns 3 -> truncated.
    fake = FakeConn(["id"], [[1], [2], [3]])
    res = run_query(
        _PG, "SELECT id FROM orders", timeout_ms=1000, max_rows=2, _connect=_fake_connect(fake)
    )
    assert res.truncated is True
    assert res.rows == [[1], [2]]


def test_run_query_wraps_execution_error_and_rolls_back():
    fake = FakeConn(["id"], [[1]], fail_on="FROM orders")
    with pytest.raises(ConnectionFailure):
        run_query(
            _PG, "SELECT id FROM orders", timeout_ms=1000, max_rows=10, _connect=_fake_connect(fake)
        )
    assert fake.rolled_back
    assert fake.closed


def test_connect_failure_from_driver(monkeypatch):
    def boom(cfg, timeout_ms):
        raise ConnectionFailure("cannot connect")

    with pytest.raises(ConnectionFailure):
        run_query(_PG, "SELECT 1", timeout_ms=1000, max_rows=1, _connect=boom)


def test_pg_connect_wraps_driver_error(monkeypatch):
    class _Raise:
        def connect(self, **kw):
            raise RuntimeError("auth failed")

    monkeypatch.setitem(__import__("sys").modules, "psycopg", _Raise())
    with pytest.raises(ConnectionFailure):
        conn_mod._connect_postgres(_PG, 1000)


def test_mysql_connect_wraps_driver_error(monkeypatch):
    class _Raise:
        def connect(self, **kw):
            raise RuntimeError("no route")

    monkeypatch.setitem(__import__("sys").modules, "pymysql", _Raise())
    with pytest.raises(ConnectionFailure):
        conn_mod._connect_mysql(_MY, 1000)


def test_unsupported_engine_rejected():
    cfg = ConnectionConfig("oracle", "h", 1, "db", "", "u", "p")
    with pytest.raises(ConnectionFailure):
        conn_mod.default_connect(cfg, 1000)


def test_probe_connectivity_runs_query():
    fake = FakeConn(["?"], [[1]])
    # probe_connectivity uses the default connect; inject via monkeypatch of run_query's factory.
    import octop.infra.data_sources.connection as c

    orig = c.default_connect
    c.default_connect = lambda cfg, t: fake  # type: ignore[assignment]
    try:
        probe_connectivity(_PG, timeout_ms=1000)
    finally:
        c.default_connect = orig  # type: ignore[assignment]
    assert any(s.strip().upper().startswith("SELECT 1") for s, _ in fake.statements)
