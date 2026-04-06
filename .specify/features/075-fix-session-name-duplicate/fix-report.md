# 问题修复报告

## 问题描述
1. **Bug 1**：默认会话的 Agent 名称显示错误（如 "Agent" / "OctoAgent"），应跟随主 Agent 名称（如 "Octo"）
2. **Bug 2**：用户向 AgentCenter 创建的会话发第一条消息后，侧边栏出现多余的重复 "Default Project" 会话

## 5-Why 根因追溯

### Bug 2（重复会话）

| 层级 | 问题 | 发现 |
|------|------|------|
| Why 1 | 为何第一条消息后出现重复会话？ | 第二遍（DIRECT_WORKER 会话）的 projected_session_id 与第一遍（Task-based）不匹配 |
| Why 2 | 为何两个 session_id 不匹配？ | 第一遍包含 `scope:` 段，第二遍没有（因为 thread_id 为空） |
| Why 3 | 为何第二遍没有 scope：段？ | `_build_session_projection_items` 第二遍：`scope_id = f"project:{pid}:chat:web:{thread_id}" if thread_id else ""`，thread_id 为空导致 scope_id = "" |
| Why 4 | 为何 thread_id 为空？ | `_handle_agent_create_worker_with_project` 创建 `AgentSession` 时没有设置 `thread_id` 或 `legacy_session_id` |
| Why 5 | 为何未被现有机制捕获？ | `_handle_session_create_with_project` 正确设置了 thread_id，但 `_handle_agent_create_worker_with_project` 遗漏了，两条路径不一致，无测试覆盖 |

**Root Cause**: `_handle_agent_create_worker_with_project` (agent_service.py:710) 创建 DIRECT_WORKER AgentSession 时未设置 `thread_id` / `legacy_session_id`，导致 projected_session_id 格式与后续 Task 不一致
**Root Cause Chain**: 重复会话 → projected_session_id 不匹配 → DIRECT_WORKER 无 thread_id → 创建路径遗漏 → 无测试覆盖

### Bug 1（Agent 名称错误）

| 层级 | 问题 | 发现 |
|------|------|------|
| Why 1 | 为何默认会话显示错误的 Agent 名称？ | 前端 `conversationOwnerName` 降级到 "OctoAgent" 硬编码（无会话时）或 "Agent"（`resolveAgentName` 找不到时） |
| Why 2 | 为何 `resolveAgentName` 找不到主 Agent？ | `resolveAgentName` 只在 `workerProfileList`（WorkerProfiles）中查找，主 Agent 使用 AgentProfile，不在列表中 |
| Why 3 | 为何 default_profile_name 为空？ | `worker_service.py` 中 `default_profile = next(item for item in items if ...)` 在 WorkerProfiles 中找不到 AgentProfile ID，返回 None，导致 `default_profile_name = ""` |
| Why 4 | 为何不在 WorkerProfiles 中？ | `get_worker_profiles_document` 中 `items` 只包含 WorkerProfiles，而默认项目的 `default_agent_profile_id` 指向 AgentProfile |
| Why 5 | 为何未被捕获？ | 两种 profile 类型（AgentProfile vs WorkerProfile）共用一套 ID 命名空间，但文档接口只返回 WorkerProfile，存在缺口 |

**Root Cause**: `worker_service.py` 构建 `WorkerProfilesDocument.summary` 时，`default_profile_name` 没有对 AgentProfile 类型的 default profile 做查找回退
**Root Cause Chain**: 名称显示错误 → 前端找不到主 Agent 名称 → `default_profile_name = ""` → 后端只在 WorkerProfiles 中查找 → AgentProfile 不在其中 → 无回退逻辑

## 影响范围扫描

### 同源问题（需同步修复）

| 文件 | 位置 | 模式 | 修复动作 |
|------|------|------|----------|
| `agent_service.py` | L710-722 | DIRECT_WORKER AgentSession 无 `thread_id` | 添加 `thread_id` + `legacy_session_id` |
| `agent_service.py` | L718 | `surface="chat"` 与 session_projection 期望 `"web"` 不一致 | 改为 `surface="web"` |
| `worker_service.py` | L321-324, 362 | `default_profile_name` 未对 AgentProfile 做回退 | 添加 AgentProfile 查找回退 |
| `ChatWorkbench.tsx` | L504-508 | `conversationOwnerName` 不使用 `default_profile_name` | 使用后端传来的 `default_profile_name` |

### 类似模式（需评估）

| 文件 | 位置 | 模式 | 评估结果 |
|------|------|------|----------|
| `WorkbenchLayout.tsx` | L299-302 | `resolveAgentName` 只查 WorkerProfiles | 安全（因为 session_owner_name 会被正确填充） |
| `session_service.py` | L589-598 | 第二遍 scope_id 逻辑 | 安全（修复 thread_id 后自动修复） |

### 同步更新清单
- 调用方: 无需额外修改
- 测试: 建议补充 `_handle_agent_create_worker_with_project` 创建 session 的 thread_id 断言
- 文档: 无需

## 修复策略

### 方案 A（推荐）：最小化精准修复

**Bug 2**：
在 `_handle_agent_create_worker_with_project` 创建 AgentSession 时：
1. 生成 `thread_id_seed = f"thread-{str(ULID())}"`
2. 设置 `thread_id=thread_id_seed, legacy_session_id=thread_id_seed`
3. 修改 `surface="chat"` → `surface="web"`（与 session_projection 对齐）

**Bug 1**：
在 `worker_service.py` `get_worker_profiles_document` 中：当 `default_profile` 为 None 时，额外查找 AgentProfile 并返回其名称作为 `default_profile_name`。
在 `ChatWorkbench.tsx` 中：从 summary 读取 `default_profile_name` 作为 `conversationOwnerName` 的第 3 优先级降级。

### 方案 B（备选）：将主 AgentProfile 注入 WorkerProfiles 文档
将默认 AgentProfile 作为一条特殊 item 加入 `WorkerProfilesDocument.profiles`，但这会改变文档语义，风险更高。

## Spec 影响

- 无需更新 spec 文件（此次为 bug fix，无架构变更）
