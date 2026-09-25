# 结构化数据源知识库（NL2SQL）设计方案

> 状态：设计定稿，待评审。本文件是评审与实现基线，不含业务代码。
> 范围：新增「数据源（Data Source）」一等资源，让 agent 以自然语言经 NL2SQL 只读查询外部
> PostgreSQL / MySQL 数据库，并把结果作为上下文注入对话。

## 1. 背景与目标

Octop 现有「知识库」是纯非结构化向量 RAG：文件 → 定长字符 chunk → embedding → 余弦检索
（`src/octop/infra/knowledge/`）。用户希望增加「结构化知识库」，真实诉求是**连接外部数据库、
用自然语言查询（NL2SQL）**，而非导入 CSV/Excel。

目标：
- 用户配置一个数据库连接（PostgreSQL / MySQL），勾选允许查询的表/列，可为表/列写业务口径注释。
- agent 在被挂载该数据源时，能把用户的自然语言问题转成 SQL，经安全护栏在只读事务执行，
  返回结果行注入上下文（复用现有「工具返回文本 → 注入」形态）。
- 全链路安全、可审计、跨渠道（Dashboard / IM / CLI）一致、可控上下文占用。

非目标（v1 明确不做）：写回/DDL、跨库 join、text-to-SQL 之外的图表生成、schema-RAG、
带状态的多步结果翻页工具、把 SQL 原语暴露给主 agent（见 §2）。

## 2. 关键决策（先发散后收敛的结论）

| 议题 | 决策 | 被排除项与理由 |
|------|------|---------------|
| 产品形态 | **独立 `data_sources` 资源**，与知识库平级 | 不做「知识库子类型」：失败模式/风险/权限差异太大，混在一个名词下误导用户 |
| 执行路线 | **路线 A：内置 NL2SQL 注入管线**，SQL 对主 agent 隐藏 | 排除路线 B（暴露 `run_sql` 给主 agent）：schema 进主上下文爆 token、run_sql 攻击面、行为随主模型强弱漂移 |
| 引擎 | **PostgreSQL + MySQL** | — |
| 调用模型 | **全同步管线**（executor 线程内 sync `.invoke` + sync 驱动） | 无需 `asyncpg`/`aiomysql`；已实测 `BaseChatModel` 具备 sync `.invoke` |
| 迭代/重试 | **分层**：非法 SQL 类内部重试 1 次；改写重问交外层 agent 复用同一工具 | 排除无界内部多轮循环（不可控成本/难归因） |
| 结果渲染 | **形状感知**：小/聚合全给；大结果有序样例+数值聚合+截断标记 | 排除「全给再截断」：中间截断误导、浪费 token |
| 可见性 | **允许 shared**（审计 + 透明，不做运行时确认弹窗） | — |
| 准确度投入 | **v1 带表/列业务口径注释** | 这是 NL2SQL 准确率最大杠杆，值得 v1 做 |
| schema 规模 | **allowlist 兼作 schema scope**（未勾表不进 prompt） | 排除 v1 引入 schema-RAG |
| subagent / skills | **v1 都不建**；subagent 留作「分析型多步查询」的升级点 | skills 在路线 A 下无可教流程；subagent 对单问单表是纯开销 |

## 3. 概念与命名

- 资源名：**数据源 / Data Source**（`data_sources`）。图标/文案与「知识库」区分，避免用户以为是传文档。
- 公开 string id：`data_source_id`（ULID/short id），内部整型 `id` 为 PK —— 遵循 AGENTS.md §7 资源表 `id` + `{entity}_id` 约定。
- 工具名：`query_data_source`（单发复合工具，隐藏 SQL）。

## 4. 架构总览与数据流

