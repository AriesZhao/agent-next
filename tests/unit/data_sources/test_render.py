"""render.py tests: shape-aware output, honesty, budget → spill (§8.5)."""

from __future__ import annotations

from octop.infra.data_sources.render import render_result

# Neutral English labels so assertions are locale-independent.
_L = {
    "total_rows": "total {n} rows",
    "total_rows_truncated": "total >= {n} rows",
    "truncated_suffix": "(truncated)",
    "aggregates": "AGG {items}",
    "aggregates_none": "AGG none",
    "sample": "[sample]",
    "sample_unordered": "[sample unordered not-first-N]",
    "spilled": "FULL_FILE {path}",
}


def test_small_result_rendered_in_full():
    r = render_result(
        ["id", "name"],
        [[1, "a"], [2, "b"]],
        truncated=False,
        has_order_by=False,
        budget_chars=4000,
        labels=_L,
    )
    assert not r.spilled
    assert "id | name" in r.text
    assert "a" in r.text and "b" in r.text
    assert "total 2 rows" in r.text


def test_truncated_marker_present():
    rows = [[i] for i in range(50)]
    r = render_result(
        ["n"],
        rows,
        truncated=True,
        has_order_by=True,
        budget_chars=100_000,
        labels=_L,
    )
    assert "(truncated)" in r.text
    assert ">= 51" in r.text


def test_large_result_uses_sample_and_aggregates():
    rows = [[i, i * 10] for i in range(50)]
    r = render_result(
        ["id", "amount"],
        rows,
        truncated=False,
        has_order_by=True,
        budget_chars=120,
        labels=_L,
    )
    # Only the first _SAMPLE_ROWS rows shown, plus numeric aggregates line.
    assert "AGG" in r.text
    assert "count=" in r.text
    # 50 rows should NOT all be present in a bounded digest.
    assert r.text.count("\n") < 50


def test_unordered_large_result_is_honest():
    rows = [[i] for i in range(30)]
    r = render_result(
        ["n"],
        rows,
        truncated=False,
        has_order_by=False,
        budget_chars=200,
        labels=_L,
    )
    assert "not-first-N" in r.text


def test_ordered_sample_not_flagged_unordered():
    rows = [[i] for i in range(30)]
    r = render_result(
        ["n"],
        rows,
        truncated=False,
        has_order_by=True,
        budget_chars=200,
        labels=_L,
    )
    assert "not-first-N" not in r.text


def test_over_budget_spills_to_file_handle():
    seen: dict[str, str] = {}

    def spill(csv_text: str) -> str:
        seen["csv"] = csv_text
        return "reports/query_result.csv"

    rows = [[i, f"row-{i}-payload"] for i in range(200)]
    r = render_result(
        ["id", "note"],
        rows,
        truncated=True,
        has_order_by=True,
        budget_chars=40,
        spill=spill,
        labels=_L,
    )
    assert r.spilled
    assert r.file_path == "reports/query_result.csv"
    assert "FULL_FILE reports/query_result.csv" in r.text
    # The injected body stays small even though full CSV was captured.
    assert "row-199" not in r.text
    assert "row-199" in seen["csv"]
    assert seen["csv"].startswith("id,note")


def test_over_budget_without_spill_hard_trims():
    rows = [[i, "x" * 30] for i in range(200)]
    r = render_result(
        ["id", "note"],
        rows,
        truncated=True,
        has_order_by=True,
        budget_chars=50,
        spill=None,
        labels=_L,
    )
    assert not r.spilled
    assert len(r.text) <= 50
