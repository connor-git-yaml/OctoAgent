---
feature_id: "056"
title: "Clean Install Skeleton Bootstrap"
milestone: "M4"
status: "In Progress"
created: "2026-03-15"
updated: "2026-03-15"
predecessor: "Feature 055"
blueprint_ref: "docs/blueprint.md §BehaviorWorkspace §Bootstrap"
---

# Feature Specification: Clean Install Skeleton Bootstrap

**Feature Branch**: `claude/bold-aryabhata`
**Created**: 2026-03-15
**Status**: In Progress

## Problem Statement

Feature 055 完成了四层 BehaviorWorkspaceScope 的定义、path_manifest 注入、Agents 页 Behavior Center、bootstrap 六步问卷 schema 等设计。但 **clean install 后系统处于空壳状态**：

经过完整的 clean install + Gateway 首次启动验证，发现以下关键缺失：

1. **behavior 目录骨架不存在** — `behavior/system/`、`behavior/agents/<agent>/`、`projects/default/behavior/` 全部未创建
2. **默认 agent profile 未创建** — agent_profiles 表为空，Agents 页无法展示任何 Agent
3. **bootstrap session 未创建** — bootstrap_sessions 表为空，六步问卷无法被前端发现
4. **默认 behavior 模板文件不存在** — IDENTITY.md、SOUL.md 等 scaffold 文件未写入磁盘
5. **project.secret-bindings.json 不存在** — Agent 的 path_manifest 指向空路径
6. **workspace_root_path 透传断裂** — DB 中 workspace.root_path = ~/.octoagent，但 resolve_behavior_workspace 默认回退到 projects/default/workspace/（一个不存在的目录）
7. **Butler 前端字符串硬编码** — presentation.ts 中 `"butler" → "主助手"` 应改用 isMainAgent 标志

## Solution

在 Gateway startup（lifespan）阶段，紧接 `ensure_default_project()` 之后，补齐所有 clean install 后应存在的文件系统骨架和 DB 记录。

### Slice A - 文件系统骨架创建

在 `ProjectWorkspaceMigrationService` 或新的 startup helper 中：
- `mkdir -p` 基础目录：`behavior/system/`、`behavior/agents/butler/`、`projects/default/behavior/`、`projects/default/behavior/agents/butler/`
- 写入空的 `projects/default/project.secret-bindings.json`（`{}`）
- 写入最小 behavior 模板文件（IDENTITY.md、SOUL.md 的 scaffold）

### Slice B - 默认 Agent Profile + Bootstrap Session 的 Startup 创建

当前 agent profile 和 bootstrap session 只在聊天路径触发。需要把 lazy init 提前到 startup：
- 在 lifespan 中调用 `_ensure_default_agent_profiles()` 或等价逻辑
- 在 lifespan 中调用 `_ensure_bootstrap_session()` 或等价逻辑

### Slice C - workspace_root_path 透传修复

确保 orchestrator 调用 `resolve_behavior_pack()` 时，从 DB 的 `workspaces.root_path` 读取并传入 `workspace_root_path`，而不是让它回退到 `project_workspace_dir()`。

### Slice D - Butler 前端去硬编码

- `presentation.ts` 中 `"butler" → "主助手"` 改为通过 agent profile 的 isMainAgent 标志判断
- `ChatWorkbench.tsx` 中 `"butler" → "正在理解问题..."` 同理

## Acceptance Criteria

1. `install-octo-home.sh --force && octo-start` 后，`~/.octoagent` 下存在完整目录骨架
2. Gateway 首次启动后，agent_profiles 表有默认 profile，bootstrap_sessions 表有 PENDING session
3. `projects/default/project.secret-bindings.json` 存在
4. `behavior/system/` 下有最小 scaffold 文件
5. Agent 系统 prompt 中 `project_workspace_root` 指向正确路径（~/.octoagent）
6. 前端不再硬编码 "butler" 字符串做 UI 判断
