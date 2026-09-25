"""pipeline.py tests: layered retry, classification, budget, statuses (§8.4, §13)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from octop.infra.data_sources.connection import (
    ConnectionConfig,
    ConnectionFailure,
    ExecutionFailure,
    QueryResult,
)
from octop.infra.data_sources.pipeline import run_pipeline
from octop.infra.data_sources.schema import AllowedTable

_CFG = ConnectionConfig("postgres", "h", 5432, "db", "public", "u", "p")
_ALLOW = [AllowedTable("orders", ["id", "amount"])]


@dataclass
class RecordingGenerate:
    replies: list[str]
    calls: list[dict] = field(default_factory=list)

    def __call__(self, question, **kw):
        self.calls.append(kw)
        return self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]


@dataclass
class ScriptedExecute:
    # each entry: "rows" | ("raise", exception)
    actions: list[Any]
    calls: list[dict] = field(default_factory=list)

    def __call__(self, cfg, sql, **kw):
        self.calls.append({"sql": sql, **kw})
        action = self.actions[min(len(self.calls) - 1, len(self.actions) - 1)]
        if isinstance(action, BaseException):
            raise action
        if isinstance(action, tuple) and action[0] == "raise":
            raise action[1]
        return QueryResult(columns=["id", "amount"], rows=action, truncated=False)


class FakeClock:
    def __init__(self, step_ms=0):
        self.t = 0.0
        self.step_ms = step_ms

    def __call__(self):
        cur = self.t
        self.t += self.step_ms / 1000.0
        return cur


def _run(generate, execute, *, chat_model="m", is_owner=True, clock=None, audit=None, **over):
    kwargs = {
        "question": "total amount",
        "connection_cfg": _CFG,
        "engine": "postgres",
        "allowlist": _ALLOW,
        "schema_text": "TABLE orders\n  id int\n  amount numeric",
        "max_rows": 100,
        "timeout_ms": 5000,
        "context_char_budget": 4000,
        "locale": "en",
        "is_owner": is_owner,
        "chat_model": chat_model,
        "generate_fn": generate,
        "execute_fn": execute,
        "clock": clock or FakeClock(),
    }
    if audit is not None:
        kwargs["audit"] = audit
    kwargs.update(over)
    return run_pipeline(**kwargs)


def test_model_not_configured_fail_fast():
    collected: list = []
    out = _run(
        RecordingGenerate(["SELECT id FROM orders"]),
        ScriptedExecute([[[1, 2]]]),
        chat_model=None,
        audit=collected.append,
    )
    assert out.status == "error"
    assert out.category == "model_not_configured"
    assert out.text  # localized
    assert collected and collected[0].status == "error"


def test_happy_path_ok_and_audited():
    collected: list = []
    gen = RecordingGenerate(["SELECT id, amount FROM orders"])
    ex = ScriptedExecute([[[1, 10], [2, 20]]])
    out = _run(gen, ex, audit=collected.append)
    assert out.status == "ok"
    assert out.attempts == 1
    assert "id | amount" in out.text
    assert out.executed_sql.upper().startswith("SELECT")
    assert out.row_count == 2
    assert collected and collected[0].status == "ok"


def test_empty_result_not_retried():
    gen = RecordingGenerate(["SELECT id FROM orders"])
    ex = ScriptedExecute([[]])
    out = _run(gen, ex)
    assert out.status == "empty"
    assert out.category == "empty"
    assert len(gen.calls) == 1  # no retry


def test_blocked_guard_not_retried_owner_sees_detail():
    gen = RecordingGenerate(["SELECT amount FROM secret_table"])
    ex = ScriptedExecute([[[1, 1]]])
    out = _run(gen, ex, is_owner=True)
    assert out.status == "blocked"
    assert len(gen.calls) == 1
    assert "secret_table" in out.text  # owner sees which table tripped the guard


def test_blocked_non_owner_does_not_leak_name():
    gen = RecordingGenerate(["SELECT amount FROM secret_table"])
    ex = ScriptedExecute([[[1, 1]]])
    out = _run(gen, ex, is_owner=False)
    assert out.status == "blocked"
    assert "secret_table" not in out.text


def test_illegal_sql_retried_once_then_ok():
    gen = RecordingGenerate(["SELECT id FROM orders", "SELECT id FROM orders"])
    ex = ScriptedExecute([ExecutionFailure('column "foo" does not exist'), [[1, 2]]])
    out = _run(gen, ex)
    assert out.status == "ok"
    assert out.attempts == 2
    assert len(gen.calls) == 2
    # second generate got a corrective hint
    assert gen.calls[1].get("previous_error")


def test_illegal_sql_two_attempts_both_fail():
    gen = RecordingGenerate(["SELECT id FROM orders", "SELECT id FROM orders"])
    ex = ScriptedExecute(
        [ExecutionFailure("syntax error at end of input"), ExecutionFailure("syntax error")]
    )
    out = _run(gen, ex, is_owner=True)
    assert out.status == "error"
    assert out.category == "invalid_sql"
    assert out.attempts == 2
    assert len(gen.calls) == 2


def test_permission_denied_not_retried():
    gen = RecordingGenerate(["SELECT id FROM orders"])
    ex = ScriptedExecute([ExecutionFailure("permission denied for table orders")])
    out = _run(gen, ex)
    assert out.status == "error"
    assert out.category == "connection"
    assert out.attempts == 1
    assert len(gen.calls) == 1


def test_missing_table_not_retried():
    gen = RecordingGenerate(["SELECT id FROM orders"])
    ex = ScriptedExecute([ExecutionFailure('relation "orders" does not exist')])
    out = _run(gen, ex)
    assert out.category == "connection"
    assert out.attempts == 1


def test_connect_failure_not_retried():
    gen = RecordingGenerate(["SELECT id FROM orders"])
    ex = ScriptedExecute([ConnectionFailure("could not connect to server")])
    out = _run(gen, ex)
    assert out.status == "error"
    assert out.category == "connection"
    assert out.attempts == 1


def test_shared_timeout_budget_shrinks_across_attempts():
    gen = RecordingGenerate(["SELECT id FROM orders", "SELECT id FROM orders"])
    ex = ScriptedExecute([ExecutionFailure("column x does not exist"), [[1, 2]]])
    clock = FakeClock(step_ms=1000)  # +1s each call
    out = _run(gen, ex, clock=clock, timeout_ms=5000)
    assert out.status == "ok"
    first_timeout = ex.calls[0]["timeout_ms"]
    second_timeout = ex.calls[1]["timeout_ms"]
    assert second_timeout < first_timeout


def test_large_result_spill_path_recorded():
    gen = RecordingGenerate(["SELECT id, amount FROM orders"])
    big = [[i, i * 1.5] for i in range(200)]
    ex = ScriptedExecute([big])

    def spill(csv_text: str) -> str:
        return "out/big.csv"

    out = _run(gen, ex, context_char_budget=40, spill=spill)
    assert out.status == "ok"
    assert out.spilled
    assert out.file_path == "out/big.csv"
