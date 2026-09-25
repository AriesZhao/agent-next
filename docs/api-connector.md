# API 连接器（API Connector）设计方案

> 状态：设计定稿，待评审。本文件是评审与实现基线，不含业务代码。
> 范围：新增「API 连接器（API Connector）」能力，让用户通过配置化方式将业务系统的 REST API
> 注册为 agent 可调用的 MCP 工具，无需编写 Python 适配器代码。

## 1. 背景与目标

Octop 现有的连接器体系（`src/octop/infra/connectors/`）支持两类集成：
- **内置连接器**（`catalog.py`）：23 个预置系统，分 `remote`（直连 MCP）和 `gateway`（硬编码 Python 适配器）两种模式。
- **自定义 MCP**（`custom_mcp.py`）：用户配置外部 MCP Server 的 transport/url/command，Octop 作为客户端连接。

两者的共同限制：**业务系统必须已经支持 MCP 协议，或者由开发者编写 Python 适配器**。
对于大量只有 REST API 的业务系统（订单、库存、CRM 等），用户无法自行接入。

目标：
- 用户在 Dashboard 配置业务系统的 base URL、认证方式、工具定义（HTTP 请求模板），系统自动生成 MCP 工具注入 agent。
- 支持服务凭证 + 身份头传递，让业务系统知道「谁在通过 Agent 操作」。
- 连接器级启用/禁用开关，控制 agent 对业务系统的访问。
- 全链路可审计、凭证加密存储、跨渠道（Dashboard / IM / CLI）一致。

非目标（v1 明确不做）：OpenAPI spec 自动导入、字段级权限控制、数据范围过滤、
OAuth 用户委托、Token Exchange、敏感操作确认弹窗、写回/批量操作优化。

## 2. 关键决策（先发散后收敛的结论）

| 议题 | 决策 | 被排除项与理由 |
|------|------|---------------|
| 产品形态 | **扩展连接器体系**，新增 `kind="api-connector"` | 不做独立资源（如 `data_sources` 那样）：API 连接器本质是「工具来源」，和现有连接器语义一致，独立资源增加概念负担 |
| 存储模式 | **虚拟实例模式**（仿 `custom-mcp`，单行存多个连接器定义） | 排除独立表：v1 连接器数量有限，单行加密存储复用现有 `credential_blob` + Fernet 机制，零迁移成本 |
| 身份传递 | **服务凭证 + 身份头**（`X-On-Behalf-Of` 等可配置 header） | 排除 OAuth 用户委托（v1 太重，需用户逐系统授权）；排除 Token Exchange（需企业 SSO 基础设施） |
| 权限模型 | **连接器级开关**（启用/禁用） | 排除操作级/字段级权限（v1 过度设计）；排除用户×Agent 双维度交集（v1 无此需求） |
| 工具注入 | **平行于 `inject_missing_gateway_tools` 新增 `inject_api_connector_tools`**，走 `agent.inject_mcp_tools()` | 排除复用 `gateway/langchain.py`：API 连接器不是 gateway adapter（无 `ConnectorCatalogEntry`、不走 `handle_mcp_request`），需独立 builder + httpx 直调 |
| 认证方式 | **Bearer Token / API Key / Basic Auth** | 排除 OAuth2 客户端模式（v1 场景不足）；排除 mTLS/证书认证 |
| 响应处理 | **JSON 提取 + 截断**（`data_path` + `truncate_at`） | 排除 XML/protobuf 支持（v1 只做 JSON）；排除自动分页 |
| 审计 | **复用 `audit_log` 表**（append-only，按 AGENTS.md 例外无需 string id） | 排除独立审计表：v1 审计字段少，复用现有表足够 |

## 3. 概念与命名

- 功能名：**API 连接器 / API Connector**。在 Dashboard 连接器页面中作为独立 tab 或分类展示。
- 内部 kind：`api-connector`（与 `custom-mcp` 平级的虚拟 kind）。
- **两层 ID 结构**（仿 `custom-mcp`）：
  - **DB 层**：每个用户一行 `connectors` 记录，`instance_id` = ULID（自动生成），`kind = "api-connector"`。该行 `credential_blob` 存储该用户所有 API 连接器定义的加密 JSON。
  - **展开层**：`expand_api_connector_instances()` 将单行展开为多个虚拟实例，每个连接器一个合成实例 ID `api:{connector_name}`（仿 `custom:{server_name}`）。API 端点操作的是展开后的虚拟实例。
