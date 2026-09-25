"""Tests for API Connector adapter — list_tools and call_tool."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import httpx

from octop.infra.connectors.api_connector.adapter import call_tool, list_tools
from octop.infra.connectors.api_connector.identity import (
    ActorContext,
    reset_actor_context,
    set_actor_context,
)


def _connector_def(**overrides):
    base = {
        "display_name": "订单系统",
        "base_url": "http://localhost:8080",
        "auth": {"type": "bearer", "token": "tok"},
        "identity": {
            "enabled": True,
            "header_name": "X-On-Behalf-Of",
            "name_header": "X-On-Behalf-Of-Name",
            "channel_header": "X-Request-Channel",
        },
        "default_headers": {},
        "timeout": 30,
        "tools": [
            {
                "name": "get_order",
                "description": "查询订单",
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
                    "verbose": {
                        "type": "boolean",
                        "in": "query",
                        "required": False,
                        "description": "详细信息",
                        "default": None,
                    },
                },
                "response_mapping": {"data_path": None, "truncate_at": 8000},
                "sensitive": False,
            },
            {
                "name": "create_order",
                "description": "创建订单",
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
                },
                "response_mapping": {"data_path": "data", "truncate_at": 8000},
                "sensitive": True,
            },
        ],
    }
    base.update(overrides)
    return base


# -- list_tools --


def test_list_tools_names_and_descriptions():
    tools = list_tools("erp", _connector_def())
    assert len(tools) == 2
    assert tools[0]["name"] == "erp__get_order"
    assert "[订单系统]" in tools[0]["description"]
    assert tools[1]["name"] == "erp__create_order"


def test_list_tools_input_schema_includes_path_params():
    tools = list_tools("erp", _connector_def())
    get_order = tools[0]
    props = get_order["inputSchema"]["properties"]
    assert "id" in props
    assert "verbose" in props
    assert "id" in get_order["inputSchema"]["required"]


def test_list_tools_required_params():
    tools = list_tools("erp", _connector_def())
    create = tools[1]
    assert "product" in create["inputSchema"]["required"]


# -- call_tool --


def _mock_client(status: int = 200, json_data=None, text: str = "ok"):
    client = MagicMock(spec=httpx.Client)
    if json_data is not None:
        text = json.dumps(json_data, ensure_ascii=False)
    resp = httpx.Response(
        status,
        text=text,
        headers={"content-type": "application/json"}
        if json_data
        else {"content-type": "text/plain"},
        request=httpx.Request("GET", "http://x"),
    )
    client.send.return_value = resp
    return client


def test_call_tool_success():
    client = _mock_client(text='{"order_id": "123"}')
    result = call_tool("erp", "get_order", _connector_def(), {"id": "123"}, client=client)
    assert result["http_status"] == 200
    assert "data" in result
    assert result["duration_ms"] >= 0


def test_call_tool_not_found_tool():
    result = call_tool("erp", "nonexistent", _connector_def(), {}, client=_mock_client())
    assert "error" in result
    assert "not found" in result["error"]


def test_call_tool_auth_failure():
    client = _mock_client(status=401)
    result = call_tool("erp", "get_order", _connector_def(), {"id": "1"}, client=client)
    assert result["error"] == "auth_failed"
    assert result["http_status"] == 401


def test_call_tool_server_error():
    client = _mock_client(status=500)
    result = call_tool("erp", "get_order", _connector_def(), {"id": "1"}, client=client)
    assert result["error"] == "server_error"


def test_call_tool_client_error():
    client = _mock_client(status=404)
    result = call_tool("erp", "get_order", _connector_def(), {"id": "1"}, client=client)
    assert result["error"] == "client_error"
    assert result["http_status"] == 404


def test_call_tool_timeout():
    client = MagicMock(spec=httpx.Client)
    client.send.side_effect = httpx.TimeoutException("timed out")
    result = call_tool("erp", "get_order", _connector_def(), {"id": "1"}, client=client)
    assert result["error"] == "timeout"


def test_call_tool_connection_error():
    client = MagicMock(spec=httpx.Client)
    client.send.side_effect = httpx.ConnectError("refused")
    result = call_tool("erp", "get_order", _connector_def(), {"id": "1"}, client=client)
    assert result["error"] == "connection_failed"


def test_call_tool_sensitive_prefix():
    client = _mock_client(text='{"ok": true}')
    result = call_tool("erp", "create_order", _connector_def(), {"product": "x"}, client=client)
    assert result.get("sensitive") is True
    assert result["data"].startswith("[自动执行的操作]")


def test_call_tool_identity_propagation():
    client = _mock_client(text="ok")
    ctx = ActorContext(user_id=42, username="alice", agent_id="a1")
    token = set_actor_context(ctx)
    try:
        call_tool("erp", "get_order", _connector_def(), {"id": "1"}, client=client)
    finally:
        reset_actor_context(token)
    sent_request = client.send.call_args[0][0]
    assert sent_request.headers.get("X-On-Behalf-Of") == "42"
    assert sent_request.headers.get("X-On-Behalf-Of-Name") == "alice"
