# Contract: CLI API — `octo import chats`

**Feature**: `021-chat-import-core`
**Created**: 2026-03-07
**Traces to**: FR-001, FR-002, FR-006, FR-012, FR-018

---

## 契约范围

本文定义 021 的最小用户入口：

- `octo import chats`

目标是保证用户能预览并执行聊天导入，而不是只能调用内部 Python API。

---

## 1. 命令签名

```bash
octo import chats \
  --input PATH \
  [--format normalized-jsonl] \
  [--source-id TEXT] \
  [--channel CHANNEL] \
  [--thread-id THREAD_ID] \
  [--dry-run] \
  [--resume]
```

### 参数说明

| 参数 | 必填 | 说明 |
|---|---|---|
| `--input PATH` | 是 | 输入文件路径 |
| `--format` | 否 | 当前仅支持 `normalized-jsonl`，默认即此值 |
| `--source-id` | 否 | 稳定导入源 ID；未提供时默认用输入路径归一化生成 |
| `--channel` | 否 | 覆盖输入消息中的 channel |
| `--thread-id` | 否 | 覆盖输入消息中的 thread_id |
| `--dry-run` | 否 | 只预览、不写副作用 |
| `--resume` | 否 | 读取最近 cursor / dedupe 状态继续导入 |

### 项目根解析

与 `octo config` / `octo backup` 保持一致：
1. `OCTOAGENT_PROJECT_ROOT`
2. `Path.cwd()`

---

## 2. `--dry-run`

### 行为

1. 解析输入文件与格式
2. 校验消息 schema
3. 读取现有 cursor / dedupe ledger（只读）
4. 计算新增数、重复数、目标 scope、窗口数
5. 输出结构化 `ImportReport`

### 约束

- 不写 `chat_import_*` 表
- 不写 artifact
- 不写 `CHAT_IMPORT_*` 事件
- 不写 SoR / Fragment

### 返回码

| 返回码 | 含义 |
|---|---|
| `0` | dry-run 成功 |
| `1` | dry-run 执行失败（格式错误、解析错误、业务校验失败） |
| `2` | 参数错误或输入文件不存在 |

---

## 3. 真实导入

### 行为

1. 解析输入文件与 CLI override
2. 初始化同连接的 core + memory + import schema
3. 创建 `ImportBatch`
4. 执行 dedupe / windowing
5. 写 raw window artifacts
6. 写 summary fragments
7. 对可治理 facts 执行 proposal -> validate -> commit
8. 更新 cursor / dedupe ledger / report
9. 写 `CHAT_IMPORT_*` lifecycle events
10. 输出导入摘要

### 输出语义

至少展示：
- `batch_id`
- `scope_id`
- `imported_count`
- `duplicate_count`
- `window_count`
- `proposal_count`
- `warnings` / `errors`

### 返回码

| 返回码 | 含义 |
|---|---|
| `0` | 导入成功 |
| `1` | 导入失败或部分失败导致批次失败 |
| `2` | 参数错误或输入文件不存在 |

---

## 4. 禁止行为

- 不得在 021 中通过 CLI 直接写 SoR current，绕过 proposal contract
- 不得在 dry-run 中产生任何持久化副作用
- 不得要求用户先手写 Python 脚本才能完成导入
- 不得把历史聊天默认写进当前 live session scope
