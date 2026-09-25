"""Tests for API Connector CRUD via ConnectorService."""

from __future__ import annotations

from pathlib import Path

import pytest

from octop.config import OctopConfig
from octop.infra.connectors.api_connector import (
    API_CONNECTOR_KIND,
    connector_mcp_server_name,
    synthetic_instance_id,
)
from octop.infra.connectors.service import ConnectorService
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.connectors import ConnectorRepo
from octop.infra.db.repos.secrets import SecretRepo
from octop.infra.db.repos.settings import SettingsRepo


@pytest.fixture
def db(tmp_path: Path) -> SqlitePool:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    return pool


@pytest.fixture
def svc(db: SqlitePool) -> ConnectorService:
    return ConnectorService(
        repo=ConnectorRepo(db),
        secret_repo=SecretRepo(db),
        settings_repo=SettingsRepo(db),
        config=OctopConfig(),
    )


def _ensure_user(db: SqlitePool, username: str = "u1") -> int:
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO users(username, password_hash, role, created_at) "
            "VALUES (?, 'x', 'user', 1)",
            (username,),
        )
        row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    assert row is not None
    return int(row["id"])


def _minimal_connector(**overrides):
    base = {
        "base_url": "http://localhost:8080",
        "auth": {"type": "bearer", "token": "tok"},
        "tools": [
            {
                "name": "get_order",
                "method": "GET",
                "path": "/orders/{id}",
                "parameters": {
                    "id": {"type": "string", "in": "path", "required": True},
                },
            },
        ],
    }
    base.update(overrides)
    return base


def test_put_and_get_api_connectors(svc: ConnectorService, db: SqlitePool):
    uid = _ensure_user(db)
    result = svc.put_api_connectors(uid, {"erp": _minimal_connector()})
    assert "erp" in result
    assert result["erp"]["base_url"] == "http://localhost:8080"

    connectors = svc.get_api_connectors(uid)
    assert "erp" in connectors
    assert connectors["erp"]["auth"]["token"] == "tok"


def test_get_api_connector_for_api_masks_secrets(svc: ConnectorService, db: SqlitePool):
    uid = _ensure_user(db)
    svc.put_api_connectors(uid, {"erp": _minimal_connector()})
    preview = svc.get_api_connector(uid, "erp")
    assert preview is not None
    assert preview["name"] == "erp"
    assert preview["auth"]["type"] == "bearer"
    assert "tok" not in str(preview["auth"].get("token_preview", ""))
    assert preview["tool_count"] == 1


def test_put_empty_deletes_row(svc: ConnectorService, db: SqlitePool):
    uid = _ensure_user(db)
    svc.put_api_connectors(uid, {"erp": _minimal_connector()})
    assert svc._repo.get_by_user_kind(uid, API_CONNECTOR_KIND) is not None  # noqa: SLF001
    svc.put_api_connectors(uid, {})
    assert svc._repo.get_by_user_kind(uid, API_CONNECTOR_KIND) is None  # noqa: SLF001


def test_delete_api_connector(svc: ConnectorService, db: SqlitePool):
    uid = _ensure_user(db)
    svc.put_api_connectors(
        uid,
        {
            "erp": _minimal_connector(),
            "crm": _minimal_connector(display_name="CRM"),
        },
    )
    svc.delete_api_connector(uid, "erp")
    connectors = svc.get_api_connectors(uid)
    assert "erp" not in connectors
    assert "crm" in connectors


def test_delete_nonexistent_raises(svc: ConnectorService, db: SqlitePool):
    uid = _ensure_user(db)
    with pytest.raises(KeyError):
        svc.delete_api_connector(uid, "nonexistent")


def test_patch_api_connector(svc: ConnectorService, db: SqlitePool):
    uid = _ensure_user(db)
    svc.put_api_connectors(uid, {"erp": _minimal_connector()})
    svc.patch_api_connector(uid, "erp", enabled=False)
    connectors = svc.get_api_connectors(uid)
    assert connectors["erp"]["enabled"] is False


def test_patch_nonexistent_raises(svc: ConnectorService, db: SqlitePool):
    uid = _ensure_user(db)
    with pytest.raises(KeyError):
        svc.patch_api_connector(uid, "nonexistent", enabled=False)


def test_list_instances_includes_api_connectors(svc: ConnectorService, db: SqlitePool):
    uid = _ensure_user(db)
    svc.put_api_connectors(uid, {"erp": _minimal_connector(display_name="ERP 系统")})
    listed = svc.list_instances_for_api(uid)
    api_items = [i for i in listed if i["kind"] == "api-connector"]
    assert len(api_items) == 1
    assert api_items[0]["instance_id"] == synthetic_instance_id("erp")
    assert api_items[0]["mcp_server_name"] == connector_mcp_server_name("erp")
    assert api_items[0]["display_name"] == "ERP 系统"


def test_list_active_mcp_server_names_includes_api_connectors(
    svc: ConnectorService, db: SqlitePool
):
    uid = _ensure_user(db)
    svc.put_api_connectors(
        uid,
        {
            "erp": _minimal_connector(),
            "disabled": _minimal_connector(display_name="Disabled", enabled=False),
        },
    )
    names = svc.list_active_mcp_server_names(uid)
    assert connector_mcp_server_name("erp") in names
    assert connector_mcp_server_name("disabled") not in names


def test_list_default_open_includes_api_connectors(svc: ConnectorService, db: SqlitePool):
    uid = _ensure_user(db)
    svc.put_api_connectors(
        uid,
        {
            "erp": _minimal_connector(default_open=True),
            "opt": _minimal_connector(display_name="Opt", default_open=False),
        },
    )
    names = svc.list_default_open_mcp_server_names(uid)
    assert connector_mcp_server_name("erp") in names
    assert connector_mcp_server_name("opt") not in names


def test_shared_api_connector_visible_to_other_user(svc: ConnectorService, db: SqlitePool):
    owner_id = _ensure_user(db, "owner")
    viewer_id = _ensure_user(db, "viewer")
    svc.put_api_connectors(
        owner_id,
        {
            "erp": _minimal_connector(shared=True, display_name="共享ERP"),
        },
    )
    listed = svc.list_instances_for_api(viewer_id)
    api_items = [i for i in listed if i["kind"] == "api-connector"]
    assert len(api_items) == 1
    assert api_items[0]["display_name"] == "共享ERP"


def test_validation_error_propagated(svc: ConnectorService, db: SqlitePool):
    uid = _ensure_user(db)
    from octop.infra.connectors.api_connector.schema import ApiConnectorValidationError

    with pytest.raises(ApiConnectorValidationError):
        svc.put_api_connectors(uid, {"bad name": _minimal_connector()})