- MCP server name：`api__{connector_name}__{instance_id_hash}`。
- 工具名格式：`{connector_name}__{tool_name}`（如 `order_system__query_orders`），由系统自动生成，用户不可自定义。
- 工具名冲突检测：注册时检查是否与已有 gateway / custom-mcp / 其他 api-connector 工具重名，重名则拒绝创建（提示用户更换 connector_name）。
- 工具描述前缀：`[{display_name}] {tool_description}`（帮助 LLM 识别工具归属）。

## 4. 架构总览与数据流

```
用户在 Dashboard 配置 API 连接器
  └─ 填写 base_url / auth / identity / tools 定义
       └─ 加密存入 connectors 表 credential_blob（kind="api-connector"，单行/用户）

Agent 启动（一次性注入）
  └─ AgentManager._post_start_agent()
       └─ builder.inject_api_connector_tools()  【新增，平行 inject_missing_gateway_tools】
            1. 加载用户可见的所有 api-connector 定义（expand_api_connector_instances）
            2. 过滤 enabled=True 的连接器
            3. 对每个连接器，遍历 tools 定义 → api_connector/langchain.py 生成 StructuredTool
            4. agent.inject_mcp_tools(tools)
            └─ agent 侧看到标准 MCP 工具，无感知

每回合补缺（对话中新增/刷新）
  └─ AgentManager._attach_api_connector_tools()  【新增，平行 _attach_gateway_tools】
       1. 检查 agent 已有工具名集合，找出缺失的 api-connector 工具
       2. 刷新凭证（ensure_fresh_api_connectors）
       3. 补缺注入 → agent.inject_mcp_tools(missing_tools)

用户: "帮我查一下 pending 状态的订单"
  └─ Agent (LLM) 选择调用 order_system__query_orders 工具
       └─ StructuredTool.func(**kwargs)  【闭包内】
            1. 从 ContextVar 获取 ActorContext（user_id / user_name / agent_id）
            2. 查找连接器定义 + 工具定义
            3. 构建 HTTP 请求（method/path/params/body）
            4. 注入认证头（Bearer / API Key / Basic）
            5. 注入身份头（X-On-Behalf-Of 等，来自 ActorContext）
            6. 检查频率限制（max_calls_per_turn / max_calls_per_minute）
            7. httpx.Client 执行请求
            8. 按 response_mapping 提取 + 截断响应
            9. 写审计日志（AuditRepo.write）
            └─ 返回文本给 LLM → LLM 组织语言回答用户
```

## 5. 数据模型

**不新增数据库表。** 复用现有 `connectors` 表，采用与 `custom-mcp` 完全一致的虚拟实例模式：

- **DB 行**：每用户一行，`kind = "api-connector"`，`instance_id` = ULID（`new_ulid()` 自动生成）。
- `credential_blob` = Fernet 加密的 JSON，结构如下（`wrap_api_connectors()` / `extract_api_connectors()` 封装序列化）：

```jsonc
{
  "connectors": {
    "order-system": {
      // ── 基本信息 ──
      "display_name": "订单系统",
      "description": "订单查询与管理",
      "base_url": "https://order.internal.com/api",
      "enabled": true,
      "default_open": true,
      "shared": false,

      // ── 认证配置 ──
      "auth": {
        "type": "bearer",          // "bearer" | "api_key" | "basic"
        "token": "sk-xxxx"
        // "api_key": "xxx",       // api_key 模式
        // "api_key_header": "X-API-Key",
        // "username": "xxx",      // basic 模式
        // "password": "xxx"
      },

      // ── 身份传递 ──
      "identity": {
        "enabled": true,
        "header_name": "X-On-Behalf-Of",
        "name_header": "X-On-Behalf-Of-Name",
        "channel_header": "X-Request-Channel"
      },

      // ── 全局请求默认值 ──
      "default_headers": {
        "Content-Type": "application/json",
        "Accept": "application/json"
      },
      "timeout": 30,

      // ── 工具定义 ──
      "tools": [
        {
          "name": "query_orders",
          "description": "查询订单列表，可按状态、日期筛选",
          "method": "GET",
          "path": "/orders",
          "parameters": {
            "status": {
              "type": "string",
              "description": "订单状态: pending/shipped/completed/cancelled",
              "in": "query",
              "enum": ["pending", "shipped", "completed", "cancelled"]
            },
            "page": {
              "type": "integer",
              "description": "页码，默认 1",
              "in": "query",
              "default": 1
            }
          },
          "response_mapping": {
            "data_path": "data.items",
            "truncate_at": 5000
          }
        },
        {
          "name": "get_order_detail",
          "description": "根据订单号查询订单详情",
          "method": "GET",
          "path": "/orders/{order_id}",
          "parameters": {
            "order_id": {
              "type": "string",
              "description": "订单号",
              "in": "path"
            }
          }
        },
        {
          "name": "create_order",
          "description": "创建新订单",
          "method": "POST",
          "path": "/orders",
          "parameters": {
            "customer_name": {
              "type": "string",
              "description": "客户名称",
              "in": "body"
            },
            "items": {
              "type": "array",
              "description": "订单商品列表",
              "in": "body",
              "items": {
                "type": "object",
                "properties": {
                  "product_id": {"type": "string"},
                  "quantity": {"type": "integer"},
                  "price": {"type": "number"}
                },
                "required": ["product_id", "quantity"]
              }
            }
          },
          "sensitive": true
        }
      ]
    }
  }
}
```

