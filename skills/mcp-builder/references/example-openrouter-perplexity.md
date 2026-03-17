# 完整示例：OpenRouter Perplexity MCP Server

一个生产级 MCP server 示例，通过 OpenRouter 调用 Perplexity 模型提供搜索和研究能力。

## 目录结构

```
~/.octoagent/mcp-servers/openrouter-perplexity/
├── package.json
├── server.js
└── node_modules/
```

## package.json

```json
{
  "name": "openrouter-perplexity-mcp",
  "version": "1.0.0",
  "description": "MCP server for Perplexity search via OpenRouter",
  "type": "module",
  "main": "server.js",
  "dependencies": {
    "@modelcontextprotocol/sdk": "^1.21.1"
  }
}
```

## server.js

```javascript
#!/usr/bin/env node
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";

// 从环境变量读取配置（敏感信息不硬编码）
const API_KEY = process.env.OPENROUTER_API_KEY;
const SEARCH_MODEL = process.env.OPENROUTER_SEARCH_MODEL || "perplexity/sonar-pro-search";
const RESEARCH_MODEL = process.env.OPENROUTER_RESEARCH_MODEL || "perplexity/sonar-deep-research";
const BASE_URL = "https://openrouter.ai/api/v1";

if (!API_KEY) {
  console.error("错误: OPENROUTER_API_KEY 环境变量未设置");
  process.exit(1);
}

// 封装 API 调用
async function callAPI(query, options = {}) {
  const { model = SEARCH_MODEL, systemPrompt, maxTokens = 4096, temperature = 0.1 } = options;
  const messages = [];
  if (systemPrompt) messages.push({ role: "system", content: systemPrompt });
  messages.push({ role: "user", content: query });

  const response = await fetch(`${BASE_URL}/chat/completions`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${API_KEY}`,
    },
    body: JSON.stringify({ model, messages, max_tokens: maxTokens, temperature }),
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`API 错误: ${response.status} ${response.statusText}\n${errorText}`);
  }

  const data = await response.json();
  if (!data.choices?.[0]) throw new Error("API 返回了无效的响应格式");

  let content = data.choices[0].message.content;

  // 附加引用来源
  if (data.citations && Array.isArray(data.citations)) {
    content += "\n\n**来源：**\n";
    data.citations.forEach((citation, i) => { content += `[${i + 1}] ${citation}\n`; });
  }
  return content;
}

// 创建 server
const server = new McpServer({ name: "openrouter-perplexity", version: "1.0.0" });

// 工具 1: 实时搜索
server.tool(
  "web_search",
  "使用 Perplexity 进行实时网络搜索。返回最新的搜索结果和摘要。",
  { query: z.string().describe("搜索查询语句") },
  async ({ query }) => {
    try {
      const result = await callAPI(query, {
        model: SEARCH_MODEL,
        systemPrompt: "你是一个专业的搜索助手。请搜索互联网并提供准确、最新的信息。包含相关的来源链接。",
      });
      return { content: [{ type: "text", text: result }] };
    } catch (error) {
      return { content: [{ type: "text", text: `搜索失败: ${error.message}` }], isError: true };
    }
  }
);

// 工具 2: 深度研究
server.tool(
  "research",
  "使用 Perplexity 进行深度研究。适合需要详细分析的复杂问题。",
  {
    topic: z.string().describe("研究主题或问题"),
    depth: z.enum(["brief", "detailed"]).default("detailed").describe("研究深度"),
  },
  async ({ topic, depth = "detailed" }) => {
    try {
      const result = await callAPI(topic, {
        model: RESEARCH_MODEL,
        systemPrompt: depth === "detailed"
          ? "请对给定主题进行深入全面的研究，包括背景、关键观点、不同角度、最新进展和来源。"
          : "请对给定主题提供简明扼要的研究总结，包含关键信息和来源。",
        maxTokens: depth === "detailed" ? 8000 : 4096,
      });
      return { content: [{ type: "text", text: result }] };
    } catch (error) {
      return { content: [{ type: "text", text: `研究失败: ${error.message}` }], isError: true };
    }
  }
);

// 启动
const transport = new StdioServerTransport();
await server.connect(transport);
```

## OctoAgent 注册配置

在 `data/ops/mcp-servers.json` 中添加：

```json
{
  "name": "openrouter-perplexity",
  "command": "node",
  "args": ["server.js"],
  "env": {
    "OPENROUTER_API_KEY": "sk-or-v1-..."
  },
  "cwd": "~/.octoagent/mcp-servers/openrouter-perplexity",
  "enabled": true,
  "mount_policy": "auto_readonly"
}
```

## 设计要点

1. **环境变量隔离** — API Key 通过 `env` 配置传入，不进代码
2. **优雅的错误处理** — 返回 `isError: true` 而非 throw
3. **引用来源附加** — Perplexity 返回 citations，格式化后附加到内容
4. **模型可配置** — 通过环境变量支持切换不同 Perplexity 模型
5. **两个工具各司其职** — `web_search` 快速搜索，`research` 深度分析
