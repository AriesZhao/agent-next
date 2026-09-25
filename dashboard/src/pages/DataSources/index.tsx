import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Descriptions,
  Drawer,
  Empty,
  Form,
  Input,
  InputNumber,
  List,
  Modal,
  Select,
  Space,
  Spin,
  Switch,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import {
  Database,
  Edit,
  History,
  Plus,
  RefreshCw,
  Search,
  Settings,
  Trash2,
  X,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { useCurrentUser } from "../../hooks/useCurrentUser";
import { userCan } from "../../utils/permissions";
import type { OctopUser } from "../../api/modules/auth";
import {
  dataSourcesApi,
  type AllowedTable,
  type Annotation,
  type DataSource,
  type QueryPreviewResult,
  type SchemaTable,
  type SqlAuditEntry,
} from "../../api/modules/dataSources";
import { CopyableResourceId } from "../../components/CopyableResourceId";
import { useIsMobile } from "../../hooks/useIsMobile";
import PageShell from "../../layouts/PageShell";
import { apiErrorMessage } from "../../utils/apiError";
import { formatServerDateTime } from "../../utils/formatMessageTime";

const { Text, Title } = Typography;

type DataSourceFormValues = {
  name: string;
  engine: "postgres" | "mysql";
  description?: string;
  default_open?: boolean;
  shared?: boolean;
  max_rows?: number;
  timeout_ms?: number;
  context_char_budget?: number;
  allow_im_export?: boolean;
};

type ConnectionFormValues = {
  host: string;
  port: number;
  database_name: string;
  schema_name?: string;
  username: string;
  password?: string;
  ssl_mode?: string;
};

function canManageDataSource(
  source: Pick<DataSource, "owner_user_id">,
  user: OctopUser | null,
): boolean {
  return Boolean(
    user && (user.role === "admin" || source.owner_user_id === user.id),
  );
}

function formatDataSourceOwner(
  source: Pick<
    DataSource,
    "owner_display_name" | "owner_username" | "owner_user_id"
  >,
): string {
  const displayName = source.owner_display_name?.trim() || "";
  const username = source.owner_username?.trim() || "";
  return displayName || username || String(source.owner_user_id);
}

export default function DataSourcesPage() {
  const { t } = useTranslation();
  const { message: messageApi, modal } = App.useApp();
  const user = useCurrentUser();
  const isMobile = useIsMobile();

  const [dataSources, setDataSources] = useState<DataSource[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedSource, setSelectedSource] = useState<DataSource | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [editModalOpen, setEditModalOpen] = useState(false);
  const [connectionDrawerOpen, setConnectionDrawerOpen] = useState(false);
  const [schemaDrawerOpen, setSchemaDrawerOpen] = useState(false);
  const [queryDrawerOpen, setQueryDrawerOpen] = useState(false);
  const [auditDrawerOpen, setAuditDrawerOpen] = useState(false);
  const [settingsDrawerOpen, setSettingsDrawerOpen] = useState(false);

  const [createForm] = Form.useForm<DataSourceFormValues>();
  const [editForm] = Form.useForm<DataSourceFormValues>();
  const [connectionForm] = Form.useForm<ConnectionFormValues>();

  const [connectionLoading, setConnectionLoading] = useState(false);
  const [testingConnection, setTestingConnection] = useState(false);
  const [connectionTestResult, setConnectionTestResult] = useState<{
    ok: boolean;
    message: string;
  } | null>(null);

  const [schema, setSchema] = useState<SchemaTable[]>([]);
  const [schemaLoading, setSchemaLoading] = useState(false);
  const [allowlist, setAllowlist] = useState<AllowedTable[]>([]);
  const [annotations, setAnnotations] = useState<Annotation[]>([]);
  const [refreshingSchema, setRefreshingSchema] = useState(false);

  const [queryText, setQueryText] = useState("");
  const [queryResult, setQueryResult] = useState<QueryPreviewResult | null>(
    null,
  );
  const [queryLoading, setQueryLoading] = useState(false);

  const [auditEntries, setAuditEntries] = useState<SqlAuditEntry[]>([]);
  const [auditLoading, setAuditLoading] = useState(false);

  const [settingsEnabled, setSettingsEnabled] = useState(false);
  const [settingsModel, setSettingsModel] = useState("");
  const [settingsLoading, setSettingsLoading] = useState(false);

  const loadDataSources = useCallback(async () => {
    setLoading(true);
    try {
      const list = await dataSourcesApi.list();
      setDataSources(list);
    } catch (err) {
      messageApi.error(apiErrorMessage(err, t("dataSources.loadFailed")));
    } finally {
      setLoading(false);
    }
  }, [messageApi, t]);

  useEffect(() => {
    loadDataSources();
  }, [loadDataSources]);

  const loadDetail = useCallback(
    async (id: string) => {
      setDetailLoading(true);
      try {
        const source = await dataSourcesApi.get(id);
        setSelectedSource(source);
      } catch (err) {
        messageApi.error(apiErrorMessage(err, t("dataSources.loadFailed")));
      } finally {
        setDetailLoading(false);
      }
    },
    [messageApi, t],
  );

  useEffect(() => {
    if (selectedId) {
      loadDetail(selectedId);
    } else {
      setSelectedSource(null);
    }
  }, [selectedId, loadDetail]);

  const handleCreate = async (values: DataSourceFormValues) => {
    try {
      const created = await dataSourcesApi.create(values);
      messageApi.success(t("dataSources.created"));
      setCreateModalOpen(false);
      createForm.resetFields();
      await loadDataSources();
      setSelectedId(created.data_source_id || created.id);
    } catch (err) {
      messageApi.error(apiErrorMessage(err, t("dataSources.createFailed")));
    }
  };

  const handleUpdate = async (values: DataSourceFormValues) => {
    if (!selectedSource) return;
    try {
      await dataSourcesApi.update(
        selectedSource.data_source_id || selectedSource.id,
        values,
      );
      messageApi.success(t("dataSources.updated"));
      setEditModalOpen(false);
      await loadDetail(selectedSource.data_source_id || selectedSource.id);
      await loadDataSources();
    } catch (err) {
      messageApi.error(apiErrorMessage(err, t("dataSources.updateFailed")));
    }
  };

  const handleDelete = (source: DataSource) => {
    modal.confirm({
      title: t("dataSources.deleteConfirmTitle"),
      content: t("dataSources.deleteConfirmContent", { name: source.name }),
      okType: "danger",
      onOk: async () => {
        try {
          await dataSourcesApi.delete(source.data_source_id || source.id);
          messageApi.success(t("dataSources.deleted"));
          if (selectedId === (source.data_source_id || source.id)) {
            setSelectedId(null);
          }
          await loadDataSources();
        } catch (err) {
          messageApi.error(
            apiErrorMessage(err, t("dataSources.deleteFailed")),
          );
        }
      },
    });
  };

  const handleConnectionSave = async (values: ConnectionFormValues) => {
    if (!selectedSource) return;
    try {
      await dataSourcesApi.updateConnection(
        selectedSource.data_source_id || selectedSource.id,
        values,
      );
      messageApi.success(t("dataSources.connectionSaved"));
      setConnectionDrawerOpen(false);
    } catch (err) {
      messageApi.error(
        apiErrorMessage(err, t("dataSources.connectionSaveFailed")),
      );
    }
  };

  const handleTestConnection = async () => {
    if (!selectedSource) return;
    setTestingConnection(true);
    setConnectionTestResult(null);
    try {
      const result = await dataSourcesApi.testSavedConnection(
        selectedSource.data_source_id || selectedSource.id,
      );
      setConnectionTestResult({
        ok: result.ok,
        message: result.ok
          ? t("dataSources.connectionTestSuccess", {
              latency: result.latency_ms,
            })
          : result.error || t("dataSources.connectionTestFailed"),
      });
    } catch (err) {
      setConnectionTestResult({
        ok: false,
        message: apiErrorMessage(err, t("dataSources.connectionTestFailed")),
      });
    } finally {
      setTestingConnection(false);
    }
  };

  const handleRefreshSchema = async () => {
    if (!selectedSource) return;
    setRefreshingSchema(true);
    try {
      const schemaData = await dataSourcesApi.refreshSchema(
        selectedSource.data_source_id || selectedSource.id,
      );
      setSchema(schemaData.tables);
      messageApi.success(t("dataSources.schemaRefreshed"));
    } catch (err) {
      messageApi.error(
        apiErrorMessage(err, t("dataSources.schemaRefreshFailed")),
      );
    } finally {
      setRefreshingSchema(false);
    }
  };

  const handleLoadSchema = async () => {
    if (!selectedSource) return;
    setSchemaLoading(true);
    try {
      const [schemaData, allowlistData, annotationsData] = await Promise.all([
        dataSourcesApi.getSchema(selectedSource.data_source_id || selectedSource.id),
        dataSourcesApi.getAllowlist(selectedSource.data_source_id || selectedSource.id),
        dataSourcesApi.getAnnotations(selectedSource.data_source_id || selectedSource.id),
      ]);
      setSchema(schemaData.tables);
      setAllowlist(allowlistData);
      setAnnotations(annotationsData);
    } catch (err) {
      messageApi.error(apiErrorMessage(err, t("dataSources.loadFailed")));
    } finally {
      setSchemaLoading(false);
    }
  };

  const handleSaveAllowlist = async (tables: AllowedTable[]) => {
    if (!selectedSource) return;
    try {
      const updated = await dataSourcesApi.updateAllowlist(
        selectedSource.data_source_id || selectedSource.id,
        tables,
      );
      setAllowlist(updated);
      messageApi.success(t("dataSources.allowlistSaved"));
    } catch (err) {
      messageApi.error(apiErrorMessage(err, t("dataSources.allowlistSaveFailed")));
    }
  };

  const handleSaveAnnotations = async (anns: Annotation[]) => {
    if (!selectedSource) return;
    try {
      const updated = await dataSourcesApi.updateAnnotations(
        selectedSource.data_source_id || selectedSource.id,
        anns,
      );
      setAnnotations(updated);
      messageApi.success(t("dataSources.annotationsSaved"));
    } catch (err) {
      messageApi.error(
        apiErrorMessage(err, t("dataSources.annotationsSaveFailed")),
      );
    }
  };

  const handleQuery = async () => {
    if (!selectedSource || !queryText.trim()) return;
    setQueryLoading(true);
    setQueryResult(null);
    try {
      const result = await dataSourcesApi.queryPreview(
        selectedSource.data_source_id || selectedSource.id,
        queryText,
      );
      setQueryResult(result);
    } catch (err) {
      messageApi.error(apiErrorMessage(err, t("dataSources.queryFailed")));
    } finally {
      setQueryLoading(false);
    }
  };

  const handleLoadAudit = async () => {
    if (!selectedSource) return;
    setAuditLoading(true);
    try {
      const entries = await dataSourcesApi.getAudit(
        selectedSource.data_source_id || selectedSource.id,
        100,
      );
      setAuditEntries(entries);
    } catch (err) {
      messageApi.error(apiErrorMessage(err, t("dataSources.loadFailed")));
    } finally {
      setAuditLoading(false);
    }
  };

  const handleOpenConnection = async () => {
    if (!selectedSource) return;
    setConnectionDrawerOpen(true);
    setConnectionLoading(true);
    try {
      const conn = await dataSourcesApi.getConnection(
        selectedSource.data_source_id || selectedSource.id,
      );
      connectionForm.setFieldsValue({
        host: conn.host,
        port: conn.port,
        database_name: conn.database_name,
        schema_name: conn.schema_name,
        username: conn.username,
        ssl_mode: conn.ssl_mode,
      });
    } catch (err) {
      messageApi.error(apiErrorMessage(err, t("dataSources.loadFailed")));
    } finally {
      setConnectionLoading(false);
    }
  };

  const handleOpenSchema = async () => {
    if (!selectedSource) return;
    setSchemaDrawerOpen(true);
    await handleLoadSchema();
  };

  const handleOpenQuery = () => {
    if (!selectedSource) return;
    setQueryDrawerOpen(true);
    setQueryText("");
    setQueryResult(null);
  };

  const handleOpenAudit = async () => {
    if (!selectedSource) return;
    setAuditDrawerOpen(true);
    await handleLoadAudit();
  };

  const handleOpenSettings = async () => {
    setSettingsDrawerOpen(true);
    setSettingsLoading(true);
    try {
      const settings = await dataSourcesApi.getSettings();
      setSettingsEnabled(settings.enabled);
      setSettingsModel(settings.model || "");
    } catch (err) {
      messageApi.error(apiErrorMessage(err, t("dataSources.loadFailed")));
    } finally {
      setSettingsLoading(false);
    }
  };

  const handleSaveSettings = async () => {
    setSettingsLoading(true);
    try {
      await dataSourcesApi.updateSettings({
        enabled: settingsEnabled,
        model: settingsModel || null,
      });
      messageApi.success(t("dataSources.settingsSaved"));
      setSettingsDrawerOpen(false);
    } catch (err) {
      messageApi.error(apiErrorMessage(err, t("dataSources.settingsSaveFailed")));
    } finally {
      setSettingsLoading(false);
    }
  };

  const isOwner = selectedSource && canManageDataSource(selectedSource, user);

  return (
    <PageShell
      title={t("dataSources.title")}
      subtitle={t("dataSources.subtitle")}
    >
      <div style={{ display: "flex", gap: 16, height: "100%" }}>
        <Card
          style={{ width: isMobile ? "100%" : 360, flexShrink: 0 }}
          styles={{ body: { padding: 0 } }}
        >
          <div style={{ padding: 16, borderBottom: "1px solid #f0f0f0" }}>
            <Space style={{ width: "100%", justifyContent: "space-between" }}>
              <Button
                type="primary"
                icon={<Plus size={16} />}
                onClick={() => setCreateModalOpen(true)}
              >
                {t("dataSources.create")}
              </Button>
              {userCan(user, "data_source_settings") && (
                <Button
                  icon={<Settings size={16} />}
                  onClick={handleOpenSettings}
                >
                  {t("dataSources.settings")}
                </Button>
              )}
            </Space>
          </div>
          {loading ? (
            <div style={{ padding: 40, textAlign: "center" }}>
              <Spin />
            </div>
          ) : dataSources.length === 0 ? (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={t("dataSources.empty")}
              style={{ padding: 40 }}
            />
          ) : (
            <List
              dataSource={dataSources}
              renderItem={(source) => {
                const isSelected =
                  selectedId === (source.data_source_id || source.id);
                return (
                  <List.Item
                    style={{
                      padding: "12px 16px",
                      cursor: "pointer",
                      backgroundColor: isSelected ? "#f5f5f5" : undefined,
                    }}
                    onClick={() =>
                      setSelectedId(source.data_source_id || source.id)
                    }
                  >
                    <List.Item.Meta
                      avatar={
                        <Database
                          size={24}
                          style={{ color: "#1890ff" }}
                        />
                      }
                      title={
                        <Space>
                          <span>{source.name}</span>
                          <Tag>{source.engine}</Tag>
                          {source.shared && (
                            <Tag color="blue">{t("dataSources.shared")}</Tag>
                          )}
                        </Space>
                      }
                      description={
                        <Space direction="vertical" size={0}>
                          <Text type="secondary" style={{ fontSize: 12 }}>
                            {formatDataSourceOwner(source)}
                          </Text>
                          {source.description && (
                            <Text
                              type="secondary"
                              ellipsis
                              style={{ fontSize: 12 }}
                            >
                              {source.description}
                            </Text>
                          )}
                        </Space>
                      }
                    />
                  </List.Item>
                );
              }}
            />
          )}
        </Card>

        {!isMobile && (
          <Card style={{ flex: 1 }} styles={{ body: { padding: 24 } }}>
            {detailLoading ? (
              <div style={{ padding: 40, textAlign: "center" }}>
                <Spin />
              </div>
            ) : selectedSource ? (
              <div>
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    marginBottom: 16,
                  }}
                >
                  <Space>
                    <Database size={32} style={{ color: "#1890ff" }} />
                    <div>
                      <Title level={4} style={{ margin: 0 }}>
                        {selectedSource.name}
                      </Title>
                      <Space size={4}>
                        <CopyableResourceId
                          value={selectedSource.data_source_id || selectedSource.id}
                          label={t("dataSources.dataSourceId")}
                        />
                        <Tag>{selectedSource.engine}</Tag>
                        {selectedSource.shared && (
                          <Tag color="blue">{t("dataSources.shared")}</Tag>
                        )}
                      </Space>
                    </div>
                  </Space>
                  {isOwner && (
                    <Space>
                      <Button
                        icon={<Edit size={16} />}
                        onClick={() => {
                          editForm.setFieldsValue({
                            name: selectedSource.name,
                            engine: selectedSource.engine,
                            description: selectedSource.description,
                            default_open: selectedSource.default_open,
                            shared: selectedSource.shared,
                            max_rows: selectedSource.max_rows,
                            timeout_ms: selectedSource.timeout_ms,
                            context_char_budget:
                              selectedSource.context_char_budget,
                            allow_im_export: selectedSource.allow_im_export,
                          });
                          setEditModalOpen(true);
                        }}
                      >
                        {t("common.edit")}
                      </Button>
                      <Button
                        danger
                        icon={<Trash2 size={16} />}
                        onClick={() => handleDelete(selectedSource)}
                      >
                        {t("common.delete")}
                      </Button>
                    </Space>
                  )}
                </div>

                {selectedSource.description && (
                  <Paragraph style={{ marginBottom: 16 }}>
                    {selectedSource.description}
                  </Paragraph>
                )}

                <Descriptions
                  column={2}
                  bordered
                  size="small"
                  style={{ marginBottom: 24 }}
                >
                  <Descriptions.Item
                    label={t("dataSources.owner")}
                    span={2}
                  >
                    {formatDataSourceOwner(selectedSource)}
                  </Descriptions.Item>
                  <Descriptions.Item label={t("dataSources.engine")}>
                    {selectedSource.engine}
                  </Descriptions.Item>
                  <Descriptions.Item label={t("dataSources.maxRows")}>
                    {selectedSource.max_rows}
                  </Descriptions.Item>
                  <Descriptions.Item label={t("dataSources.timeoutMs")}>
                    {selectedSource.timeout_ms}ms
                  </Descriptions.Item>
                  <Descriptions.Item
                    label={t("dataSources.contextCharBudget")}
                  >
                    {selectedSource.context_char_budget}
                  </Descriptions.Item>
                  <Descriptions.Item label={t("dataSources.defaultOpen")}>
                    {selectedSource.default_open ? (
                      <Tag color="green">{t("common.enabled")}</Tag>
                    ) : (
                      <Tag>{t("common.disabled")}</Tag>
                    )}
                  </Descriptions.Item>
                  <Descriptions.Item label={t("dataSources.allowImExport")}>
                    {selectedSource.allow_im_export ? (
                      <Tag color="green">{t("common.enabled")}</Tag>
                    ) : (
                      <Tag>{t("common.disabled")}</Tag>
                    )}
                  </Descriptions.Item>
                  <Descriptions.Item label={t("common.createdAt")}>
                    {formatServerDateTime(selectedSource.created_at)}
                  </Descriptions.Item>
                  <Descriptions.Item label={t("common.updatedAt")}>
                    {formatServerDateTime(selectedSource.updated_at)}
                  </Descriptions.Item>
                </Descriptions>

                <Space wrap>
                  <Button
                    icon={<Settings size={16} />}
                    onClick={handleOpenConnection}
                  >
                    {t("dataSources.connection")}
                  </Button>
                  <Button
                    icon={<Database size={16} />}
                    onClick={handleOpenSchema}
                  >
                    {t("dataSources.schemaAndAllowlist")}
                  </Button>
                  <Button
                    icon={<Search size={16} />}
                    onClick={handleOpenQuery}
                  >
                    {t("dataSources.queryPreview")}
                  </Button>
                  <Button
                    icon={<History size={16} />}
                    onClick={handleOpenAudit}
                  >
                    {t("dataSources.auditLog")}
                  </Button>
                </Space>
              </div>
            ) : (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={t("dataSources.selectDataSource")}
              />
            )}
          </Card>
        )}
      </div>

      <Modal
        title={t("dataSources.create")}
        open={createModalOpen}
        onCancel={() => {
          setCreateModalOpen(false);
          createForm.resetFields();
        }}
        footer={null}
      >
        <Form
          form={createForm}
          layout="vertical"
          onFinish={handleCreate}
          initialValues={{
            engine: "postgres",
            max_rows: 100,
            timeout_ms: 5000,
            context_char_budget: 4000,
          }}
        >
          <Form.Item
            name="name"
            label={t("dataSources.name")}
            rules={[{ required: true, message: t("dataSources.nameRequired") }]}
          >
            <Input maxLength={200} />
          </Form.Item>
          <Form.Item
            name="engine"
            label={t("dataSources.engine")}
            rules={[{ required: true }]}
          >
            <Select
              options={[
                { value: "postgres", label: "PostgreSQL" },
                { value: "mysql", label: "MySQL" },
              ]}
            />
          </Form.Item>
          <Form.Item name="description" label={t("dataSources.description")}>
            <Input.TextArea maxLength={2000} rows={3} />
          </Form.Item>
          <Form.Item
            name="default_open"
            label={t("dataSources.defaultOpen")}
            valuePropName="checked"
          >
            <Switch />
          </Form.Item>
          <Form.Item
            name="shared"
            label={t("dataSources.shared")}
            valuePropName="checked"
          >
            <Switch />
          </Form.Item>
          <Form.Item name="max_rows" label={t("dataSources.maxRows")}>
            <InputNumber min={1} max={5000} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="timeout_ms" label={t("dataSources.timeoutMs")}>
            <InputNumber min={100} max={120000} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item
            name="context_char_budget"
            label={t("dataSources.contextCharBudget")}
          >
            <InputNumber min={200} max={100000} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item
            name="allow_im_export"
            label={t("dataSources.allowImExport")}
            valuePropName="checked"
          >
            <Switch />
          </Form.Item>
          <Form.Item>
            <Space>
              <Button type="primary" htmlType="submit">
                {t("common.create")}
              </Button>
              <Button onClick={() => setCreateModalOpen(false)}>
                {t("common.cancel")}
              </Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={t("dataSources.edit")}
        open={editModalOpen}
        onCancel={() => {
          setEditModalOpen(false);
          editForm.resetFields();
        }}
        footer={null}
      >
        <Form form={editForm} layout="vertical" onFinish={handleUpdate}>
          <Form.Item
            name="name"
            label={t("dataSources.name")}
            rules={[{ required: true, message: t("dataSources.nameRequired") }]}
          >
            <Input maxLength={200} />
          </Form.Item>
          <Form.Item name="description" label={t("dataSources.description")}>
            <Input.TextArea maxLength={2000} rows={3} />
          </Form.Item>
          <Form.Item
            name="default_open"
            label={t("dataSources.defaultOpen")}
            valuePropName="checked"
          >
            <Switch />
          </Form.Item>
          <Form.Item
            name="shared"
            label={t("dataSources.shared")}
            valuePropName="checked"
          >
            <Switch />
          </Form.Item>
          <Form.Item name="max_rows" label={t("dataSources.maxRows")}>
            <InputNumber min={1} max={5000} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="timeout_ms" label={t("dataSources.timeoutMs")}>
            <InputNumber min={100} max={120000} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item
            name="context_char_budget"
            label={t("dataSources.contextCharBudget")}
          >
            <InputNumber min={200} max={100000} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item
            name="allow_im_export"
            label={t("dataSources.allowImExport")}
            valuePropName="checked"
          >
            <Switch />
          </Form.Item>
          <Form.Item>
            <Space>
              <Button type="primary" htmlType="submit">
                {t("common.save")}
              </Button>
              <Button onClick={() => setEditModalOpen(false)}>
                {t("common.cancel")}
              </Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>

      <Drawer
        title={t("dataSources.connection")}
        open={connectionDrawerOpen}
        onClose={() => {
          setConnectionDrawerOpen(false);
          setConnectionTestResult(null);
        }}
        width={500}
      >
        {connectionLoading ? (
          <Spin />
        ) : (
          <Form
            form={connectionForm}
            layout="vertical"
            onFinish={handleConnectionSave}
            initialValues={{
              port: selectedSource?.engine === "mysql" ? 3306 : 5432,
            }}
          >
            <Form.Item
              name="host"
              label={t("dataSources.host")}
              rules={[{ required: true }]}
            >
              <Input maxLength={255} />
            </Form.Item>
            <Form.Item
              name="port"
              label={t("dataSources.port")}
              rules={[{ required: true }]}
            >
              <InputNumber min={1} max={65535} style={{ width: "100%" }} />
            </Form.Item>
            <Form.Item
              name="database_name"
              label={t("dataSources.databaseName")}
              rules={[{ required: true }]}
            >
              <Input maxLength={200} />
            </Form.Item>
            <Form.Item
              name="schema_name"
              label={t("dataSources.schemaName")}
            >
              <Input
                maxLength={200}
                placeholder={t("dataSources.schemaNamePlaceholder")}
              />
            </Form.Item>
            <Form.Item
              name="username"
              label={t("dataSources.username")}
              rules={[{ required: true }]}
            >
              <Input maxLength={200} />
            </Form.Item>
            <Form.Item
              name="password"
              label={t("dataSources.password")}
              extra={t("dataSources.passwordHint")}
            >
              <Input.Password maxLength={200} />
            </Form.Item>
            <Form.Item name="ssl_mode" label={t("dataSources.sslMode")}>
              <Input maxLength={32} />
            </Form.Item>
            {connectionTestResult && (
              <Alert
                type={connectionTestResult.ok ? "success" : "error"}
                message={connectionTestResult.message}
                style={{ marginBottom: 16 }}
              />
            )}
            <Form.Item>
              <Space>
                <Button type="primary" htmlType="submit">
                  {t("common.save")}
                </Button>
                <Button
                  onClick={handleTestConnection}
                  loading={testingConnection}
                >
                  {t("dataSources.testConnection")}
                </Button>
              </Space>
            </Form.Item>
          </Form>
        )}
      </Drawer>

      <Drawer
        title={t("dataSources.schemaAndAllowlist")}
        open={schemaDrawerOpen}
        onClose={() => setSchemaDrawerOpen(false)}
        width={700}
      >
        {schemaLoading ? (
          <Spin />
        ) : (
          <Tabs
            items={[
              {
                key: "schema",
                label: t("dataSources.schema"),
                children: (
                  <div>
                    <Button
                      icon={<RefreshCw size={16} />}
                      onClick={handleRefreshSchema}
                      loading={refreshingSchema}
                      style={{ marginBottom: 16 }}
                    >
                      {t("dataSources.refreshSchema")}
                    </Button>
                    {schema.length === 0 ? (
                      <Empty description={t("dataSources.noSchema")} />
                    ) : (
                      <List
                        dataSource={schema}
                        renderItem={(table) => (
                          <List.Item>
                            <List.Item.Meta
                              title={table.name}
                              description={
                                <div>
                                  {table.columns.map((col) => (
                                    <Tag key={col.name} style={{ margin: 2 }}>
                                      {col.name}: {col.type}
                                    </Tag>
                                  ))}
                                </div>
                              }
                            />
                          </List.Item>
                        )}
                      />
                    )}
                  </div>
                ),
              },
              {
                key: "allowlist",
                label: t("dataSources.allowlist"),
                children: (
                  <AllowlistEditor
                    schema={schema}
                    allowlist={allowlist}
                    onSave={handleSaveAllowlist}
                    t={t}
                  />
                ),
              },
              {
                key: "annotations",
                label: t("dataSources.annotations"),
                children: (
                  <AnnotationsEditor
                    schema={schema}
                    annotations={annotations}
                    onSave={handleSaveAnnotations}
                    t={t}
                  />
                ),
              },
            ]}
          />
        )}
      </Drawer>

      <Drawer
        title={t("dataSources.queryPreview")}
        open={queryDrawerOpen}
        onClose={() => setQueryDrawerOpen(false)}
        width={700}
      >
        <Input.TextArea
          value={queryText}
          onChange={(e) => setQueryText(e.target.value)}
          placeholder={t("dataSources.queryPlaceholder")}
          rows={3}
          style={{ marginBottom: 16 }}
        />
        <Button
          type="primary"
          onClick={handleQuery}
          loading={queryLoading}
          disabled={!queryText.trim()}
          style={{ marginBottom: 16 }}
        >
          {t("dataSources.runQuery")}
        </Button>
        {queryResult && <QueryResultDisplay result={queryResult} t={t} />}
      </Drawer>

      <Drawer
        title={t("dataSources.auditLog")}
        open={auditDrawerOpen}
        onClose={() => setAuditDrawerOpen(false)}
        width={800}
      >
        {auditLoading ? (
          <Spin />
        ) : (
          <Table
            dataSource={auditEntries}
            rowKey="id"
            size="small"
            pagination={{ pageSize: 20 }}
            columns={[
              {
                title: t("dataSources.auditTime"),
                dataIndex: "created_at",
                render: (ts: number) => formatServerDateTime(ts),
              },
              {
                title: t("dataSources.auditQuery"),
                dataIndex: "natural_query",
                ellipsis: true,
              },
              {
                title: t("dataSources.auditSql"),
                dataIndex: "generated_sql",
                ellipsis: true,
                render: (sql: string) => (
                  <Tooltip title={sql}>
                    <code style={{ fontSize: 11 }}>{sql}</code>
                  </Tooltip>
                ),
              },
              {
                title: t("dataSources.auditStatus"),
                dataIndex: "status",
                render: (status: string) => {
                  const color =
                    status === "ok"
                      ? "green"
                      : status === "empty"
                        ? "blue"
                        : status === "blocked"
                          ? "orange"
                          : "red";
                  return <Tag color={color}>{status}</Tag>;
                },
              },
              {
                title: t("dataSources.auditRows"),
                dataIndex: "row_count",
              },
              {
                title: t("dataSources.auditLatency"),
                dataIndex: "latency_ms",
                render: (ms: number | null) => (ms ? `${ms}ms` : "-"),
              },
            ]}
          />
        )}
      </Drawer>

      <Drawer
        title={t("dataSources.settings")}
        open={settingsDrawerOpen}
        onClose={() => setSettingsDrawerOpen(false)}
        width={500}
      >
        {settingsLoading ? (
          <Spin />
        ) : (
          <div>
            <div style={{ marginBottom: 24 }}>
              <div style={{ marginBottom: 8 }}>
                <strong>{t("dataSources.settingsEnabled")}</strong>
              </div>
              <Switch
                checked={settingsEnabled}
                onChange={setSettingsEnabled}
              />
            </div>
            <div style={{ marginBottom: 24 }}>
              <div style={{ marginBottom: 8 }}>
                <strong>{t("dataSources.settingsModel")}</strong>
              </div>
              <Input
                value={settingsModel}
                onChange={(e) => setSettingsModel(e.target.value)}
                placeholder="provider/model"
              />
              <Text type="secondary" style={{ fontSize: 12, marginTop: 4 }}>
                {t("dataSources.settingsModelHint")}
              </Text>
            </div>
            <Button
              type="primary"
              onClick={handleSaveSettings}
              loading={settingsLoading}
            >
              {t("common.save")}
            </Button>
          </div>
        )}
      </Drawer>
    </PageShell>
  );
}