### 5.1 连接器定义字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `display_name` | string | 是 | 显示名称，≤64 字符 |
| `description` | string | 否 | 描述 |
| `base_url` | string | 是 | API 基础 URL，必须 https（loopback 允许 http） |
| `enabled` | bool | 否 | 默认 true |
| `default_open` | bool | 否 | 默认 true。**优先级**：agent 显式配置 > 连接器 `default_open`（agent 明确排除的连接器不强制挂载） |
| `shared` | bool | 否 | 默认 false，是否共享给其他用户 |
| `auth` | object | 是 | 认证配置（见下表） |
| `identity` | object | 否 | 身份传递配置（见下表） |
| `default_headers` | object | 否 | 全局请求头 |
| `timeout` | integer | 否 | 请求超时秒数，默认 30，上限 120 |
| `max_calls_per_turn` | integer | 否 | 每对话最大调用次数，默认 20。超限返回错误，防 agent 循环调用 |
| `max_calls_per_minute` | integer | 否 | 每分钟最大调用次数，默认 60。超限返回错误 |
| `tools` | array | 是 | 工具定义列表 |

### 5.2 auth 字段

| type | 必填子字段 | 生成的 HTTP 头 |
|------|-----------|---------------|
| `bearer` | `token` | `Authorization: Bearer {token}` |
| `api_key` | `api_key`，可选 `api_key_header`（默认 `X-API-Key`） | `{api_key_header}: {api_key}` |
| `basic` | `username`, `password` | `Authorization: Basic {base64(user:pass)}` |

### 5.3 identity 字段

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | true | 是否注入身份头 |
| `header_name` | `X-On-Behalf-Of` | 携带用户 ID 的头名 |
| `name_header` | `X-On-Behalf-Of-Name` | 携带用户名的头名 |
| `channel_header` | `X-Request-Channel` | 携带请求渠道的头名（值为 `agent`） |

### 5.4 工具参数字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `type` | string | 是 | `string` / `integer` / `number` / `boolean` / `array` / `object` |
| `description` | string | 否 | 参数描述（LLM 据此决定传值） |
| `in` | string | 否 | 参数位置：`query`（默认）/ `path` / `header` / `body` |
| `required` | bool | 否 | 是否必填 |
| `default` | any | 否 | 默认值 |
| `enum` | array | 否 | 枚举值列表 |
| `items` | object | 否 | array 类型的元素 schema（完整 JSON Schema，含 `properties`/`required`，递归转换） |

### 5.4b 工具级 `sensitive` 标记

工具定义中的 `sensitive: true`（默认 `false`）表示该工具执行写操作或敏感读操作。v1 行为：
- **审计加重**：`payload` 中额外标记 `"sensitive": true`，便于审计筛选。
- **返回文案前缀**：工具返回文本前追加 `[自动执行的操作]`，提示 LLM 和用户该操作已实际生效。
- 不触发确认弹窗（v1 非目标），但为 P3+ 的确认机制预留标记。

### 5.5 response_mapping 字段

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `data_path` | null（返回完整响应） | 从 JSON 响应中提取数据的路径，如 `data.items` |
| `truncate_at` | 8000 | 返回给 LLM 的最大字符数 |

## 6. 模块边界与新增/改动文件

分层沿用 AGENTS.md §5：域在 `infra/connectors/api_connector/`，HTTP 在 `api/routers/connectors.py`（扩展现有路由），`infra` 不 import `api`/`cli`。

### 新增

```
src/octop/infra/connectors/api_connector/
  __init__.py
  schema.py          # 数据结构定义 + 校验（normalize / validate）
  adapter.py         # list_tools / call_tool / probe_credentials
  http_executor.py   # HTTP 请求构建与执行（build_request / apply_auth / execute）
  identity.py        # 身份头注入逻辑
  audit.py           # 审计日志记录（写 audit_log）
  langchain.py       # StructuredTool 生成（仿 gateway/langchain.py）
```