```
用户/agent 问题（自然语言）
  └─ query_data_source 工具（主 agent 调用，per-turn 已挂载 data_source_ids）
       └─ infra/data_sources/pipeline.py  【executor 同步线程内】
            1. 读取该 data_source 的 allowlist 表 + 列 + 口径注释（schema store）
            2. generate: build_probe_chat_model(provider, model).invoke(prompt[含 schema 子集+few-shot+方言+约束]) → SQL
            3. guard: sqlglot 解析 → SELECT-only / 单语句 / 表列 ⊆ allowlist / 注入或钳制 LIMIT
            4. execute: sync 驱动（psycopg / PyMySQL）只读事务 + 超时 → 行集
            5. render: 形状感知（全量 or 有序样例+数值聚合+截断标记）；大结果落 workspace 文件，返回 digest+路径
            6. audit: 写 data_source_sql_audit
            └─ 错误分类分流（见 §8）
       └─ 返回注入文本（受独立注入预算约束）；失败/空信号让主 agent 改写问题重调本工具
```

上下文隔离要点（§7）：**schema、few-shot、多列宽表原始行只存在于步骤 1-6 的一次性调用里**，
绝不进主 agent 对话；只有步骤 render 出的紧凑 digest 进主上下文。

## 5. 数据模型与迁移

**当前 schema 版本 = v15**（最新迁移 `015_sso_provider_kind`；AGENTS.md 里「v==7」为过时描述）。
新增迁移对 `016_data_source_nl2sql.sql`（SQLite）+ `016_data_source_nl2sql.pg.sql`（PostgreSQL），
成对、canonical、可折叠进未发布的 016。迁移后把**当前版本断言**从 15 抬到 16（注意两种写法 `assert v == 15` 与 `assert version == 15`），涉及文件：
`tests/unit/db/test_db_pool.py`（多处，含 100/172/309/334/369/452/658）、`test_repo_knowledge.py`、
`test_skill_packages_repo.py`、`test_skill_package_icons.py`、`test_published_experts_repo.py`、
`test_clip_thread_title.py`、`test_agent_profile_columns.py`。
**例外（不可改）**：`test_db_pool.py:614` 的 `assert version == 7` 是「手工只应用到 007」的**中间态断言**，非当前版本，保持 7 不动。

### 5.1 表

- `data_sources`（主资源表）
  - `id` INTEGER PK（AUTOINCREMENT / IDENTITY）
  - `data_source_id` TEXT NOT NULL UNIQUE
  - `owner_user_id` INTEGER NOT NULL
  - `name` TEXT NOT NULL, `description` TEXT NOT NULL DEFAULT ''
  - `engine` TEXT NOT NULL（`postgres` | `mysql`）
  - `default_open` INTEGER NOT NULL DEFAULT 0, `shared` INTEGER NOT NULL DEFAULT 0
  - `icon_name` TEXT NOT NULL DEFAULT ''
  - `max_rows` INTEGER NOT NULL DEFAULT 100, `timeout_ms` INTEGER NOT NULL DEFAULT 5000
  - `context_char_budget` INTEGER NOT NULL DEFAULT 4000（数据源专属注入预算）
  - `allow_im_export` INTEGER NOT NULL DEFAULT 0（默认不允许将完整结果以附件外发到 IM，见 §8.7）
  - `created_at`, `updated_at` INTEGER
  - `UNIQUE(owner_user_id, name)`
- `data_source_connections`（1:1 扩展，键 `data_source_id`，按 AGENTS.md「1:1 extension」例外）
  - `data_source_id` TEXT PK REFERENCES `data_sources(data_source_id)` ON DELETE CASCADE
  - `host`, `port` INTEGER, `database_name`, `schema_name`（pg namespace，默认 public）
  - `username`, `password_blob` BLOB/TEXT（Fernet 密文，复用 `infra/connectors/crypto.py` 模式）
  - `ssl_mode` TEXT, `last_tested_at` INTEGER
- `data_source_allowed_tables`（allowlist；亦可用 JSON 存于 connections，二选一，倾向独立表便于校验）
  - `data_source_id` FK, `table_name` TEXT, `columns_json` TEXT DEFAULT '[]'（该表允许的列）
  - `PK(data_source_id, table_name)`
- `data_source_annotations`（表/列业务口径注释，v1）
  - `data_source_id` FK, `scope` TEXT（`table`|`column`）, `table_name`, `column_name`
  - `note` TEXT（口径说明+同义词）, `PK(data_source_id, scope, table_name, column_name)`
