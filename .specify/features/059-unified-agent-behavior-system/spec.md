# Feature 059 — 统一 Agent Profile 与行为文件系统

**状态**: Draft
**里程碑**: M4+
**上游**: Feature 049, 055, 056, 057
**驱动**: 主 Agent 与 Worker 在数据模型和行为文件访问上的结构性裂缝

---

## 问题陈述

当前系统存在两套 Agent 配置模型（`AgentProfile` 与 `WorkerProfile`），字段重叠但不对齐：
- 主 Agent 用 `AgentProfile`，有 `behavior_system` 但没有版本管理
- Worker 用 `WorkerProfile`，有版本管理但没有 `behavior_system`
- 行为文件三层架构（Global / Agent-Private / Project）在 README 中定义清晰，但运行时只有主 Agent 能完整享有
- Worker 的 Agent-Private 行为文件（IDENTITY.md / SOUL.md / HEARTBEAT.md）虽然在磁盘上创建了，但 UI 不可见、资源模型不承载
- 前端 `WorkerProfileViewItem` 缺少 `behavior_system` 字段，导致 Worker 行为文件无法查看/编辑

## 设计原则

1. **主 Agent 与 Worker 在代码架构上是同一种对象**，只是字段值不同（工具集、权限、Template）
2. **行为文件三层架构是 Agent 的一等公民**，不是主 Agent 的特权
3. **创建时即可初始化行为文件**，因为创建者知道为什么创建
4. **行为文件变更必须尽快反映到运行时 Context**

---

## 工作项

### WI-1: 统一 Agent 数据模型

**现状裂缝**:

| 字段 | AgentProfile | WorkerProfile | 应有归属 |
|------|-------------|---------------|---------|
| persona_summary | ✓ (已清空) | ✗ | 删除，统一由 IDENTITY.md 承载 |
| summary | ✗ | ✓ | 保留为卡片摘要描述，不注入 LLM |
| base_archetype | ✗ | ✓ | 统一到 Agent |
| status / origin_kind / revisions | ✗ | ✓ | 统一到 Agent |
| default_tool_groups / selected_tools | ✗ | ✓ | 统一到 Agent |
| memory_access_policy / context_budget_policy | ✓ | ✗ | 统一到 Agent |
| bootstrap_template_ids | ✓ | ✗ | 统一到 Agent |
| behavior_system | ✓ (在 View Item 中) | ✗ | 统一到 Agent |

**目标**:
- 定义 `AgentProfile` 作为唯一 Agent 配置模型（合并 WorkerProfile 的字段）
- 主 Agent 与 Worker 的区分通过 `role: butler | worker` 字段标记，而非不同类型
- 主 Agent Template 特殊处理：赋予 worker 管理工具（创建/合并/删除 worker）+ 复杂任务委托引导
- Worker Template 无需特殊设计，创建时由调用方初始化行为文件

**迁移策略**:
- `WorkerProfile` 表数据迁移到统一 `AgentProfile` 表
- 前端 `WorkerProfileItem` → `AgentProfileItem`（统一类型）
- 保留 `worker_profiles` API 端点兼容性，内部转换为统一模型

### WI-2: 行为文件三层全覆盖

**README 定义的三层**:

```
behavior/system/              → AGENTS.md, USER.md, TOOLS.md, BOOTSTRAP.md  (Global)
behavior/agents/{slug}/       → IDENTITY.md, SOUL.md, HEARTBEAT.md          (Agent-Private)
projects/{slug}/behavior/     → PROJECT.md, KNOWLEDGE.md                    (Project)
```

**每个层级的创建时机和填充方式**:

| 层级 | 创建时机 | 默认内容来源 | 创建者可立即定制 |
|------|---------|------------|----------------|
| Global | `octo-start` 首次启动（`ensure_filesystem_skeleton`） | 内置 Template | ✗ 后续通过 UI / CLI 修改 |
| Agent-Private | Agent 创建时（`materialize_agent_behavior_files`） | 内置 Template + 创建参数 | ✓ 创建 API 可接收初始内容 |
| Project | Project 创建时（`materialize_project_behavior_files`） | 内置 Template + 创建参数 | ✓ 创建 API 可接收初始内容 |

