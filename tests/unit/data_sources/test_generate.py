"""generate.py tests: extraction, prompt assembly, fail-fast model (§8.1)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from octop.infra.data_sources.generate import (
    GenerationFailed,
    ModelNotConfigured,
    build_chat_model_for_ref,
    build_prompt,
    extract_sql,
    generate_sql,
    is_ref_configured,
    resolve_model_ref,
)


class EchoModel:
    def __init__(self, reply):
        self._reply = reply
        self.calls = []

    def invoke(self, prompt):
        self.calls.append(prompt)
        return self._reply


def test_extract_sql_from_fenced_block():
    text = "Here you go:\n```sql\nSELECT id FROM orders LIMIT 10;\n```\nDone."
    assert extract_sql(text) == "SELECT id FROM orders LIMIT 10"


def test_extract_sql_from_bare_sql_label():
    text = "SQL: SELECT COUNT(*) FROM orders"
    assert extract_sql(text) == "SELECT COUNT(*) FROM orders"


def test_extract_sql_stops_at_semicolon_and_prose():
    text = "SELECT id FROM orders;\n\nThis query returns the ids."
    assert extract_sql(text) == "SELECT id FROM orders"


def test_extract_sql_empty():
    assert extract_sql("   ") == ""


def test_build_prompt_includes_schema_dialect_question():
    p = build_prompt("how many users", schema_text="TABLE users\n  id int", dialect="mysql")
    assert "MySQL" in p
    assert "TABLE users" in p
    assert "how many users" in p


def test_build_prompt_previous_error_hint():
    p = build_prompt("q", schema_text="s", dialect="postgres", previous_error="bad column")
    assert "bad column" in p


def test_generate_sql_plain_string_reply():
    m = EchoModel("SELECT id FROM orders LIMIT 10")
    sql = generate_sql("q", schema_text="s", dialect="postgres", chat_model=m)
    assert sql == "SELECT id FROM orders LIMIT 10"
    assert m.calls  # invoked once with prompt string


def test_generate_sql_message_object_reply():
    m = EchoModel(SimpleNamespace(content="```sql\nSELECT 1 FROM orders\n```"))
    sql = generate_sql("q", schema_text="s", dialect="postgres", chat_model=m)
    assert sql == "SELECT 1 FROM orders"


def test_generate_sql_raises_when_no_sql():
    m = EchoModel("I cannot answer that.")
    with pytest.raises(GenerationFailed):
        generate_sql("q", schema_text="s", dialect="postgres", chat_model=m)


def test_resolve_model_ref_reads_setting():
    assert resolve_model_ref(lambda k: "openai/gpt-4o-mini") == "openai/gpt-4o-mini"
    assert resolve_model_ref(lambda k: None) == ""


def test_is_ref_configured():
    assert is_ref_configured("openai/gpt-4o")
    assert not is_ref_configured("openai")
    assert not is_ref_configured("")


def test_build_chat_model_unknown_provider():
    repo = SimpleNamespace(get_by_name=lambda n: None)
    with pytest.raises(ModelNotConfigured):
        build_chat_model_for_ref(repo, "missing/model")


def test_build_chat_model_unknown_model():
    row = SimpleNamespace(get_models=lambda: [{"id": "other"}])
    repo = SimpleNamespace(get_by_name=lambda n: row)
    with pytest.raises(ModelNotConfigured):
        build_chat_model_for_ref(repo, "prov/wrong-model")


def test_build_chat_model_success(monkeypatch):
    row = SimpleNamespace(name="prov", kind="openai", get_models=lambda: [{"id": "m1"}])
    repo = SimpleNamespace(get_by_name=lambda n: row)
    sentinel = object()
    monkeypatch.setattr(
        "octop.infra.agents.providers.probe.build_probe_chat_model",
        lambda r, model_id=None: sentinel,
        raising=True,
    )
    assert build_chat_model_for_ref(repo, "prov/m1") is sentinel


def test_build_chat_model_empty_ref_fail_fast():
    repo = SimpleNamespace(get_by_name=lambda n: None)
    with pytest.raises(ModelNotConfigured):
        build_chat_model_for_ref(repo, "")
