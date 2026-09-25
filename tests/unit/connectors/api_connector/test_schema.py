"""Tests for API Connector schema validation."""

from __future__ import annotations

import pytest

from octop.infra.connectors.api_connector.schema import (
    ApiConnectorValidationError,
    validate_auth,
    validate_base_url,
    validate_connector_name,
    validate_connector_spec,
    validate_connectors_map,
    validate_identity,
    validate_tool,
)

# -- connector name --


def test_valid_connector_names():
    assert validate_connector_name("erp") == "erp"
    assert validate_connector_name("my-system") == "my-system"
    assert validate_connector_name("v2_prod") == "v2_prod"


def test_invalid_connector_names():
    for bad in ("", "has space", "a.b", "x/y", "中文"):
        with pytest.raises(ApiConnectorValidationError):
            validate_connector_name(bad)


# -- base_url --


def test_base_url_loopback_http_allowed():
    assert validate_base_url("http://localhost:8080/api") == "http://localhost:8080/api"
    assert validate_base_url("http://127.0.0.1/") == "http://127.0.0.1"


def test_base_url_https_allowed():
    assert validate_base_url("https://api.example.com/v1") == "https://api.example.com/v1"


def test_base_url_rejects_non_https_external():
    with pytest.raises(ApiConnectorValidationError, match="https"):
        validate_base_url("http://api.example.com/v1")


def test_base_url_rejects_empty():
    with pytest.raises(ApiConnectorValidationError):
        validate_base_url("")


def test_base_url_strips_trailing_slash():
    assert validate_base_url("https://api.example.com/v1/") == "https://api.example.com/v1"


# -- auth --


def test_auth_bearer():
    result = validate_auth({"type": "bearer", "token": "tok123"})
    assert result == {"type": "bearer", "token": "tok123"}


def test_auth_bearer_missing_token():
    with pytest.raises(ApiConnectorValidationError, match="token"):
        validate_auth({"type": "bearer"})


def test_auth_api_key():
    result = validate_auth({"type": "api_key", "api_key": "k", "api_key_header": "X-Key"})
    assert result["api_key_header"] == "X-Key"


def test_auth_api_key_default_header():
    result = validate_auth({"type": "api_key", "api_key": "k"})
    assert result["api_key_header"] == "X-API-Key"


def test_auth_basic():
    result = validate_auth({"type": "basic", "username": "u", "password": "p"})
    assert result == {"type": "basic", "username": "u", "password": "p"}


def test_auth_basic_missing_password():
    with pytest.raises(ApiConnectorValidationError, match="password"):
        validate_auth({"type": "basic", "username": "u"})


def test_auth_unknown_type():
    with pytest.raises(ApiConnectorValidationError, match="auth.type"):
        validate_auth({"type": "oauth2"})


# -- identity --


def test_identity_defaults():
    result = validate_identity(None)
    assert result["enabled"] is True
    assert result["header_name"] == "X-On-Behalf-Of"


def test_identity_custom():
    result = validate_identity({"header_name": "X-User", "enabled": False})
    assert result["enabled"] is False
    assert result["header_name"] == "X-User"


# -- tool --


def _minimal_tool(**overrides):
    base = {
        "name": "get_order",
        "method": "GET",
        "path": "/orders/{id}",
        "parameters": {
            "id": {"type": "string", "in": "path", "required": True},
        },
    }
    base.update(overrides)
    return base


def test_tool_valid():
    result = validate_tool(_minimal_tool(), connector_name="erp")
    assert result["name"] == "get_order"
    assert result["method"] == "GET"
    assert "id" in result["parameters"]


def test_tool_missing_path():
    with pytest.raises(ApiConnectorValidationError, match="path"):
        validate_tool({"name": "x", "method": "GET"}, connector_name="erp")


def test_tool_path_not_starting_with_slash():
    with pytest.raises(ApiConnectorValidationError, match="/"):
        validate_tool({"name": "x", "method": "GET", "path": "orders"}, connector_name="erp")


def test_tool_path_placeholder_without_param():
    with pytest.raises(ApiConnectorValidationError, match="no parameter definition"):
        validate_tool(
            {"name": "x", "method": "GET", "path": "/orders/{id}"},
            connector_name="erp",
        )


def test_tool_path_param_not_in_template():
    with pytest.raises(ApiConnectorValidationError, match="not in path"):
        validate_tool(
            {
                "name": "x",
                "method": "GET",
                "path": "/orders",
                "parameters": {"id": {"type": "string", "in": "path"}},
            },
            connector_name="erp",
        )


def test_tool_invalid_method():
    with pytest.raises(ApiConnectorValidationError, match="method"):
        validate_tool(_minimal_tool(method="INVALID"), connector_name="erp")


def test_tool_sensitive_flag():
    result = validate_tool(_minimal_tool(sensitive=True), connector_name="erp")
    assert result["sensitive"] is True


def test_tool_response_mapping_defaults():
    result = validate_tool(_minimal_tool(), connector_name="erp")
    assert result["response_mapping"]["truncate_at"] == 8000
    assert result["response_mapping"]["data_path"] is None


# -- connector spec --


def _minimal_connector(**overrides):
    base = {
        "base_url": "http://localhost:8080",
        "auth": {"type": "bearer", "token": "tok"},
        "tools": [_minimal_tool()],
    }
    base.update(overrides)
    return base


def test_connector_spec_valid():
    result = validate_connector_spec("erp", _minimal_connector())
    assert result["display_name"] == "erp"
    assert result["timeout"] == 30
    assert result["max_calls_per_turn"] == 20
    assert len(result["tools"]) == 1


def test_connector_spec_display_name_override():
    result = validate_connector_spec("erp", _minimal_connector(display_name="ERP 系统"))
    assert result["display_name"] == "ERP 系统"


def test_connector_spec_display_name_too_long():
    with pytest.raises(ApiConnectorValidationError, match="display_name"):
        validate_connector_spec("erp", _minimal_connector(display_name="x" * 65))


def test_connector_spec_timeout_out_of_range():
    with pytest.raises(ApiConnectorValidationError, match="timeout"):
        validate_connector_spec("erp", _minimal_connector(timeout=200))


def test_connector_spec_empty_tools():
    with pytest.raises(ApiConnectorValidationError, match="non-empty"):
        validate_connector_spec("erp", _minimal_connector(tools=[]))


def test_connector_spec_duplicate_tool_names():
    with pytest.raises(ApiConnectorValidationError, match="duplicate"):
        validate_connector_spec(
            "erp",
            _minimal_connector(tools=[_minimal_tool(), _minimal_tool()]),
        )


# -- connectors map --


def test_connectors_map_valid():
    result = validate_connectors_map({"erp": _minimal_connector()})
    assert "erp" in result


def test_connectors_map_none():
    assert validate_connectors_map(None) == {}


def test_connectors_map_reserved_name():
    with pytest.raises(ApiConnectorValidationError, match="conflicts"):
        validate_connectors_map(
            {"erp": _minimal_connector()},
            reserved_names={"erp"},
        )


def test_connectors_map_invalid_name():
    with pytest.raises(ApiConnectorValidationError, match="invalid"):
        validate_connectors_map({"bad name": _minimal_connector()})