### 改动

| 文件 | 改动内容 |
|------|---------|
| `infra/connectors/service.py` | 新增 `get_api_connectors(user_id)` / `put_api_connectors(user_id, connectors)` / `patch_api_connector(user_id, name, ...)` — 仿 `custom_mcp` 系列方法。凭证加解密复用 `crypto.py` |
| `infra/connectors/builder.py` | `_iter_active_connectors()` 跳过 `api-connector` kind（不构建 HTTP MCP spec）；新增 `inject_api_connector_tools(agent, svc=..., connector_repo=..., user_id=..., agent_id=...)` — 平行 `inject_missing_gateway_tools`，调用 `api_connector/langchain.py` 生成工具 |
| `infra/agents/manager.py` | `_post_start_agent()` 新增调用 `inject_api_connector_tools()`；新增 `_attach_api_connector_tools()` 平行 `_attach_gateway_tools()`，在 `prepare_chat_mcp` 每回合路径中补缺注入 |
| `api/routers/connectors.py` | 新增 API 连接器 CRUD 端点（§10） |
| `api/app.py` / `api/openapi_meta.py` | 如新增独立路由文件则注册；否则复用现有 connectors 路由 |
| `i18n/en.json` / `zh.json` | 新增 `api_connector.*` 命名空间 |
| `dashboard/` | 新增 API 连接器管理 UI（§12） |

## 7. 身份传递机制

### 7.1 设计原则

Agent 调用业务系统 API 时，业务系统需要知道「谁在通过 Agent 操作」。v1 采用**服务凭证 + 身份头**模式：
- Agent 用自己的服务凭证（Bearer Token / API Key）认证 → 业务系统信任来自 Octop 的请求。
- 同时在可配置的 HTTP Header 中携带当前用户身份 → 业务系统据此做数据权限过滤和审计。

### 7.2 运行时身份注入

```python
# identity.py
def inject_identity_headers(
    headers: dict[str, str],
    identity_config: IdentityConfig,
    *,
    user_id: str,
    user_name: str,
) -> None:
    if not identity_config.enabled:
        return
    headers[identity_config.header_name] = user_id
    if identity_config.name_header:
        headers[identity_config.name_header] = user_name
    if identity_config.channel_header:
        headers[identity_config.channel_header] = "agent"
```

### 7.3 上下文获取（ContextVar 方案）

工具闭包（`StructuredTool.func`）执行时无法直接访问 HTTP 请求上下文。采用 `contextvars.ContextVar` 传递身份信息：

```python
# identity.py
from contextvars import ContextVar
from dataclasses import dataclass

@dataclass(frozen=True)
class ActorContext:
    user_id: int
    username: str
    agent_id: str

_current_actor: ContextVar[ActorContext | None] = ContextVar("_current_actor", default=None)

def set_actor_context(ctx: ActorContext) -> None:
    """在对话处理入口（processor / chat router）设置，回合结束自动清理。"""
    _current_actor.set(ctx)

def get_actor_context() -> ActorContext | None:
    return _current_actor.get()
```

**设置时机**：在 `infra/gateway/process/processor.py` 处理 IM 消息时、以及 `api/routers/chat.py` 处理 Dashboard 消息时，从已认证的 `current_user` + 当前 `agent_id` 构造 `ActorContext` 并 `set_actor_context()`。

**读取时机**：`api_connector/langchain.py` 生成的工具闭包内，调用 `get_actor_context()` 获取当前用户信息，传给 `inject_identity_headers()`。

**清理**：`ContextVar` 是 token-based 的，回合结束自动失效（无需显式清理）。

### 7.4 业务系统侧

业务系统需做的最小改动（中间件/拦截器）：
1. 验证请求来自受信任的 Agent 服务（校验 Bearer Token / API Key）。
2. 读取身份头，确定实际操作人。
3. 按实际操作人的权限过滤数据。
4. 审计日志记录：`{user} via Agent`。

## 8. 管线细则

### 8.1 工具生成（list_tools）

对每个 enabled 的 API 连接器，遍历其 `tools` 定义，生成 MCP 兼容的工具描述：

