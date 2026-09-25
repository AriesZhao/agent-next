"""Shape-aware result rendering + big-result spill handle (§8.5, §7 control 3).

Small / aggregate results are given in full. Large results are compressed into an
ordered sample plus numeric aggregates and an honest truncation marker; when even
the digest would blow the per-source ``context_char_budget`` the full rows are
handed to a ``spill`` callable (which writes them into the agent workspace and
returns a path) and only the digest + path is injected.

The renderer never claims the sample is "the first N rows" unless the query had
an ``ORDER BY`` — an unordered sample is labeled as such (§8.5 honesty).
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

# Max characters for a wide text cell in the sample.
_CELL_CLIP = 40
# Rows shown in the sample digest before spilling.
_SAMPLE_ROWS = 8

SpillFn = Callable[[str], str]  # csv_text -> workspace path

# Default (zh) label templates; the pipeline passes localized ones via ``labels``.
# Keeping templates here means the renderer stays locale-agnostic and testable.
_DEFAULT_LABELS: dict[str, str] = {
    "total_rows": "共 {n} 行",
    "total_rows_truncated": "共 ≥{n} 行",
    "truncated_suffix": "（结果已达上限，可能截断）",
    "aggregates": "[数值聚合] {items}",
    "aggregates_none": "[数值聚合] 无",
    "sample_unordered": "[样例（未排序，仅为样例，非前 N 行）]",
    "sample": "[样例]",
    "spilled": "[结果较大，完整数据已写入文件: {path}]",
}


def _label(labels: dict[str, str], key: str, **fmt: Any) -> str:
    template = labels.get(key) or _DEFAULT_LABELS[key]
    return template.format(**fmt) if fmt else template


@dataclass(frozen=True)
class Rendered:
    text: str
    row_count: int
    spilled: bool = False
    file_path: str = ""
    truncated: bool = False


def _is_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    return isinstance(value, (int, float))


def _clip(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    if len(text) > _CELL_CLIP:
        return text[: _CELL_CLIP - 1] + "…"
    return text


def _numeric_aggregates(columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[str]:
    out: list[str] = []
    for idx, name in enumerate(columns):
        vals = [r[idx] for r in rows if idx < len(r) and _is_number(r[idx])]
        if len(vals) < 2:
            continue
        count = len(vals)
        total = sum(vals)
        out.append(
            f"{name}: count={count} min={min(vals):g} max={max(vals):g} "
            f"sum={total:g} avg={total / count:g}"
        )
    return out


def _render_table(columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    header = " | ".join(str(c) for c in columns)
    lines = [header]
    for r in rows:
        lines.append(" | ".join(_clip(v) for v in r))
    return "\n".join(lines)


def _to_csv(columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(list(columns))
    for r in rows:
        writer.writerow(["" if v is None else v for v in r])
    return buf.getvalue()


def render_result(
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
    *,
    truncated: bool,
    has_order_by: bool,
    budget_chars: int,
    spill: SpillFn | None = None,
    labels: dict[str, str] | None = None,
) -> Rendered:
    """Render a guard-approved result set into an injectable text block.

    ``budget_chars`` bounds the injected digest. ``spill`` (when provided) is a
    callable that persists the full CSV and returns a workspace path. ``labels``
    supplies localized template strings (falling back to zh defaults).
    """
    labels = labels or _DEFAULT_LABELS
    row_count = len(rows)
    full = _render_table(columns, rows)
    if truncated:
        total_hint = _label(labels, "total_rows_truncated", n=row_count + 1)
    else:
        total_hint = _label(labels, "total_rows", n=row_count)
    suffix = _label(labels, "truncated_suffix") if truncated else ""

    small = full
    if row_count <= _SAMPLE_ROWS and len(full) <= budget_chars:
        return Rendered(
            text=f"{small}\n[{total_hint}{suffix}]", row_count=row_count, truncated=truncated
        )

    # Large or wide: sample + numeric aggregates.
    sample = rows[:_SAMPLE_ROWS]
    order_note = _label(labels, "sample") if has_order_by else _label(labels, "sample_unordered")
    aggs = _numeric_aggregates(columns, rows)
    agg_line = (
        _label(labels, "aggregates", items="; ".join(aggs))
        if aggs
        else _label(labels, "aggregates_none")
    )
    digest_lines = [
        _render_table(columns, sample),
        f"[{total_hint}{suffix}]",
        agg_line,
        order_note,
    ]
    digest = "\n".join(part for part in digest_lines if part)

    if len(digest) <= budget_chars:
        return Rendered(text=digest, row_count=row_count, truncated=truncated)

    # Digest itself is over budget → spill full CSV, keep a lean digest + handle.
    if spill is not None:
        path = spill(_to_csv(columns, rows))
        lean = _render_table(columns, rows[:3])
        text = f"{lean}\n{_label(labels, 'spilled', path=path)}\n[{total_hint}{suffix}]"
        return Rendered(
            text=text, row_count=row_count, spilled=True, file_path=path, truncated=truncated
        )

    # No spill available: hard-trim the digest to the budget.
    return Rendered(text=digest[:budget_chars], row_count=row_count, truncated=truncated)
