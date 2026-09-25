"""HTTP request construction and execution for API Connector."""

from __future__ import annotations

import base64
import re
from typing import Any

import httpx


def build_auth_headers(auth: dict[str, Any]) -> dict[str, str]:
    auth_type = auth.get("type", "bearer")
    if auth_type == "bearer":
        return {"Authorization": f"Bearer {auth['token']}"}
    if auth_type == "api_key":
        header = auth.get("api_key_header", "X-API-Key")
        return {header: auth["api_key"]}
    if auth_type == "basic":
        raw = f"{auth['username']}:{auth['password']}".encode()
        return {"Authorization": f"Basic {base64.b64encode(raw).decode('ascii')}"}
    return {}


def _resolve_path(path_template: str, params: dict[str, Any]) -> str:
    def _replace(m: re.Match[str]) -> str:
        key = m.group(1)
        if key not in params:
            raise ValueError(f"missing path parameter: {key}")
        return str(params[key])

    return re.sub(r"\{(\w+)\}", _replace, path_template)


def build_request(
    *,
    base_url: str,
    tool_def: dict[str, Any],
    args: dict[str, Any],
    auth: dict[str, Any],
    default_headers: dict[str, str],
    identity_headers: dict[str, str],
) -> httpx.Request:
    method = tool_def["method"]
    path_template = tool_def["path"]
    param_defs = tool_def.get("parameters", {})

    resolved_args = _resolve_defaults(param_defs, args)

    path_params = {
        k: v for k, v in resolved_args.items() if param_defs.get(k, {}).get("in") == "path"
    }
    query_params: dict[str, Any] = {}
    extra_headers: dict[str, str] = {}
    body_fields: dict[str, Any] = {}

    for key, value in resolved_args.items():
        pdef = param_defs.get(key, {})
        location = pdef.get("in", "query")
        if location == "path":
            continue
        if location == "query":
            query_params[key] = value
        elif location == "header":
            extra_headers[key] = str(value)
        elif location == "body":
            body_fields[key] = value

    url = base_url.rstrip("/") + _resolve_path(path_template, path_params)

    headers: dict[str, str] = {}
    headers.update(default_headers)
    headers.update(build_auth_headers(auth))
    headers.update(identity_headers)
    headers.update(extra_headers)

    content: str | None = None
    if body_fields and method in ("POST", "PUT", "PATCH"):
        import json

        content = json.dumps(body_fields, ensure_ascii=False)
        headers.setdefault("Content-Type", "application/json")

    return httpx.Request(
        method=method,
        url=url,
        params=query_params or None,
        headers=headers,
        content=content,
    )


def _resolve_defaults(param_defs: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    for key, pdef in param_defs.items():
        if key in args and args[key] is not None:
            resolved[key] = args[key]
        elif "default" in pdef and pdef["default"] is not None:
            resolved[key] = pdef["default"]
        elif pdef.get("required") and pdef.get("in") == "path":
            raise ValueError(f"missing required path parameter: {key}")
    for key, value in args.items():
        if key not in param_defs and value is not None:
            resolved[key] = value
    return resolved


def execute_request(
    client: httpx.Client,
    request: httpx.Request,
    *,
    timeout: int = 30,
) -> httpx.Response:
    return client.send(request, follow_redirects=True)


def extract_response(
    response: httpx.Response,
    *,
    data_path: str | None = None,
    truncate_at: int = 8000,
) -> tuple[str, bool]:
    body = response.text
    truncated = False

    if data_path and response.headers.get("content-type", "").startswith("application/json"):
        try:
            data = response.json()
            for segment in data_path.split("."):
                if isinstance(data, dict):
                    data = data.get(segment)
                else:
                    data = None
                    break
            if data is not None:
                import json

                body = json.dumps(data, ensure_ascii=False, indent=2)
        except Exception:
            pass

    if len(body) > truncate_at:
        body = body[:truncate_at] + f"...（已截断，共 {len(response.text)} 字符）"
        truncated = True

    return body, truncated