```python
# adapter.py
def _param_to_json_schema(param: dict) -> dict:
    """递归将工具参数定义转为 JSON Schema（含 array items 嵌套）。"""
    prop: dict = {"type": param["type"], "description": param.get("description", "")}
    if param.get("enum"):
        prop["enum"] = param["enum"]
    if param.get("items"):
        items = param["items"]
        if items.get("type") == "object" and items.get("properties"):
            nested_props = {}
            nested_req = []
            for k, v in items["properties"].items():
                nested_props[k] = _param_to_json_schema(v)
                if v.get("required"):
                    nested_req.append(k)
            prop["items"] = {
                "type": "object",
                "properties": nested_props,
                "required": nested_req,
            }
        else:
            prop["items"] = {"type": items.get("type", "string")}
    return prop


def list_tools(connector_name: str, connector_def: dict) -> list[dict]:
    tools = []
    display_name = connector_def.get("display_name", connector_name)
    for tool_def in connector_def.get("tools", []):
        properties = {}
        required = []
        for param_name, param in tool_def.get("parameters", {}).items():
            properties[param_name] = _param_to_json_schema(param)
            if param.get("required"):
                required.append(param_name)

        tools.append({
            "name": f"{connector_name}__{tool_def['name']}",
            "description": f"[{display_name}] {tool_def.get('description', tool_def['name'])}",
            "inputSchema": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        })
    return tools
```

### 8.2 请求构建（http_executor）

参数按 `in` 字段分发到正确位置：

| `in` 值 | 行为 |
|---------|------|
| `path` | 替换 URL 中的 `{param_name}` |
| `query` | 追加到 URL query string |
| `header` | 追加到请求头 |
| `body` | 放入 JSON request body |

默认值处理：用户未传参时，使用 `default` 值；无默认值且 `required` 时，返回错误。

### 8.3 响应处理

1. 按 `response_mapping.data_path` 从 JSON 响应中提取目标数据（点号分隔路径，如 `data.items` → `response["data"]["items"]`）。
2. 序列化为 JSON 字符串（`ensure_ascii=False`）。
3. 按 `truncate_at` 截断，超出部分追加 `...（已截断，共 {total_length} 字符）`。
4. 非 JSON 响应 → 直接返回文本，同样受 `truncate_at` 约束。

### 8.4 错误处理

| 类别 | 处理 | 面向用户文案 |
|------|------|------------|
| 连接器未找到/工具未定义 | 不重试，返回错误 | 「API 连接器配置异常，请联系管理员。」 |
| 认证失败（401/403） | 不重试 | 「API 认证失败，请检查连接器凭证配置。」 |
| 连接超时/网络错误 | 不重试 | 「业务系统暂时无法访问，请稍后重试。」 |
| 业务错误（4xx/5xx） | 不重试，返回响应体摘要 | 「业务系统返回错误：{status_code}」 |
| 响应过大 | 截断后返回 | 正常返回截断后的数据 |

所有错误均写审计日志。凭证/URL 等敏感信息不出现在面向用户的文案中（对非 owner 脱敏）。

### 8.5 审计日志

每次 API 调用写入 `audit_log` 表（复用现有 append-only 表，`AuditRepo.write()`）。

字段映射（`AuditRepo` 签名为 `actor, action, target, payload`）：

| AuditRepo 字段 | 值 |
|----------------|-----|
| `actor` | `username`（如 `"zhangsan"`）；系统级为 `"_system"` |
| `action` | `"api_connector_call"` |
| `target` | `"{connector_name}:{tool_name}"`（如 `"order-system:query_orders"`） |
| `payload` | JSON 字符串，包含完整调用细节（见下） |

`payload` JSON 结构：

```json
{
  "agent_id": "sales-bot",
  "http_method": "GET",
  "http_url": "https://order.internal.com/api/orders?status=pending",
  "http_status": 200,
  "duration_ms": 123,
  "identity_propagated": true,
  "truncated": false,
  "error": null
}
```

`audit.py` 封装 `record_api_connector_call()` 函数，内部调用 `audit_repo.user_event(actor=username, action="api_connector_call", target=..., payload=json.dumps(...))`。

## 9. 依赖

- 无新增 Python 依赖。`httpx` 已在核心依赖中。
- 复用现有 `infra/connectors/crypto.py`（Fernet 加密）。
- 复用现有 `audit_log` 表。

## 10. API（扩展现有 `api/routers/connectors.py`）

| 端点 | 作用 |
|------|------|
| `GET /api/connectors/api-connectors` | 列出当前用户可见的所有 API 连接器（展开每个连接器为独立条目） |
| `POST /api/connectors/api-connectors` | 创建或更新 API 连接器（传入完整定义，含 auth/tools） |
| `GET /api/connectors/api-connectors/{name}` | 获取单个 API 连接器详情（凭证掩码） |
| `PATCH /api/connectors/api-connectors/{name}` | 部分更新（enabled / default_open / shared / display_name） |
| `DELETE /api/connectors/api-connectors/{name}` | 删除指定 API 连接器 |
| `POST /api/connectors/api-connectors/test` | 测试连接器连通性：向 `base_url` 发 HEAD 或 GET 请求（不带业务参数），验证 HTTP 可达 + 凭证有效（2xx/4xx 区分连通但认证失败 vs 网络不通） |

