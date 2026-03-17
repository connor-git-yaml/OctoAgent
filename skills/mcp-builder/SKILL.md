---
name: mcp-builder
description: "Build, scaffold, and register MCP (Model Context Protocol) servers for OctoAgent. Use when: (1) creating a new MCP server from scratch, (2) adding tools/resources to an existing MCP server, (3) registering an MCP server with OctoAgent's MCP registry, (4) debugging MCP server connectivity. NOT for: using existing MCP tools (those are auto-available), general API integrations without MCP."
version: 1.0.0
author: OctoAgent
tags:
  - mcp
  - server
  - integration
  - builder
tools_required:
  - terminal.exec
  - filesystem.read_text
  - filesystem.list_dir
---

# MCP Builder

Build and register MCP (Model Context Protocol) servers for OctoAgent.

## When to Use

- Creating a new MCP server to integrate an external API or service
- Scaffolding an MCP server project (Node.js or Python)
- Adding tools or resources to an existing MCP server
- Registering a locally built MCP server with OctoAgent
- Debugging MCP server startup or tool discovery issues

## When NOT to Use

- Using already-registered MCP tools -> they're auto-available, just call them
- Simple REST API calls -> use `web.fetch` directly
- Non-MCP integrations -> use regular Pydantic Skills instead

## MCP Overview

MCP servers expose **tools** and **resources** over stdio transport. OctoAgent discovers and registers them as `mcp.{server}.{tool}` in the ToolBroker.

```
OctoAgent ToolBroker
  └── McpRegistryService
        └── stdio_client(command, args, env)
              └── MCP Server process (Node.js / Python)
                    ├── tool: web_search
                    ├── tool: create_issue
                    └── resource: config://settings
```

## Scaffolding a New MCP Server

### Option A: Node.js (推荐，启动快)

```bash
mkdir -p ~/.octoagent/mcp-servers/<server-name>
cd ~/.octoagent/mcp-servers/<server-name>

cat > package.json << 'EOF'
{
  "name": "<server-name>-mcp",
  "version": "1.0.0",
  "type": "module",
  "main": "server.js",
  "dependencies": {
    "@modelcontextprotocol/sdk": "^1.21.1"
  }
}
EOF

npm install
```

Minimal `server.js`:

```javascript
#!/usr/bin/env node
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";

const server = new McpServer({
  name: "<server-name>",
  version: "1.0.0",
});

// 注册工具
server.tool(
  "tool_name",
  "工具描述：做什么、什么场景用",
  {
    param1: z.string().describe("参数说明"),
    param2: z.number().optional().describe("可选参数"),
  },
  async ({ param1, param2 }) => {
    // 实现逻辑
    const result = `处理: ${param1}`;
    return {
      content: [{ type: "text", text: result }],
    };
  }
);

// 启动
const transport = new StdioServerTransport();
await server.connect(transport);
```

### Option B: Python (适合调用 Python 生态库)

```bash
mkdir -p ~/.octoagent/mcp-servers/<server-name>
cd ~/.octoagent/mcp-servers/<server-name>

python3 -m venv .venv
source .venv/bin/activate
pip install mcp
```

Minimal `server.py`:

```python
#!/usr/bin/env python3
"""<server-name> MCP Server"""
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("<server-name>")

@mcp.tool()
async def tool_name(param1: str, param2: int = 0) -> str:
    """工具描述：做什么、什么场景用"""
    return f"处理: {param1}, {param2}"

if __name__ == "__main__":
    mcp.run(transport="stdio")
```

## Registering with OctoAgent

MCP servers 通过 `data/ops/mcp-servers.json` 配置注册。

### 配置格式

```json
{
  "servers": [
    {
      "name": "server-name",
      "command": "node",
      "args": ["server.js"],
      "env": {
        "API_KEY": "sk-..."
      },
      "cwd": "/absolute/path/to/server",
      "enabled": true,
      "mount_policy": "auto_readonly"
    }
  ]
}
```

### 字段说明

| 字段 | 必填 | 说明 |
|------|------|------|
| `name` | 是 | 唯一标识，注册后工具名为 `mcp.{name}.{tool}` |
| `command` | 是 | 启动命令 (`node`, `python3`, `.venv/bin/python`) |
| `args` | 否 | 命令参数 (`["server.js"]`, `["server.py"]`) |
| `env` | 否 | 环境变量（API key 等敏感信息放这里） |
| `cwd` | 否 | 工作目录（默认项目根目录） |
| `enabled` | 否 | 是否启用（默认 `true`） |
| `mount_policy` | 否 | 工具挂载策略：`auto_readonly`（默认）、`auto_readwrite`、`manual` |

### mount_policy 选择

