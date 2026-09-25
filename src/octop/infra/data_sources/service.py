"""Data-source ownership, configuration CRUD, and pipeline wiring (§6, §10).

``DataSourceService`` is the single domain entry the HTTP routers and the
``query_data_source`` tool call. It enforces ownership / shared visibility
(mirroring :class:`octop.infra.knowledge.service.KnowledgeService`), keeps
credentials encrypted and masked, refreshes the cached schema, and assembles a
:class:`~octop.infra.data_sources.connection.ConnectionConfig` for the pipeline.

Config is **fail-fast** (design §8.1): enabling the feature or setting the
NL2SQL model validates that the model ref resolves to a chat-capable provider
before it is persisted — there is no silent runtime fallback.
"""

from __future__ import annotations

from typing import Any, cast

from octop.infra.data_sources.connection import (
    ENGINES,
    ConnectionConfig,
    probe_connectivity,
)
from octop.infra.data_sources.credentials import decrypt_password, encrypt_password
from octop.infra.data_sources.generate import (
    NL2SQL_MODEL_KEY,
    ModelNotConfigured,
    build_chat_model_for_ref,
    is_ref_configured,
    resolve_model_ref,
)
from octop.infra.data_sources.introspect import introspect
from octop.infra.data_sources.schema import (
    SchemaDoc,
    deserialize_schema,
    render_schema_for_prompt,
    scope_schema,
    serialize_schema,
)
from octop.infra.db.repos.data_sources import (
    AllowedTable,
    Annotation,
    DataSourceConnectionRow,
    DataSourceRow,
)

MAX_SOURCES_PER_OWNER = 20
MAX_TABLES_PER_SOURCE = 100
_FEATURE_ENABLED_KEY = "data_sources_enabled"
_MASK = "••••••••"
_VALID_ENGINES = set(ENGINES)


def _as_bool(value: str | None) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