function Paragraph({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return <div style={style}>{children}</div>;
}

function AllowlistEditor({
  schema,
  allowlist,
  onSave,
  t,
}: {
  schema: SchemaTable[];
  allowlist: AllowedTable[];
  onSave: (tables: AllowedTable[]) => Promise<void>;
  t: (key: string) => string;
}) {
  const [selected, setSelected] = useState<AllowedTable[]>(allowlist || []);

  useEffect(() => {
    setSelected(allowlist || []);
  }, [allowlist]);

  const handleToggleTable = (tableName: string) => {
    const exists = selected.find((t) => t.table_name === tableName);
    if (exists) {
      setSelected(selected.filter((t) => t.table_name !== tableName));
    } else {
      const table = schema.find((t) => t.name === tableName);
      if (table) {
        setSelected([
          ...selected,
          { table_name: tableName, columns: table.columns.map((c) => c.name) },
        ]);
      }
    }
  };

  const handleToggleColumn = (tableName: string, columnName: string) => {
    setSelected(
      selected.map((t) => {
        if (t.table_name === tableName) {
          const hasCol = t.columns.includes(columnName);
          return {
            ...t,
            columns: hasCol
              ? t.columns.filter((c) => c !== columnName)
              : [...t.columns, columnName],
          };
        }
        return t;
      }),
    );
  };

  return (
    <div>
      <Button
        type="primary"
        onClick={() => onSave(selected)}
        style={{ marginBottom: 16 }}
      >
        {t("common.save")}
      </Button>
      {schema.length === 0 ? (
        <Empty description={t("dataSources.noSchema")} />
      ) : (
        <List
          dataSource={schema}
          renderItem={(table) => {
            const isSelected = selected.some(
              (t) => t.table_name === table.name,
            );
            const selectedCols =
              selected.find((t) => t.table_name === table.name)?.columns || [];
            return (
              <List.Item>
                <div style={{ width: "100%" }}>
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      marginBottom: 8,
                    }}
                  >
                    <Switch
                      checked={isSelected}
                      onChange={() => handleToggleTable(table.name)}
                      size="small"
                      style={{ marginRight: 8 }}
                    />
                    <strong>{table.name}</strong>
                  </div>
                  {isSelected && (
                    <div style={{ marginLeft: 32 }}>
                      {table.columns.map((col) => (
                        <Tag
                          key={col.name}
                          style={{
                            margin: 2,
                            cursor: "pointer",
                            backgroundColor: selectedCols.includes(col.name)
                              ? "#1890ff"
                              : undefined,
                            color: selectedCols.includes(col.name)
                              ? "#fff"
                              : undefined,
                          }}
                          onClick={() =>
                            handleToggleColumn(table.name, col.name)
                          }
                        >
                          {col.name}
                        </Tag>
                      ))}
                    </div>
                  )}
                </div>
              </List.Item>
            );
          }}
        />
      )}
    </div>
  );
}

