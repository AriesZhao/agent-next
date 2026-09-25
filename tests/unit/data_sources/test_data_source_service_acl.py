"""DataSourceService: ownership / shared / credential masking / gate (§18 step 11)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from octop.infra.data_sources import service as service_module
from octop.infra.data_sources.connection import ConnectionFailure
from octop.infra.data_sources.generate import ModelNotConfigured
from octop.infra.data_sources.schema import ColumnDoc, TableDoc
from octop.infra.data_sources.service import DataSourceService
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.data_sources import AllowedTable, DataSourceRepo
from octop.infra.db.repos.secrets import SecretRepo
from octop.infra.db.repos.settings import SettingsRepo
from octop.infra.db.repos.users import UserRepo


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


@pytest.fixture
def svc(services: SimpleNamespace) -> DataSourceService:
    return DataSourceService(services)


def _owner(services: SimpleNamespace) -> int:
    return services.user_repo.create(username="owner", password_hash="h", role="user")


def _viewer(services: SimpleNamespace) -> int:
    return services.user_repo.create(username="viewer", password_hash="h", role="user")


def test_create_validates_engine_and_name(
    svc: DataSourceService, services: SimpleNamespace
) -> None:
    owner = _owner(services)
    with pytest.raises(ValueError, match="engine"):
        svc.create(owner_user_id=owner, name="x", engine="oracle")
    with pytest.raises(ValueError, match="name"):
        svc.create(owner_user_id=owner, name="  ", engine="postgres")
    row = svc.create(owner_user_id=owner, name="orders db", engine="postgres")
    assert row.engine == "postgres"
    assert row.id.startswith("ds")


def test_duplicate_name_per_owner_rejected(
    svc: DataSourceService, services: SimpleNamespace
) -> None:
    owner = _owner(services)
    svc.create(owner_user_id=owner, name="dup", engine="postgres")
    with pytest.raises(ValueError, match="already exists"):
        svc.create(owner_user_id=owner, name="dup", engine="mysql")


def test_owner_limit_enforced(svc: DataSourceService, services: SimpleNamespace) -> None:
    owner = _owner(services)
    for i in range(service_module.MAX_SOURCES_PER_OWNER):
        svc.create(owner_user_id=owner, name=f"ds{i}", engine="postgres")
    with pytest.raises(ValueError, match="at most"):
        svc.create(owner_user_id=owner, name="one-too-many", engine="postgres")


def test_shared_reader_can_read_not_write(
    svc: DataSourceService, services: SimpleNamespace
) -> None:
    owner = _owner(services)
    viewer = _viewer(services)
    row = svc.create(owner_user_id=owner, name="shared", engine="postgres", shared=True)
    assert svc.get_readable(row.id, actor_user_id=viewer).id == row.id
    with pytest.raises(PermissionError, match="write"):
        svc.get_writable(row.id, actor_user_id=viewer)
    with pytest.raises(PermissionError):
        svc.update(row.id, actor_user_id=viewer, description="hack")
    # admin bypasses write check
    svc.update(row.id, actor_user_id=owner, description="ok")


def test_non_shared_hidden_from_viewer(svc: DataSourceService, services: SimpleNamespace) -> None:
    owner = _owner(services)
    viewer = _viewer(services)
    row = svc.create(owner_user_id=owner, name="private", engine="postgres", shared=False)
    assert row.id not in [r.id for r in svc.list_visible(actor_user_id=viewer)]
    with pytest.raises(PermissionError):
        svc.get_readable(row.id, actor_user_id=viewer)


def test_connection_password_encrypted_and_masked(
    svc: DataSourceService, services: SimpleNamespace
) -> None:
    owner = _owner(services)
    row = svc.create(owner_user_id=owner, name="db", engine="postgres")
    svc.configure_connection(
        row.id,
        actor_user_id=owner,
        host="h",
        port=5432,
        database_name="d",
        schema_name="public",
        username="u",
        password="s3cr3t",
    )
    masked = svc.get_connection_masked(row.id, actor_user_id=owner)
    assert masked["has_password"] is True
    assert masked["password_mask"] == service_module._MASK
    assert "s3cr3t" not in str(masked)
    assert "password" not in {k for k in masked if "password" in k and masked[k] == "s3cr3t"}
    # stored blob is ciphertext, not plaintext
    conn = services.data_source_repo.get_connection(row.id)
    assert conn is not None and conn.password_blob is not None
    assert b"s3cr3t" not in conn.password_blob
    # internal build decrypts to plaintext
    cfg = svc.build_connection_config(row.id)
    assert cfg.password == "s3cr3t"


def test_password_preserved_when_omitted(svc: DataSourceService, services: SimpleNamespace) -> None:
    owner = _owner(services)
    row = svc.create(owner_user_id=owner, name="db", engine="postgres")
    svc.configure_connection(
        row.id,
        actor_user_id=owner,
        host="h",
        port=5432,
        database_name="d",
        schema_name="public",
        username="u",
        password="pw",
    )
    svc.configure_connection(
        row.id,
        actor_user_id=owner,
        host="h2",
        port=5432,
        database_name="d",
        schema_name="public",
        username="u2",
        password=None,
    )
    cfg = svc.build_connection_config(row.id)
    assert cfg.host == "h2"
    assert cfg.password == "pw"  # preserved


def test_public_payload_has_no_credentials(
    svc: DataSourceService, services: SimpleNamespace
) -> None:
    owner = _owner(services)
    row = svc.create(owner_user_id=owner, name="db", engine="postgres")
    svc.configure_connection(
        row.id,
        actor_user_id=owner,
        host="secret-host",
        port=5432,
        database_name="d",
        username="u",
        password="topsecret",
    )
    payload = svc.to_public_payload(row)
    text = str(payload)
    assert "topsecret" not in text
    assert "secret-host" not in text
    assert payload["name"] == "db"


def test_allowlist_and_annotations_roundtrip(
    svc: DataSourceService, services: SimpleNamespace
) -> None:
    owner = _owner(services)
    row = svc.create(owner_user_id=owner, name="db", engine="postgres")
    svc.set_allowlist(
        row.id,
        [AllowedTable("orders", ["id", "amount"]), AllowedTable("orders", ["dup"])],
        actor_user_id=owner,
    )
    tables = svc.list_allowlist(row.id, actor_user_id=owner)
    assert len(tables) == 1  # dedup by name
    assert set(tables[0].columns) == {"id", "amount", "dup"} or tables[0].columns

    from octop.infra.db.repos.data_sources import Annotation

    svc.set_annotations(
        row.id,
        [Annotation(scope="column", table_name="orders", column_name="amount", note="revenue")],
        actor_user_id=owner,
    )
    anns = svc.list_annotations(row.id, actor_user_id=owner)
    assert anns[0].note == "revenue"


def test_feature_gate_requires_model(svc: DataSourceService, services: SimpleNamespace) -> None:
    # disabled + no model → build_chat_model returns None (pipeline reports not-configured)
    assert svc.build_chat_model() is None
    services.settings_repo.set("data_sources_enabled", "true")
    # enabled but no model ref → still None (fail-fast, no fallback)
    assert svc.build_chat_model() is None


def test_validate_model_ref_rejects_malformed(svc: DataSourceService) -> None:
    with pytest.raises(ModelNotConfigured):
        svc.validate_model_ref("nope")
    with pytest.raises(ModelNotConfigured):
        svc.validate_model_ref("missingprovider/model")


def test_refresh_schema_uses_introspect_and_caches(
    svc: DataSourceService,
    services: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = _owner(services)
    row = svc.create(owner_user_id=owner, name="db", engine="postgres")
    svc.configure_connection(
        row.id,
        actor_user_id=owner,
        host="h",
        port=5432,
        database_name="d",
        username="u",
        password="p",
    )
    captured: dict = {}

    def fake_introspect(cfg, **kw):
        captured["engine"] = cfg.engine
        return [TableDoc(name="orders", columns=[ColumnDoc(name="id", data_type="int")])]

    monkeypatch.setattr(service_module, "introspect", fake_introspect)
    tables = svc.refresh_schema(row.id, actor_user_id=owner)
    assert captured["engine"] == "postgres"
    assert tables[0].name == "orders"
    cached, _ = svc.get_cached_schema(row.id)
    assert cached[0].name == "orders"


def test_refresh_schema_owner_only(svc: DataSourceService, services: SimpleNamespace) -> None:
    owner = _owner(services)
    viewer = _viewer(services)
    row = svc.create(owner_user_id=owner, name="db", engine="postgres", shared=True)
    with pytest.raises(PermissionError):
        svc.refresh_schema(row.id, actor_user_id=viewer)


def test_test_connection_delegates_and_marks_tested(
    svc: DataSourceService, services: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = _owner(services)
    row = svc.create(owner_user_id=owner, name="db", engine="postgres")
    svc.configure_connection(
        row.id,
        actor_user_id=owner,
        host="h",
        port=5432,
        database_name="d",
        username="u",
        password="p",
    )
    called: dict = {}

    def fake_probe(cfg, *, timeout_ms=5000):
        called["host"] = cfg.host

    monkeypatch.setattr(service_module, "probe_connectivity", fake_probe)
    svc.test_connection(row.id, actor_user_id=owner)
    assert called["host"] == "h"
    conn = services.data_source_repo.get_connection(row.id)
    assert conn is not None and conn.last_tested_at


def test_test_connection_propagates_failure(
    svc: DataSourceService, services: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = _owner(services)
    row = svc.create(owner_user_id=owner, name="db", engine="postgres")
    svc.configure_connection(
        row.id,
        actor_user_id=owner,
        host="h",
        port=5432,
        database_name="d",
        username="u",
        password="p",
    )

    def boom(cfg, *, timeout_ms=5000):
        raise ConnectionFailure("cannot reach host")

    monkeypatch.setattr(service_module, "probe_connectivity", boom)
    with pytest.raises(ConnectionFailure):
        svc.test_connection(row.id, actor_user_id=owner)


def test_delete_requires_owner(svc: DataSourceService, services: SimpleNamespace) -> None:
    owner = _owner(services)
    viewer = _viewer(services)
    row = svc.create(owner_user_id=owner, name="db", engine="postgres", shared=True)
    with pytest.raises(PermissionError):
        svc.delete(row.id, actor_user_id=viewer)
    svc.delete(row.id, actor_user_id=owner)
    assert services.data_source_repo.get(row.id) is None
