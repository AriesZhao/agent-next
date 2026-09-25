"""Audit logging for API Connector calls."""

from __future__ import annotations

import json
from typing import Any

from octop.infra.db.repos.audit import AuditRepo


def record_api_call(
    audit_repo: AuditRepo,
    *,
    username: str,
    agent_id: str,
    connector: str,
    tool: str,
    http_method: str,
    http_url: str,
    http_status: int | None = None,
    duration_ms: int | None = None,
    identity_propagated: bool = False,
    truncated: bool = False,
    error: str | None = None,
    sensitive: bool = False,
) -> None:
    payload: dict[str, Any] = {
        "event": "api_connector_call",
        "agent_id": agent_id,
        "connector": connector,
        "tool": tool,
        "http_method": http_method,
        "http_url": http_url,
        "identity_propagated": identity_propagated,
        "truncated": truncated,
        "sensitive": sensitive,
    }
    if http_status is not None:
        payload["http_status"] = http_status
    if duration_ms is not None:
        payload["duration_ms"] = duration_ms
    if error is not None:
        payload["error"] = error

    audit_repo.user_event(
        username=username,
        action="api_connector_call",
        target=f"{connector}:{tool}",
        payload=json.dumps(payload, ensure_ascii=False),
    )
