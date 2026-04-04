# Feature 073: Deprecated 残留全面清理

## 问题全貌

| 残留类别 | Python 引用 | 前端引用 | SQLite 影响 |
|----------|------------|---------|-------------|
| **Workspace 概念** | 787 处 / 82 文件 | 166 处 / 17 文件 | 1 整表 + 14 表有列 |
| **ToolProfile** | 760 处 / 75 文件 | 0 | 2 表有列 |
| **PolicyProfile** | 87 处 / 9 文件（仍活跃使用） | 0 | 0 |
| **LLM Service 旧类型** | 9 处 / 1 文件 | 0 | 0 |
| **其他 DEPRECATED** | ~20 处零散 | 0 | 0 |
| **合计** | ~1,663 | 166 | 16 表 |

## 执行计划：4 个 Phase

### Phase A: ToolProfile 清除（独立，无跨层依赖）

**范围**：760 处 / 75 文件 + 2 个 SQLite 列

**做法**：
1. `ToolMeta` 删除 `tool_profile` 字段（models.py）
2. `@tool_contract` 装饰器删除 `tool_profile` 参数（decorators.py）
3. `capability_pack.py` 删除 75 处 `tool_profile=ToolProfile.XXX`
4. MCP 注册（mcp_registry.py）删除 ToolProfile 推断逻辑
5. 删除枚举 + 映射 + 兼容函数：
   - `ToolProfile` 枚举
   - `PROFILE_LEVELS` 映射
   - `profile_allows()` 函数
   - `TOOL_PROFILE_TO_PRESET` 映射
   - `migrate_tool_profile_to_preset()` 函数
6. `ExecutionContext` 删除 `profile` 字段
7. `SkillManifest` 删除 `tool_profile` 字段
8. Orchestrator/TaskService/WorkerRuntime 中传 tool_profile 的地方全部删除
9. SQLite: `agent_profiles` 和 `worker_profiles` 表的 `tool_profile` 列改为可选或删除
10. 更新 `__init__.py` 导出
11. 更新/删除相关测试

**热点文件**：
- `capability_pack.py` (75 处) — 核心，58 个工具注册声明
- `orchestrator.py` (16 处) — 路由层传参
- `models.py` (tooling) — 枚举定义
- `decorators.py` — 装饰器参数

**预期净删**：~500 行

### Phase B: Workspace 概念清除 — 后端模型层（核心，需先做）

**范围**：models + store 层清理

**做法**：
1. `core/models/control_plane.py` — 删除所有 `workspace_id` / `active_workspace_id` 字段（38 处）
2. `core/models/behavior.py` — 删除 `workspace_id` / `workspace_slug` 字段
3. `core/models/agent_context.py` — 删除 workspace 字段（9 处）
4. `core/models/project.py` — 删除 workspace 字段（6 处）
5. `core/store/sqlite_init.py` — 删除 `workspaces` 表 DDL + workspace 列 + 索引
6. `core/store/agent_context_store.py` — 删除 workspace 相关查询/写入（57 处）
7. `core/store/project_store.py` — 删除 workspace 相关逻辑（33 处）
8. `memory/store/sqlite_init.py` — 删除 workspace_id 列
9. `memory/store/memory_store.py` — 删除 workspace 相关逻辑（25 处）
10. `memory/service.py` — 删除 workspace 参数传递（13 处）

**SQLite 迁移**：
- 新增迁移脚本 `migrations/003_drop_workspace.py`
- `ALTER TABLE xxx DROP COLUMN workspace_id`（SQLite 3.35+ 支持）
- 回退策略：迁移前自动备份

**热点文件**：
- `_legacy.py` (105 处) — control plane 旧兼容层
- `agent_context_store.py` (57 处)
- `control_plane.py` models (37 处)
- `project_store.py` (33 处)

**预期净删**：~800 行

### Phase C: Workspace 概念清除 — Gateway 服务层 + 前端

**范围**：API 路由 + 服务层 + 前端类型

**做法**：
1. `_legacy.py` — 删除 workspace 兼容逻辑（105 处），这个文件可能可以整体删除
2. `session_service.py` — 删除 workspace 参数（40 处）
3. `agent_context.py` — 删除 workspace 传递（23 处）
4. `memory_console_service.py` — 删除 workspace 参数（46 处）
5. `memory_service.py` (control_plane) — 删除 workspace 参数（29 处）
6. `import_workbench_service.py` — 删除 workspace 参数（18 处）
7. API 路由：删除 `workspace_id` deprecated 查询参数
8. 前端 `types/index.ts` — 删除 41 处 workspace 类型字段
9. 前端 `controlPlane.ts` / `client.ts` — 删除 workspace 传参
10. 前端测试 — 删除 workspace mock 数据

**预期净删**：~600 行 Python + ~200 行 TypeScript

### Phase D: PolicyProfile 收口 + 零散清理

**范围**：PolicyProfile 仍活跃使用，不能直接删。需要替代方案。

**做法**：
1. PolicyProfile 的活跃调用方（`_legacy.py`, `_base.py`, `agent_service.py`, `setup_service.py`）
   改用 `PermissionPreset` 替代 PolicyProfile 的策略选择逻辑
2. 删除 `PolicyProfile` 类 + 三个预设实例 + `PolicyStep` 类
3. 删除 `POLICY_ACTION_SEVERITY` 字典
4. 更新 policy `__init__.py` 导出
5. 删除 `llm_service.py` 中的 4 个 deprecated 旧类型（LLMResponse/LLMProvider/EchoProvider/MockProvider）
6. 删除 FlushPromptInjector 废弃代码
7. 删除 API 路由中的 deprecated 参数

**预期净删**：~200 行

## 执行顺序

```
Phase A (ToolProfile)  ←  独立，可先做
       ↓
Phase B (Workspace 模型层)  ←  核心，后端先行
       ↓
Phase C (Workspace 服务+前端)  ←  依赖 Phase B
       ↓
Phase D (PolicyProfile + 零散)  ←  最后收尾
```

Phase A 和 Phase B 互相独立，可以并行。
Phase C 依赖 Phase B（模型字段先删，API 再跟进）。
Phase D 最后做（PolicyProfile 替代方案需要思考）。

## 预期总效果

| 指标 | 清理前 | 清理后 |
|------|--------|--------|
| DEPRECATED 标记数 | 118 处 | 0 |
| Workspace 相关引用 | 953 处（Python + 前端） | 0 |
| ToolProfile 相关引用 | 760 处 | 0 |
| PolicyProfile（活跃） | 87 处 | 0（替换为 PermissionPreset） |
| 代码净删 | — | ~2,300 行 |
| SQLite 冗余列 | 16 表 | 0 |
| 新开发者困惑度 | 高（workspace vs project, ToolProfile vs PermissionPreset） | 低 |
