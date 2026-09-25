"""Per-turn data-source selection defaults + catalog (§6, §12 step 12).

Mirrors :mod:`octop.infra.knowledge.default_open`: when a turn omits an explicit
list, the actor's own ``default_open`` sources are auto-injected (shared sources
marked default-open apply only to their owner). The selected ids and a compact
``{id, name, description}`` catalog are stamped onto the harness request so the
``query_data_source`` tool can scope and describe this turn's sources.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from octop.infra.db.repos.data_sources import DataSourceRow


def merge_data_source_ids(
    visible_sources: Sequence[DataSourceRow],
    explicit_ids: list[str] | None,
    *,
    owner_user_id: int,
    extra_ids: Sequence[str] | None = None,
) -> list[str]:
    if explicit_ids is not None:
        return list(explicit_ids)
    selected: list[str] = []
    visible_ids = {src.id for src in visible_sources}
    for src in visible_sources:
        if src.default_open and int(src.owner_user_id) == int(owner_user_id):
            selected.append(src.id)
    for ds_id in extra_ids or []:
        text = str(ds_id).strip()
        if text and text in visible_ids and text not in selected:
            selected.append(text)
    return selected


def catalog_for_selected_data_sources(
    sources: Sequence[DataSourceRow],
    selected_ids: Sequence[str],
) -> list[dict[str, str]]:
    by_id = {str(getattr(src, "id", "")): src for src in sources}
    catalog: list[dict[str, str]] = []
    for ds_id in selected_ids:
        key = str(ds_id).strip()
        if not key:
            continue
        src = by_id.get(key)
        if src is None:
            continue
        raw_name = getattr(src, "name", "")
        name = raw_name.strip() if isinstance(raw_name, str) else ""
        raw_desc = getattr(src, "description", "")
        description = raw_desc.strip() if isinstance(raw_desc, str) else ""
        catalog.append({"id": key, "name": name or key, "description": description})
    return catalog


def stamp_turn_data_source_config(
    request: dict[str, Any],
    *,
    visible_sources: Sequence[DataSourceRow],
    explicit_ids: list[str] | None,
    owner_user_id: int,
    extra_ids: Sequence[str] | None = None,
    is_admin: bool = False,
    locale: str,
) -> list[str]:
    """Write this turn's data-source selection + catalog onto the harness request."""
    selected_ids = merge_data_source_ids(
        visible_sources,
        explicit_ids,
        owner_user_id=owner_user_id,
        extra_ids=extra_ids,
    )
    configurable = dict(request.get("configurable") or {})
    configurable["data_source_ids"] = selected_ids
    configurable["data_source_catalog"] = catalog_for_selected_data_sources(
        visible_sources, selected_ids
    )
    configurable["user_is_admin"] = is_admin
    configurable["locale"] = locale
    request["configurable"] = configurable
    return selected_ids
