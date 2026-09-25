"""Tests for API Connector audit logging."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from octop.infra.connectors.api_connector.audit import record_api_call
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.audit import AuditRepo


@pytest.fixture
def db(tmp_path: Path) -> SqlitePool:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    return pool


@pytest.fixture
def audit_repo(db: SqlitePool) -> AuditRepo:
    return AuditRepo(db)


def test_record_api_call_success(audit_repo: AuditRepo, db: SqlitePool):
    record_api_call(
        audit_repo,
        username="alice",
        agent_id="a1",
        connector="erp",
        tool="get_order",
        http_method="GET",
        http_url="http://localhost:8080/orders/1",
        http_status=200,
        duration_ms=42,
        identity_propagated=True,
    )
    with db.transaction() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE action = 'api_connector_call'"
        ).fetchall()
    assert len(rows) == 1
    payload = json.loads(rows[0]["payload"])
    assert payload["connector"] == "erp"
    assert payload["tool"] == "get_order"
    assert payload["http_status"] == 200
    assert payload["duration_ms"] == 42
    assert payload["identity_propagated"] is True
    assert rows[0]["actor"] == "alice"
    assert rows[0]["target"] == "erp:get_order"


def test_record_api_call_error(audit_repo: AuditRepo, db: SqlitePool):
    record_api_call(
        audit_repo,
        username="bob",
        agent_id="a2",
        connector="erp",
        tool="create_order",
        http_method="POST",
        http_url="http://localhost:8080/orders",
        error="timeout",
        sensitive=True,
    )
    with db.transaction() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE action = 'api_connector_call'"
        ).fetchall()
    assert len(rows) == 1
    payload = json.loads(rows[0]["payload"])
    assert payload["error"] == "timeout"
    assert payload["sensitive"] is True
    assert "http_status" not in payload
