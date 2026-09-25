"""ContextVar-based identity propagation for API Connector tool calls."""

from __future__ import annotations

import contextvars
from dataclasses import dataclass
from typing import Any

_actor_context: contextvars.ContextVar[ActorContext | None] = contextvars.ContextVar(
    "api_connector_actor", default=None
)


@dataclass(frozen=True)
class ActorContext:
    user_id: int
    username: str
    agent_id: str


def set_actor_context(ctx: ActorContext | None) -> contextvars.Token[ActorContext | None]:
    return _actor_context.set(ctx)


def get_actor_context() -> ActorContext | None:
    return _actor_context.get()


def reset_actor_context(token: contextvars.Token[ActorContext | None]) -> None:
    _actor_context.reset(token)


def inject_identity_headers(
    headers: dict[str, str],
    identity_config: dict[str, Any],
    *,
    user_id: int | str,
    user_name: str,
) -> None:
    if not identity_config.get("enabled", True):
        return
    header_name = identity_config.get("header_name", "X-On-Behalf-Of")
    if header_name:
        headers[header_name] = str(user_id)
    name_header = identity_config.get("name_header", "X-On-Behalf-Of-Name")
    if name_header:
        headers[name_header] = user_name
    channel_header = identity_config.get("channel_header", "X-Request-Channel")
    if channel_header:
        headers[channel_header] = "agent"