**需要新增/修改**:
- Agent 创建 API（`worker_profile.apply`）接受可选 `behavior_init` 参数，包含 `{file_id: content}` 映射
- Project 创建 API 同理接受 `behavior_init`
- 主 Agent 创建 Worker 时，LLM 可通过 `worker.create` 工具传入行为文件初始内容
- `materialize_agent_behavior_files()` 支持 `initial_content: dict[str, str]` 参数覆盖默认 Template

### WI-3: Worker 行为文件在资源模型和 UI 的可见性

**当前缺陷**:
- `WorkerProfileViewItem` 没有 `behavior_system` 字段
- `get_worker_profiles_document()` 不调用 `build_behavior_system_summary()`
- 前端 Worker 卡片不展示行为文件 chips

**改动**:
- `WorkerProfileViewItem`（→ 统一后的 `AgentProfileItem`）增加 `behavior_system` 字段
- `get_worker_profiles_document()`（→ 统一后的 `get_agent_profiles_document()`）为每个 profile 调用 `build_behavior_system_summary()`
- 前端 Agent 卡片统一展示行为文件 chips（IDENTITY.md / SOUL.md / HEARTBEAT.md）
- 点击行为文件 chip → modal 查看/编辑

### WI-4: 行为文件变更的 Context 实时更新

**现状**: 行为文件在磁盘上修改后，已运行的 Agent session 不会感知变更。下一次 LLM 调用时才会重新 `resolve_behavior_pack()`。

**Agent Zero 参考**: Agent Zero 的 system prompt 是每轮消息都重新装配（`message_loop_prompts_after/`），因此配置变更自然生效。

**OctoAgent 当前机制**: `_build_system_blocks()` 在每次 LLM 请求前调用 `resolve_behavior_pack()`，这意味着行为文件变更已经会在下一次 LLM 调用时生效。

**需确认/改进**:
- ✓ 文件系统读取 → 每次 LLM 调用时 resolve → 已经是"尽快更新"
- 需要增加：文件变更后在 UI 侧刷新 `behavior_system` 快照（通过 Control Plane 资源刷新）
- 可选增强：文件系统 watch（inotify/kqueue）→ 主动通知 SSE 客户端刷新

### WI-5: UI — 所有 Agent 的行为文件可查可改

**当前**: 只有主 Agent 展示行为文件 chips。

**目标**: 每个 Agent 卡片（无论主 Agent 还是 Worker）下方都展示其 Agent-Private 行为文件 chips，点击弹出 modal 查看/编辑。

**实现**:
- `renderAgentCard()` 接收 `behaviorFiles?: BehaviorManifestFile[]` 参数
- 如果 `behaviorFiles` 非空，在卡片底部渲染 chips
- 点击 chip → 调用 `handleOpenBehaviorFile(path, fileId)` → modal 展示

---

## 实施顺序建议

```
Phase 1: 数据对齐（WI-1 模型统一 + WI-3 资源可见性）
  ↓
Phase 2: 行为文件增强（WI-2 创建时初始化 + WI-5 UI 全覆盖）
  ↓
Phase 3: 实时性（WI-4 Context 更新确认 + 可选 FS watch）
```

Phase 1 是最核心的结构性改动，解决 AgentProfile/WorkerProfile 双模型裂缝。
Phase 2 完善行为文件的生命周期管理。
Phase 3 是锦上添花的实时性增强。

---

## 不在范围内

- Project 管理页面（用户说"还没有露出先不着急"）
- 行为文件的版本历史 / diff 对比
- 行为文件的 Git 集成（auto-commit on change）
- Worker 之间的行为文件继承（如"从主 Agent 继承 SOUL.md"）

## 依赖

- Feature 057（行为文件 materialize 和 LLM 工具读写）需先完成或同步推进
- 数据库 migration：WorkerProfile → 统一 AgentProfile 表需要编写迁移脚本

## 风险

| 风险 | 缓解 |
|------|------|
| 模型统一涉及大量代码改动 | 分 Phase 实施，Phase 1 先在资源层统一前端视图，不急着改底层表 |
| Worker behavior_system 查询增加启动时 IO | 按需加载 + 缓存 Pack 到 metadata |
| 前端类型大面积修改 | 先统一 TypeScript 类型定义，再逐页面适配 |
