"""Unit tests for DataSourceRepo (schema v16 data-source tables)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.data_sources import AllowedTable, Annotation, DataSourceRepo
from octop.infra.db.repos.users import UserRepo


@pytest.fixture
def db(tmp_path: Path) -> SqlitePool:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    return pool


@pytest.fixture
def repo(db: SqlitePool) -> DataSourceRepo:
    return DataSourceRepo(db)


@pytest.fixture
def owner_id(db: SqlitePool) -> int:
    return UserRepo(db).create(username="owner", password_hash="h", role="user")


@pytest.fixture
def other_id(db: SqlitePool) -> int:
    return UserRepo(db).create(username="other", password_hash="h", role="user")


def test_tables_migrated(db: SqlitePool) -> None:
    with db.connect() as conn:
        names = {
            r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        v = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
        agent_cols = {r["name"] for r in conn.execute("PRAGMA table_info(agents)").fetchall()}
    assert {
        "data_sources",
        "data_source_connections",
        "data_source_allowed_tables",
        "data_source_annotations",
        "data_source_schemas",
        "data_source_sql_audit",
    }.issubset(names)
    assert v == 16
    assert "data_source_ids" in agent_cols


def test_create_and_get(repo: DataSourceRepo, owner_id: int) -> None:
    row = repo.create(
        owner_user_id=owner_id,
        name="Sales DB",
        engine="postgres",
        description="analytics",
        shared=True,
        max_rows=50,
        timeout_ms=3000,
    )
    assert row.id.startswith("ds")
    assert row.engine == "postgres"
    assert row.shared is True
    assert row.max_rows == 50
    assert row.timeout_ms == 3000
    assert row.context_char_budget == 4000
    assert row.allow_im_export is False
    fetched = repo.get(row.id)
    assert fetched is not None and fetched.name == "Sales DB"
    assert repo.get_by_owner_name(owner_id, "Sales DB") is not None


def test_list_visible_owner_and_shared(repo: DataSourceRepo, owner_id: int, other_id: int) -> None:
    mine = repo.create(owner_user_id=owner_id, name="mine", engine="mysql")
    shared = repo.create(owner_user_id=other_id, name="shared", engine="mysql", shared=True)
    priv = repo.create(owner_user_id=other_id, name="private", engine="mysql")
    visible_ids = {r.id for r in repo.list_visible(owner_id)}
    assert {mine.id, shared.id} <= visible_ids
    assert priv.id not in visible_ids


def test_update_and_delete(repo: DataSourceRepo, owner_id: int) -> None:
    row = repo.create(owner_user_id=owner_id, name="db", engine="postgres")
    repo.update(row.id, description="updated", default_open=True, context_char_budget=2000)
    after = repo.get(row.id)
    assert after is not None
    assert after.description == "updated"
    assert after.default_open is True
    assert after.context_char_budget == 2000
    repo.delete(row.id)
    assert repo.get(row.id) is None


def test_connection_upsert_preserves_password(repo: DataSourceRepo, owner_id: int) -> None:
    row = repo.create(owner_user_id=owner_id, name="db", engine="postgres")
    repo.upsert_connection(
        row.id,
        host="h",
        port=5432,
        database_name="d",
        schema_name="public",
        username="u",
        password_blob=b"secret",
        ssl_mode="require",
    )
    conn = repo.get_connection(row.id)
    assert conn is not None
    assert conn.password_blob == b"secret"
    assert conn.has_password is True
    # Omitting the password on a later update keeps the stored secret.
    repo.upsert_connection(
        row.id,
        host="h2",
        port=5432,
        database_name="d",
        schema_name="public",
        username="u2",
        password_blob=None,
    )
    conn2 = repo.get_connection(row.id)
    assert conn2 is not None
    assert conn2.host == "h2"
    assert conn2.username == "u2"
    assert conn2.password_blob == b"secret"


def test_allowlist_replace_roundtrip(repo: DataSourceRepo, owner_id: int) -> None:
    row = repo.create(owner_user_id=owner_id, name="db", engine="postgres")
    repo.replace_allowlist(
        row.id,
        [AllowedTable("orders", ["id", "amount"]), AllowedTable("customers", [])],
    )
    items = repo.list_allowlist(row.id)
    assert {t.table_name for t in items} == {"orders", "customers"}
    orders = next(t for t in items if t.table_name == "orders")
    assert orders.columns == ["id", "amount"]
    # Replacing removes the previous set.
    repo.replace_allowlist(row.id, [AllowedTable("orders", ["id"])])
    assert len(repo.list_allowlist(row.id)) == 1


def test_annotations_roundtrip(repo: DataSourceRepo, owner_id: int) -> None:
    row = repo.create(owner_user_id=owner_id, name="db", engine="postgres")
    repo.replace_annotations(
        row.id,
        [
            Annotation(scope="table", table_name="orders", column_name="", note="order header"),
            Annotation(scope="column", table_name="orders", column_name="amount", note="gross USD"),
        ],
    )
    anns = repo.list_annotations(row.id)
    assert len(anns) == 2
    col = next(a for a in anns if a.scope == "column")
    assert col.note == "gross USD"


def test_schema_cache_upsert(repo: DataSourceRepo, owner_id: int) -> None:
    row = repo.create(owner_user_id=owner_id, name="db", engine="postgres")
    assert repo.get_schema(row.id) is None
    repo.upsert_schema(row.id, json.dumps([{"table": "orders"}]))
    tables_json, refreshed = repo.get_schema(row.id)  # type: ignore[misc]
    assert json.loads(tables_json)[0]["table"] == "orders"
    assert refreshed > 0
    # Upsert replaces (single row per source).
    repo.upsert_schema(row.id, json.dumps([{"table": "customers"}]))
    tables_json2, _ = repo.get_schema(row.id)  # type: ignore[misc]
    assert json.loads(tables_json2)[0]["table"] == "customers"
    with repo._db.connect() as conn:  # type: ignore[attr-defined]
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM data_source_schemas WHERE data_source_id = ?",
            (row.id,),
        ).fetchone()[0]
    assert n == 1


def test_audit_append_and_list(repo: DataSourceRepo, owner_id: int) -> None:
    row = repo.create(owner_user_id=owner_id, name="db", engine="postgres")
    audit_id = repo.add_audit(
        data_source_id=row.id,
        status="ok",
        actor_user_id=owner_id,
        natural_query="how many orders",
        generated_sql="SELECT COUNT(*) FROM orders",
        executed_sql="SELECT COUNT(*) FROM orders LIMIT 101",
        row_count=1,
        latency_ms=42,
    )
    assert audit_id > 0
    repo.add_audit(data_source_id=row.id, status="blocked", error_message="table not allowed")
    rows = repo.list_audit(row.id)
    assert len(rows) == 2
    statuses = {r.status for r in rows}
    assert statuses == {"ok", "blocked"}
    ok = next(r for r in rows if r.status == "ok")
    assert ok.row_count == 1
    assert ok.attempt == 1
