"""Central i18n: JSON locale bundles, lookup, and domain helpers."""

from __future__ import annotations

from octop.i18n.domains.channel import (
    channel_probe_field_label,
    channel_probe_incomplete,
    channel_runtime_reason,
    channel_tool_hint_end,
    channel_tool_hint_start,
)
from octop.i18n.domains.data_source import (
    error_text as data_source_error_text,
)
from octop.i18n.domains.data_source import (
    generated_sql_note as data_source_generated_sql_note,
)
from octop.i18n.domains.data_source import (
    guard_reason_phrase as data_source_guard_reason_phrase,
)
from octop.i18n.domains.data_source import (
    render_labels as data_source_render_labels,
)
from octop.i18n.domains.errors import error_message
from octop.i18n.domains.skills import all_skill_labels, skill_display_name
from octop.i18n.domains.tools import all_tool_labels, hitl_tool_catalog, tool_display_name
from octop.i18n.loader import all_keys_for_locale, flatten_keys, lookup, tr
from octop.infra.utils.locale import (
    DEFAULT_LOCALE,
    SUPPORTED_LOCALES,
    Locale,
    normalize_locale,
    resolve_locale,
)

__all__ = [
    "DEFAULT_LOCALE",
    "Locale",
    "SUPPORTED_LOCALES",
    "all_keys_for_locale",
    "all_skill_labels",
    "all_tool_labels",
    "hitl_tool_catalog",
    "channel_probe_field_label",
    "channel_probe_incomplete",
    "channel_runtime_reason",
    "channel_tool_hint_end",
    "channel_tool_hint_start",
    "data_source_error_text",
    "data_source_generated_sql_note",
    "data_source_guard_reason_phrase",
    "data_source_render_labels",
    "error_message",
    "flatten_keys",
    "lookup",
    "normalize_locale",
    "resolve_locale",
    "skill_display_name",
    "tool_display_name",
    "tr",
]