- 全部 `Depends(get_server)` + `require_permission("connectors")`。
- 凭证（token / api_key / password）只写不回显；GET 响应中 auth 字段返回掩码预览（如 `Bearer sk-****xxxx`）。
- typed `response_model`；路由 `summary`；改后核对 `/api/docs` 可读性。

### 10.1 请求/响应示例

**POST /api/connectors/api-connectors**

```json
{
  "name": "order-system",
  "display_name": "订单系统",
  "description": "订单查询与管理",
  "base_url": "https://order.internal.com/api",
  "auth": {
    "type": "bearer",
    "token": "sk-xxxx"
  },
  "identity": {
    "enabled": true,
    "header_name": "X-On-Behalf-Of"
  },
  "tools": [
    {
      "name": "query_orders",
      "description": "查询订单列表",
      "method": "GET",
      "path": "/orders",
      "parameters": {
        "status": {
          "type": "string",
          "description": "订单状态",
          "in": "query"
        }
      }
    }
  ]
}
```

**GET /api/connectors/api-connectors/order-system**（响应，凭证掩码）

```json
{
  "name": "order-system",
  "display_name": "订单系统",
  "base_url": "https://order.internal.com/api",
  "enabled": true,
  "default_open": true,
  "shared": false,
  "auth": {
    "type": "bearer",
    "token_preview": "sk-****xxxx"
  },
  "identity": {
    "enabled": true,
    "header_name": "X-On-Behalf-Of",
    "name_header": "X-On-Behalf-Of-Name",
    "channel_header": "X-Request-Channel"
  },
  "tool_count": 1,
  "tools_summary": [
    {"name": "query_orders", "method": "GET", "path": "/orders"}
  ]
}
```

## 11. i18n

新增 `api_connector.*` 命名空间到 `i18n/en.json` + `zh.json`：

| 键 | 用途 |
|----|------|
| `api_connector.title` | UI 标题「API 连接器」 |
| `api_connector.create` | 创建按钮 |
| `api_connector.test_success` | 测试成功提示 |
| `api_connector.test_failed` | 测试失败提示 |
| `api_connector.error.auth_failed` | 认证失败文案 |
| `api_connector.error.connection_failed` | 连接失败文案 |
| `api_connector.error.not_found` | 连接器不存在 |
| `api_connector.error.invalid_config` | 配置校验失败 |
| `api_connector.error.rate_limited` | 调用频率超限 |
| `api_connector.error.name_conflict` | 工具名冲突 |
| `api_connector.tool.sensitive_warning` | 敏感操作提示（`[自动执行的操作]`） |

验收：`uv run pytest tests/unit/i18n -q`。

## 12. 前端（`dashboard/`，非 `src/octop/dashboard/`）

- **连接器页面新增 tab/分类**：「API 连接器」，与现有内置连接器、自定义 MCP 并列。
- **列表视图**：卡片式展示每个 API 连接器（名称、base_url、工具数量、启用状态开关）。
- **创建/编辑表单**：
  - 基本信息：名称、描述、Base URL
  - 认证方式选择（Bearer / API Key / Basic）+ 对应字段
  - 身份传递配置（开关 + header 名称自定义）
  - 工具定义：可增删的工具卡片，每个卡片含 name/description/method/path/参数表/sensitive 开关
  - 参数表：每行 name/type/in/required/description，支持 array items 子 schema
- **测试连接**：点击后向 `base_url` 发探测请求（HEAD/GET），验证 HTTP 可达 + 凭证有效，展示结果。
- **凭证掩码**：编辑时 token/key 显示为 `****`，清空后重新输入才更新。
- `api/modules/apiConnectors.ts` 走 `request.ts`；locales 同步。
- 改后：`cd dashboard && npx tsc -b` + `make build-frontend`。

## 13. 测试矩阵

- **单测**（纯逻辑，mock httpx）：
  - `schema.py`：校验（name 格式、base_url 格式/SSRF、auth 字段完整性、tool 参数合法性、timeout 上限）。
  - `http_executor.py`：请求构建（path/query/header/body 参数分发、bearer/api_key/basic 认证头生成、default_headers 合并、identity 头注入）。
  - `adapter.py`：list_tools（工具名/描述生成）、call_tool（mock httpx，验证请求正确性、响应提取、截断、错误分类）。
  - `audit.py`：断言每次调用写审计。