function AnnotationsEditor({
  schema,
  annotations,
  onSave,
  t,
}: {
  schema: SchemaTable[];
  annotations: Annotation[];
  onSave: (annotations: Annotation[]) => Promise<void>;
  t: (key: string) => string;
}) {
  const [anns, setAnns] = useState<Annotation[]>(annotations || []);

  useEffect(() => {
    setAnns(annotations || []);
  }, [annotations]);

  const handleAddTableAnnotation = (tableName: string) => {
    setAnns([
      ...anns,
      { scope: "table", table_name: tableName, column_name: "", note: "" },
    ]);
  };

  const handleAddColumnAnnotation = (tableName: string, columnName: string) => {
    setAnns([
      ...anns,
      { scope: "column", table_name: tableName, column_name: columnName, note: "" },
    ]);
  };

  const handleUpdateNote = (index: number, note: string) => {
    setAnns(anns.map((a, i) => (i === index ? { ...a, note } : a)));
  };

  const handleRemove = (index: number) => {
    setAnns(anns.filter((_, i) => i !== index));
  };

  return (
    <div>
      <Button
        type="primary"
        onClick={() => onSave(anns)}
        style={{ marginBottom: 16 }}
      >
        {t("common.save")}
      </Button>
      {schema.length === 0 ? (
        <Empty description={t("dataSources.noSchema")} />
      ) : (
        <List
          dataSource={schema}
          renderItem={(table) => {
            const tableAnns = anns.filter(
              (a) => a.table_name === table.name && a.scope === "table",
            );
            const colAnns = anns.filter(
              (a) => a.table_name === table.name && a.scope === "column",
            );
            return (
              <List.Item>
                <div style={{ width: "100%" }}>
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      marginBottom: 8,
                    }}
                  >
                    <strong>{table.name}</strong>
                    <Button
                      size="small"
                      onClick={() => handleAddTableAnnotation(table.name)}
                    >
                      {t("dataSources.addTableAnnotation")}
                    </Button>
                  </div>
                  {tableAnns.map((ann, idx) => {
                    const annIdx = anns.indexOf(ann);
                    return (
                      <div
                        key={idx}
                        style={{
                          display: "flex",
                          gap: 8,
                          marginBottom: 8,
                          alignItems: "center",
                        }}
                      >
                        <Tag color="blue">{t("dataSources.tableScope")}</Tag>
                        <Input
                          value={ann.note}
                          onChange={(e) =>
                            handleUpdateNote(annIdx, e.target.value)
                          }
                          placeholder={t("dataSources.annotationPlaceholder")}
                        />
                        <Button
                          danger
                          size="small"
                          onClick={() => handleRemove(annIdx)}
                        >
                          <X size={14} />
                        </Button>
                      </div>
                    );
                  })}
                  <div style={{ marginLeft: 16, marginTop: 8 }}>
                    {table.columns.map((col) => {
                      const colAnnsForCol = colAnns.filter(
                        (a) => a.column_name === col.name,
                      );
                      return (
                        <div key={col.name} style={{ marginBottom: 8 }}>
                          <div
                            style={{
                              display: "flex",
                              justifyContent: "space-between",
                              alignItems: "center",
                            }}
                          >
                            <Text code>{col.name}</Text>
                            <Text type="secondary" style={{ fontSize: 11 }}>
                              {col.type}
                            </Text>
                            <Button
                              size="small"
                              onClick={() =>
                                handleAddColumnAnnotation(table.name, col.name)
                              }
                            >
                              {t("dataSources.addColumnAnnotation")}
                            </Button>
                          </div>
                          {colAnnsForCol.map((ann, idx) => {
                            const annIdx = anns.indexOf(ann);
                            return (
                              <div
                                key={idx}
                                style={{
                                  display: "flex",
                                  gap: 8,
                                  marginTop: 4,
                                  marginLeft: 16,
                                  alignItems: "center",
                                }}
                              >
                                <Tag color="green">
                                  {t("dataSources.columnScope")}
                                </Tag>
                                <Input
                                  value={ann.note}
                                  onChange={(e) =>
                                    handleUpdateNote(annIdx, e.target.value)
                                  }
                                  placeholder={t(
                                    "dataSources.annotationPlaceholder",
                                  )}
                                />
                                <Button
                                  danger
                                  size="small"
                                  onClick={() => handleRemove(annIdx)}
                                >
                                  <X size={14} />
                                </Button>
                              </div>
                            );
                          })}
                        </div>
                      );
                    })}
                  </div>
                </div>
              </List.Item>
            );
          }}
        />
      )}
    </div>
  );
}