- `data_source_schemas`（introspection 缓存）
  - `data_source_id` PK, `tables_json` TEXT, `refreshed_at` INTEGER
- `data_source_sql_audit`（append-only，按 AGENTS.md 例外允许无 string id）
  - `id` PK, `data_source_id`, `actor_user_id` NULL, `thread_id` NULL, `agent_id` NULL
  - `natural_query` TEXT, `generated_sql` TEXT, `executed_sql` TEXT
  - `status` TEXT（`ok`|`empty`|`blocked`|`error`）, `row_count` INTEGER, `latency_ms` INTEGER
  - `error_message` TEXT, `attempt` INTEGER, `created_at` INTEGER

### 5.2 Row / Repo

- `infra/db/repos/data_sources.py`：`DataSourceRow` / `DataSourceConnectionRow` / 各 repo，**纯 SQL**。
- 凭证列不进 `to_public_payload`；读取掩码。

## 6. 模块边界与新增/改动文件

分层沿用 AGENTS.md §5：域在 `infra/data_sources/`，HTTP 在 `api/routers/`，repos 只 SQL，工具文本走 i18n，`infra` 不 import `api`/`cli`。

新增：
- `infra/data_sources/`
  - `service.py`（所有权/共享校验、配置 CRUD、连通测试、schema 刷新、allowlist/注释维护）
  - `connection.py`（sync psycopg / PyMySQL 封装，只读事务 + 超时）
  - `introspect.py`（读 `information_schema` → SchemaDoc）
  - `schema_store.py`（缓存读写 + 渲染进 prompt 的裁剪）
  - `guard.py`（sqlglot 校验净化）
  - `generate.py`（prompt 组装 + `build_probe_chat_model(...).invoke` + SQL 抽取）
  - `render.py`（形状感知渲染 + 大结果落盘句柄）
  - `pipeline.py`（编排 + 错误分类 + 重试上界 + 共享超时预算）
  - `audit.py`
  - `default_open.py`（仿 `knowledge/default_open.py`：`merge_data_source_ids` / `stamp_turn_data_source_config` + catalog）
  - `tools.py`（`build_data_source_tools` / `query_data_source`）
- `api/routers/data_sources.py`（thin）
- 迁移 `016_data_source_nl2sql.sql` / `.pg.sql`

改动（镜像 `knowledge_base_ids` 全套接线，共 8 处，这是主要增量）：
- `infra/db/services.py` / `RepoBundle`：注册 data_source 系列 repos
- `infra/agents/profile.py`：序列化白名单加 `data_source_ids`
- `infra/agents/manager.py`：`AgentCreateSpec.data_source_ids`、`default_data_source_ids`、
  `validate_data_source_ids`、`persist_data_source_ids`、`build_data_source_tools(...)` 挂载
  （见 manager L2723-2731 knowledge_tools 模式，需注入 `provider_repo` 以便调用时造 chat model）
- `infra/agents/experts/{published_creation,market_creation,catalog}.py`：创建选项加 `data_source_ids`
- `infra/gateway/process/processor.py`：`_attach_turn_data_source_config`（平行 `_attach_turn_knowledge_config`）
- `infra/users/permissions.py`：`"data_sources"`（资源）与 `"data_source_settings"`（实例级设置，镜像 `knowledge_settings`）两个权限位
- `api/app.py` / `api/openapi_meta.py`：注册路由 + tag
- `i18n/en.json` / `zh.json` + `i18n/domains/data_source.py`
- `dashboard/`：`pages/DataSources/`、`api/modules/dataSources.ts`、picker、locales
- `pyproject.toml` + `docker/requirements.txt`：新增依赖（§9）

## 7. 上下文预算与隔离（本方案核心约束）

区分两个上下文：
- **内部生成上下文**（pipeline 的一次性调用）：schema 子集 + few-shot + 问题 + 结果 crunch。**即弃，不进主对话**。
- **主对话上下文**：只有 `query_data_source` 的 **render 输出** 进入，随回合累积。

