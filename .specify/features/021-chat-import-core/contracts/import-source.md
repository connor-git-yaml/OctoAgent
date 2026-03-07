# Contract: Generic Source Format — `normalized-jsonl`

**Feature**: `021-chat-import-core`
**Created**: 2026-03-07
**Traces to**: FR-003, FR-004, FR-007, FR-016

---

## 契约范围

本文定义 021 本轮冻结的 generic source contract。未来微信 / Slack / Telegram 历史 adapter 只需要把原始导出转换成这个格式，即可复用 021 内核。

---

## 1. 文件格式

- 文件类型：JSONL
- 编码：UTF-8
- 每行一个 `ImportedChatMessage` JSON object
- 当前唯一支持格式名：`normalized-jsonl`

---

## 2. 行结构

```json
{
  "source_message_id": "msg-001",
  "source_cursor": "cursor-001",
  "channel": "wechat_import",
  "thread_id": "project-alpha",
  "sender_id": "u-1",
  "sender_name": "Connor",
  "timestamp": "2026-03-07T09:30:00Z",
  "text": "今天把备份流程先补齐。",
  "attachments": [],
  "metadata": {
    "source_file": "wechat-export-20260307.txt"
  },
  "fact_hints": [
    {
      "subject_key": "project.alpha.backup.status",
      "content": "备份流程需要先补齐。",
      "rationale": "消息中明确提到当前项目状态",
      "confidence": 0.82,
      "partition": "chat",
      "is_sensitive": false
    }
  ]
}
```

---

## 3. 字段规则

### 必填字段

- `channel`
- `thread_id`
- `sender_id`
- `timestamp`
- `text`

### 可选字段

- `source_message_id`
  - 存在时优先作为 dedupe key 的主来源
- `source_cursor`
  - 存在时用于 resume / incremental import
- `sender_name`
- `attachments`
- `metadata`
- `fact_hints`

### CLI Override

若命令行提供：
- `--channel`
- `--thread-id`

则 CLI override 优先于文件中的同名字段。

---

## 4. `fact_hints`

`fact_hints` 是 021 MVP 对接 020 proposal contract 的最小桥梁。

### 规则

- 可为空
- 非空时，每条 hint 都必须能映射为一条 `WriteProposal`
- 若 hint 验证失败，导入流程不得崩溃；必须降级记录错误并继续其他窗口
- 没有 hint 的聊天仍然可以导入，只是只写 fragment / artifact，不写 SoR

---

## 5. 兼容性要求

- future adapter 只需产出 `normalized-jsonl`，不需要接触 memory / artifact / event internals
- 021 不要求 source 提供真实 message id；没有时使用 hash 去重
- source 行顺序默认视为原始时间顺序；若时间乱序，系统按 `timestamp` 排序后分窗

---

## 6. 禁止行为

- 不允许 future adapter 直接写 `memory_*` 或 `chat_import_*` 表
- 不允许把 source 自定义字段混成顶层未声明字段；应进入 `metadata`
- 不允许把未经约束的大正文摘要或系统输出直接伪装成 `fact_hints`
