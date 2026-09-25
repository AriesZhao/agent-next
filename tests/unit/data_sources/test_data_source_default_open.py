"""Unit tests for data-source turn selection defaults + catalog (§12 step 12)."""

from __future__ import annotations

from types import SimpleNamespace

from octop.infra.data_sources.default_open import (
    catalog_for_selected_data_sources,
    merge_data_source_ids,
    stamp_turn_data_source_config,
)


def _src(
    i: str,
    *,
    owner: int = 1,
    default_open: bool = False,
    shared: bool = False,
    name: str = "",
    desc: str = "",
):
    return SimpleNamespace(
        id=i,
        owner_user_id=owner,
        default_open=default_open,
        shared=shared,
        name=name,
        description=desc,
    )


def test_merge_applies_owner_defaults_when_omitted() -> None:
    visible = [_src("default", default_open=True), _src("optional")]
    assert merge_data_source_ids(visible, None, owner_user_id=1) == ["default"]
    assert merge_data_source_ids(visible, [], owner_user_id=1) == []
    assert merge_data_source_ids(visible, ["optional", "unknown"], owner_user_id=1) == [
        "optional",
        "unknown",
    ]


def test_merge_default_open_only_for_owner() -> None:
    visible = [
        _src("mine", owner=1, default_open=True),
        _src("shared-default", owner=2, default_open=True, shared=True),
    ]
    assert merge_data_source_ids(visible, None, owner_user_id=1) == ["mine"]
    assert merge_data_source_ids(visible, None, owner_user_id=2) == ["shared-default"]


def test_merge_unions_visible_extra_ids() -> None:
    visible = [_src("default", default_open=True), _src("expert-pick")]
    assert merge_data_source_ids(
        visible, None, owner_user_id=1, extra_ids=["expert-pick", "gone", ""]
    ) == ["default", "expert-pick"]


def test_catalog_skips_unknown() -> None:
    visible = [_src("a", name="Alpha", desc="sales")]
    assert catalog_for_selected_data_sources(visible, ["a", "missing"]) == [
        {"id": "a", "name": "Alpha", "description": "sales"}
    ]


def test_stamp_writes_ids_catalog_admin_locale() -> None:
    visible = [_src("pick", name="Policies", desc="refund")]
    request: dict = {}
    selected = stamp_turn_data_source_config(
        request,
        visible_sources=visible,
        explicit_ids=None,
        owner_user_id=1,
        extra_ids=["pick", "gone"],
        is_admin=True,
        locale="zh",
    )
    assert selected == ["pick"]
    cfg = request["configurable"]
    assert cfg["data_source_ids"] == ["pick"]
    assert cfg["data_source_catalog"] == [
        {"id": "pick", "name": "Policies", "description": "refund"}
    ]
    assert cfg["user_is_admin"] is True
    assert cfg["locale"] == "zh"
