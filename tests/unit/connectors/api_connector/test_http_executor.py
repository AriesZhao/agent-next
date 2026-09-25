"""Tests for API Connector HTTP executor."""

from __future__ import annotations

import json

import httpx

from octop.infra.connectors.api_connector.http_executor import (
    build_auth_headers,
    build_request,
    extract_response,
)

# -- build_auth_headers --


def test_auth_bearer():
    h = build_auth_headers({"type": "bearer", "token": "abc"})
    assert h == {"Authorization": "Bearer abc"}


def test_auth_api_key():
    h = build_auth_headers({"type": "api_key", "api_key": "k1", "api_key_header": "X-Key"})
    assert h == {"X-Key": "k1"}


def test_auth_basic():
    h = build_auth_headers({"type": "basic", "username": "u", "password": "p"})
    assert h["Authorization"].startswith("Basic ")
    import base64

    decoded = base64.b64decode(h["Authorization"].removeprefix("Basic ")).decode()
    assert decoded == "u:p"


# -- build_request --


def _tool_def(**overrides):
    base = {
        "name": "get_order",
        "method": "GET",
        "path": "/orders/{id}",
        "parameters": {
            "id": {"type": "string", "in": "path", "required": True, "default": None},
            "verbose": {"type": "boolean", "in": "query", "required": False, "default": None},
        },
    }
    base.update(overrides)
    return base


def test_build_request_path_and_query():
    req = build_request(
        base_url="http://localhost:8080",
        tool_def=_tool_def(),
        args={"id": "123", "verbose": True},
        auth={"type": "bearer", "token": "t"},
        default_headers={},
        identity_headers={},
    )
    assert str(req.url) == "http://localhost:8080/orders/123?verbose=true"
    assert req.headers["Authorization"] == "Bearer t"


def test_build_request_body_for_post():
    tool = {
        "name": "create_order",
        "method": "POST",
        "path": "/orders",
        "parameters": {
            "product": {"type": "string", "in": "body", "required": True, "default": None},
            "qty": {"type": "integer", "in": "body", "required": True, "default": None},
        },
    }
    req = build_request(
        base_url="http://localhost:8080",
        tool_def=tool,
        args={"product": "widget", "qty": 5},
        auth={"type": "bearer", "token": "t"},
        default_headers={},
        identity_headers={},
    )
    assert req.method == "POST"
    body = json.loads(req.content)
    assert body == {"product": "widget", "qty": 5}
    assert req.headers["Content-Type"] == "application/json"


def test_build_request_header_params():
    tool = {
        "name": "t",
        "method": "GET",
        "path": "/x",
        "parameters": {
            "X-Custom": {"type": "string", "in": "header", "required": False, "default": None},
        },
    }
    req = build_request(
        base_url="http://localhost:8080",
        tool_def=tool,
        args={"X-Custom": "val"},
        auth={"type": "bearer", "token": "t"},
        default_headers={},
        identity_headers={},
    )
    assert req.headers["X-Custom"] == "val"


def test_build_request_default_headers_and_identity():
    req = build_request(
        base_url="http://localhost:8080",
        tool_def=_tool_def(),
        args={"id": "1"},
        auth={"type": "bearer", "token": "t"},
        default_headers={"Accept": "application/json"},
        identity_headers={"X-On-Behalf-Of": "42"},
    )
    assert req.headers["Accept"] == "application/json"
    assert req.headers["X-On-Behalf-Of"] == "42"


def test_build_request_default_value_fills():
    tool = {
        "name": "list",
        "method": "GET",
        "path": "/items",
        "parameters": {
            "page": {"type": "integer", "in": "query", "required": False, "default": 1},
        },
    }
    req = build_request(
        base_url="http://localhost:8080",
        tool_def=tool,
        args={},
        auth={"type": "bearer", "token": "t"},
        default_headers={},
        identity_headers={},
    )
    assert "page=1" in str(req.url)


# -- extract_response --


def test_extract_response_plain():
    resp = httpx.Response(
        200,
        text="hello",
        headers={"content-type": "text/plain"},
        request=httpx.Request("GET", "http://x"),
    )
    body, truncated = extract_response(resp)
    assert body == "hello"
    assert truncated is False


def test_extract_response_json_data_path():
    data = {"result": {"orders": [1, 2, 3]}, "meta": {}}
    resp = httpx.Response(
        200,
        json=data,
        headers={"content-type": "application/json"},
        request=httpx.Request("GET", "http://x"),
    )
    body, truncated = extract_response(resp, data_path="result.orders")
    parsed = json.loads(body)
    assert parsed == [1, 2, 3]


def test_extract_response_truncation():
    big = "x" * 200
    resp = httpx.Response(
        200,
        text=big,
        headers={"content-type": "text/plain"},
        request=httpx.Request("GET", "http://x"),
    )
    body, truncated = extract_response(resp, truncate_at=50)
    assert truncated is True
    assert len(body) < 200
    assert "已截断" in body