- **集成测**：
  - API 端点：创建/列表/详情（凭证掩码）/部分更新/删除/测试连通（mock httpx）。
  - 工具注入：agent 启动 → `inject_api_connector_tools` → agent 工具列表包含生成的工具。
  - 端到端：配置连接器 → agent 对话 → LLM 选择工具 → mock 业务系统返回 → 结果注入上下文。
- **跨平台**（AGENTS.md §7）：httpx mock、路径用 `tmp_path`/`pathlib`、无 POSIX 假设。

## 14. 分期

- **P1 后端闭环**：`api_connector/` 模块 + service 扩展 + API 端点 + 工具注入 + 审计 + 测试。
  → curl/API 可端到端：创建连接器 → agent 启动 → 工具可用 → 调用返回结果。
- **P2 前端**：Dashboard API 连接器管理 UI（列表/创建/编辑/测试）。
  → 浏览器手工走查 + `npx tsc -b` + `make build-frontend`。
- **P3+（暂缓）**：OpenAPI spec 自动导入、字段级权限、数据范围过滤、OAuth 用户委托、
  Token Exchange、敏感操作确认弹窗、XML 响应支持、自动分页。

## 15. 安全清单（实现必须逐条满足）

1. 凭证 Fernet 加密存储，GET 响应永远掩码，内存不常驻。
2. `base_url` 校验：loopback 允许 http，公网必须 https + SSRF 防护（复用 `validate_https_url`）。
3. 请求超时强制（`timeout` 上限 120s），防止 agent 循环调用拖垮业务系统。
4. 每次调用写审计（含 user/agent/connector/tool/method/url/status/耗时）。
5. 面向用户的错误文案不泄露凭证、内部 URL 结构、响应体敏感内容。
6. 共享连接器：非 owner 可挂载使用但**不可改**配置；错误文案脱敏。
7. 工具名/参数名严格校验（仅允许 `[a-zA-Z0-9_-]`），防注入。
8. 响应截断（`truncate_at`）防止大响应撑爆对话上下文。
9. **频率限制**：`max_calls_per_turn`（默认 20）+ `max_calls_per_minute`（默认 60），超限返回「调用频率超限，请稍后重试」，防 agent 死循环打爆业务系统。
10. **工具名冲突检测**：创建连接器时检查 `{connector_name}__{tool_name}` 是否与已有工具重名，重名拒绝。

## 16. 主要风险与未决

- **工具数量膨胀**：一个连接器可能定义几十个工具，全部注入会增加 LLM 工具选择负担。
  缓解：`default_open` 控制是否自动挂载；未来可按需挂载（用户/对话级选择）。
- **参数描述质量**：LLM 依赖 `description` 决定如何传参，描述不清会导致调用失败。
  缓解：前端表单引导用户写清楚描述；未来可从 OpenAPI spec 自动填充。
- **业务系统兼容性**：不同系统的认证方式、请求格式、响应结构差异大。
  缓解：v1 覆盖最常见的 JSON REST + Bearer/API Key/Basic Auth；复杂场景走自定义 MCP 或 Gateway Adapter。
- **身份头信任**：业务系统必须信任来自 Octop 的身份头，否则身份传递形同虚设。
  缓解：文档明确说明业务系统侧需做的最小改动；未来升级到 Token Exchange 可解决信任问题。

## 17. 开发环境备注

本项目真实运行环境在 **WSL**（`/home/aries/.venvs/octop`），Windows 侧 `.venv`（drvfs）软链损坏不可用；
跑测试/命令走 WSL（`uv run pytest`）。httpx 调用全 mock，不依赖真实业务系统。

## 18. P1 实施与验收清单（step → verify）

> 每步做完跑对应 verify；全部通过后的**总验收（ship bar）= `make all` 绿**（format-all+lint+typecheck+test），
> 并含 §13 跨平台要求（httpx 全 mock、无 POSIX 假设）。命令均在 **WSL** 用 `uv run` 跑。