控制手段（v1）：
1. **schema 已被路线 A 天然隔离**：主 agent 从来看不到库结构 → 不需要为「隔离 schema」上 subagent。
2. **结果压缩**：小/聚合全给；大结果只给有序样例 + 数值聚合 + 截断标记。
3. **digest + 落盘句柄**：超阈值时完整结果写进 agent workspace 文件，注入体只放「列名+前几行+聚合+总行数+文件路径」，全量走带外展开（复用现有 workspace 文件 IO，几乎零新机制）。
4. **独立注入预算**：`data_sources.context_char_budget`（默认 4000，独立于 knowledge 的 6000）。
5. **每回合累计上限**：一回合内多次 `query_data_source` 的注入总量设上限，超出自动降级为 digest+句柄。
6. **重试上界**：单发 + 非法 SQL 类内部最多重试 1 次（总 LLM 调用 ≤ 2）。

subagent / skills 结论：
- **v1 都不建。** skills 在路线 A 下无可教多步流程；subagent 对单问单表是纯额外整轮 LLM，有害。
- **subagent 是「分析型多步查询」的升级点**：当需多步拆解且中间大量行不应进主上下文时，把整个
  `query_data_source` 关进独立上下文的子 agent、只回结论。v1 仅把 pipeline 设计成「未来可被某
  subagent 内部调用」，不建子 agent 本体。

## 8. NL2SQL 管线细则

### 8.1 generate
- prompt：任务说明 + 被勾选表/列（列名+类型+**口径注释优先于裸列名**）+ 方言规则（pg/mysql）
  + 安全约束（只读、必带 LIMIT、预期多行时加 ORDER BY）+ 2~3 few-shot。
- temperature=0，支持处 seed。
- 从模型输出中稳健抽取 SQL（代码围栏容错）。
- 模型来源：实例设置 `data_source_nl2sql_model`（`provider/model` ref）→ `build_probe_chat_model`。
  **配置期 fail-fast，不做运行期静默回退**：在创建/测试端点校验模型存在且 chat-eligible；
  未配置则该数据源能力关闭（仿 knowledge 的 `assert_knowledge_usable`），查询返回「模型未配置」。
  **不**自动 `resolve_first_model_ref()` 兜底（静默选到弱/任意模型会悄悄产出错数、成本不可预期）；
  如需便利，提供**显式**「使用默认对话模型」开关并记录实际所用模型。

### 8.2 guard（sqlglot，纵深防御的一层，非唯一防线）
强制：单语句、**仅 SELECT**、遍历所有表引用（含 CTE/子查询/UNION）⊆ allowlist、列 ⊆ allowlist、
封系统 schema（`information_schema`/`pg_catalog`/`mysql.*`）、封危险函数/语句
（`pg_sleep`/`pg_read_file`/`LOAD_FILE`/`INTO OUTFILE`/DML/DDL/GRANT）、缺 LIMIT 则注入/钳制
`max_rows`。通过 → 净化 SQL；拒绝 → `blocked`。

### 8.3 execute
- **同步驱动**在 executor 线程内：pg `psycopg`（已有）、mysql `PyMySQL`（新增）。
- **只读事务**：pg `SET TRANSACTION READ ONLY` + `statement_timeout`；mysql `SET SESSION MAX_EXECUTION_TIME`。
- 短连接（不池化），连接超时 + 读超时；凭证不在内存常驻。
- 取 `N+1` 行探测是否截断。

### 8.4 错误分类分流（分层重试）
| 类别 | 处理 | status |
|------|------|--------|
| 护栏/策略拦截 | **不重试**，返回「查询被安全策略拦截：<原因>」 | blocked |
| 连接/鉴权/超时/表不存在 | **不重试**，运维型错误，提示检查数据源 | error |
| 非法 SQL（语法/列名幻觉/函数不支持） | **内部重试 1 次**（回灌精简错误+相关 schema 片段，共享超时预算） | error/ok |
| 结果 0 行 | **有效答案**，返回「查询成功但无匹配」，**不触发重试** | empty |
- **改写重问交外层 agent**：工具返回结构化失败/空信号，主 agent 可在同回合改写 NL 问题重调本工具（免费、已存在、跨端一致）。
- **共享脱敏**：对非 owner 回传前净化原始 DB 错误（防结构信息泄露）。

