-- Schema v16: NL2SQL data sources (structured external DB querying).
-- Six tables: main resource, 1:1 connection, allowlist, annotations,
-- introspection cache, and append-only SQL audit. An ``agents.data_source_ids``
-- profile column is added alongside (mirrors ``knowledge_base_ids``).
--
-- SQLite applies this via migrate.py::_ensure_data_source_schema (idempotent);
-- this file is the canonical v15 -> v16 DDL. PostgreSQL runs the .pg.sql pair.

PRAGMA foreign_keys = OFF;

CREATE TABLE IF NOT EXISTS data_sources (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  data_source_id      TEXT NOT NULL UNIQUE,
  owner_user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name                TEXT NOT NULL,
  description         TEXT NOT NULL DEFAULT '',
  engine              TEXT NOT NULL,
  default_open        INTEGER NOT NULL DEFAULT 0,
  shared              INTEGER NOT NULL DEFAULT 0,
  icon_name           TEXT NOT NULL DEFAULT '',
  max_rows            INTEGER NOT NULL DEFAULT 100,
  timeout_ms          INTEGER NOT NULL DEFAULT 5000,
  context_char_budget INTEGER NOT NULL DEFAULT 4000,
  allow_im_export     INTEGER NOT NULL DEFAULT 0,
  created_at          INTEGER NOT NULL,
  updated_at          INTEGER NOT NULL,
  UNIQUE(owner_user_id, name)
);
CREATE INDEX IF NOT EXISTS idx_data_sources_owner ON data_sources(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_data_sources_shared ON data_sources(shared) WHERE shared = 1;

CREATE TABLE IF NOT EXISTS data_source_connections (
  data_source_id  TEXT PRIMARY KEY REFERENCES data_sources(data_source_id) ON DELETE CASCADE,
  host            TEXT NOT NULL DEFAULT '',
  port            INTEGER NOT NULL DEFAULT 0,
  database_name   TEXT NOT NULL DEFAULT '',
  schema_name     TEXT NOT NULL DEFAULT '',
  username        TEXT NOT NULL DEFAULT '',
  password_blob   BLOB,
  ssl_mode        TEXT NOT NULL DEFAULT '',
  last_tested_at  INTEGER
);

CREATE TABLE IF NOT EXISTS data_source_allowed_tables (
  data_source_id TEXT NOT NULL REFERENCES data_sources(data_source_id) ON DELETE CASCADE,
  table_name     TEXT NOT NULL,
  columns_json   TEXT NOT NULL DEFAULT '[]',
  PRIMARY KEY (data_source_id, table_name)
);

CREATE TABLE IF NOT EXISTS data_source_annotations (
  data_source_id TEXT NOT NULL REFERENCES data_sources(data_source_id) ON DELETE CASCADE,
  scope          TEXT NOT NULL,
  table_name     TEXT NOT NULL,
  column_name    TEXT NOT NULL DEFAULT '',
  note           TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (data_source_id, scope, table_name, column_name)
);

CREATE TABLE IF NOT EXISTS data_source_schemas (
  data_source_id TEXT PRIMARY KEY REFERENCES data_sources(data_source_id) ON DELETE CASCADE,
  tables_json    TEXT NOT NULL DEFAULT '[]',
  refreshed_at   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS data_source_sql_audit (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  data_source_id TEXT NOT NULL,
  actor_user_id  INTEGER,
  thread_id      TEXT,
  agent_id       TEXT,
  natural_query  TEXT NOT NULL DEFAULT '',
  generated_sql  TEXT NOT NULL DEFAULT '',
  executed_sql   TEXT NOT NULL DEFAULT '',
  status         TEXT NOT NULL,
  row_count      INTEGER NOT NULL DEFAULT 0,
  latency_ms     INTEGER NOT NULL DEFAULT 0,
  error_message  TEXT NOT NULL DEFAULT '',
  attempt        INTEGER NOT NULL DEFAULT 1,
  created_at     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_data_source_sql_audit_ds
  ON data_source_sql_audit(data_source_id, created_at DESC);

ALTER TABLE agents ADD COLUMN data_source_ids TEXT;

PRAGMA foreign_keys = ON;

UPDATE _schema_version SET version = 16;
