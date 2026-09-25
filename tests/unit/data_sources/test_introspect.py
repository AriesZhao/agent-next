"""Introspection tests: information_schema rows → SchemaDoc (fully mocked)."""

from __future__ import annotations

import pytest

from octop.infra.data_sources.connection import (
    ConnectionConfig,
    ConnectionFailure,
)
from octop.infra.data_sources.introspect import introspect

_PG = ConnectionConfig("postgres", "h", 5432, "db", "public", "u", "p")
_MY = ConnectionConfig("mysql", "h", 3306, "db", "", "u", "p")


class Cursor:
    def __init__(self, conn, sql, params):
        self._conn = conn
        self._sql = sql
        self._rows = conn.route(sql)

    def execute(self, sql, params=None):
        self._rows = self._conn.route(sql)

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class Conn:
    def __init__(self, columns_rows, comment_rows):
        self._columns_rows = columns_rows
        self._comment_rows = comment_rows
        self.closed = False

    def route(self, sql):
        s = sql.lower()
        if "columns" in s:
            return self._columns_rows
        return self._comment_rows

    def cursor(self):
        return Cursor(self, "", None)

    def rollback(self):
        pass

    def close(self):
        self.closed = True


def _connect(conn):
    return lambda cfg, timeout_ms: conn


def test_introspect_pg_groups_columns_and_comments():
    columns = [
        ("orders", "id", "integer", "NO"),
        ("orders", "amount", "numeric", "YES"),
        ("users", "id", "integer", "NO"),
    ]
    comments = [("orders", "Order header")]
    conn = Conn(columns, comments)
    schema = introspect(_PG, _connect=_connect(conn))
    names = [t.name for t in schema]
    assert names == ["orders", "users"]
    orders = schema[0]
    assert orders.comment == "Order header"
    assert [c.name for c in orders.columns] == ["id", "amount"]
    assert orders.columns[0].nullable is False
    assert orders.columns[1].nullable is True
    assert conn.closed


def test_introspect_mysql_uses_database_and_uppercase():
    columns = [
        ("orders", "id", "int", "NO"),
        ("orders", "name", "varchar", "YES"),
    ]
    comments = [("orders", "mysql comment")]
    conn = Conn(columns, comments)
    schema = introspect(_MY, _connect=_connect(conn))
    assert schema[0].name == "orders"
    assert schema[0].comment == "mysql comment"


def test_introspect_wraps_driver_error():
    class Boom(Conn):
        def cursor(self):
            raise RuntimeError("net down")

    with pytest.raises(ConnectionFailure):
        introspect(_PG, _connect=_connect(Boom([], [])))