- **`auto_readonly`**（默认）：工具自动注册，side_effect_level = none，适合只读查询
- **`auto_readwrite`**：工具自动注册，side_effect_level = reversible，适合有副作用的操作
- **`manual`**：工具发现但不自动注册，需手动启用

### 注册方式

**方式 1: Web UI**（推荐）
访问 OctoAgent Web 管理台 → MCP Providers → 添加

**方式 2: API**
```bash
curl -X POST http://localhost:6400/api/control/actions \
  -H "Content-Type: application/json" \
  -d '{
    "action_id": "mcp_provider.save",
    "params": {
      "name": "server-name",
      "command": "node",
      "args": ["server.js"],
      "env": {"API_KEY": "sk-..."},
      "cwd": "/path/to/server",
      "enabled": true
    }
  }'
```

**方式 3: 直接编辑配置文件**
编辑 `data/ops/mcp-servers.json`，重启或调用 refresh。

### 验证注册

```bash
# 通过 API 检查
curl http://localhost:6400/api/control/resources/mcp-provider-catalog | python3 -m json.tool

# 或使用内置工具
mcp.tools.list
mcp.servers.list
```

## 添加工具的最佳实践

### 工具设计原则

1. **单一职责** — 每个工具做一件事，名字说清楚做什么
2. **参数用 Zod/类型注解** — MCP SDK 自动生成 JSON Schema
3. **描述写给 LLM 看** — 说明用途、输入格式、返回内容
4. **错误返回 `isError: true`** — 不要 throw，返回结构化错误

### Node.js 工具模板

```javascript
server.tool(
  "action_name",          // 动词_名词 格式
  "一句话说明做什么",       // LLM 靠这个决定是否调用
  {
    // 参数 schema（Zod）
    query: z.string().describe("搜索关键词"),
    limit: z.number().default(10).describe("返回数量上限"),
  },
  async ({ query, limit }) => {
    try {
      const data = await callExternalAPI(query, limit);
      return {
        content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
      };
    } catch (error) {
      return {
        content: [{ type: "text", text: `失败: ${error.message}` }],
        isError: true,
      };
    }
  }
);
```

### Python 工具模板

```python
@mcp.tool()
async def action_name(query: str, limit: int = 10) -> str:
    """一句话说明做什么

    Args:
        query: 搜索关键词
        limit: 返回数量上限
    """
    try:
        data = await call_external_api(query, limit)
        return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"失败: {e}"
```

## 常见集成示例

### API 转 MCP（带 API Key）

适用场景：把任何 REST API 包装成 MCP 工具。

```javascript
// env: { "SERVICE_API_KEY": "sk-..." }
const API_KEY = process.env.SERVICE_API_KEY;

server.tool("query", "查询服务数据", { q: z.string() },
  async ({ q }) => {
    const res = await fetch("https://api.example.com/search", {
      headers: { "Authorization": `Bearer ${API_KEY}` },
      method: "POST",
      body: JSON.stringify({ query: q }),
    });
    if (!res.ok) return { content: [{ type: "text", text: `API ${res.status}` }], isError: true };
    const data = await res.json();
    return { content: [{ type: "text", text: JSON.stringify(data, null, 2) }] };
  }
);
```

### 数据库查询 MCP（Python）

```python
import aiosqlite

@mcp.tool()
async def query_db(sql: str) -> str:
    """执行只读 SQL 查询（仅 SELECT）"""
    if not sql.strip().upper().startswith("SELECT"):
        return "错误: 仅允许 SELECT 查询"
    async with aiosqlite.connect("data.db") as db:
        cursor = await db.execute(sql)
        rows = await cursor.fetchall()
        cols = [d[0] for d in cursor.description]
        return json.dumps([dict(zip(cols, row)) for row in rows], ensure_ascii=False, indent=2)
```

## 调试

### 手动测试 stdio 连接

```bash
# Node.js
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"0.1"}}}' | node server.js

# Python
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"0.1"}}}' | python3 server.py
```

成功时返回 JSON-RPC response（含 `serverInfo`）。

### 常见问题

| 问题 | 原因 | 解决 |
|------|------|------|
| `tool_count: 0` | server 启动但没注册工具 | 检查 `server.tool()` 调用 |
| `status: error` | 进程启动失败 | 检查 command/cwd/env 配置 |
| `ENOENT` | 找不到命令 | 用绝对路径或确认 PATH |
| 超时 | server 没发 response | 确认用了 StdioServerTransport |
| 环境变量为空 | env 没配到 mcp-servers.json | 把 key 加到 `"env": {}` |

## 目录约定

| 用途 | 路径 |
|------|------|
| OctoAgent 内置 MCP 配置 | `data/ops/mcp-servers.json` |
| 自建 MCP server 推荐目录 | `~/.octoagent/mcp-servers/<name>/` |
| 项目级 MCP server | `{project_root}/mcp-servers/<name>/` |
