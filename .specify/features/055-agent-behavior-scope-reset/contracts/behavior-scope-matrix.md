# Behavior Scope Matrix

## 1. 默认归属

| 文件 | 默认归属 | 默认目录 | 主要作用 | 默认可见对象 |
|---|---|---|---|---|
| `AGENTS.md` | Shared | `behavior/system/` | 全 Agent 共享的操作原则、委派与回答契约 | 所有 Agent |
| `USER.md` | Shared | `behavior/system/` | 用户长期偏好、默认地点/时区、协作习惯 | 所有 Agent（可按策略过滤） |
| `TOOLS.md` | Shared | `behavior/system/` | 工具治理、审批、路径与默认能力边界 | 所有 Agent |
| `BOOTSTRAP.md` | Shared | `behavior/system/` | 会话启动、上下文注入与初始化规则 | 所有 Agent |
| `IDENTITY.md` | Agent Private | `behavior/agents/<agent>/` | 角色身份、专长、角色使命 | 当前 Agent |
| `SOUL.md` | Agent Private | `behavior/agents/<agent>/` | 语气、风格、价值排序 | 当前 Agent |
| `HEARTBEAT.md` | Agent Private | `behavior/agents/<agent>/` | 节奏、打断与协作方式 | 当前 Agent |
| `PROJECT.md` | Project Shared | `projects/<project>/behavior/` | 项目目标、术语、成功标准 | 当前项目内所有 Agent |
| `KNOWLEDGE.md` | Project Shared | `projects/<project>/behavior/` | 项目知识入口与关键文档说明 | 当前项目内所有 Agent |
| `USER.md` | Project Shared Override | `projects/<project>/behavior/` | 项目内用户协作偏好 override | 当前项目内所有 Agent |
| `TOOLS.md` | Project Shared Override | `projects/<project>/behavior/` | 项目内工具/路径/environment override | 当前项目内所有 Agent |

## 2. 高级 Overlay

| 文件 | 高级目录 | 用途 |
|---|---|---|
| `IDENTITY.md` | `projects/<project>/behavior/agents/<agent>/` | 仅当前项目内该 Agent 的身份补充 |
| `SOUL.md` | `projects/<project>/behavior/agents/<agent>/` | 仅当前项目内该 Agent 的风格补充 |
| `TOOLS.md` | `projects/<project>/behavior/agents/<agent>/` | 仅当前项目内该 Agent 的工具边界补充 |
| `PROJECT.md` | `projects/<project>/behavior/agents/<agent>/` | 仅当前项目内该 Agent 的项目工作视角补充 |

## 3. Effective 解析顺序

```text
system_shared
-> agent_private
-> project_shared
-> project_agent
-> project_path_manifest
-> runtime hints / session facts / recall capsule
```

## 4. 边界约束

- `Behavior Files`：存规则，不存动态事实
- `Memory`：存事实，不存密钥，不替代 behavior 文件
- `Secrets`：存敏感值，允许 project-scoped，但不进 md
- `Project Workspace`：存代码、数据、文档、notes、artifacts

## 5. Bootstrap 模板与路由

### 默认模板集合

- Shared: `AGENTS.md`、`USER.md`、`TOOLS.md`、`BOOTSTRAP.md`
- Agent Private: `IDENTITY.md`、`SOUL.md`、`HEARTBEAT.md`
- Project Shared: `PROJECT.md`、`KNOWLEDGE.md`、`USER.md`、`TOOLS.md`、`instructions/README.md`
- 具体模板骨架见：`contracts/behavior-template-skeletons.md`

### Bootstrap 采集结果的落点

- 用户事实、长期偏好、时区/地点：进入 `Memory`
- 默认会话 Agent 的名字/性格偏好：进入 behavior proposal，目标通常为 `IDENTITY.md / SOUL.md`
- 敏感信息：进入 `project.secret-bindings.json` 对应的 secret bindings 元数据与 `SecretService` 主链

## 6. Project Path Manifest

任意 Agent 的 effective context 都应附带一份 `project_path_manifest`，至少包括：

- `project_root`
- `project_behavior_root`
- `project_workspace_root`
- `project_data_root`
- `project_notes_root`
- `project_artifacts_root`
- `shared_behavior_root`
- `agent_behavior_root`
- `effective_behavior_files`
  - `file_id`
  - `path`
  - `scope`
  - `editable_mode`
  - `review_mode`

## 7. handoff 约束

- subordinate / worker continuation 不得只裸转发原始用户问题
- handoff 应复用：
  - `project_path_manifest`
  - `effective_behavior_source_chain`
  - `shared/project instructions summary`
  - `agent private identity summary`

## 8. Web 管理入口

- `Agents`：
  - Shared Files
  - Agent Private
  - Project Shared
  - Project-Agent Override
  - Effective View
  - Project Path Manifest
  - Editability / Review Mode

- `Settings`：
  - 不再承担 behavior 文件管理
