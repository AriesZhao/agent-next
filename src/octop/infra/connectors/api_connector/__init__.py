"""API Connector — user-defined REST API tools stored as one connector doc."""

from __future__ import annotations

import re
from typing import Any

API_CONNECTOR_KIND = "api-connector"
API_CONNECTOR_DISPLAY_NAME = "API 连接器"

_CONNECTOR_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def synthetic_instance_id(connector_name: str) -> str:
    return f"api:{connector_name}"


def shared_synthetic_instance_id(parent_instance_id: str, connector_name: str) -> str:
    return f"api:{parent_instance_id}:{connector_name}"


def parse_synthetic_instance_id(instance_id: str) -> str | None:
    if not instance_id.startswith("api:"):
        return None
    name = instance_id.removeprefix("api:")
    return name if name else None


def connector_mcp_server_name(connector_name: str) -> str:
    return f"api__{connector_name}"


def tool_fqn(connector_name: str, tool_name: str) -> str:
    return f"{connector_name}__{tool_name}"


def connector_enabled(spec: dict[str, Any]) -> bool:
    return spec.get("enabled", True) is not False


def extract_connectors(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {}
    raw = payload.get("connectors")
    if isinstance(raw, dict):
        return dict(raw)
    return {}


def wrap_connectors(connectors: dict[str, Any]) -> dict[str, Any]:
    return {"connectors": connectors}


def redact_connector_for_api(name: str, spec: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": name,
        "display_name": spec.get("display_name", name),
        "description": spec.get("description", ""),
        "base_url": spec.get("base_url", ""),
        "enabled": connector_enabled(spec),
        "default_open": spec.get("default_open", True),
        "shared": spec.get("shared", False),
    }
    auth = spec.get("auth", {})
    auth_type = auth.get("type", "bearer")
    auth_preview: dict[str, Any] = {"type": auth_type}
    if auth_type == "bearer":
        token = str(auth.get("token") or "")
        auth_preview["token_preview"] = _mask_secret(token, "Bearer ")
    elif auth_type == "api_key":
        key = str(auth.get("api_key") or "")
        auth_preview["api_key_preview"] = _mask_secret(key)
        auth_preview["api_key_header"] = auth.get("api_key_header", "X-API-Key")
    elif auth_type == "basic":
        auth_preview["username"] = auth.get("username", "")
        auth_preview["password_configured"] = bool(auth.get("password"))
    out["auth"] = auth_preview

    identity = spec.get("identity", {})
    out["identity"] = {
        "enabled": identity.get("enabled", True),
        "header_name": identity.get("header_name", "X-On-Behalf-Of"),
        "name_header": identity.get("name_header", "X-On-Behalf-Of-Name"),
        "channel_header": identity.get("channel_header", "X-Request-Channel"),
    }

    tools = spec.get("tools", [])
    out["tool_count"] = len(tools)
    out["tools_summary"] = [
        {"name": t.get("name", ""), "method": t.get("method", "GET"), "path": t.get("path", "")}
        for t in tools
        if isinstance(t, dict)
    ]

    out["max_calls_per_turn"] = spec.get("max_calls_per_turn", 20)
    out["max_calls_per_minute"] = spec.get("max_calls_per_minute", 60)
    return out


def expand_api_connector_instances(
    *,
    parent: Any,
    connectors: dict[str, Any],
    shared_view: bool = False,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for name, spec in connectors.items():
        if not isinstance(spec, dict):
            continue
        enabled = connector_enabled(spec)
        if shared_view and spec.get("shared") is not True:
            continue
        items.append(
            {
                "instance_id": (
                    shared_synthetic_instance_id(parent.instance_id, name)
                    if shared_view
                    else synthetic_instance_id(name)
                ),
                "kind": API_CONNECTOR_KIND,
                "display_name": spec.get("display_name", name),
                "status": "active" if enabled else "disabled",
                "mcp_server_name": connector_mcp_server_name(name),
                "has_credentials": True,
                "default_open": spec.get("default_open", True),
                "shared": spec.get("shared", False),
                "owner_user_id": parent.user_id,
                "created_at": parent.created_at,
                "updated_at": parent.updated_at,
            }
        )
    items.sort(key=lambda r: str(r["display_name"]).casefold())
    return items


def _mask_secret(value: str, prefix: str = "") -> str:
    if not value:
        return prefix + "****"
    if len(value) <= 4:
        return prefix + "****"
    return prefix + value[:2] + "****" + value[-2:]