class DataSourceService:
    def __init__(self, services: Any) -> None:
        self._services = services

    # --- repos ----------------------------------------------------------

    @property
    def _repo(self) -> Any:
        return self._services.data_source_repo

    @property
    def _secret_repo(self) -> Any:
        return self._services.secret_repo

    @property
    def _settings(self) -> Any:
        return self._services.settings_repo

    @property
    def _providers(self) -> Any:
        return getattr(self._services, "provider_repo", None)

    # --- capability gate (§8.1) ----------------------------------------

    def feature_enabled(self) -> bool:
        return _as_bool(self._settings.get(_FEATURE_ENABLED_KEY))

    def model_ref(self) -> str:
        return resolve_model_ref(self._settings.get)

    def validate_model_ref(self, ref: str) -> str:
        """Fail-fast: raise :class:`ModelNotConfigured` unless ``ref`` resolves."""
        cleaned = (ref or "").strip()
        if not cleaned:
            raise ModelNotConfigured("data source NL2SQL model is not configured")
        if not is_ref_configured(cleaned):
            raise ModelNotConfigured("model ref must be 'provider/model'")
        build_chat_model_for_ref(self._providers, cleaned)  # raises if unknown
        return cleaned

    def set_feature_enabled(self, *, enabled: bool, model: str | None = None) -> None:
        if model is not None:
            cleaned = model.strip()
            if cleaned:
                self.validate_model_ref(cleaned)
                self._settings.set(NL2SQL_MODEL_KEY, cleaned)
        if enabled:
            current = self.model_ref()
            if not current:
                raise ModelNotConfigured("enabling data sources requires an NL2SQL model")
            self.validate_model_ref(current)
            self._settings.set(_FEATURE_ENABLED_KEY, "true")
        else:
            self._settings.set(_FEATURE_ENABLED_KEY, "false")

    def build_chat_model(self) -> Any:
        """Resolve the configured NL2SQL model, or ``None`` when unusable.

        The pipeline treats ``None`` as ``model_not_configured`` and never falls
        back to another model (§8.1).
        """
        if not self.feature_enabled():
            return None
        ref = self.model_ref()
        if not ref:
            return None
        try:
            return build_chat_model_for_ref(self._providers, ref)
        except ModelNotConfigured:
            return None

    # --- ownership / visibility (mirror knowledge) ----------------------

    def _require_source(self, data_source_id: str) -> DataSourceRow:
        row = self._repo.get(data_source_id)
        if row is None:
            raise LookupError("data source not found")
        return cast(DataSourceRow, row)

    def get_readable(
        self, data_source_id: str, *, actor_user_id: int, is_admin: bool = False
    ) -> DataSourceRow:
        row = self._require_source(data_source_id)
        if is_admin or row.owner_user_id == actor_user_id or row.shared:
            return row
        raise PermissionError("data source read access is required")

    def get_writable(
        self, data_source_id: str, *, actor_user_id: int, is_admin: bool = False
    ) -> DataSourceRow:
        row = self._require_source(data_source_id)
        if is_admin or row.owner_user_id == actor_user_id:
            return row
        raise PermissionError("data source write access is required")

    def require_owner(
        self, data_source_id: str, *, actor_user_id: int, is_admin: bool = False
    ) -> DataSourceRow:
        return self.get_writable(data_source_id, actor_user_id=actor_user_id, is_admin=is_admin)

    # --- CRUD -----------------------------------------------------------

    def create(
        self,
        *,
        owner_user_id: int,
        name: str,
        engine: str,
        description: str = "",
        default_open: bool = False,
        shared: bool = False,
        icon_name: str = "",
        max_rows: int = 100,
        timeout_ms: int = 5000,
        context_char_budget: int = 4000,
        allow_im_export: bool = False,
    ) -> DataSourceRow:
        cleaned = (name or "").strip()
        if not cleaned:
            raise ValueError("data source name is required")
        if engine not in _VALID_ENGINES:
            raise ValueError(f"unsupported engine: {engine}")
        if max_rows < 1 or max_rows > 5000:
            raise ValueError("max_rows must be between 1 and 5000")
        if timeout_ms < 100 or timeout_ms > 120000:
            raise ValueError("timeout_ms must be between 100 and 120000")
        if context_char_budget < 200:
            raise ValueError("context_char_budget must be at least 200")
        if self._repo.count_for_owner(owner_user_id) >= MAX_SOURCES_PER_OWNER:
            raise ValueError(f"a user may own at most {MAX_SOURCES_PER_OWNER} data sources")
        if self._repo.get_by_owner_name(owner_user_id, cleaned) is not None:
            raise ValueError("a data source with this name already exists")
        return cast(
            DataSourceRow,
            self._repo.create(
                owner_user_id=owner_user_id,
                name=cleaned,
                engine=engine,
                description=description,
                default_open=default_open,
                shared=shared,
                icon_name=icon_name,
                max_rows=max_rows,
                timeout_ms=timeout_ms,
                context_char_budget=context_char_budget,
                allow_im_export=allow_im_export,
            ),
        )

    def list_visible(self, *, actor_user_id: int, is_admin: bool = False) -> list[DataSourceRow]:
        if is_admin:
            return cast("list[DataSourceRow]", self._repo.list_all())
        return cast("list[DataSourceRow]", self._repo.list_visible(actor_user_id))

    def get(
        self, data_source_id: str, *, actor_user_id: int, is_admin: bool = False
    ) -> DataSourceRow:
        return self.get_readable(data_source_id, actor_user_id=actor_user_id, is_admin=is_admin)

    def update(
        self,
        data_source_id: str,
        *,
        actor_user_id: int,
        name: str | None = None,
        description: str | None = None,
        default_open: bool | None = None,
        shared: bool | None = None,
        icon_name: str | None = None,
        max_rows: int | None = None,
        timeout_ms: int | None = None,
        context_char_budget: int | None = None,
        allow_im_export: bool | None = None,
        is_admin: bool = False,
    ) -> DataSourceRow:
        self.get_writable(data_source_id, actor_user_id=actor_user_id, is_admin=is_admin)
        if name is not None:
            cleaned = name.strip()
            if not cleaned:
                raise ValueError("data source name is required")
            name = cleaned
        if max_rows is not None and not (1 <= max_rows <= 5000):
            raise ValueError("max_rows must be between 1 and 5000")
        if timeout_ms is not None and not (100 <= timeout_ms <= 120000):
            raise ValueError("timeout_ms must be between 100 and 120000")
        self._repo.update(
            data_source_id,
            name=name,
            description=description,
            default_open=default_open,
            shared=shared,
            icon_name=icon_name,
            max_rows=max_rows,
            timeout_ms=timeout_ms,
            context_char_budget=context_char_budget,
            allow_im_export=allow_im_export,
        )
        return self._require_source(data_source_id)

    def delete(self, data_source_id: str, *, actor_user_id: int, is_admin: bool = False) -> None:
        self.require_owner(data_source_id, actor_user_id=actor_user_id, is_admin=is_admin)
        self._repo.delete(data_source_id)

    # --- connection (encrypted, masked) --------------------------------

    def configure_connection(
        self,
        data_source_id: str,
        *,
        actor_user_id: int,
        host: str,
        port: int,
        database_name: str,
        schema_name: str = "",
        username: str,
        password: str | None = None,
        ssl_mode: str = "",
        is_admin: bool = False,
    ) -> DataSourceConnectionRow:
        row = self.get_writable(data_source_id, actor_user_id=actor_user_id, is_admin=is_admin)
        if row.engine not in _VALID_ENGINES:
            raise ValueError(f"unsupported engine: {row.engine}")
        if not (host or "").strip():
            raise ValueError("host is required")
        if not (database_name or "").strip():
            raise ValueError("database_name is required")
        if not (username or "").strip():
            raise ValueError("username is required")
        blob = encrypt_password(self._secret_repo, password) if password is not None else None
        self._repo.upsert_connection(
            data_source_id,
            host=host.strip(),
            port=int(port or 0),
            database_name=database_name.strip(),
            schema_name=(schema_name or "").strip(),
            username=username.strip(),
            password_blob=blob,
            ssl_mode=(ssl_mode or "").strip(),
        )
        conn = self._repo.get_connection(data_source_id)
        if conn is None:
            raise LookupError("data source connection not found")
        return cast(DataSourceConnectionRow, conn)

    def get_connection_masked(
        self, data_source_id: str, *, actor_user_id: int, is_admin: bool = False
    ) -> dict[str, Any]:
        self.get_readable(data_source_id, actor_user_id=actor_user_id, is_admin=is_admin)
        conn = self._repo.get_connection(data_source_id)
        if conn is None:
            return {"configured": False}
        return {
            "configured": True,
            "host": conn.host,
            "port": conn.port,
            "database_name": conn.database_name,
            "schema_name": conn.schema_name,
            "username": conn.username,
            "ssl_mode": conn.ssl_mode,
            "has_password": conn.has_password,
            "password_mask": _MASK if conn.has_password else "",
            "last_tested_at": conn.last_tested_at,
        }

    def build_connection_config(self, data_source_id: str) -> ConnectionConfig:
        """Internal: decrypt the stored password for one pipeline/query call."""
        row = self._require_source(data_source_id)
        conn = self._repo.get_connection(data_source_id)
        if conn is None:
            raise LookupError("data source connection not configured")
        password = decrypt_password(self._secret_repo, conn.password_blob)
        return ConnectionConfig(
            engine=row.engine,
            host=conn.host,
            port=conn.port,
            database=conn.database_name,
            schema=conn.schema_name,
            username=conn.username,
            password=password,
            ssl_mode=conn.ssl_mode,
        )

    def test_connection(
        self,
        data_source_id: str,
        *,
        actor_user_id: int,
        is_admin: bool = False,
        timeout_ms: int | None = None,
    ) -> None:
        """Probe a saved connection (owner/admin only). Raises ``ConnectionFailure``."""
        row = self.get_writable(data_source_id, actor_user_id=actor_user_id, is_admin=is_admin)
        cfg = self.build_connection_config(data_source_id)
        probe_connectivity(cfg, timeout_ms=timeout_ms or row.timeout_ms)
        self._repo.mark_connection_tested(data_source_id)

    def probe_new_connection(
        self,
        *,
        engine: str,
        host: str,
        port: int,
        database_name: str,
        schema_name: str = "",
        username: str,
        password: str = "",
        ssl_mode: str = "",
        timeout_ms: int = 5000,
    ) -> None:
        """Validate credentials before persisting (used by the create/test wizard)."""
        if engine not in _VALID_ENGINES:
            raise ValueError(f"unsupported engine: {engine}")
        cfg = ConnectionConfig(
            engine=engine,
            host=host,
            port=int(port or 0),
            database=database_name,
            schema=schema_name,
            username=username,
            password=password,
            ssl_mode=ssl_mode,
        )
        probe_connectivity(cfg, timeout_ms=timeout_ms)

    # --- schema ---------------------------------------------------------

    def refresh_schema(
        self, data_source_id: str, *, actor_user_id: int, is_admin: bool = False
    ) -> SchemaDoc:
        self.get_writable(data_source_id, actor_user_id=actor_user_id, is_admin=is_admin)
        cfg = self.build_connection_config(data_source_id)
        tables = introspect(cfg)
        self._repo.upsert_schema(data_source_id, serialize_schema(tables))
        return tables

    def get_cached_schema(self, data_source_id: str) -> tuple[SchemaDoc, int]:
        cached = self._repo.get_schema(data_source_id)
        if cached is None:
            return [], 0
        tables_json, refreshed_at = cached
        return deserialize_schema(tables_json), refreshed_at

    def list_allowlist(
        self, data_source_id: str, *, actor_user_id: int, is_admin: bool = False
    ) -> list[AllowedTable]:
        self.get_readable(data_source_id, actor_user_id=actor_user_id, is_admin=is_admin)
        return cast("list[AllowedTable]", self._repo.list_allowlist(data_source_id))

    def set_allowlist(
        self,
        data_source_id: str,
        items: list[AllowedTable],
        *,
        actor_user_id: int,
        is_admin: bool = False,
    ) -> None:
        self.get_writable(data_source_id, actor_user_id=actor_user_id, is_admin=is_admin)
        if len(items) > MAX_TABLES_PER_SOURCE:
            raise ValueError(f"a data source may allow at most {MAX_TABLES_PER_SOURCE} tables")
        cleaned: list[AllowedTable] = []
        seen: set[str] = set()
        for item in items:
            name = (item.table_name or "").strip()
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            cols = [str(c).strip() for c in item.columns if str(c).strip()]
            cleaned.append(AllowedTable(name, cols))
        self._repo.replace_allowlist(data_source_id, cleaned)

    def list_annotations(
        self, data_source_id: str, *, actor_user_id: int, is_admin: bool = False
    ) -> list[Annotation]:
        self.get_readable(data_source_id, actor_user_id=actor_user_id, is_admin=is_admin)
        return cast("list[Annotation]", self._repo.list_annotations(data_source_id))

    def set_annotations(
        self,
        data_source_id: str,
        items: list[Annotation],
        *,
        actor_user_id: int,
        is_admin: bool = False,
    ) -> None:
        self.get_writable(data_source_id, actor_user_id=actor_user_id, is_admin=is_admin)
        cleaned: list[Annotation] = []
        for item in items:
            scope = (item.scope or "").strip().lower()
            if scope not in {"table", "column"}:
                continue
            table = (item.table_name or "").strip()
            if not table:
                continue
            column = (item.column_name or "").strip() if scope == "column" else ""
            cleaned.append(
                Annotation(
                    scope=scope,
                    table_name=table,
                    column_name=column,
                    note=(item.note or "").strip(),
                )
            )
        self._repo.replace_annotations(data_source_id, cleaned)

    # --- prompt assembly (used by tool / preview) -----------------------

    def build_scoped_prompt(self, data_source_id: str) -> tuple[str, list[AllowedTable]]:
        """Return ``(schema_text, allowlist)`` narrowed to the allowlist + caliber."""
        row = self._require_source(data_source_id)
        allowlist = self._repo.list_allowlist(data_source_id)
        schema, _ = self.get_cached_schema(data_source_id)
        annotations = self._repo.list_annotations(data_source_id)
        scoped = scope_schema(schema, allowlist, annotations=annotations)
        text = render_schema_for_prompt(scoped, dialect=row.engine)
        return text, allowlist

    # --- audit ----------------------------------------------------------

    def list_audit(
        self, data_source_id: str, *, actor_user_id: int, is_admin: bool = False, limit: int = 50
    ) -> list[Any]:
        self.require_owner(data_source_id, actor_user_id=actor_user_id, is_admin=is_admin)
        return cast("list[Any]", self._repo.list_audit(data_source_id, limit=limit))

    # --- public payload -------------------------------------------------

    def to_public_payload(self, row: DataSourceRow) -> dict[str, Any]:
        """Metadata safe to return to any reader — never carries credentials."""
        return {
            "id": row.id,
            "name": row.name,
            "description": row.description,
            "engine": row.engine,
            "default_open": row.default_open,
            "shared": row.shared,
            "icon_name": row.icon_name,
            "max_rows": row.max_rows,
            "timeout_ms": row.timeout_ms,
            "context_char_budget": row.context_char_budget,
            "allow_im_export": row.allow_im_export,
            "owner_user_id": row.owner_user_id,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
