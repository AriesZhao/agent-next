"""i18n data_source.* domain helper + parity tests (§11, §8.6)."""

from __future__ import annotations

from octop.i18n import all_keys_for_locale
from octop.i18n.domains.data_source import (
    error_text,
    generated_sql_note,
    guard_reason_phrase,
    render_labels,
)


def test_en_and_zh_have_data_source_keys():
    en = {k for k in all_keys_for_locale("en") if k.startswith("data_source.")}
    zh = {k for k in all_keys_for_locale("zh") if k.startswith("data_source.")}
    assert en == zh
    assert "data_source.error.blocked" in en
    assert "data_source.render.total_rows" in en


def test_render_labels_keep_placeholders():
    labels = render_labels("zh")
    assert "{n}" in labels["total_rows"]
    assert "{path}" in labels["spilled"]
    assert labels["aggregates_none"]  # non-empty


def test_guard_reason_phrase_splits_code_and_token():
    phrase, token = guard_reason_phrase("table_not_allowed:secret", "en")
    assert token == "secret"
    assert phrase  # localized, not the raw code


def test_error_text_interpolates():
    owner = error_text("invalid_sql_owner", "en", sql="SELECT 1")
    assert "SELECT 1" in owner


def test_generated_sql_note():
    note = generated_sql_note("SELECT id FROM orders", "zh")
    assert "SELECT id FROM orders" in note