| # | 步骤 | verify（可执行的验收） |
|---|------|----------------------|
| 1 | `schema.py`：数据结构 + 校验（name/base_url/auth/tools/timeout/rate limits） | `test_schema.py`：合法定义通过、非法 name/URL/auth 缺失/超时超限/频率上限为负 均拒绝 全绿 |
| 2 | `http_executor.py`：请求构建 + 认证 + 身份头注入 | `test_http_executor.py`：path/query/header/body 分发正确、bearer/api_key/basic 头正确、identity 头注入正确、default_headers 合并正确 全绿 |
| 3 | `adapter.py`：list_tools（工具名/描述生成 + 递归 schema） | `test_adapter_list.py`：工具名 `{connector}__{tool}`、描述前缀 `[{display_name}]`、inputSchema 正确、array items 嵌套 object 递归转换正确 全绿 |
| 4 | `adapter.py`：call_tool（mock httpx，响应提取/截断/错误分类/频率限制） | `test_adapter_call.py`：正常响应提取、data_path 提取、截断标记、401/403/超时/5xx 错误分类、超限返回错误 全绿 |
| 5 | `identity.py`：ContextVar ActorContext 设置/读取 | `test_identity.py`：set → get 正确、未设置时返回 None、sensitive 标记工具返回文案含前缀 全绿 |
| 6 | `audit.py`：每次调用写 audit_log（字段映射正确） | `test_audit.py`：成功/失败/超时 均写审计、action=`api_connector_call`、target=`{connector}:{tool}`、payload JSON 字段完整 全绿 |
| 7 | `langchain.py`：StructuredTool 生成 | `test_langchain.py`：生成的 StructuredTool name/description/args_schema 正确、闭包内可获取 ActorContext 全绿 |
| 8 | `service.py` 扩展：get/put/patch api_connectors（仿 custom_mcp） | `test_service_api_connector.py`：CRUD 正确、凭证加密存储、读取掩码、shared 可见性、工具名冲突拒绝 全绿 |
| 9 | `builder.py` + `manager.py`：启动注入 + 每回合补缺 | 集成测：配置 api-connector → agent 启动 → `agent.tools` 包含生成的工具；新回合补缺注入正确 全绿 |
| 10 | API 端点（CRUD + test） | `tests/integration` API 连接器用例绿：创建/列表/详情(掩码)/更新/删除/test(mock base_url 连通) |
| 11 | i18n：`api_connector.*`（en/zh 对齐） | `uv run pytest tests/unit/i18n -q` 绿 |
| 12 | OpenAPI 可读性 | 手核 `/api/docs`：新路由有 tag/summary、typed request/response |
| 13 | 总验收 | `make all` 绿；端到端 smoke：创建连接器 → agent 挂载 → 工具调用 → 结果返回 |

### P1 端到端验收场景（Definition of Done）

1. 通过 API 创建一个 API 连接器（base_url + bearer token + 2 个 GET 工具 + 1 个 POST 工具）→ 创建成功，GET 响应凭证掩码。
2. 新建 agent → agent 启动后工具列表包含 `{connector_name}__{tool_name}` 格式的工具。
3. 模拟调用 GET 工具（mock httpx 返回 JSON）→ 按 `data_path` 提取 + `truncate_at` 截断 → 结果正确返回。
4. 模拟调用 POST 工具（sensitive=true）→ 请求 body 正确构建 → 响应返回 → 返回文本含 `[自动执行的操作]` 前缀 → 审计 payload 含 `"sensitive": true`。
5. 模拟认证失败（mock httpx 返回 401）→ 返回「API 认证失败」文案 → 审计记录 action=`api_connector_call`，payload 含 http_status=401。
6. 模拟超时（mock httpx 抛 TimeoutException）→ 返回「业务系统暂时无法访问」文案 → 审计记录 error。
7. 共享场景：用户 A 创建 shared=true 的连接器 → 用户 B 可见并可挂载使用，但不可修改配置。
8. 身份传递：设置 ActorContext 后调用工具 → 验证请求头包含 `X-On-Behalf-Of: {user_id}` + `X-On-Behalf-Of-Name: {username}` + `X-Request-Channel: agent`。
9. 频率限制：连接器配置 `max_calls_per_turn=3` → 第 4 次调用返回「调用频率超限」错误 → 审计记录 error。
10. 工具名冲突：创建两个连接器使用相同 connector_name → 第二个被拒绝，返回工具名冲突错误。

### P2 前端验收场景（Definition of Done）

1. 连接器页面出现「API 连接器」tab，列表展示已配置的连接器卡片（名称/base_url/工具数/启用开关）。
2. 创建表单：填写基本信息 → 选择认证方式 → 配置身份头 → 添加工具（name/method/path/参数表）→ 保存成功。
3. 编辑表单：凭证显示为 `****`，清空后可重新输入；其他字段可修改。
4. 测试连接：点击后展示成功/失败结果。
5. 删除连接器：二次确认 → 删除成功 → 列表刷新。
6. 构建门槛：`cd dashboard && npx tsc -b` 绿、`make build-frontend` 成功、locales en/zh 键对齐。
