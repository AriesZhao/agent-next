# Mock Server

模拟业务 API 服务器，用于 AriesAgent API Connector 和 MCP Connector 的开发与测试。

## 快速启动

```bash
cd workspace/mock-server
uv sync
uv run python -m mock_server.main
# → http://127.0.0.1:8888
# → API docs: http://127.0.0.1:8888/docs
```

## 目录结构

```
mock-server/
├── src/mock_server/
│   ├── main.py              # FastAPI 应用 + CLI 入口
│   ├── config.py            # 配置（host/port/data）
│   ├── data/                # 内存数据存储 + 种子数据加载
│   ├── routers/
│   │   ├── business.py      # 业务 API（订单/产品/客户）
│   │   ├── auth.py          # 模拟认证
│   │   └── health.py        # 健康检查
│   └── mcp/
│       └── server.py        # 模拟 MCP 服务器（预留）
├── data/
│   └── seed.json            # 种子数据
└── scripts/
    └── start.sh             # 启动脚本
```

## API 端点

| 方法   | 路径                    | 说明         |
|--------|-------------------------|------------|
| GET    | /health                 | 健康检查       |
| GET    | /orders                 | 订单列表（支持 status 筛选） |
| GET    | /orders/{id}            | 订单详情       |
| POST   | /orders                 | 创建订单       |
| GET    | /products               | 产品列表（支持 in_stock 筛选） |
| GET    | /products/{id}          | 产品详情       |
| GET    | /customers              | 客户列表       |
| GET    | /customers/{id}         | 客户详情       |
| POST   | /auth/login             | 模拟登录       |
| GET    | /auth/me                | 当前用户（需 Bearer token） |
| POST   | /_reset                 | 重置数据到种子默认值 |

## 认证

模拟 Token：`sk-test-token-001`（alice/admin）、`sk-test-token-002`（bob/member）。

请求头：`Authorization: Bearer sk-test-token-001`

## 配置

环境变量（前缀 `MOCK_`）：

| 变量            | 默认值       | 说明      |
|---------------|-----------|---------|
| MOCK_HOST     | 127.0.0.1 | 监听地址    |
| MOCK_PORT     | 8888      | 监听端口    |
| MOCK_DATA_DIR | data      | 数据目录    |
| MOCK_SEED_FILE| seed.json | 种子文件名   |

## 扩展

### 添加新的业务模块

1. 在 `routers/` 下新建模块文件
2. 在 `data/__init__.py` 的 `_STORE` 中添加新集合
3. 在 `data/seed.json` 中添加种子数据
4. 在 `main.py` 的 `create_app()` 中注册路由

### 模拟 MCP 服务器

`mcp/server.py` 预留了 MCP 服务器接口。未来可实现 stdio/SSE 传输，
将业务 API 以 MCP 工具形式暴露，用于测试 MCP Connector 路径。