### 8.5 render（形状感知）
- 小结果/聚合 → 全量。
- 大结果 → 表头 + 有序样例（长文本列缩略 ~40 字）+ 数值列聚合（count/min/max/sum/avg）
  + 「共 ≥X 行（取 N+1 探测）/ 返回 Y 行」标记。
- 大结果**无 ORDER BY** → 标注「未排序，仅样例」，不谎称「前 N 行」。
- 超 `context_char_budget` → 走 digest + workspace 文件句柄（§7 控制手段 3）。

### 8.6 错误文案（面向用户）
原则：**三要素（发生了什么 + 简要原因 + 下一步）**、**按 owner/非 owner 分级脱敏**、**诚实标注「自动生成查询」**、不把 DB 原始报错/堆栈回传给用户或 agent。安全要点：非 owner 查询他人共享源时，错误**不泄露表/列是否存在**。文案落 i18n `data_source.error.*`：

| 类别 | 面向用户措辞（owner 版可附技术细节） |
|------|--------------------------------------|
| 护栏拦截 | 「查询被数据源安全策略拦截：仅允许读取已授权表的只读查询。请缩小问题或联系所有者调整授权。」（owner 附被拦原因，如「引用未授权表 X」） |
| 连接/鉴权/超时 | 「数据源暂时无法访问（连接/权限/超时），请稍后重试或检查配置。」（不回传 DB 方言原文） |
| 空结果 | 「查询成功，但没有匹配的数据。」（明确为有效答案） |
| 非法 SQL（重试后仍失败） | owner：展示尝试的 SQL + 「换种问法或确认字段口径」；非 owner：「未能得到结果，请换一种问法。」 |
| 模型未配置 | 「数据源查询模型尚未配置，请管理员在设置中指定。」 |

### 8.7 大结果在 IM 渠道的呈现
- **默认只给 digest**（列名 + 前几行 + 数值聚合 + 总行数）+ 指向 Dashboard 深链；**不把整表原始行自动外发到 IM**（IM 可能是群/第三方通道、有大小限制，DB 行是敏感数据）。
- 完整结果始终留 agent workspace 文件（§7 控制手段 3 句柄）。
- 增强项（默认关闭）：仅当数据源开启 `allow_im_export` **且**为 1:1 私聊时，才以附件（CSV）发送。

## 9. 依赖

- 新增 `sqlglot`（SQL 解析/净化/transpile）。
- 新增 `PyMySQL`（MySQL 同步驱动，纯 Python）。
- `psycopg[binary]` 已在核心依赖。
- 更新 `pyproject.toml`、`docker/requirements.txt`、`docker/wheels/`。

## 10. API（`api/routers/data_sources.py`，保持 thin：验证 HTTP → 调 infra → 映射错误）

| 端点 | 作用 |
|------|------|
| `POST /api/data-sources` | 建源（engine/name/desc/shared/max_rows/timeout_ms…） |
| `GET /api/data-sources` / `GET /api/data-sources/{id}` | 列表（owner+shared 可见）/ 详情（凭证掩码） |
| `PATCH` / `DELETE /api/data-sources/{id}` | 更新元数据 / 删除（owner，级联清缓存与审计外的文件） |
| `PUT/GET /api/data-sources/{id}/connection` | 配置连接（密码只写不回显）/ 读取（掩码） |
| `POST /api/data-sources/{id}/connection/test` | 测连通（不落凭证前先校验） |
| `POST /api/data-sources/{id}/schema/refresh` | introspection 并写缓存 |
| `GET /api/data-sources/{id}/schema` | 缓存 schema + 当前 allowlist |
| `PUT /api/data-sources/{id}/allowlist` | 设置允许表/列（兼作 schema scope） |
| `PUT/GET /api/data-sources/{id}/annotations` | 表/列口径注释维护 |
| `POST /api/data-sources/{id}/query` | owner 预览：跑管线返回 SQL + 行（调试验证，可选 execute） |
| `GET /api/data-sources/{id}/audit` | 审计查询（owner/admin） |
| `GET/PUT /api/data-sources/settings` | 实例级：启用开关 + `data_source_nl2sql_model`（配置期校验 chat-eligible，未配则能力关闭，§8.1）；需 `data_source_settings` 权限 |

