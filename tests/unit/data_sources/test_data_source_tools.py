"""Unit tests for the query_data_source tool + execution assembly (§12 step 12)."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from langgraph.config import var_child_runnable_config

from octop.infra.data_sources import tools as tools_module
from octop.infra.data_sources.connection import ConnectionConfig
from octop.infra.data_sources.credentials import encrypt_password
from octop.infra.data_sources.schema import AllowedTable
from octop.infra.data_sources.tools import (
    QUERY_DATA_SOURCE_TOOL,
    _select_id,
    build_data_source_tools,
    execute_data_source_query,
)
from octop.infra.data_sources.types import PipelineOutcome
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.data_sources import DataSourceRepo
from octop.infra.db.repos.secrets import SecretRepo
from octop.infra.db.repos.settings import SettingsRepo
from octop.infra.db.repos.users import UserRepo


@contextmanager
def _configurable(**kwargs: object):
    token = var_child_runnable_config.set({"configurable": kwargs})
    try:
        yield
    finally:
        var_child_runnable_config.reset(token)


def _tool(tools: list, name: str = QUERY_DATA_SOURCE_TOOL):
    for t in tools:
        if t.name == name:
            return t
    raise KeyError(name)


@pytest.fixture
def services(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path / "home"))
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    return SimpleNamespace(
        data_source_repo=DataSourceRepo(pool),
        secret_repo=SecretRepo(pool),
        settings_repo=SettingsRepo(pool),
        user_repo=UserRepo(pool),
        provider_repo=None,
    )


# --- _select_id ---------------------------------------------------------


def test_select_single_auto() -> None:
    assert _select_id(["ds1"], None) == ("ds1", True)


def test_select_explicit_must_be_mounted() -> None:
    assert _select_id(["ds1", "ds2"], "ds2") == ("ds2", True)
    assert _select_id(["ds1", "ds2"], "nope") == (None, False)


def test_select_ambiguous_needs_id() -> None:
    assert _select_id(["ds1", "ds2"], None) == (None, False)


def test_select_none_attached() -> None:
    assert _select_id([], None) == (None, False)


# --- tool build ---------------------------------------------------------


def test_default_description_is_empty_catalog() -> None:
    tool = _tool(build_data_source_tools(SimpleNamespace()))
    assert "No data sources are attached this turn." in tool.description


@pytest.mark.asyncio
async def test_tool_no_selection_localized() -> None:
    tool = _tool(build_data_source_tools(SimpleNamespace()))
    with _configurable(user="1", data_source_ids=[], locale="zh"):
        out = await tool.ainvoke({"question": "total sales"})
    assert out  # localized no-selection text


@pytest.mark.asyncio
async def test_tool_dispatches_to_executor_query() -> None:
    captured: dict = {}

    def fake_exec(services, **kw):
        captured.update(kw)
        return PipelineOutcome(status="ok", text="id | amount\n1 | 5")

    tool = _tool(build_data_source_tools(SimpleNamespace()))
    with (
        _configurable(
            user="7",
            user_is_admin=False,
            data_source_ids=["ds-a"],
            locale="en",
            agent_id="ag1",
            thread_id="th1",
        ),
        patch.object(tools_module, "execute_data_source_query", fake_exec),
    ):
        out = await tool.ainvoke({"question": "top orders", "data_source_id": "ds-a"})
    assert out == "id | amount\n1 | 5"
    assert captured["data_source_id"] == "ds-a"
    assert captured["actor_user_id"] == 7
    assert captured["agent_id"] == "ag1"
    assert captured["thread_id"] == "th1"


@pytest.mark.asyncio
async def test_tool_invalid_id_not_found() -> None:
    tool = _tool(build_data_source_tools(SimpleNamespace()))
    with _configurable(user="1", data_source_ids=["ds-a"], locale="en"):
        out = await tool.ainvoke({"question": "x", "data_source_id": "other"})
    assert out  # localized not-found text


# --- execute_data_source_query assembly (real repos, fake pipeline) -----


def test_execute_assembles_and_runs(services: SimpleNamespace) -> None:
    owner = services.user_repo.create(username="o", password_hash="h", role="user")
    repo = services.data_source_repo
    row = repo.create(owner_user_id=owner, name="db", engine="postgres")
    repo.upsert_connection(
        row.id,
        host="h",
        port=5432,
        database_name="d",
        schema_name="public",
        username="u",
        password_blob=encrypt_password(services.secret_repo, "pw"),
    )
    repo.replace_allowlist(row.id, [AllowedTable("orders", ["id"])])
    repo.upsert_schema(row.id, '[{"name":"orders","columns":[{"name":"id","data_type":"int"}]}]')

    seen: dict = {}

    def fake_pipeline(question, **kw):
        seen["question"] = question
        seen.update(kw)
        return PipelineOutcome(status="ok", text="digest", row_count=1)

    out = execute_data_source_query(
        services,
        data_source_id=row.id,
        question="count orders",
        actor_user_id=owner,
        is_admin=False,
        locale="en",
        pipeline_run=fake_pipeline,
    )
    assert out.status == "ok"
    assert seen["question"] == "count orders"
    assert seen["engine"] == "postgres"
    assert seen["max_rows"] == row.max_rows
    assert isinstance(seen["connection_cfg"], ConnectionConfig)
    assert seen["is_owner"] is True
    assert "orders" in seen["schema_text"]
    assert callable(seen["audit"])  # bound writer
    # audit writer persists an ok row
    seen["audit"](PipelineOutcome(status="ok", text="x", row_count=3, natural_query="q"))
    audits = repo.list_audit(row.id)
    assert audits and audits[0].status == "ok" and audits[0].row_count == 3


def test_execute_chat_model_none_when_not_configured(services: SimpleNamespace) -> None:
    owner = services.user_repo.create(username="o", password_hash="h", role="user")
    repo = services.data_source_repo
    row = repo.create(owner_user_id=owner, name="db", engine="postgres")
    repo.upsert_connection(
        row.id,
        host="h",
        port=5432,
        database_name="d",
        schema_name="",
        username="u",
        password_blob=None,
    )

    seen: dict = {}

    def fake_pipeline(question, **kw):
        seen["chat_model"] = kw.get("chat_model")
        return PipelineOutcome(status="error", text="no model", category="model_not_configured")

    out = execute_data_source_query(
        services,
        data_source_id=row.id,
        question="q",
        actor_user_id=owner,
        pipeline_run=fake_pipeline,
    )
    assert out.category == "model_not_configured"
    assert seen["chat_model"] is None


def test_execute_spill_writes_to_workspace(services: SimpleNamespace) -> None:
    owner = services.user_repo.create(username="o", password_hash="h", role="user")
    repo = services.data_source_repo
    row = repo.create(owner_user_id=owner, name="db", engine="mysql")
    repo.upsert_connection(
        row.id,
        host="h",
        port=3306,
        database_name="d",
        schema_name="",
        username="u",
        password_blob=None,
    )
    written: dict = {}

    class FakeWs:
        def upload_bytes(self, path, data):
            written["path"] = path
            written["data"] = data

    seen: dict = {}

    def fake_pipeline(question, **kw):
        seen["spill"] = kw.get("spill")
        # exercise the spill callable to confirm workspace write + returned path
        p = seen["spill"]("a,b\n1,2")
        seen["spilled_path"] = p
        return PipelineOutcome(status="ok", text="x")

    execute_data_source_query(
        services,
        workspace=FakeWs(),
        data_source_id=row.id,
        question="q",
        actor_user_id=owner,
        pipeline_run=fake_pipeline,
    )
    assert seen["spill"] is not None
    assert written["data"] == b"a,b\n1,2"
    assert seen["spilled_path"] == written["path"]
    assert seen["spilled_path"].startswith("outbound/data_source_")
