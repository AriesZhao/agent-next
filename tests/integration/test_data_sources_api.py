"""End-to-end HTTP coverage for the NL2SQL data-source router (§18 step 14/17).

Network-bound operations (connection probe, schema introspection, query
pipeline) are monkeypatched at the ``infra``/router seams so the test exercises
routing, ACL, credential masking, and payload shape without a real database or
LLM.
"""

from __future__ import annotations

from typing import Any

import pytest

from octop.infra.data_sources.schema import ColumnDoc, TableDoc
from octop.infra.data_sources.types import PipelineOutcome
from tests.support.auth import create_user


async def _create_source(client: Any, auth: dict[str, str], name: str = "orders-db") -> str:
    r = await client.post(
        "/api/data-sources",
        headers=auth,
        json={"name": name, "engine": "postgres", "description": "sales"},
    )
    assert r.status_code == 201, r.text
    return r.json()["data_source_id"]


async def test_settings_toggle_requires_data_source_settings(env: Any) -> None:
    client, _server, admin_auth = env
    page_auth = await create_user(client, admin_auth, username="ds_page_user")
    denied = await client.put(
        "/api/data-sources/settings", headers=page_auth, json={"enabled": False}
    )
    assert denied.status_code == 403, denied.text
    assert denied.json()["error"]["code"] == "FORBIDDEN"

    settings_auth = await create_user(
        client, admin_auth, username="ds_settings_user", permissions=["data_source_settings"]
    )
    allowed = await client.put(
        "/api/data-sources/settings", headers=settings_auth, json={"enabled": False}
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["enabled"] is False


async def test_enable_without_model_is_fail_fast(env: Any) -> None:
    client, _server, admin_auth = env
    settings_auth = await create_user(
        client, admin_auth, username="ds_enable_user", permissions=["data_source_settings"]
    )
    resp = await client.put(
        "/api/data-sources/settings", headers=settings_auth, json={"enabled": True}
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "DATA_SOURCE_MODEL_NOT_CONFIGURED"


async def test_create_list_get(env: Any) -> None:
    client, _server, admin_auth = env
    auth = await create_user(client, admin_auth, username="ds_owner")
    ds_id = await _create_source(client, auth)

    listed = (await client.get("/api/data-sources", headers=auth)).json()
    assert any(s["data_source_id"] == ds_id for s in listed)

    detail = await client.get(f"/api/data-sources/{ds_id}", headers=auth)
    assert detail.status_code == 200, detail.text
    assert detail.json()["engine"] == "postgres"
    assert "password" not in detail.json()


async def test_duplicate_name_conflict(env: Any) -> None:
    client, _server, admin_auth = env
    auth = await create_user(client, admin_auth, username="ds_dup")
    await _create_source(client, auth, name="dup-db")
    again = await client.post(
        "/api/data-sources", headers=auth, json={"name": "dup-db", "engine": "postgres"}
    )
    assert again.status_code == 409, again.text
    assert again.json()["error"]["code"] == "DATA_SOURCE_NAME_TAKEN"


async def test_connection_is_masked_on_read(env: Any) -> None:
    client, _server, admin_auth = env
    auth = await create_user(client, admin_auth, username="ds_conn")
    ds_id = await _create_source(client, auth)

    empty = await client.get(f"/api/data-sources/{ds_id}/connection", headers=auth)
    assert empty.status_code == 200, empty.text
    assert empty.json()["configured"] is False

    put = await client.put(
        f"/api/data-sources/{ds_id}/connection",
        headers=auth,
        json={
            "host": "db.internal",
            "port": 5432,
            "database_name": "sales",
            "schema_name": "public",
            "username": "ro",
            "password": "s3cret",
        },
    )
    assert put.status_code == 200, put.text
    body = put.json()
    assert body["configured"] is True
    assert body["has_password"] is True
    assert "password" not in body
    assert "s3cret" not in put.text


async def test_connection_test_and_schema_refresh(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _server, admin_auth = env
    auth = await create_user(client, admin_auth, username="ds_probe")
    ds_id = await _create_source(client, auth)
    await client.put(
        f"/api/data-sources/{ds_id}/connection",
        headers=auth,
        json={
            "host": "db.internal",
            "port": 5432,
            "database_name": "sales",
            "username": "ro",
            "password": "pw",
        },
    )

    calls: list[str] = []

    def _fake_probe(_cfg: Any, *, timeout_ms: int = 5000) -> None:
        calls.append("probe")

    def _fake_introspect(_cfg: Any) -> list[TableDoc]:
        calls.append("introspect")
        return [TableDoc("orders", [ColumnDoc("id", "integer"), ColumnDoc("total", "numeric")])]

    monkeypatch.setattr("octop.infra.data_sources.service.probe_connectivity", _fake_probe)
    monkeypatch.setattr("octop.infra.data_sources.service.introspect", _fake_introspect)

    tested = await client.post(f"/api/data-sources/{ds_id}/connection/test", headers=auth)
    assert tested.status_code == 200, tested.text
    assert tested.json()["ok"] is True

    refreshed = await client.post(f"/api/data-sources/{ds_id}/schema/refresh", headers=auth)
    assert refreshed.status_code == 200, refreshed.text
    tables = refreshed.json()["tables"]
    assert tables and tables[0]["name"] == "orders"
    assert calls == ["probe", "introspect"]

    schema = await client.get(f"/api/data-sources/{ds_id}/schema", headers=auth)
    assert schema.status_code == 200, schema.text
    assert schema.json()["tables"][0]["name"] == "orders"


async def test_allowlist_and_annotations(env: Any) -> None:
    client, _server, admin_auth = env
    auth = await create_user(client, admin_auth, username="ds_meta")
    ds_id = await _create_source(client, auth)

    al = await client.put(
        f"/api/data-sources/{ds_id}/allowlist",
        headers=auth,
        json={"tables": [{"table_name": "orders", "columns": ["id", "total"]}]},
    )
    assert al.status_code == 200, al.text
    got_al = await client.get(f"/api/data-sources/{ds_id}/allowlist", headers=auth)
    assert got_al.json()["tables"][0]["table_name"] == "orders"

    an = await client.put(
        f"/api/data-sources/{ds_id}/annotations",
        headers=auth,
        json={
            "annotations": [
                {"scope": "column", "table_name": "orders", "column_name": "total", "note": "USD"}
            ]
        },
    )
    assert an.status_code == 200, an.text
    got_an = await client.get(f"/api/data-sources/{ds_id}/annotations", headers=auth)
    assert got_an.json()["annotations"][0]["note"] == "USD"


async def test_query_preview_and_audit(env: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _server, admin_auth = env
    auth = await create_user(client, admin_auth, username="ds_query")
    ds_id = await _create_source(client, auth)

    def _fake_execute(_services: Any, **kwargs: Any) -> PipelineOutcome:
        assert kwargs["data_source_id"] == ds_id
        return PipelineOutcome(
            status="ok",
            text="digest",
            natural_query=kwargs["question"],
            generated_sql="SELECT 1",
            executed_sql="SELECT 1 LIMIT 100",
            row_count=1,
        )

    monkeypatch.setattr("octop.api.routers.data_sources.execute_data_source_query", _fake_execute)

    resp = await client.post(
        f"/api/data-sources/{ds_id}/query", headers=auth, json={"question": "how many orders?"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert body["executed_sql"] == "SELECT 1 LIMIT 100"
    assert body["row_count"] == 1

    audit = await client.get(f"/api/data-sources/{ds_id}/audit", headers=auth)
    assert audit.status_code == 200, audit.text
    assert isinstance(audit.json()["entries"], list)


async def test_non_owner_cannot_write(env: Any) -> None:
    client, _server, admin_auth = env
    owner_auth = await create_user(client, admin_auth, username="ds_alice")
    other_auth = await create_user(client, admin_auth, username="ds_bob")
    ds_id = await _create_source(client, owner_auth)

    denied = await client.patch(
        f"/api/data-sources/{ds_id}", headers=other_auth, json={"description": "hijack"}
    )
    # bob cannot see alice's private source at all.
    assert denied.status_code in (403, 404), denied.text
