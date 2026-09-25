"""End-to-end test with a virtual business system (mock HTTP)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from octop.config import OctopConfig
from octop.infra.connectors.api_connector import (
    synthetic_instance_id,
)
from octop.infra.connectors.api_connector.adapter import call_tool, list_tools
from octop.infra.connectors.api_connector.audit import record_api_call
from octop.infra.connectors.api_connector.identity import (
    ActorContext,
    reset_actor_context,
    set_actor_context,
)
from octop.infra.connectors.api_connector.langchain import (
    build_api_connector_langchain_tools,
)
from octop.infra.connectors.service import ConnectorService
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.audit import AuditRepo
from octop.infra.db.repos.connectors import ConnectorRepo
from octop.infra.db.repos.secrets import SecretRepo
from octop.infra.db.repos.settings import SettingsRepo

# ---------------------------------------------------------------------------
# Virtual business system — simulated order/inventory management API
# ---------------------------------------------------------------------------

_FAKE_ORDERS: dict[str, dict[str, Any]] = {
    "ORD-001": {"order_id": "ORD-001", "product": "Widget A", "qty": 10, "status": "shipped"},
    "ORD-002": {"order_id": "ORD-002", "product": "Widget B", "qty": 5, "status": "pending"},
}

_FAKE_INVENTORY: dict[str, int] = {"Widget-A": 100, "Widget-B": 42}


def _virtual_business_handler(request: httpx.Request) -> httpx.Response:
    """Route requests for the virtual business system."""
    path = request.url.path
    headers = dict(request.headers)

    if path == "/orders" and request.method == "GET":
        product_filter = str(request.url.params.get("product", ""))
        orders = list(_FAKE_ORDERS.values())
        if product_filter:
            orders = [o for o in orders if o["product"] == product_filter]
        return httpx.Response(
            200,
            json={"data": orders, "total": len(orders)},
            headers={"content-type": "application/json"},
            request=request,
        )

    if path.startswith("/orders/") and request.method == "GET":
        order_id = path.removeprefix("/orders/")
        order = _FAKE_ORDERS.get(order_id)
        if order is None:
            return httpx.Response(404, json={"error": "not found"}, request=request)
        return httpx.Response(
            200,
            json={"data": order},
            headers={"content-type": "application/json"},
            request=request,
        )

    if path == "/orders" and request.method == "POST":
        body = json.loads(request.content)
        new_id = f"ORD-{len(_FAKE_ORDERS) + 1:03d}"
        order = {"order_id": new_id, "status": "pending", **body}
        return httpx.Response(
            201,
            json={"data": order},
            headers={"content-type": "application/json"},
            request=request,
        )

    if path.startswith("/inventory/") and request.method == "GET":
        sku = path.removeprefix("/inventory/")
        qty = _FAKE_INVENTORY.get(sku)
        if qty is None:
            return httpx.Response(404, json={"error": "not found"}, request=request)
        return httpx.Response(
            200,
            json={"data": {"sku": sku, "quantity": qty}},
            headers={"content-type": "application/json"},
            request=request,
        )

    if path == "/auth-check":
        auth = headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            return httpx.Response(401, json={"error": "unauthorized"}, request=request)
        return httpx.Response(
            200,
            json={"data": {"user": "test", "on_behalf_of": headers.get("x-on-behalf-of", "")}},
            headers={"content-type": "application/json"},
            request=request,
        )

    return httpx.Response(404, json={"error": "not found"}, request=request)


@pytest.fixture
def mock_client() -> httpx.Client:
    transport = httpx.MockTransport(_virtual_business_handler)
    return httpx.Client(transport=transport)


# ---------------------------------------------------------------------------
# Connector definition matching the virtual system
# ---------------------------------------------------------------------------


def _erp_connector_def() -> dict[str, Any]:
    return {
        "display_name": "虚拟 ERP",
        "base_url": "http://localhost:9999",
        "auth": {"type": "bearer", "token": "test-token-123"},
        "identity": {
            "enabled": True,
            "header_name": "X-On-Behalf-Of",
            "name_header": "X-On-Behalf-Of-Name",
            "channel_header": "X-Request-Channel",
        },
        "default_headers": {"Accept": "application/json"},
        "timeout": 30,
        "tools": [
            {
                "name": "list_orders",
                "description": "查询订单列表",
                "method": "GET",
                "path": "/orders",
                "parameters": {
                    "product": {
                        "type": "string",
                        "in": "query",
                        "required": False,
                        "description": "按产品过滤",
                        "default": None,
                    },
                },
                "response_mapping": {"data_path": "data", "truncate_at": 8000},
                "sensitive": False,
            },
            {
                "name": "get_order",
                "description": "查询单个订单",
                "method": "GET",
                "path": "/orders/{id}",
                "parameters": {
                    "id": {
                        "type": "string",
                        "in": "path",
                        "required": True,
                        "description": "订单ID",
                        "default": None,
                    },
                },
                "response_mapping": {"data_path": "data", "truncate_at": 8000},
                "sensitive": False,
            },
            {
                "name": "create_order",
                "description": "创建新订单",
                "method": "POST",
                "path": "/orders",
                "parameters": {
                    "product": {
                        "type": "string",
                        "in": "body",
                        "required": True,
                        "description": "产品名",
                        "default": None,
                    },
                    "qty": {
                        "type": "integer",
                        "in": "body",
                        "required": True,
                        "description": "数量",
                        "default": None,
                    },
                },
                "response_mapping": {"data_path": "data", "truncate_at": 8000},
                "sensitive": True,
            },
            {
                "name": "get_inventory",
                "description": "查询库存",
                "method": "GET",
                "path": "/inventory/{sku}",
                "parameters": {
                    "sku": {
                        "type": "string",
                        "in": "path",
                        "required": True,
                        "description": "SKU",
                        "default": None,
                    },
                },
                "response_mapping": {"data_path": "data", "truncate_at": 8000},
                "sensitive": False,
            },
        ],
    }


# ---------------------------------------------------------------------------
# E2E tests
# ---------------------------------------------------------------------------


def test_e2e_list_tools():
    tools = list_tools("erp", _erp_connector_def())
    assert len(tools) == 4
    names = {t["name"] for t in tools}
    assert names == {
        "erp__list_orders",
        "erp__get_order",
        "erp__create_order",
        "erp__get_inventory",
    }
    for t in tools:
        assert "[虚拟 ERP]" in t["description"]


def test_e2e_list_orders(mock_client: httpx.Client):
    result = call_tool("erp", "list_orders", _erp_connector_def(), {}, client=mock_client)
    assert result["http_status"] == 200
    data = json.loads(result["data"])
    assert len(data) == 2


def test_e2e_get_order(mock_client: httpx.Client):
    result = call_tool(
        "erp", "get_order", _erp_connector_def(), {"id": "ORD-001"}, client=mock_client
    )
    assert result["http_status"] == 200
    data = json.loads(result["data"])
    assert data["order_id"] == "ORD-001"
    assert data["product"] == "Widget A"


def test_e2e_get_order_not_found(mock_client: httpx.Client):
    result = call_tool("erp", "get_order", _erp_connector_def(), {"id": "NOPE"}, client=mock_client)
    assert result["error"] == "client_error"
    assert result["http_status"] == 404


def test_e2e_create_order_sensitive(mock_client: httpx.Client):
    result = call_tool(
        "erp",
        "create_order",
        _erp_connector_def(),
        {"product": "Widget C", "qty": 3},
        client=mock_client,
    )
    assert result["http_status"] == 201
    assert result.get("sensitive") is True
    assert result["data"].startswith("[自动执行的操作]")
    data = json.loads(result["data"].removeprefix("[自动执行的操作] ").strip())
    assert data["product"] == "Widget C"


def test_e2e_get_inventory(mock_client: httpx.Client):
    result = call_tool(
        "erp",
        "get_inventory",
        _erp_connector_def(),
        {"sku": "Widget-A"},
        client=mock_client,
    )
    assert result["http_status"] == 200
    data = json.loads(result["data"])
    assert data["sku"] == "Widget-A"
    assert data["quantity"] == 100


def test_e2e_identity_headers_propagated(mock_client: httpx.Client):
    ctx = ActorContext(user_id=42, username="alice", agent_id="a1")
    token = set_actor_context(ctx)
    try:
        result = call_tool(
            "erp",
            "get_order",
            _erp_connector_def(),
            {"id": "ORD-001"},
            client=mock_client,
        )
    finally:
        reset_actor_context(token)
    assert result["http_status"] == 200


def test_e2e_langchain_tools(mock_client: httpx.Client):
    import octop.infra.connectors.api_connector.langchain as lc_mod

    original = lc_mod.call_tool

    def patched(cn, tn, cdef, args, *, client=None):
        return original(cn, tn, cdef, args, client=mock_client)

    lc_mod.call_tool = patched
    try:
        tools = build_api_connector_langchain_tools(
            connector_name="erp",
            connector_def=_erp_connector_def(),
        )
        assert len(tools) == 4
        get_tool = next(t for t in tools if "get_order" in t.name)
        result = get_tool.invoke({"id": "ORD-001"})
        assert "ORD-001" in result
    finally:
        lc_mod.call_tool = original


# ---------------------------------------------------------------------------
# Service CRUD + audit integration
# ---------------------------------------------------------------------------


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


def _ensure_user(db: SqlitePool) -> int:
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO users(username, password_hash, role, created_at) "
            "VALUES ('u1', 'x', 'user', 1)"
        )
        row = conn.execute("SELECT id FROM users WHERE username = 'u1'").fetchone()
    assert row is not None
    return int(row["id"])


def test_e2e_service_crud_and_audit(svc: ConnectorService, db: SqlitePool):
    uid = _ensure_user(db)
    raw_connector = {
        "base_url": "http://localhost:9999",
        "auth": {"type": "bearer", "token": "test-token-123"},
        "display_name": "虚拟 ERP",
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
    saved = svc.put_api_connectors(uid, {"erp": raw_connector})
    assert "erp" in saved

    connectors = svc.get_api_connectors(uid)
    assert connectors["erp"]["auth"]["token"] == "test-token-123"

    preview = svc.get_api_connectors_for_api(uid)
    assert "erp" in preview
    assert preview["erp"]["tool_count"] == 1
    assert preview["erp"]["auth"]["type"] == "bearer"

    listed = svc.list_instances_for_api(uid)
    api_items = [i for i in listed if i["kind"] == "api-connector"]
    assert len(api_items) == 1
    assert api_items[0]["instance_id"] == synthetic_instance_id("erp")

    audit_repo = AuditRepo(db)
    record_api_call(
        audit_repo,
        username="u1",
        agent_id="a1",
        connector="erp",
        tool="get_order",
        http_method="GET",
        http_url="http://localhost:9999/orders/ORD-001",
        http_status=200,
        duration_ms=15,
        identity_propagated=True,
    )
    with db.transaction() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE action = 'api_connector_call'"
        ).fetchall()
    assert len(rows) == 1
    payload = json.loads(rows[0]["payload"])
    assert payload["connector"] == "erp"
    assert payload["http_status"] == 200