function QueryResultDisplay({
  result,
  t,
}: {
  result: QueryPreviewResult;
  t: (key: string) => string;
}) {
  const statusColor =
    result.status === "ok"
      ? "green"
      : result.status === "empty"
        ? "blue"
        : result.status === "blocked"
          ? "orange"
          : "red";

  return (
    <div>
      <Alert
        type={
          result.status === "ok"
            ? "success"
            : result.status === "empty"
              ? "info"
              : result.status === "blocked"
                ? "warning"
                : "error"
        }
        message={
          <Space>
            <Tag color={statusColor}>{result.status}</Tag>
            <span>
              {result.status === "ok" &&
                `${t("dataSources.querySuccess")}`.replace("{{count}}", String(result.row_count))}
              {result.status === "empty" && t("dataSources.queryEmpty")}
              {result.status === "blocked" && t("dataSources.queryBlocked")}
              {result.status === "error" && t("dataSources.queryError")}
            </span>
          </Space>
        }
      />
      {result.text && (
        <div style={{ marginTop: 16, marginBottom: 16 }}>
          <div style={{ marginBottom: 8 }}>
            <strong>{t("dataSources.queryAnswer")}</strong>
          </div>
          <div
            style={{
              backgroundColor: "#f9f9f9",
              padding: 12,
              borderRadius: 4,
              whiteSpace: "pre-wrap",
              lineHeight: 1.6,
            }}
          >
            {result.text}
          </div>
        </div>
      )}
      <div style={{ marginBottom: 16 }}>
        <div style={{ marginBottom: 8 }}>
          <strong>{t("dataSources.generatedSql")}</strong>
        </div>
        <pre
          style={{
            backgroundColor: "#f5f5f5",
            padding: 12,
            borderRadius: 4,
            fontSize: 12,
            overflow: "auto",
          }}
        >
          {result.generated_sql}
        </pre>
      </div>
      <Space size="middle" style={{ fontSize: 12, color: "#888" }}>
        <span>{result.row_count} {t("dataSources.auditRows")}</span>
        <span>{result.latency_ms}ms</span>
        <span>{result.attempts} {t("dataSources.queryAttempts")}</span>
      </Space>
    </div>
  );
}