- 全部 `Depends(get_server)` + `require_permission("data_sources")`；加载行后 `assert_*_access`
  （owner / shared 读 / admin 旁路），仿 knowledge 的 `get_readable_base/get_writable_base`。
- typed `response_model`；路由 `summary`；改后核对 `/api/docs` 可读性。

## 11. i18n

新增 `data_source.*` 命名空间到 `i18n/en.json` + `zh.json` + `i18n/domains/data_source.py`：
连接失败、被拦截（表/列越权、非只读）、结果为空、模型未配置、SQL 执行错误、工具展示名、
UI 标签、审计描述。若含 `ErrorCode`，同步后端 `errors` 与 dashboard `apiErrors`。
验收：`uv run pytest tests/unit/i18n -q`。

## 12. 前端（`dashboard/`，非 `src/octop/dashboard/`）

- `pages/DataSources/`：列表 + 创建向导（引擎/连接/测试）+ 配置页（连接、schema 刷新、
  表/列 allowlist、口径注释编辑、查询预览、审计）。
- picker：仿 KB，把可见数据源纳入 chat 挂载选择。
- `api/modules/dataSources.ts` 走 `request.ts`；locales 同步。
- 改后：`cd dashboard && npx tsc -b`（+ `npm run lint` 视情况）+ `make build-frontend`。

## 13. 测试矩阵

- 单测（纯逻辑，mock）：`guard`（SELECT-only/多语句拒绝/allowlist 越权/LIMIT 注入/pg+mysql 方言/危险函数）、
  `generate`（mock chat model、SQL 抽取容错）、`introspect`（mock information_schema）、
  `render`（形状感知、截断标记、无 ORDER BY 诚实性）、`pipeline`（错误分类分流、空结果不重试、
  重试上界与超时预算）、`service`（所有权/共享/凭证掩码）、`audit` 写入。
- repo/迁移：`test_repo_data_sources`、`v == 16` 断言、`_ensure_*` 幂等。
- API 集成：建源/配连接（掩码回读）/测试（mock）/refresh/allowlist/query 预览/audit。
- gateway：`stamp_turn_data_source_config` + catalog、`query_data_source` 工具 ctx。
- 跨平台（AGENTS.md §7）：DB 连接与模型全 mock；路径用 `tmp_path`/`pathlib`；POSIX-only 行为
  `@pytest.mark.skipif(os.name != "posix")`；大结果落盘用例用 `OCTOP_HOME=tmp_path`。
- live（默认跳过）：dockerized pg/mysql 真实 NL2SQL 端到端，`-m live`。

## 14. 分期

- **P1 后端闭环**：迁移+repos+`infra/data_sources/`+工具+per-turn 接线（8 处）+API+护栏/管线/渲染测试
  → curl/API 可端到端查询。**逐项验收见 §18。**
- **P2 前端**：DataSources 页面、picker、口径注释编辑、查询预览。
  → **验收见 §18 P2 DoD（浏览器手工走查 + `npx tsc -b`/`make build-frontend`）**。
- **P3+（暂缓）**：subagent 分析型多步、全量结果带外翻页/下载、schema-RAG、更多引擎、写回。

## 15. 安全清单（实现必须逐条满足）

1. 仅 SELECT、单语句、无 DDL/DML/GRANT/危险函数/多语句。
2. 表/列 allowlist 强制（未勾表不可查、不进 prompt）。
3. 强制 LIMIT（`max_rows`）+ 连接/语句超时 + 只读事务。
4. 凭证 Fernet 加密存储，读取永远掩码，内存不常驻。
5. 每次查询写审计（含 actor/owner、生成与执行 SQL、耗时、状态）。
6. 共享数据源：非 owner 可查/挂载但**不可改**连接/schema/allowlist/注释；错误脱敏。
7. 文档/表单**强烈建议配置数据库只读账号**（护栏是纵深一层，只读账号是真正兜底）。
8. 结果注入受独立预算约束，防对话上下文膨胀；大结果走落盘句柄。

