# Contract: Import Source Adapter

**Feature**: `029-wechat-import-workbench`  
**Created**: 2026-03-08  
**Traces to**: FR-002 ~ FR-007, FR-021

---

## 契约范围

本文定义 029 的 source adapter contract，以及首个 WeChat adapter 的最低兼容要求。

---

## 1. Adapter 生命周期

每个 source adapter 必须支持三个阶段：

1. `detect`
2. `preview`
3. `materialize`

### `detect`

目的：

- 判断输入是否可识别
- 提取 source metadata
- 枚举 conversation / account / media roots
- 返回 warnings/errors

### `preview`

目的：

- 基于 mapping 生成 dry-run 结果
- 返回 counts、dedupe、warnings/errors、目标 scope 预览
- 不产生副作用

### `materialize`

目的：

- 输出兼容 021 的 `ImportedChatMessage` 流
- 输出 attachment descriptors / provenance hints
- 供 021 import core 消费

---

## 2. 通用接口

```python
class ImportSourceAdapter(Protocol):
    source_type: str

    async def detect(self, input_ref: ImportInputRef) -> ImportSourceDocument: ...
    async def preview(
        self,
        input_ref: ImportInputRef,
        mapping: ImportMappingProfile | None,
    ) -> ImportRunDocument: ...
    async def materialize(
        self,
        input_ref: ImportInputRef,
        mapping: ImportMappingProfile,
    ) -> AsyncIterator[ImportedChatMessage]: ...
```

### 规则

- `detect` / `preview` MUST NOT 写入 artifact、memory、event、report durability
- `materialize` MUST NOT 直接写入 Memory / Artifact Store
- adapter 输出的消息 MUST 能被 021 `ChatImportService.import_chats()` 消费

---

## 3. WeChat Adapter 最低输入集

WeChat adapter 默认支持以下离线输入之一：

- 导出目录
- HTML 导出文件
- JSON 导出文件
- SQLite snapshot

### 输入要求

`ImportInputRef` 最小字段：

```json
{
  "source_type": "wechat",
  "input_path": "/path/to/export",
  "media_root": "/path/to/media",
  "format_hint": "html"
}
```

### 规则

- adapter MUST 允许 `media_root` 为空，但要在 detect/preview 中明确 warnings
- adapter MUST NOT 依赖在线账号登录、服务端 API 拉取或运行时进程注入作为主路径
- adapter SHOULD 尽量从离线导出物中提取 conversation 和 account metadata

---

## 4. `detect` 输出

最小返回：

```json
{
  "resource_type": "import_source",
  "resource_id": "import-source:wechat:source-001",
  "source_id": "wechat-source-001",
  "source_type": "wechat",
  "detected_conversations": [
    {
      "conversation_key": "wechat:chat:alice",
      "label": "Alice",
      "message_count": 124
    }
  ],
  "attachment_roots": ["/path/to/media"],
  "warnings": [],
  "errors": []
}
```

### 规则

- `errors` 非空时，真实导入 MUST fail-closed
- `warnings` 非空时，允许进入 preview，但 UI/CLI 必须显式展示

---

## 5. `preview` 输出

`preview` 必须返回 `ImportRunDocument`，至少包含：

- source / project / workspace
- `dry_run = true`
- mapping 预览
- counts
- dedupe detail 摘要
- cursor / resume 信息
- warnings/errors

### 规则

- 若 mapping 缺失或不合法，`status` 必须是 `action_required`
- `artifact_refs` 在 preview 中必须为空
- preview 结果必须可被 Control Plane / CLI 同时消费

---

## 6. `materialize` 输出

每条消息至少应满足：

```json
{
  "source_message_id": "msg-001",
  "source_cursor": "cursor-001",
  "channel": "wechat_import",
  "thread_id": "mapped-thread",
  "sender_id": "wechat:user:alice",
  "timestamp": "2026-03-08T10:00:00Z",
  "text": "hello",
  "attachments": [],
  "metadata": {
    "source_type": "wechat",
    "conversation_key": "wechat:chat:alice"
  }
}
```

### 规则

- `channel` / `thread_id` 必须已经过 mapping 和 normalization
- source-specific 字段必须进入 `metadata`
- 若缺失 `source_message_id`，仍必须保证后续 hash dedupe 可用

---

## 7. 明确禁止

- adapter 不得直接写 `chat_import_*`、`memory_*` 或 `artifacts` 持久化
- adapter 不得把 source-specific 未声明字段混入顶层，必须进入 `metadata`
- WeChat adapter 不得把“在线抓取聊天历史”作为默认主路径
