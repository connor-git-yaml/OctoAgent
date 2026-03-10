# Implementation Plan: Feature 038 Agent Memory Recall Optimization

## 1. 目标

038 的目标不是重写 Memory Core，而是把当前已有的 Memory governance / MemU degrade path 真正接进主 Agent runtime：

1. recall 有正式 contract
2. runtime / import / tool 共享同一 project-scoped resolver
3. ContextFrame 能解释 recall provenance

## 2. 设计决策

### 2.1 recall 作为 runtime contract，而不是 search 的薄包装

- 继续保留 `search_memory()` 作为底层读取接口
- 新增 `recall_memory()` 供 Agent/runtime/tooling 复用
- recall pack 负责 query expansion、citation、preview、backend truth

### 2.2 resolver 统一，而不是每个入口自己 new `MemoryService`

- `AgentContextService`、`TaskService`、`ChatImportService`、`CapabilityPackService` 全部通过 `MemoryRuntimeService`
- project/workspace 是 runtime truth；default project 只作为最后 fallback

### 2.3 吸收参考实现，但不回退治理边界

- 借鉴 Agent Zero 的 query expansion / post-filter 思路
- 借鉴 OpenClaw MemU 脚本的“分步提取 + 压缩 + 关联”经验
- 不照抄单一可变 index、best-effort 后台写入、无权限的脚本落地

## 3. 模块落点

### Phase A - Memory Contract

- `packages/memory/src/octoagent/memory/models/integration.py`
- `packages/memory/src/octoagent/memory/service.py`

职责：

- 定义 `MemoryRecallHit` / `MemoryRecallResult`
- 实现 `recall_memory()` 和 helper

### Phase B - Runtime Wiring

- `apps/gateway/src/octoagent/gateway/services/agent_context.py`
- `apps/gateway/src/octoagent/gateway/services/task_service.py`
- `packages/provider/src/octoagent/provider/dx/memory_runtime_service.py`
- `packages/provider/src/octoagent/provider/dx/chat_import_service.py`

职责：

- 主 Agent 使用 recall pack
- compaction flush / chat import 使用同一 resolver

### Phase C - Tool Surface

- `apps/gateway/src/octoagent/gateway/services/capability_pack.py`

职责：

- 增加 `memory.recall`
- 现有 memory tools 优先解析当前 runtime project/workspace

### Phase D - Verification

- `packages/memory/tests/test_memory_service.py`
- `apps/gateway/tests/test_task_service_context_integration.py`
- `apps/gateway/tests/test_capability_pack_tools.py`
- `packages/provider/tests/test_chat_import_service.py`

### Phase E - Control Plane Provenance

- `packages/core/src/octoagent/core/models/control_plane.py`
- `apps/gateway/src/octoagent/gateway/services/control_plane.py`
- `apps/gateway/tests/test_control_plane_api.py`
- `frontend/src/types/index.ts`

职责：

- 让 `ContextContinuityDocument` 直接暴露 recall provenance
- 保持前端控制面 contract 与后端 snapshot 资源集合一致

### Phase F - Delayed Recall Carrier

- `packages/core/src/octoagent/core/models/enums.py`
- `packages/core/src/octoagent/core/models/payloads.py`
- `apps/gateway/src/octoagent/gateway/services/agent_context.py`
- `apps/gateway/src/octoagent/gateway/services/task_service.py`
- `apps/gateway/tests/test_task_service_context_integration.py`

职责：

- 为 delayed recall 建立 durable request/result artifact carrier
- 通过 task events 审计 delayed recall 的 scheduled/completed/failed 生命周期
- 把 delayed recall 状态回填到 `ContextFrame.budget`

### Phase G - Recall Hook Quality Layer

- `packages/memory/src/octoagent/memory/models/integration.py`
- `packages/memory/src/octoagent/memory/service.py`
- `apps/gateway/src/octoagent/gateway/services/agent_context.py`
- `apps/gateway/src/octoagent/gateway/services/capability_pack.py`
- `packages/memory/tests/test_memory_service.py`
- `apps/gateway/tests/test_task_service_context_integration.py`
- `apps/gateway/tests/test_capability_pack_tools.py`

职责：

- 为 recall 定义内建 `post-filter / rerank` hook contract 与 trace
- 默认主 Agent runtime 启用安全的 hook 组合，但保留 fallback，避免 recall 被误伤清空
- 让 `memory.recall` 工具可以显式传入 hook 选项，便于调试与审计

## 4. 非目标

- 不新增新的 memory backend
- 不引入 destructive consolidation
- 不让工具或 UI 直接绕过治理层改 SoR / Vault
- 不引入可执行任意外部脚本或 LLM 自由裁剪的 recall hook 插件机制

## 5. 验证策略

- 单元测试验证 recall contract 输出
- 集成测试验证 `ContextFrame` 和记忆工具真实消费 recall pack
- provider 测试验证 chat import 已走 project-scoped runtime resolver
- control plane 测试验证 `ContextFrame` provenance 可以被直接投影和审计
- task/context 测试验证 delayed recall carrier 可在 events/artifacts/context frame 中重建
- recall 测试验证 hook trace、fallback 与 runtime 默认接线真实生效
- `ruff + pytest` 作为收口门禁
