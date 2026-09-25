"""Guard tests: SELECT-only, allowlist scope, LIMIT clamping, dialects (§8.2, §13)."""

from __future__ import annotations

import pytest

from octop.infra.data_sources.guard import guard_sql
from octop.infra.db.repos.data_sources import AllowedTable

_ALLOW = [
    AllowedTable("orders", ["id", "amount", "status", "user_id"]),
    AllowedTable("users", ["id", "name", "city"]),
]


def _ok(sql: str, *, engine: str = "postgres", allow=_ALLOW, max_rows: int = 100):
    return guard_sql(sql, engine=engine, allowlist=allow, max_rows=max_rows)


def test_select_allowed_and_limited():
    r = _ok("SELECT id, amount FROM orders")
    assert r.ok
    assert "LIMIT 100" in r.sql.upper()


def test_missing_limit_is_injected():
    r = _ok("SELECT id FROM orders")
    assert "LIMIT" in r.sql.upper()


def test_oversized_limit_is_clamped():
    r = _ok("SELECT id FROM orders LIMIT 99999", max_rows=50)
    assert r.ok
    assert "LIMIT 50" in r.sql.upper()


def test_small_limit_is_preserved():
    r = _ok("SELECT id FROM orders LIMIT 5", max_rows=100)
    assert r.ok
    assert "LIMIT 5" in r.sql.upper()


def test_star_select_allowed():
    assert _ok("SELECT * FROM orders").ok


def test_join_over_allowed_tables():
    r = _ok("SELECT o.id, u.name FROM orders o JOIN users u ON o.user_id = u.id")
    assert r.ok


def test_reject_disallowed_table():
    r = _ok("SELECT amount FROM secret_table")
    assert not r.ok
    assert r.reason.startswith("table_not_allowed")


def test_reject_multi_statement():
    r = _ok("SELECT id FROM orders; DROP TABLE orders")
    assert not r.ok
    assert r.reason == "multi_statement"


def test_reject_dml_update():
    r = _ok("UPDATE orders SET amount = 1")
    assert not r.ok
    assert r.reason == "not_select"


def test_reject_dml_delete():
    r = _ok("DELETE FROM orders")
    assert not r.ok


def test_reject_ddl_create():
    r = _ok("CREATE TABLE t (a int)")
    assert not r.ok


def test_cte_local_name_not_in_allowlist_is_ok():
    r = _ok("WITH t AS (SELECT id FROM orders) SELECT id FROM t")
    assert r.ok


def test_cte_referencing_disallowed_table_rejected():
    r = _ok("WITH t AS (SELECT x FROM secret) SELECT * FROM t")
    assert not r.ok
    assert "table_not_allowed" in r.reason


def test_subquery_disallowed_table_rejected():
    r = _ok("SELECT id FROM orders WHERE user_id IN (SELECT id FROM secret)")
    assert not r.ok


def test_system_schema_pg_catalog_blocked():
    r = _ok("SELECT relname FROM pg_catalog.pg_class")
    assert not r.ok
    assert "system_schema" in r.reason


def test_information_schema_blocked():
    r = _ok("SELECT * FROM information_schema.tables", engine="mysql")
    assert not r.ok
    assert "system_schema" in r.reason


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT pg_sleep(10) FROM orders",
        "SELECT pg_read_file('/etc/passwd') FROM orders",
        "SELECT load_file('/tmp/x') FROM orders",
    ],
)
def test_dangerous_functions_blocked(sql):
    r = _ok(sql, engine="mysql" if "load_file" in sql else "postgres")
    assert not r.ok
    assert r.reason.startswith("dangerous_function")


def test_into_outfile_blocked():
    r = _ok('SELECT id FROM orders INTO OUTFILE "/tmp/x"', engine="mysql")
    assert not r.ok
    assert r.reason.startswith("forbidden_export")


def test_column_scope_enforced():
    # orders/users have explicit allowlists; a stray column is rejected.
    r = _ok("SELECT secret_col FROM orders")
    assert not r.ok
    assert r.reason.startswith("column_not_allowed")


def test_open_table_allows_any_column():
    # Empty column list == all columns of that table are allowed.
    allow = [AllowedTable("orders", [])]
    r = _ok("SELECT any_column_we_like FROM orders", allow=allow)
    assert r.ok


def test_mysql_dialect_identifier_quote_roundtrip():
    r = guard_sql(
        "SELECT `id`, `amount` FROM `orders`",
        engine="mysql",
        allowlist=_ALLOW,
        max_rows=10,
    )
    assert r.ok
    assert "orders" in r.sql


def test_empty_sql_rejected():
    assert not _ok("   ;  ").ok


def test_unparseable_rejected():
    r = _ok("SELECT FROM WHERE ((((")
    assert not r.ok
    assert r.reason in {"unparseable", "not_select", "no_allowed_table"}