## 16. 主要风险与未决

- **正确性**：NL2SQL 给错数风险；靠口径注释、展示 SQL、软提示（「自动 SQL，请核对口径」）、
  有序/截断诚实性、必要时 subagent 缓解。v1 不做自纠多轮。
- **接线面成本**：8 处 knowledge 镜像易漏（尤其 experts 三处 + processor）；P1 用测试兜覆盖挂载路径。
- **成本/延迟**：热路径额外 1~2 次 LLM + 一次查库；靠显式挂载（不默认进 default_open 自动注入）
  与重试上界控制。
- **方言细节**：日期函数、标识符引用、LIMIT/OFFSET、`MAX_EXECUTION_TIME` 差异 → sqlglot transpile + 分类测试。
- （已定稿）错误文案见 §8.6（三要素 + owner/非 owner 分级脱敏）；大结果在 IM 的呈现见 §8.7（默认 digest + Dashboard 深链，附件需 `allow_im_export` 且 1:1）；生成模型回退策略见 §8.1（配置期 fail-fast，不静默兜底）。

## 17. 开发环境备注

本项目真实运行环境在 **WSL**（`/home/aries/.venvs/octop`），Windows 侧 `.venv`（drvfs）软链损坏不可用；
跑测试/命令走 WSL（`uv run pytest`）。`harness-agent` 为 PyPI 包 `orcakit-harness-agent`，
`harness_agent.llm.factory.build_chat_model(...) -> BaseChatModel`，其 sync `.invoke` 已实测可用，
故管线全同步成立。

## 18. P1 实施与验收清单（step → verify）

> 每步做完跑对应 verify；全部通过后的**总验收（ship bar）= `make all` 绿**（format-all+lint+typecheck+test），
> 并含 §13 跨平台要求（DB/模型全 mock、无 POSIX 假设）。命令均在 **WSL** 用 `uv run` 跑。

