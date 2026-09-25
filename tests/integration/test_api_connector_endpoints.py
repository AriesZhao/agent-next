"""Integration tests for API Connector HTTP endpoints."""

from __future__ import annotations

import pytest

from tests.support.app import octop_client
from tests.support.auth import auth_header, bootstrap_admin


@pytest.fixture
async def env(env_with_agent):
    yield env_with_agent


CONNECTOR_SPEC = {
    "name": "order-system",
    "display_name": "订单系统",
    "description": "订单查询与管理",
    "base_url": "https://order.example.com/api",
    "auth": {
        "type": "bearer",
        "token": "sk-test-token-12345",
    },
    "identity": {
        "enabled": True,
        "header_name": "X-On-Behalf-Of",
    },
    "tools": [
        {
            "name": "list_orders",
            "description": "查询订单列表",
            "method": "GET",
            "path": "/orders",
            "parameters": {
                "status": {
                    "type": "string",
                    "description": "订单状态",
                    "in": "query",
                },
            },
        },
        {
            "name": "get_order",
            "description": "获取订单详情",
            "method": "GET",
            "path": "/orders/{id}",
            "parameters": {
                "id": {
                    "type": "string",
                    "description": "订单ID",
                    "in": "path",
                    "required": True,
                },
            },
        },
    ],
}


async def test_list_api_connectors_empty(env):
    c, _, auth, _ = env
    r = await c.get("/api/connectors/api-connectors", headers=auth)
    assert r.status_code == 200
    assert r.json()["connectors"] == []


async def test_put_and_get_api_connector(env):
    c, _, auth, _ = env
    r = await c.post(
        "/api/connectors/api-connectors",
        headers=auth,
        json=CONNECTOR_SPEC,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["connector"]["name"] == "order-system"
    assert body["connector"]["display_name"] == "订单系统"
    assert body["connector"]["base_url"] == "https://order.example.com/api"
    assert body["connector"]["tool_count"] == 2
    assert body["connector"]["auth"]["type"] == "bearer"
    assert "sk-test-token-12345" not in str(body["connector"]["auth"])
    assert "****" in body["connector"]["auth"]["token_preview"]

    r2 = await c.get("/api/connectors/api-connectors/order-system", headers=auth)
    assert r2.status_code == 200
    conn = r2.json()["connector"]
    assert conn["name"] == "order-system"
    assert conn["tool_count"] == 2
    tools_summary = conn["tools_summary"]
    assert len(tools_summary) == 2
    assert tools_summary[0]["name"] == "list_orders"
    assert tools_summary[1]["name"] == "get_order"


async def test_list_api_connectors_after_create(env):
    c, _, auth, _ = env
    await c.post("/api/connectors/api-connectors", headers=auth, json=CONNECTOR_SPEC)
    r = await c.get("/api/connectors/api-connectors", headers=auth)
    assert r.status_code == 200
    connectors = r.json()["connectors"]
    assert len(connectors) == 1
    assert connectors[0]["name"] == "order-system"


async def test_patch_api_connector(env):
    c, _, auth, _ = env
    await c.post("/api/connectors/api-connectors", headers=auth, json=CONNECTOR_SPEC)

    r = await c.patch(
        "/api/connectors/api-connectors/order-system",
        headers=auth,
        json={"enabled": False, "display_name": "订单系统(已停用)"},
    )
    assert r.status_code == 200
    conn = r.json()["connector"]
    assert conn["enabled"] is False
    assert conn["display_name"] == "订单系统(已停用)"


async def test_patch_nonexistent(env):
    c, _, auth, _ = env
    r = await c.patch(
        "/api/connectors/api-connectors/nonexistent",
        headers=auth,
        json={"enabled": False},
    )
    assert r.status_code == 404


async def test_delete_api_connector(env):
    c, _, auth, _ = env
    await c.post("/api/connectors/api-connectors", headers=auth, json=CONNECTOR_SPEC)

    r = await c.delete("/api/connectors/api-connectors/order-system", headers=auth)
    assert r.status_code == 200
    assert r.json()["ok"] is True

    r2 = await c.get("/api/connectors/api-connectors/order-system", headers=auth)
    assert r2.status_code == 404


async def test_delete_nonexistent(env):
    c, _, auth, _ = env
    r = await c.delete("/api/connectors/api-connectors/nonexistent", headers=auth)
    assert r.status_code == 404


async def test_get_nonexistent(env):
    c, _, auth, _ = env
    r = await c.get("/api/connectors/api-connectors/nonexistent", headers=auth)
    assert r.status_code == 404


async def test_put_invalid_base_url(env):
    c, _, auth, _ = env
    spec = dict(CONNECTOR_SPEC)
    spec["base_url"] = "not-a-url"
    r = await c.post("/api/connectors/api-connectors", headers=auth, json=spec)
    assert r.status_code == 400


async def test_put_invalid_name(env):
    c, _, auth, _ = env
    spec = dict(CONNECTOR_SPEC)
    spec["name"] = "invalid name with spaces"
    r = await c.post("/api/connectors/api-connectors", headers=auth, json=spec)
    assert r.status_code == 400


async def test_update_existing_connector(env):
    c, _, auth, _ = env
    await c.post("/api/connectors/api-connectors", headers=auth, json=CONNECTOR_SPEC)

    updated = dict(CONNECTOR_SPEC)
    updated["display_name"] = "订单系统 v2"
    updated["tools"] = CONNECTOR_SPEC["tools"] + [
        {
            "name": "create_order",
            "description": "创建订单",
            "method": "POST",
            "path": "/orders",
            "parameters": {},
        },
    ]
    r = await c.post("/api/connectors/api-connectors", headers=auth, json=updated)
    assert r.status_code == 200
    assert r.json()["connector"]["display_name"] == "订单系统 v2"
    assert r.json()["connector"]["tool_count"] == 3


async def test_test_endpoint_with_inline_spec(env):
    c, _, auth, _ = env
    r = await c.post(
        "/api/connectors/api-connectors/test",
        headers=auth,
        json={
            "base_url": "https://httpbin.org/get",
            "auth": {"type": "bearer", "token": "test"},
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert "ok" in body
    assert "reachable" in body


async def test_test_endpoint_with_saved_name(env):
    c, _, auth, _ = env
    await c.post("/api/connectors/api-connectors", headers=auth, json=CONNECTOR_SPEC)

    r = await c.post(
        "/api/connectors/api-connectors/test",
        headers=auth,
        json={"name": "order-system"},
    )
    assert r.status_code == 200
    body = r.json()
    assert "ok" in body


async def test_test_endpoint_no_url(env):
    c, _, auth, _ = env
    r = await c.post(
        "/api/connectors/api-connectors/test",
        headers=auth,
        json={},
    )
    assert r.status_code == 400


async def test_credentials_not_leaked(env):
    c, _, auth, _ = env
    await c.post("/api/connectors/api-connectors", headers=auth, json=CONNECTOR_SPEC)

    r = await c.get("/api/connectors/api-connectors/order-system", headers=auth)
    body = r.text
    assert "sk-test-token-12345" not in body


async def test_connector_instances_includes_api_connectors(env):
    c, _, auth, _ = env
    await c.post("/api/connectors/api-connectors", headers=auth, json=CONNECTOR_SPEC)

    r = await c.get("/api/connector-instances", headers=auth)
    assert r.status_code == 200
    instances = r.json()
    api_instances = [i for i in instances if i["kind"] == "api-connector"]
    assert len(api_instances) == 1
    assert api_instances[0]["display_name"] == "订单系统"
