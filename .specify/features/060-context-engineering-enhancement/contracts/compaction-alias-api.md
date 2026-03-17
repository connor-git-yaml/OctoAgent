# API Contract: Compaction Alias Configuration

**Feature**: 060 Context Engineering Enhancement
**Module**: `packages/provider/src/octoagent/provider/alias.py` + Settings API
**Date**: 2026-03-17

## 概览

在 AliasRegistry 中新增 `compaction` 语义别名，支持用户在 Settings 中为上下文压缩指定独立的轻量模型。本契约定义 alias 注册、fallback 链和 Settings UI 展示规则。

---

## AliasRegistry 变更

### 新增默认 alias

```python
AliasConfig(
    name="compaction",
    category="cheap",
    runtime_group="cheap",
    description="上下文压缩（推荐轻量模型如 haiku / gpt-4o-mini）",
)
```

### Fallback 解析链

```
compaction -> summarizer -> main
```

解析逻辑在 `ContextCompactionService._call_summarizer()` 中实现（非 AliasRegistry 自身）：

1. 尝试 `compaction` alias -> 解析为 runtime_group -> 调用
2. 如果 (a) `compaction` 未在 Settings 中绑定具体模型 或 (b) 调用失败 -> 降级到 `summarizer`
3. 如果 `summarizer` 也失败 -> 降级到 `main`
4. 全部失败 -> 返回空摘要（降级保障）

### "未绑定"判断

AliasRegistry 中 `compaction` 默认映射到 `cheap` runtime_group。如果用户在 Settings 中没有为 `compaction` 绑定具体模型，它会解析到 `cheap` group 的默认模型——这是正确行为，不是"未绑定"。

真正的 fallback 场景是：
- `cheap` group 的 provider 不可用（网络/配额问题）
- 用户手动删除了 `compaction` alias

---

## Settings API 交互

### 读取

```
GET /api/setup/review
```

返回的 `aliases` 数组中包含 `compaction` 条目：

```json
{
    "aliases": [
        {
            "alias": "compaction",
            "provider": "openrouter",
            "model": "anthropic/claude-3-haiku",
            "description": "上下文压缩（推荐轻量模型）",
            "thinking_level": "off"
        },
        ...
    ]
}
```

如果用户未配置，`compaction` 条目显示为默认值：

```json
{
    "alias": "compaction",
    "provider": "",
    "model": "",
    "description": "上下文压缩（推荐轻量模型如 haiku / gpt-4o-mini）未配置，使用 summarizer",
    "thinking_level": "off"
}
```

### 写入

```
POST /api/setup/apply
```

```json
{
    "aliases": [
        {
            "alias": "compaction",
            "provider": "openrouter",
            "model": "anthropic/claude-3-haiku",
            "description": "上下文压缩"
        }
    ]
}
```

通过现有 `setup.review -> setup.apply` 流程验证（FR-024）。

---

## Settings 前端展示

### SettingsProviderSection.tsx 变更

在 alias 编辑器中，当 `alias === "compaction"` 时，在 alias 行下方显示辅助信息：

```
用途: 上下文压缩（推荐轻量模型如 haiku / gpt-4o-mini）
Fallback: compaction -> summarizer -> main
```

使用现有的 alias 编辑 UI 组件，不需要新增独立区域。

---

## 事件记录

压缩完成事件 `CONTEXT_COMPACTION_COMPLETED` 的 payload 字段（`ContextCompactionCompletedPayload`）：

```json
{
    "model_alias": "compaction",          // 实际使用的 alias
    "input_tokens_before": 500,           // 压缩前 token 数
    "input_tokens_after": 200,            // 压缩后 token 数
    "compressed_turn_count": 6,           // 被压缩的轮次数
    "kept_turn_count": 2,                 // 保留的轮次数
    "summary_artifact_ref": "artifact-001", // 摘要 Artifact 引用
    "request_artifact_ref": "artifact-002", // 请求快照 Artifact 引用
    "memory_flush_run_id": "flush-001",   // Memory 刷新运行 ID
    "reason": "history_over_budget",      // 压缩原因
    "fallback_used": false,               // 是否触发了 fallback
    "fallback_chain": ["compaction"],     // 实际走过的 alias 链（含去重）
    "compaction_phases": [                // 两阶段压缩执行详情
        {"phase": "cheap_truncation", "messages_affected": 2, "tokens_saved": 600},
        {"phase": "llm_summary", "messages_affected": 1, "tokens_saved": 400, "model_used": "compaction"}
    ],
    "layers": [                           // 三层压缩各层级审计信息
        {"layer_id": "recent", "turns": 2, "token_count": 100, "max_tokens": 150, "entry_count": 2},
        {"layer_id": "compressed", "turns": 4, "token_count": 80, "max_tokens": 90, "entry_count": 1},
        {"layer_id": "archive", "turns": 6, "token_count": 40, "max_tokens": 60, "entry_count": 1}
    ],
    "compaction_version": "v2"            // 压缩版本: "v1"(扁平) | "v2"(三层)
}
```

**字段说明**：

| 字段 | 类型 | 默认值 | 来源 Phase | 说明 |
|------|------|--------|-----------|------|
| `model_alias` | string | `"summarizer"` | Phase 1 | 实际使用的模型别名 |
| `input_tokens_before` | int | 0 | M0 | 压缩前估算 token 数 |
| `input_tokens_after` | int | 0 | M0 | 压缩后估算 token 数 |
| `compressed_turn_count` | int | 0 | M0 | 被压缩的对话轮次数 |
| `kept_turn_count` | int | 0 | M0 | 保留原文的轮次数 |
| `summary_artifact_ref` | string? | null | M0 | 摘要文本 Artifact ID |
| `request_artifact_ref` | string? | null | M0 | 请求快照 Artifact ID |
| `memory_flush_run_id` | string? | null | M0 | 关联的 Memory 刷新运行 ID |
| `reason` | string | `""` | M0 | 压缩触发原因 |
| `fallback_used` | bool | false | Phase 1 | 是否触发了 alias fallback |
| `fallback_chain` | list[str] | [] | Phase 1 | 实际走过的 alias 链 |
| `compaction_phases` | list[dict] | [] | Phase 2 | 两阶段压缩执行详情 |
| `layers` | list[dict] | [] | Phase 3 | 三层压缩各层级审计 |
| `compaction_version` | string | `""` | Phase 3 | 压缩版本标识 |

---

## ContextCompactionConfig 扩展

新增环境变量：

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `OCTOAGENT_CONTEXT_COMPACTION_ALIAS` | `"compaction"` | 压缩专用 alias 名称 |
| `OCTOAGENT_CONTEXT_RECENT_RATIO` | `0.50` | Recent 层 token 预算比例 |
| `OCTOAGENT_CONTEXT_COMPRESSED_RATIO` | `0.30` | Compressed 层 token 预算比例 |
| `OCTOAGENT_CONTEXT_ARCHIVE_RATIO` | `0.20` | Archive 层 token 预算比例 |
| `OCTOAGENT_CONTEXT_COMPRESSED_WINDOW` | `4` | Compressed 层分组窗口大小 |
| `OCTOAGENT_CONTEXT_ASYNC_COMPACTION_TIMEOUT` | `10.0` | 后台压缩超时秒数 (1.0-60.0) |

用户也可通过 Settings API 动态修改 `compaction` alias 的绑定（优先级高于环境变量默认值）。
