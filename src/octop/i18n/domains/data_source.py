"""``data_source.*`` — NL2SQL data-source copy (errors, guard reasons, render).

Keeps all user-facing strings for the data-source pipeline in the backend i18n
bundles (design §8.6, §11). The pipeline calls these with the resolved locale so
no English/Chinese literals are hard-coded in ``infra/data_sources``.
"""

from __future__ import annotations

from octop.i18n.loader import lookup, tr
from octop.infra.utils.locale import Locale

__all__ = [
    "error_text",
    "generated_sql_note",
    "guard_reason_phrase",
    "no_selection_text",
    "not_found_text",
    "render_labels",
]

_RENDER_KEYS = (
    "total_rows",
    "total_rows_truncated",
    "truncated_suffix",
    "aggregates",
    "aggregates_none",
    "sample",
    "sample_unordered",
    "spilled",
)


def render_labels(locale: str | Locale = "en") -> dict[str, str]:
    """Template dict for :func:`octop.infra.data_sources.render.render_result`.

    Values keep their ``{n}``/``{items}``/``{path}`` placeholders — the renderer
    formats them.
    """
    out: dict[str, str] = {}
    for key in _RENDER_KEYS:
        text = lookup(f"data_source.render.{key}", locale)
        if text is not None:
            out[key] = text
    return out


def error_text(category: str, locale: str | Locale = "en", **fmt: object) -> str:
    """Localized outcome message for an error category (§8.6)."""
    return tr(f"data_source.error.{category}", locale, **fmt)


def guard_reason_phrase(reason: str, locale: str | Locale = "en") -> tuple[str, str]:
    """Split a guard machine reason into ``(localized_phrase, detail_token)``.

    ``reason`` looks like ``table_not_allowed:secret``. The phrase is safe to show;
    ``detail_token`` (the offending table/column name) must only be surfaced to the
    owner — non-owners get the generic blocked message (§8.6, §15.6).
    """
    code, _, extra = reason.partition(":")
    label = lookup(f"data_source.guard.{code}", locale) or code
    return label, extra


def generated_sql_note(sql: str, locale: str | Locale = "en") -> str:
    return tr("data_source.query.generated_sql", locale, sql=sql)


def no_selection_text(locale: str | Locale = "en") -> str:
    return tr("data_source.query.no_selection", locale)


def not_found_text(locale: str | Locale = "en") -> str:
    return tr("data_source.query.not_found", locale)
