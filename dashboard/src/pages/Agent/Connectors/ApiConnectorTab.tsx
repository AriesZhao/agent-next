import { useCallback, useEffect, useState } from "react";
import { Button, Drawer, Form, Input, Select, Switch, Spin, Table, Modal } from "antd";
import { message } from "@/utils/antdMessage";

import { Plus, RefreshCw, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  connectorsApi,
  type ApiConnector,
  type ApiConnectorSpec,
} from "../../../api/modules/connectors";
import { apiErrorMessage } from "../../../utils/apiError";
import styles from "./index.module.less";

export function ApiConnectorTab() {
  const { t } = useTranslation();
  const [loading, setLoading] = useState(true);
  const [connectors, setConnectors] = useState<ApiConnector[]>([]);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editingConnector, setEditingConnector] = useState<ApiConnector | null>(
    null,
  );
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);
  const [deletingName, setDeletingName] = useState<string | null>(null);
  const [authType, setAuthType] = useState<string>("bearer");
  const [toolsModalOpen, setToolsModalOpen] = useState(false);
  const [selectedConnector, setSelectedConnector] = useState<ApiConnector | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const result = await connectorsApi.listApiConnectors();
      setConnectors(result.connectors);
    } catch (e) {
      console.error(e);
      message.error(
        apiErrorMessage(
          e,
          t("apiConnector.loadFailed", "加载 API 连接器失败"),
          t,
        ),
      );
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleCreate = () => {
    setEditingConnector(null);
    form.resetFields();
    form.setFieldsValue({
      enabled: true,
      default_open: false,
      shared: false,
      auth_type: "bearer",
    });
    setAuthType("bearer");
    setDrawerOpen(true);
  };

  const handleEdit = (connector: ApiConnector) => {
    setEditingConnector(connector);
    const currentAuthType = connector.auth?.type || "bearer";
    form.setFieldsValue({
      name: connector.name,
      display_name: connector.display_name,
      description: connector.description,
      base_url: connector.base_url,
      enabled: connector.enabled,
      default_open: connector.default_open,
      shared: connector.shared,
      auth_type: currentAuthType,
      tools_json: JSON.stringify(connector.tools_summary || [], null, 2),
    });
    setAuthType(currentAuthType);
    setDrawerOpen(true);
  };

  const handleDelete = async (name: string) => {
    setDeletingName(name);
    try {
      await connectorsApi.deleteApiConnector(name);
      message.success(t("apiConnector.deleteSuccess", "已删除"));
      await load();
    } catch (e) {
      console.error(e);
      message.error(
        apiErrorMessage(e, t("apiConnector.deleteFailed", "删除失败"), t),
      );
    } finally {
      setDeletingName(null);
    }
  };

  const handleViewTools = (connector: ApiConnector) => {
    setSelectedConnector(connector);
    setToolsModalOpen(true);
  };

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields();
      setSaving(true);

      let auth: Record<string, unknown>;
      if (values.auth_type === "bearer") {
        auth = { type: "bearer", token: values.auth_token || "" };
      } else if (values.auth_type === "api_key") {
        auth = {
          type: "api_key",
          api_key: values.auth_api_key || "",
          api_key_header: values.auth_api_key_header || "X-API-Key",
        };
      } else if (values.auth_type === "basic") {
        auth = {
          type: "basic",
          username: values.auth_username || "",
          password: values.auth_password || "",
        };
      } else {
        auth = { type: "none" };
      }

      let tools: Array<Record<string, unknown>> = [];
      if (values.tools_json) {
        try {
          tools = JSON.parse(values.tools_json);
        } catch {
          message.error(t("apiConnector.toolsJsonInvalid", "工具定义 JSON 格式错误"));
          setSaving(false);
          return;
        }
      }

      const spec: ApiConnectorSpec = {
        name: values.name,
        display_name: values.display_name,
        description: values.description,
        base_url: values.base_url,
        auth,
        tools,
        enabled: values.enabled,
        default_open: values.default_open,
        shared: values.shared,
      };

      await connectorsApi.putApiConnector(spec);
      message.success(
        editingConnector
          ? t("apiConnector.updateSuccess", "已更新")
          : t("apiConnector.createSuccess", "已创建"),
      );
      setDrawerOpen(false);
      await load();
    } catch (e) {
      console.error(e);
      message.error(
        apiErrorMessage(e, t("apiConnector.saveFailed", "保存失败"), t),
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <div className={styles.listToolbar}>
        <span className={styles.listToolbarMeta}>
          {t("apiConnector.summary", {
            count: connectors.length,
            defaultValue: `共 ${connectors.length} 个 API 连接器`,
          })}
        </span>
        <div style={{ display: "flex", gap: 8 }}>
          <Button
            icon={<RefreshCw size={14} />}
            loading={loading}
            onClick={() => void load()}
          >
            {t("common.refresh")}
          </Button>
          <Button type="primary" icon={<Plus size={14} />} onClick={handleCreate}>
            {t("apiConnector.create", "新建 API 连接器")}
          </Button>
        </div>
      </div>

      {loading ? (
        <div className={styles.loadingState}>
          <Spin />
        </div>
      ) : connectors.length === 0 ? (
        <div className={styles.emptyState}>
          <p>{t("apiConnector.empty", "尚未配置 API 连接器")}</p>
          <Button type="primary" icon={<Plus size={14} />} onClick={handleCreate}>
            {t("apiConnector.createFirst", "创建第一个 API 连接器")}
          </Button>
        </div>
      ) : (
        <div className={styles.typeGrid}>
          {connectors.map((connector) => (
            <div key={connector.name} className={styles.card}>
              <div className={styles.cardBody} onClick={() => handleViewTools(connector)} style={{ cursor: "pointer" }}>
                <div className={styles.cardTitle}>{connector.display_name}</div>
                <div className={styles.cardDescription}>
                  {connector.description || connector.base_url}
                </div>
                <div className={styles.cardMeta}>
                  <span>
                    {t("apiConnector.toolCount", {
                      count: connector.tool_count,
                      defaultValue: `${connector.tool_count} 个工具`,
                    })}
                  </span>
                  {!connector.enabled && (
                    <span className={styles.badgeDisabled}>
                      {t("common.disabled")}
                    </span>
                  )}
                  {connector.shared && (
                    <span className={styles.badgeShared}>
                      {t("common.shared")}
                    </span>
                  )}
                </div>
              </div>
              <div className={styles.cardActions}>
                <Button size="small" onClick={() => handleEdit(connector)}>
                  {t("common.edit")}
                </Button>
                <Button
                  size="small"
                  danger
                  icon={<Trash2 size={14} />}
                  loading={deletingName === connector.name}
                  onClick={() => void handleDelete(connector.name)}
                >
                  {t("common.delete")}
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}

      <Drawer
        title={
          editingConnector
            ? t("apiConnector.edit", "编辑 API 连接器")
            : t("apiConnector.create", "新建 API 连接器")
        }
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        width={480}
        destroyOnHidden
        footer={
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
            <Button onClick={() => setDrawerOpen(false)}>
              {t("common.cancel")}
            </Button>
            <Button type="primary" loading={saving} onClick={handleSubmit}>
              {t("common.save")}
            </Button>
          </div>
        }
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="name"
            label={t("apiConnector.name", "连接器名称")}
            rules={[
              { required: true },
              { pattern: /^[a-z0-9-]+$/, message: t("apiConnector.namePattern", "只能包含小写字母、数字和连字符") },
            ]}
            extra={t("apiConnector.nameHint", "唯一标识符，创建后不可修改")}
          >
            <Input disabled={!!editingConnector} placeholder="order-system" />
          </Form.Item>

          <Form.Item
            name="display_name"
            label={t("apiConnector.displayName", "显示名称")}
            rules={[{ required: true }]}
          >
            <Input placeholder={t("apiConnector.displayNamePlaceholder", "订单系统")} />
          </Form.Item>

          <Form.Item
            name="description"
            label={t("apiConnector.description", "描述")}
          >
            <Input.TextArea rows={2} maxLength={200} showCount />
          </Form.Item>

          <Form.Item
            name="base_url"
            label={t("apiConnector.baseUrl", "Base URL")}
            rules={[{ required: true }, { type: "url" }]}
            extra={t("apiConnector.baseUrlHint", "例如 https://api.example.com/v1")}
          >
            <Input placeholder="https://api.example.com/v1" />
          </Form.Item>

          <Form.Item
            name="auth_type"
            label={t("apiConnector.authType", "认证方式")}
            rules={[{ required: true }]}
          >
            <Select onChange={(value) => setAuthType(value)}>
              <Select.Option value="bearer">Bearer Token</Select.Option>
              <Select.Option value="api_key">API Key</Select.Option>
              <Select.Option value="basic">Basic Auth</Select.Option>
            </Select>
          </Form.Item>

          {authType === "bearer" && (
            <Form.Item
              name="auth_token"
              label={t("apiConnector.authToken", "Token")}
              rules={[{ required: true, message: t("apiConnector.tokenRequired", "请输入 Token") }]}
            >
              <Input.Password placeholder="sk-xxxx" />
            </Form.Item>
          )}

          {authType === "api_key" && (
            <>
              <Form.Item
                name="auth_api_key"
                label={t("apiConnector.apiKey", "API Key")}
                rules={[{ required: true, message: t("apiConnector.apiKeyRequired", "请输入 API Key") }]}
              >
                <Input.Password placeholder="your-api-key" />
              </Form.Item>
              <Form.Item
                name="auth_api_key_header"
                label={t("apiConnector.apiKeyHeader", "Header 名称")}
                initialValue="X-API-Key"
              >
                <Input placeholder="X-API-Key" />
              </Form.Item>
            </>
          )}

          {authType === "basic" && (
            <>
              <Form.Item
                name="auth_username"
                label={t("apiConnector.username", "用户名")}
                rules={[{ required: true, message: t("apiConnector.usernameRequired", "请输入用户名") }]}
              >
                <Input placeholder="username" />
              </Form.Item>
              <Form.Item
                name="auth_password"
                label={t("apiConnector.password", "密码")}
                rules={[{ required: true, message: t("apiConnector.passwordRequired", "请输入密码") }]}
              >
                <Input.Password placeholder="password" />
              </Form.Item>
            </>
          )}

          <Form.Item
            name="enabled"
            label={t("apiConnector.enabled", "启用")}
            valuePropName="checked"
          >
            <Switch />
          </Form.Item>

          <Form.Item
            name="default_open"
            label={t("apiConnector.defaultOpen", "默认开启")}
            valuePropName="checked"
            extra={t(
              "apiConnector.defaultOpenHint",
              "开启后新对话默认携带此连接器的工具",
            )}
          >
            <Switch />
          </Form.Item>

          <Form.Item
            name="shared"
            label={t("apiConnector.shared", "共享")}
            valuePropName="checked"
            extra={t(
              "apiConnector.sharedHint",
              "共享后其他用户可以使用，但不能查看配置",
            )}
          >
            <Switch />
          </Form.Item>

          <Form.Item
            name="tools_json"
            label={t("apiConnector.toolsJson", "工具定义 (JSON)")}
            extra={t(
              "apiConnector.toolsJsonHint",
              "定义 API 工具列表，每个工具需包含 name、method、path 字段",
            )}
          >
            <Input.TextArea
              rows={8}
              placeholder={`[
  {
    "name": "list_items",
    "description": "查询列表",
    "method": "GET",
    "path": "/items",
    "parameters": {}
  }
]`}
              style={{ fontFamily: "monospace", fontSize: 12 }}
            />
          </Form.Item>
        </Form>
      </Drawer>

      <Modal
        title={
          selectedConnector
            ? `${selectedConnector.display_name} - API 工具列表`
            : "API 工具列表"
        }
        open={toolsModalOpen}
        onCancel={() => setToolsModalOpen(false)}
        footer={null}
        width={800}
      >
        {selectedConnector && (
          <Table
            dataSource={selectedConnector.tools_summary}
            rowKey="name"
            pagination={false}
            size="small"
            columns={[
              {
                title: "工具名称",
                dataIndex: "name",
                key: "name",
                width: 200,
              },
              {
                title: "描述",
                dataIndex: "description",
                key: "description",
              },
              {
                title: "方法",
                dataIndex: "method",
                key: "method",
                width: 80,
                render: (method: string) => (
                  <span
                    style={{
                      color:
                        method === "GET"
                          ? "#52c41a"
                          : method === "POST"
                            ? "#1890ff"
                            : method === "PUT"
                              ? "#faad14"
                              : method === "DELETE"
                                ? "#ff4d4f"
                                : "#666",
                      fontWeight: "bold",
                    }}
                  >
                    {method}
                  </span>
                ),
              },
              {
                title: "路径",
                dataIndex: "path",
                key: "path",
                width: 200,
                render: (path: string) => (
                  <code style={{ fontSize: 12 }}>{path}</code>
                ),
              },
            ]}
          />
        )}
      </Modal>
    </>
  );
}
