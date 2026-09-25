"""Tests for API Connector identity propagation."""

from __future__ import annotations

from octop.infra.connectors.api_connector.identity import (
    ActorContext,
    get_actor_context,
    inject_identity_headers,
    reset_actor_context,
    set_actor_context,
)


def test_context_var_set_get_reset():
    assert get_actor_context() is None
    ctx = ActorContext(user_id=1, username="alice", agent_id="a1")
    token = set_actor_context(ctx)
    try:
        assert get_actor_context() == ctx
        assert get_actor_context().user_id == 1
        assert get_actor_context().username == "alice"
    finally:
        reset_actor_context(token)
    assert get_actor_context() is None


def test_inject_identity_headers_enabled():
    headers: dict[str, str] = {}
    config = {
        "enabled": True,
        "header_name": "X-On-Behalf-Of",
        "name_header": "X-On-Behalf-Of-Name",
        "channel_header": "X-Request-Channel",
    }
    inject_identity_headers(headers, config, user_id=42, user_name="bob")
    assert headers["X-On-Behalf-Of"] == "42"
    assert headers["X-On-Behalf-Of-Name"] == "bob"
    assert headers["X-Request-Channel"] == "agent"


def test_inject_identity_headers_disabled():
    headers: dict[str, str] = {}
    config = {"enabled": False}
    inject_identity_headers(headers, config, user_id=1, user_name="a")
    assert headers == {}


def test_inject_identity_headers_custom_names():
    headers: dict[str, str] = {}
    config = {
        "enabled": True,
        "header_name": "X-User-Id",
        "name_header": "X-User-Name",
        "channel_header": "",
    }
    inject_identity_headers(headers, config, user_id=7, user_name="c")
    assert headers["X-User-Id"] == "7"
    assert headers["X-User-Name"] == "c"
    assert "X-Request-Channel" not in headers
