"""Data structures and validation for API Connector definitions."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from octop.infra.utils.ssrf_guard import UnsafeOutboundUrl, validate_https_url

_CONNECTOR_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
_TOOL_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
_PARAM_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
_DISPLAY_NAME_MAX = 64
_TIMEOUT_MAX = 120
_TIMEOUT_DEFAULT = 30
_TRUNCATE_DEFAULT = 8000
_VALID_AUTH_TYPES = frozenset({"bearer", "api_key", "basic"})
_VALID_PARAM_TYPES = frozenset({"string", "integer", "number", "boolean", "array", "object"})
_VALID_PARAM_IN = frozenset({"query", "path", "header", "body"})
_VALID_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})


class ApiConnectorValidationError(ValueError):
    pass


def validate_connector_name(name: str) -> str:
    text = str(name or "").strip()
    if not text:
        raise ApiConnectorValidationError("connector name is required")
    if not _CONNECTOR_NAME_RE.match(text):
        raise ApiConnectorValidationError(
            f"invalid connector name {name!r}: use letters, digits, _ or -"
        )
    return text


def validate_base_url(url: str) -> str:
    text = str(url or "").strip()
    if not text:
        raise ApiConnectorValidationError("base_url is required")
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https"):
        raise ApiConnectorValidationError("base_url must be http or https")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        raise ApiConnectorValidationError("base_url missing hostname")
    if host in {"localhost", "127.0.0.1", "::1"}:
        return text.rstrip("/")
    if parsed.scheme != "https":
        raise ApiConnectorValidationError("non-local base_url must use https")
    try:
        validate_https_url(text, field="base_url")
    except UnsafeOutboundUrl as exc:
        raise ApiConnectorValidationError(str(exc)) from exc
    return text.rstrip("/")


def validate_auth(auth: Any) -> dict[str, Any]:
    if not isinstance(auth, dict):
        raise ApiConnectorValidationError("auth must be an object")
    auth_type = str(auth.get("type") or "").strip()
    if auth_type not in _VALID_AUTH_TYPES:
        raise ApiConnectorValidationError(f"auth.type must be one of {sorted(_VALID_AUTH_TYPES)}")
    result: dict[str, Any] = {"type": auth_type}
    if auth_type == "bearer":
        token = str(auth.get("token") or "").strip()
        if not token:
            raise ApiConnectorValidationError("auth.token is required for bearer")
        result["token"] = token
    elif auth_type == "api_key":
        key = str(auth.get("api_key") or "").strip()
        if not key:
            raise ApiConnectorValidationError("auth.api_key is required for api_key")
        result["api_key"] = key
        header = str(auth.get("api_key_header") or "X-API-Key").strip()
        if not header:
            raise ApiConnectorValidationError("auth.api_key_header must be non-empty")
        result["api_key_header"] = header
    elif auth_type == "basic":
        username = str(auth.get("username") or "").strip()
        password = str(auth.get("password") or "").strip()
        if not username:
            raise ApiConnectorValidationError("auth.username is required for basic")
        if not password:
            raise ApiConnectorValidationError("auth.password is required for basic")
        result["username"] = username
        result["password"] = password
    return result


def validate_identity(identity: Any) -> dict[str, Any]:
    if identity is None:
        return {
            "enabled": True,
            "header_name": "X-On-Behalf-Of",
            "name_header": "X-On-Behalf-Of-Name",
            "channel_header": "X-Request-Channel",
        }
    if not isinstance(identity, dict):
        raise ApiConnectorValidationError("identity must be an object")
    return {
        "enabled": identity.get("enabled", True),
        "header_name": str(identity.get("header_name") or "X-On-Behalf-Of").strip(),
        "name_header": str(identity.get("name_header") or "X-On-Behalf-Of-Name").strip(),
        "channel_header": str(identity.get("channel_header") or "X-Request-Channel").strip(),
    }


def _param_to_json_schema(param: dict[str, Any]) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": param.get("type", "string")}
    if param.get("description"):
        schema["description"] = param["description"]
    if param.get("enum"):
        schema["enum"] = list(param["enum"])
    if param.get("type") == "array" and param.get("items"):
        schema["items"] = _param_to_json_schema(param["items"])
    if param.get("type") == "object" and param.get("properties"):
        props: dict[str, Any] = {}
        for k, v in param["properties"].items():
            if isinstance(v, dict):
                props[k] = _param_to_json_schema(v)
        schema["properties"] = props
    return schema


def validate_tool(tool: Any, *, connector_name: str) -> dict[str, Any]:
    if not isinstance(tool, dict):
        raise ApiConnectorValidationError(f"tool in {connector_name!r} must be an object")
    name = str(tool.get("name") or "").strip()
    if not name:
        raise ApiConnectorValidationError(f"tool name is required in {connector_name!r}")
    if not _TOOL_NAME_RE.match(name):
        raise ApiConnectorValidationError(
            f"invalid tool name {name!r}: use letters, digits, _ or -"
        )
    method = str(tool.get("method") or "GET").upper()
    if method not in _VALID_METHODS:
        raise ApiConnectorValidationError(
            f"tool {name!r}: method must be one of {sorted(_VALID_METHODS)}"
        )
    path = str(tool.get("path") or "").strip()
    if not path:
        raise ApiConnectorValidationError(f"tool {name!r}: path is required")
    if not path.startswith("/"):
        raise ApiConnectorValidationError(f"tool {name!r}: path must start with /")

    raw_params = tool.get("parameters", {})
    if not isinstance(raw_params, dict):
        raise ApiConnectorValidationError(f"tool {name!r}: parameters must be an object")

    validated_params: dict[str, Any] = {}
    path_placeholders = set(re.findall(r"\{(\w+)\}", path))
    for param_name, raw_param in raw_params.items():
        pn = str(param_name).strip()
        if not pn:
            raise ApiConnectorValidationError(f"tool {name!r}: parameter name must be non-empty")
        if not _PARAM_NAME_RE.match(pn):
            raise ApiConnectorValidationError(f"tool {name!r}: invalid parameter name {pn!r}")
        if not isinstance(raw_param, dict):
            raise ApiConnectorValidationError(f"tool {name!r}: parameter {pn!r} must be an object")
        ptype = str(raw_param.get("type") or "string")
        if ptype not in _VALID_PARAM_TYPES:
            raise ApiConnectorValidationError(
                f"tool {name!r}: parameter {pn!r} type must be one of {sorted(_VALID_PARAM_TYPES)}"
            )
        pin = str(raw_param.get("in") or "query")
        if pin not in _VALID_PARAM_IN:
            raise ApiConnectorValidationError(
                f"tool {name!r}: parameter {pn!r} 'in' must be one of {sorted(_VALID_PARAM_IN)}"
            )
        if pin == "path" and pn not in path_placeholders:
            raise ApiConnectorValidationError(
                f"tool {name!r}: path parameter {pn!r} not in path template"
            )
        validated_params[pn] = {
            "type": ptype,
            "description": str(raw_param.get("description") or ""),
            "in": pin,
            "required": bool(raw_param.get("required", pin == "path")),
            "default": raw_param.get("default"),
        }
        if raw_param.get("enum"):
            validated_params[pn]["enum"] = list(raw_param["enum"])
        if ptype == "array" and raw_param.get("items"):
            validated_params[pn]["items"] = _param_to_json_schema(
                {"type": "array", "items": raw_param["items"]}
            ).get("items", {})
        if ptype == "object" and raw_param.get("properties"):
            validated_params[pn]["properties"] = {
                k: v for k, v in raw_param["properties"].items() if isinstance(v, dict)
            }

    for ph in path_placeholders:
        if ph not in validated_params:
            raise ApiConnectorValidationError(
                f"tool {name!r}: path placeholder {{{ph}}} has no parameter definition"
            )

    response_mapping = tool.get("response_mapping") or {}
    if not isinstance(response_mapping, dict):
        raise ApiConnectorValidationError(f"tool {name!r}: response_mapping must be an object")

    result: dict[str, Any] = {
        "name": name,
        "description": str(tool.get("description") or name),
        "method": method,
        "path": path,
        "parameters": validated_params,
        "response_mapping": {
            "data_path": response_mapping.get("data_path"),
            "truncate_at": int(response_mapping.get("truncate_at", _TRUNCATE_DEFAULT)),
        },
        "sensitive": bool(tool.get("sensitive", False)),
    }
    return result


def validate_connector_spec(name: str, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ApiConnectorValidationError(f"connector {name!r} must be an object")
    connector_name = validate_connector_name(name)
    display_name = str(raw.get("display_name") or "").strip()
    if display_name:
        if len(display_name) > _DISPLAY_NAME_MAX:
            raise ApiConnectorValidationError(
                f"connector {name!r}: display_name must be at most {_DISPLAY_NAME_MAX} characters"
            )
    else:
        display_name = connector_name

    base_url = validate_base_url(str(raw.get("base_url") or ""))
    auth = validate_auth(raw.get("auth"))
    identity = validate_identity(raw.get("identity"))

    raw_tools = raw.get("tools")
    if not isinstance(raw_tools, list) or not raw_tools:
        raise ApiConnectorValidationError(f"connector {name!r}: tools must be a non-empty array")
    tools = [validate_tool(t, connector_name=connector_name) for t in raw_tools]

    tool_names = [t["name"] for t in tools]
    if len(set(tool_names)) != len(tool_names):
        raise ApiConnectorValidationError(f"connector {name!r}: duplicate tool names")

    default_headers = raw.get("default_headers")
    if default_headers is not None:
        if not isinstance(default_headers, dict):
            raise ApiConnectorValidationError(
                f"connector {name!r}: default_headers must be an object"
            )
        default_headers = {str(k): str(v) for k, v in default_headers.items()}
    else:
        default_headers = {}

    timeout = int(raw.get("timeout", _TIMEOUT_DEFAULT))
    if timeout < 1 or timeout > _TIMEOUT_MAX:
        raise ApiConnectorValidationError(
            f"connector {name!r}: timeout must be between 1 and {_TIMEOUT_MAX}"
        )

    max_per_turn = int(raw.get("max_calls_per_turn", 20))
    if max_per_turn < 1 or max_per_turn > 100:
        raise ApiConnectorValidationError(
            f"connector {name!r}: max_calls_per_turn must be between 1 and 100"
        )
    max_per_minute = int(raw.get("max_calls_per_minute", 60))
    if max_per_minute < 1 or max_per_minute > 300:
        raise ApiConnectorValidationError(
            f"connector {name!r}: max_calls_per_minute must be between 1 and 300"
        )

    spec: dict[str, Any] = {
        "display_name": display_name,
        "description": str(raw.get("description") or ""),
        "base_url": base_url,
        "enabled": raw.get("enabled", True) is not False,
        "default_open": raw.get("default_open", True) is not False,
        "shared": raw.get("shared") is True,
        "auth": auth,
        "identity": identity,
        "default_headers": default_headers,
        "timeout": timeout,
        "max_calls_per_turn": max_per_turn,
        "max_calls_per_minute": max_per_minute,
        "tools": tools,
    }
    return spec


def validate_connectors_map(
    connectors: Any,
    *,
    reserved_names: set[str] | None = None,
) -> dict[str, Any]:
    if connectors is None:
        return {}
    if not isinstance(connectors, dict):
        raise ApiConnectorValidationError("connectors must be an object")
    reserved = reserved_names or set()
    out: dict[str, Any] = {}
    for name, raw in connectors.items():
        key = str(name).strip()
        if not key:
            raise ApiConnectorValidationError("connector name must be non-empty")
        if not _CONNECTOR_NAME_RE.match(key):
            raise ApiConnectorValidationError(
                f"invalid connector name {name!r}: use letters, digits, _ or -"
            )
        if key in reserved:
            raise ApiConnectorValidationError(
                f"connector name {key!r} conflicts with a built-in connector"
            )
        if key in out:
            raise ApiConnectorValidationError(f"duplicate connector name {key!r}")
        out[key] = validate_connector_spec(key, raw)
    return out