| # | 步骤 | verify（可执行的验收） |
|---|------|----------------------|
| 1 | 加依赖 `sqlglot`+`PyMySQL`（pyproject + docker/requirements） | `uv run python -c "import sqlglot, pymysql"` 成功 |
| 2 | 迁移 `016_data_source_nl2sql.sql` + `.pg.sql`；建 6 表（§5.1） | `uv run pytest tests/unit/db/test_db_pool.py -q` 绿；新库 `version==16`；`test_db_pool.py:614` 中间态 `==7` 仍绿 |
| 3 | repos：`DataSourceRow`/连接/allowlist/annotations/schemas/audit（纯 SQL） | `uv run pytest tests/unit/db/test_repo_data_sources.py -q` 绿；凭证列不在 public payload |
| 4 | `connection.py`：sync psycopg/PyMySQL + 只读事务 + 超时 + 短连接 | 单测 mock 驱动绿；`-m live` 可选 dockerized pg 连通测（默认 skip） |
| 5 | `introspect.py`：information_schema → SchemaDoc | `uv run pytest tests/unit/data_sources/test_introspect.py -q` 绿（mock 行） |
| 6 | `guard.py`：sqlglot 校验净化 | `test_guard.py` 覆盖：SELECT-only/多语句拒/CTE·子查询越权/LIMIT 注入钳制/pg+mysql 方言/危险函数拦截 全绿 |
| 7 | `generate.py`：`build_probe_chat_model(...).invoke` + SQL 抽取 + **配置期 fail-fast** | mock chat model 单测绿；未配模型→能力关闭并返回「模型未配置」的断言绿 |
| 8 | `render.py`：形状感知 + 落盘句柄 | `test_render.py`：小/聚合全给、大结果有序样例+数值聚合+「共≥X/返回Y」标记、无 ORDER BY 不谎称前N行、超预算→文件句柄 全绿 |
| 9 | `pipeline.py`：编排+错误分类+重试上界+共享超时预算 | `test_pipeline.py`：非法SQL内部重试1次且总 LLM≤2、护栏/连接类不重试、空结果不重试、超时预算跨两次共享 全绿 |
| 10 | `audit.py`：每次查询落 `data_source_sql_audit` | 单测断言 ok/empty/blocked/error 四类均写审计 |
| 11 | `service.py`：所有权/共享/凭证掩码/配置 CRUD/test/refresh/allowlist/annotations | `test_service_acl.py`（仿 knowledge）：owner 可改、shared 只读、非 owner 不可改、admin 旁路 全绿 |
| 12 | `tools.py` `query_data_source` + `default_open.py` stamp + catalog | 工具 ctx 单测 + `stamp_turn_data_source_config` 注入/`data_source_catalog` 单测绿 |
| 13 | 8 处 agent 接线（profile/manager/experts×3/processor/permissions） | 集成测：agent 配 `data_source_ids` → 一轮消息触发 `query_data_source`；`data_sources`/`data_source_settings` 权限位生效 |
| 14 | `api/routers/data_sources.py` 全端点 + settings；挂 `api/app.py`/`openapi_meta.py` | `tests/integration` 数据源用例绿：建源/配连接(掩码回读)/test(mock)/refresh/allowlist/query 预览/audit；settings 端点需 `data_source_settings` |
| 15 | i18n：`data_source.*` + `data_source.error.*`（en/zh 对齐）+ `domains/data_source.py` | `uv run pytest tests/unit/i18n -q` 绿；apiErrors 前后端键一致 |
| 15b | 错误文案合规（§8.6） | `test_pipeline.py`/`test_errors.py` 断言：五类错误均走 `data_source.error.*` 无硬编码英文；**非 owner 文案不含表/列名**（如「表 X 未授权」仅 owner 版出现）；DB 原始报错不出现在返回体与审计展示字段 |
| 16 | OpenAPI 可读性 | 手核 `/api/docs`：新路由有 tag/summary、typed request/response |
| 17 | 总验收 | `make all` 绿；`uv run pytest tests/integration -q` 绿；端到端 smoke：建源→挂载→提问→结果注入 |

### P1 端到端验收场景（Definition of Done）
1. 建 PostgreSQL 数据源、勾 2 表 + 写 1 条列口径注释 → schema refresh 成功。
2. 新建 agent 挂载该源；问「按 X 分组的 Y 总量」→ `query_data_source` 内部生成 SQL、过护栏、只读执行、返回带生成 SQL 的 digest。
3. 问一个引用未授权表的问题 → 返回 §8.6「护栏拦截」文案、非错误静默、审计记 `blocked`。
4. 数据源未配 `data_source_nl2sql_model` → 能力关闭、查询返回「模型未配置」，不静默用其它模型。
5. 构造大结果（超 `context_char_budget`）→ 注入体为 digest + workspace 文件路径，主对话不被原始行撑爆；非 owner 场景下错误/结果文案均不泄露未授权表/列存在性。

### P2 前端验收场景（Definition of Done）
1. DataSources 页：创建向导（引擎/连接/测试）→ 密码保存后回读恒为掩码，任何 GET 响应不含密文/明文。
2. schema 刷新后表/列树可勾选 allowlist、可编辑口径注释，保存后刷新页面数据持久。
3. 查询预览：自然语言 → 展示生成的 SQL + 结果 digest + 「自动生成查询」软提示；`blocked`/`error`/`empty` 状态各有可视化区分。
4. chat 挂载选择器出现数据源条目（仿 KB picker）；agent 配置页 `data_source_ids` 读写正确。
5. 审计列表可见 actor/查询/SQL/状态/耗时；非 owner 视角对他人共享源只读（无编辑入口）。
6. 构建门槛：`cd dashboard && npx tsc -b` 绿、`npm run lint` 无新增错、`make build-frontend` 成功、locales en/zh 键对齐。
