"""Tool listing and execution for API Connector."""

from __future__ import annotations

import time
from typing import Any

import httpx

from octop.infra.connectors.api_connector.http_executor import (
    build_request,
    execute_request,
    extract_response,
)
from octop.infra.connectors.api_connector.identity import (
    get_actor_context,
    inject_identity_headers,
)


def list_tools(connector_name: str, connector_def: dict[str, Any]) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    display_name = connector_def.get("display_name", connector_name)
    for tool_def in connector_def.get("tools", []):
        properties: dict[str, Any] = {}
        required: list[str] = []
        for param_name, param in tool_def.get("parameters", {}).items():
            prop: dict[str, Any] = {
                "type": param.get("type", "string"),
                "description": param.get("description", ""),
            }
            if param.get("enum"):
                prop["enum"] = param["enum"]
            if param.get("type") == "array" and param.get("items"):
                prop["items"] = param["items"]
            if param.get("type") == "object" and param.get("properties"):
                prop["properties"] = param["properties"]
            properties[param_name] = prop
            if param.get("required"):
                required.append(param_name)

        tools.append(
            {
                "name": f"{connector_name}__{tool_def['name']}",
                "description": f"[{display_name}] {tool_def.get('description', tool_def['name'])}",
                "inputSchema": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            }
        )
    return tools


def call_tool(
    connector_name: str,
    tool_name: str,
    connector_def: dict[str, Any],
    args: dict[str, Any],
    *,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    tool_def = _find_tool_def(connector_def, tool_name)
    if tool_def is None:
        return {"error": f"tool {tool_name!r} not found in connector {connector_name!r}"}

    auth = connector_def.get("auth", {})
    identity_config = connector_def.get("identity", {})
    default_headers = connector_def.get("default_headers", {})
    timeout = connector_def.get("timeout", 30)

    identity_headers: dict[str, str] = {}
    actor = get_actor_context()
    if actor is not None:
        inject_identity_headers(
            identity_headers,
            identity_config,
            user_id=actor.user_id,
            user_name=actor.username,
        )

    request = build_request(
        base_url=connector_def["base_url"],
        tool_def=tool_def,
        args=args,
        auth=auth,
        default_headers=default_headers,
        identity_headers=identity_headers,
    )

    should_close = client is None
    if client is None:
        client = httpx.Client()

    start = time.monotonic()
    try:
        response = execute_request(client, request, timeout=timeout)
        duration_ms = int((time.monotonic() - start) * 1000)
        return _handle_response(response, tool_def, duration_ms=duration_ms)
    except httpx.TimeoutException:
        duration_ms = int((time.monotonic() - start) * 1000)
        return {
            "error": "timeout",
            "message": "业务系统暂时无法访问，请稍后重试。",
            "duration_ms": duration_ms,
        }
    except httpx.HTTPError as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        return {
            "error": "connection_failed",
            "message": "业务系统暂时无法访问，请稍后重试。",
            "detail": str(exc),
            "duration_ms": duration_ms,
        }
    finally:
        if should_close:
            client.close()


def _find_tool_def(connector_def: dict[str, Any], tool_name: str) -> dict[str, Any] | None:
    for t in connector_def.get("tools", []):
        if isinstance(t, dict) and t.get("name") == tool_name:
            return t
    return None


def _handle_response(
    response: httpx.Response, tool_def: dict[str, Any], *, duration_ms: int
) -> dict[str, Any]:
    status = response.status_code
    rm = tool_def.get("response_mapping", {})
    data_path = rm.get("data_path")
    truncate_at = rm.get("truncate_at", 8000)

    if status in (401, 403):
        return {
            "error": "auth_failed",
            "message": "API 认证失败，请检查连接器凭证配置。",
            "http_status": status,
            "duration_ms": duration_ms,
        }

    if status >= 500:
        return {
            "error": "server_error",
            "message": f"业务系统返回错误：{status}",
            "http_status": status,
            "duration_ms": duration_ms,
        }

    if status >= 400:
        return {
            "error": "client_error",
            "message": f"业务系统返回错误：{status}",
            "http_status": status,
            "duration_ms": duration_ms,
        }

    body, truncated = extract_response(response, data_path=data_path, truncate_at=truncate_at)

    result: dict[str, Any] = {
        "data": body,
        "http_status": status,
        "duration_ms": duration_ms,
        "truncated": truncated,
    }
    if tool_def.get("sensitive"):
        result["sensitive"] = True
        result["data"] = f"[自动执行的操作] {body}"
    return result
