# 技术决策研究: MCP 安装与生命周期管理

**Feature**: 058-mcp-install-lifecycle
**Date**: 2026-03-16
**Status**: Final

---

## Decision 1: 安装注册表存储格式

**结论**: JSON 文件 (`data/ops/mcp-installs.json`)

**理由**:
- 与现有 `mcp-servers.json` 运行时配置保持一致的存储模式
- 安装注册表是低频写入、小规模数据（通常 <50 条记录）
- JSON 可直接人工查看和编辑，便于调试
- 不需要事务、JOIN 等关系数据库特性

**Alternatives**:
1. **SQLite** -- 产研汇总推荐方案。优势是事务一致性和查询能力，但对 <50 条记录的场景过度设计。引入 SQLite 还需要 migration 管理，增加维护成本。若后续需要复杂查询（如与 Event Store 联合查询），可迁移。
2. **内嵌到 mcp-servers.json** -- 最简方案，但违反 FR-005（安装记录不污染运行时配置），且 McpServerConfig 是 ToolBroker 的数据契约，不应扩展。

---

## Decision 2: 持久连接管理方案

**结论**: 自建 McpSessionPool，由 McpRegistryService 独占持有

**理由**:
- 接缝分析明确指出 Pydantic AI MCPServer Toolset 路径与现有 ToolBroker 链路不兼容
- OctoAgent 不直接使用 Pydantic AI Agent 类，工具执行走 SkillRunner -> LiteLLMSkillClient -> ToolBroker -> handler
- McpSessionPool 作为独立模块便于单元测试，但由 McpRegistryService 独占持有确保 MCP 运行时的单一入口

**Alternatives**:
1. **直接复用 Pydantic AI MCPServer Toolset** -- 产研汇总和 Pydantic AI 调研推荐方案。但需要重构 Worker 的工具注入方式（从 ToolBroker 路径迁移到 Toolset 路径），改动范围大，与"ToolBroker/SkillRunner 零改动"约束冲突。适合长期演进但不适合本次 Feature 范围。
2. **保持 per-operation session** -- 最简方案，但每次工具调用启动子进程（1-5 秒延迟），Constitution #8 要求的工具调用性能无法满足。

---

## Decision 3: 安装进度反馈机制

**结论**: 轮询（polling）模式

**理由**:
- 现有 ControlPlane action 机制是 request-response 模式，不支持中间进度推送
- 2 秒轮询间隔对安装场景（30s~3min）无感知差异
- 实现简单，不需要新增 SSE 通道或 WebSocket 端点

**Alternatives**:
1. **SSE 事件推送** -- 更实时，但 OctoAgent 的 SSE 通道是 snapshot 全量推送机制，不适合细粒度进度。新增专用 SSE 通道增加复杂度。
2. **WebSocket** -- 最实时，但 OctoAgent 当前未使用 WebSocket，引入新协议不合理。

---

## Decision 4: npm 包入口点检测策略

**结论**: 分层检测（bin -> main -> npx fallback），检测失败后允许用户手动修正

**理由**:
- MCP 生态中入口点格式多样：有的用 bin 字段，有的需要 node 执行 main 文件，有的只能用 npx
- 分层策略覆盖率最高
- 检测后执行 tools/list 验证，确保配置正确

**Alternatives**:
1. **只用 npx** -- 最简单，但部分包（如局部安装到 node_modules 的）npx 找不到
2. **要求用户手动填写 command** -- 违反"一键安装"体验目标

---

## Decision 5: McpServerConfig 是否扩展安装字段

**结论**: 不扩展。安装元数据仅存于 McpInstallRecord

**理由**:
- FR-005 明确要求"安装记录不污染运行时配置"
- McpServerConfig 是 ToolBroker 工具链的数据契约，扩展会影响零改动约束
- 前端展示安装信息时，由 ControlPlaneService 在 `get_mcp_provider_catalog_document()` 中合并两个数据源

**Alternatives**:
1. **在 McpServerConfig 中新增 install_source/install_path** -- 更简单（单数据源），但违反 FR-005 和零改动约束

---

## Decision 6: 安装目录结构

**结论**: `~/.octoagent/mcp-servers/{server-id}/`，每个 server 独立子目录

**理由**:
- 参考 OpenClaw 的 `~/.openclaw/extensions/{id}/` 模式
- 每个 server 独立目录确保依赖隔离（npm 包独立 node_modules，pip 包独立 venv）
- 使用 home 目录下的 `.octoagent/` 路径，与 OctoAgent 的数据目录规范一致
- server-id 使用 package_name 的 slugified 版本（小写 + 非字母数字替换为下划线）

**Alternatives**:
1. **全局共享 node_modules/venv** -- 简单但有依赖冲突风险（FR-003 明确要求独立依赖目录）
2. **项目目录下（如 data/mcp-servers/）** -- 可行，但 ~/.octoagent/ 更符合系统级工具的惯例，且不与项目数据目录混合

---

## Decision 7: 健康检查实现方式

**结论**: 通过 `tools/list` RPC 调用探测，超时 5 秒判定为不健康

**理由**:
- MCP 协议 spec 中没有定义 ping/heartbeat RPC
- `tools/list` 是所有 MCP server 必须实现的基础 RPC，兼容性最好
- 5 秒超时既能覆盖正常响应，又不会因单次慢响应误判

**Alternatives**:
1. **检查子进程 PID 存活** -- 只能检测进程是否在运行，不能检测 MCP 协议层是否正常
2. **自定义 ping RPC** -- 需要 MCP server 支持，大多数第三方 server 不支持

---

## Decision 8: P2 特性（Docker 安装来源）的预留设计

**结论**: 在 InstallSource enum 中预留 DOCKER 值，但 MVP 不实现 Docker 安装策略

**理由**:
- spec 中 Docker 安装为 P2（SHOULD），MVP 聚焦 npm + pip
- enum 预留确保数据模型不需要破坏性变更
- McpInstallerService 的策略模式设计使得后续添加 DockerStrategy 无需改动核心逻辑

**Alternatives**:
1. **MVP 就实现 Docker** -- 增加实现范围，且 Docker 在 MCP 生态中使用率较低
2. **不预留** -- 后续添加需要修改 enum，可能影响已有安装记录
