import { request } from "../request";

export interface DataSource {
  id: string;
  data_source_id?: string;
  pk?: number;
  owner_user_id: number;
  owner_username?: string | null;
  owner_display_name?: string | null;
  name: string;
  description: string;
  engine: "postgres" | "mysql";
  default_open: boolean;
  shared: boolean;
  icon_name: string;
  max_rows: number;
  timeout_ms: number;
  context_char_budget: number;
  allow_im_export: boolean;
  created_at: number;
  updated_at: number;
}

export interface DataSourceConnection {
  data_source_id: string;
  host: string;
  port: number;
  database_name: string;
  schema_name: string;
  username: string;
  password: string | null;
  ssl_mode: string;
  last_tested_at: number | null;
}

export interface AllowedTable {
  table_name: string;
  columns: string[];
}

export interface Annotation {
  scope: "table" | "column";
  table_name: string;
  column_name: string;
  note: string;
}

export interface SchemaTable {
  name: string;
  columns: { name: string; type: string; nullable: boolean }[];
}

export interface DataSourceSchema {
  tables: SchemaTable[];
  refreshed_at: number | null;
}

export interface SqlAuditEntry {
  id: number;
  data_source_id: string;
  actor_user_id: number | null;
  thread_id: string | null;
  agent_id: string | null;
  natural_query: string;
  generated_sql: string;
  executed_sql: string;
  status: "ok" | "empty" | "blocked" | "error";
  row_count: number | null;
  latency_ms: number | null;
  error_message: string | null;
  attempt: number;
  created_at: number;
}

export interface QueryPreviewResult {
  status: "ok" | "empty" | "blocked" | "error";
  text: string;
  natural_query: string;
  generated_sql: string;
  executed_sql: string;
  row_count: number;
  latency_ms: number;
  attempts: number;
  spilled: boolean;
  file_path: string;
  category: string;
}

export interface DataSourceSettings {
  enabled: boolean;
  model: string | null;
}

export interface DataSourceLimits {
  max_sources_per_owner: number;
  max_tables_per_source: number;
}

export const dataSourcesApi = {
  list: () => request<DataSource[]>("/data-sources"),

  get: (id: string) => request<DataSource>(`/data-sources/${id}`),

  create: (body: {
    name: string;
    engine: "postgres" | "mysql";
    description?: string;
    default_open?: boolean;
    shared?: boolean;
    icon_name?: string;
    max_rows?: number;
    timeout_ms?: number;
    context_char_budget?: number;
    allow_im_export?: boolean;
  }) =>
    request<DataSource>("/data-sources", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  update: (
    id: string,
    body: {
      name?: string;
      description?: string;
      default_open?: boolean;
      shared?: boolean;
      icon_name?: string;
      max_rows?: number;
      timeout_ms?: number;
      context_char_budget?: number;
      allow_im_export?: boolean;
    },
  ) =>
    request<DataSource>(`/data-sources/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  delete: (id: string) =>
    request<void>(`/data-sources/${id}`, { method: "DELETE" }),

  getConnection: (id: string) =>
    request<DataSourceConnection>(`/data-sources/${id}/connection`),

  updateConnection: (
    id: string,
    body: {
      host: string;
      port: number;
      database_name: string;
      schema_name?: string;
      username: string;
      password?: string | null;
      ssl_mode?: string;
    },
  ) =>
    request<DataSourceConnection>(`/data-sources/${id}/connection`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),

  testConnection: (body: {
    engine: "postgres" | "mysql";
    host: string;
    port: number;
    database_name: string;
    schema_name?: string;
    username: string;
    password?: string | null;
    ssl_mode?: string;
  }) =>
    request<{ ok: boolean; latency_ms?: number; error?: string }>(
      "/data-sources/connection/test",
      {
        method: "POST",
        body: JSON.stringify(body),
      },
    ),

  testSavedConnection: (id: string) =>
    request<{ ok: boolean; latency_ms?: number; error?: string }>(
      `/data-sources/${id}/connection/test`,
      { method: "POST" },
    ),

  refreshSchema: (id: string) =>
    request<DataSourceSchema>(`/data-sources/${id}/schema/refresh`, {
      method: "POST",
    }),

  getSchema: (id: string) =>
    request<DataSourceSchema>(`/data-sources/${id}/schema`),

  updateAllowlist: (id: string, tables: AllowedTable[]) =>
    request<AllowedTable[]>(`/data-sources/${id}/allowlist`, {
      method: "PUT",
      body: JSON.stringify({ tables }),
    }),

  getAllowlist: (id: string) =>
    request<{ tables: AllowedTable[]; max_tables: number }>(
      `/data-sources/${id}/allowlist`,
    ).then((res) => res.tables || []),

  updateAnnotations: (id: string, annotations: Annotation[]) =>
    request<Annotation[]>(`/data-sources/${id}/annotations`, {
      method: "PUT",
      body: JSON.stringify({ annotations }),
    }),

  getAnnotations: (id: string) =>
    request<{ annotations: Annotation[] }>(
      `/data-sources/${id}/annotations`,
    ).then((res) => res.annotations || []),

  queryPreview: (id: string, naturalQuery: string) =>
    request<QueryPreviewResult>(`/data-sources/${id}/query`, {
      method: "POST",
      body: JSON.stringify({ question: naturalQuery }),
    }),

  getAudit: (id: string, limit?: number) =>
    request<{ entries: SqlAuditEntry[] }>(
      `/data-sources/${id}/audit${limit ? `?limit=${limit}` : ""}`,
    ).then((res) => res.entries || []),

  getSettings: () => request<DataSourceSettings>("/data-sources/settings"),

  updateSettings: (body: { enabled?: boolean; model?: string | null }) =>
    request<DataSourceSettings>("/data-sources/settings", {
      method: "PUT",
      body: JSON.stringify(body),
    }),

  getLimits: () => request<DataSourceLimits>("/data-sources/limits"),
};
