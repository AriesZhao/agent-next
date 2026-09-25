"""Tests for API Connector LangChain tool generation."""

from __future__ import annotations

from unittest.mock import patch

from octop.infra.connectors.api_connector.langchain import (
    build_api_connector_langchain_tools,
)


def _connector_def():
    return {
        "display_name": "订单系统",
        "base_url": "http://localhost:8080",
        "auth": {"type": "bearer", "token": "tok"},
        "identity": {"enabled": False},
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
        ],
    }


def test_build_tools_count_and_name():
    tools = build_api_connector_langchain_tools(
        connector_name="erp",
        connector_def=_connector_def(),
    )
    assert len(tools) == 1
    assert "erp" in tools[0].name
    assert "get_order" in tools[0].name


def test_build_tools_description():
    tools = build_api_connector_langchain_tools(
        connector_name="erp",
        connector_def=_connector_def(),
    )
    assert "[订单系统]" in tools[0].description
    assert "查询订单" in tools[0].description


def test_build_tools_args_schema_includes_path_params():
    tools = build_api_connector_langchain_tools(
        connector_name="erp",
        connector_def=_connector_def(),
    )
    schema = tools[0].args_schema.model_json_schema()
    props = schema.get("properties", {})
    assert "id" in props
    assert "verbose" in props


def test_build_tools_invocation():
    with patch("octop.infra.connectors.api_connector.langchain.call_tool") as mock_call:
        mock_call.return_value = {"data": '{"order_id": "1"}', "http_status": 200}
        tools = build_api_connector_langchain_tools(
            connector_name="erp",
            connector_def=_connector_def(),
        )
        result = tools[0].invoke({"id": "1", "verbose": True})
        assert "order_id" in result
        mock_call.assert_called_once()
        cn, tn = mock_call.call_args[0][0], mock_call.call_args[0][1]
        assert cn == "erp"
        assert tn == "get_order"


def test_build_tools_error_returns_message():
    with patch("octop.infra.connectors.api_connector.langchain.call_tool") as mock_call:
        mock_call.return_value = {"error": "timeout", "message": "业务系统暂时无法访问"}
        tools = build_api_connector_langchain_tools(
            connector_name="erp",
            connector_def=_connector_def(),
        )
        result = tools[0].invoke({"id": "1"})
        assert "暂时无法访问" in result
