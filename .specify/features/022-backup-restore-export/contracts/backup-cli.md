# Contract: CLI API — `octo backup` / `octo restore` / `octo export`

**Feature**: `022-backup-restore-export`
**Created**: 2026-03-07
**Traces to**: FR-001, FR-006, FR-009, FR-011, FR-014

---

## 契约范围

本文定义 022 的三条 CLI 主路径：

- `octo backup create`
- `octo restore dry-run`
- `octo export chats`

目标是保证 backup / dry-run / export 都有稳定入口，而不是留给用户手写 shell 命令。

---

## 1. 命令签名

```bash
octo backup create [--output PATH] [--label TEXT]
octo restore dry-run --bundle PATH [--target-root PATH]
octo export chats [--task-id TASK_ID] [--thread-id THREAD_ID] [--since ISO8601] [--until ISO8601] [--output PATH]
```

### 项目根解析

与 `octo config` 保持一致：
1. `OCTOAGENT_PROJECT_ROOT`
2. `Path.cwd()`

### 默认输出目录

| 命令 | 默认目录 |
|---|---|
| `octo backup create` | `data/backups/` |
| `octo export chats` | `data/exports/` |
| `octo restore dry-run` | 不写 bundle，仅更新 `data/ops/recovery-drill.json` |

---

## 2. `octo backup create`

### 行为

1. 解析项目根目录与 data 路径
2. 生成 SQLite 在线快照
3. 收集 config metadata / artifacts / chats 范围摘要
4. 生成 `manifest.json`
5. 打包为 ZIP bundle
6. 更新 `latest-backup.json`
7. 输出结构化摘要

### 输出语义

至少展示：
- bundle 路径
- bundle 大小
- scopes
- 默认排除的 secrets 文件
- 敏感性提示

### 返回码

| 返回码 | 含义 |
|---|---|
| `0` | backup 成功 |
| `1` | backup 失败（路径不可写、SQLite 备份失败、打包失败等） |

---

## 3. `octo restore dry-run`

### 行为

1. 读取 `--bundle`
2. 校验 ZIP / manifest / schema version / 必需文件
3. 对目标目录执行冲突检查
4. 生成 `RestorePlan`
5. 更新 `recovery-drill.json`
6. 输出计划摘要

### 约束

- 本命令不得写入目标 config / db / artifacts
- 本命令是 preview-first 路径，不承担真正 restore apply

### 输出语义

至少展示：
- bundle 是否兼容
- blocking conflicts 数量
- warnings 数量
- 建议下一步动作

### 返回码

| 返回码 | 含义 |
|---|---|
| `0` | dry-run 通过，无 blocking conflicts |
| `1` | dry-run 完成，但存在 blocking conflicts 或 bundle 无效 |
| `2` | 参数错误或 bundle 路径不存在 |

---

## 4. `octo export chats`

### 行为

1. 解析筛选条件（`task_id` / `thread_id` / `since` / `until`）
2. 读取 task/event/artifact 投影
3. 生成 `ExportManifest`
4. 写入默认输出目录或 `--output`
5. 输出导出摘要

### 约束

- 允许空结果导出，不应把“没有匹配会话”视为错误
- 若同时给出 `task_id` 与 `thread_id`，必须做交叉过滤并在 manifest 中记录

### 返回码

| 返回码 | 含义 |
|---|---|
| `0` | 导出成功（包括空结果） |
| `1` | 导出失败 |
| `2` | 参数格式错误 |

---

## 5. 禁止行为

- 不得让 `octo restore dry-run` 执行 destructive restore apply
- 不得默认打包 `.env` / `.env.litellm`
- 不得把 bundle manifest 隐藏为内部实现细节；用户必须能看到摘要
- 不得要求用户直接操作 SQLite 或 artifact 目录才能完成 chats export
