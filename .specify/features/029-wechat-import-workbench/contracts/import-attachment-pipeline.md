# Contract: Import Attachment Pipeline

**Feature**: `029-wechat-import-workbench`  
**Created**: 2026-03-08  
**Traces to**: FR-013 ~ FR-017

---

## 契约范围

本文定义多源附件从 source adapter 到 artifact / fragment / MemU integration point 的最小管线契约。

---

## 1. 处理顺序

附件处理必须按以下顺序：

1. source adapter 识别附件引用
2. 导入执行时 materialize 为 artifact
3. 生成可检索 fragment/ref 或 attachment summary
4. 如 MemU 可用，则执行 best-effort sync
5. 将结果汇总到 `ImportRunDocument` / `ImportReport`

---

## 2. `ImportAttachmentEnvelope`

最小结构：

```json
{
  "attachment_id": "att-001",
  "source_id": "wechat-source-001",
  "conversation_key": "wechat:chat:alice",
  "source_message_id": "msg-001",
  "source_path": "/path/to/media/1.jpg",
  "mime_type": "image/jpeg",
  "checksum": "sha256:...",
  "artifact_id": "artifact-001",
  "fragment_ref_id": "fragment-001",
  "memu_sync_state": "synced",
  "warnings": []
}
```

---

## 3. Artifact Rules

- 所有成功 materialize 的附件 MUST 产生 `artifact_id`
- artifact metadata MUST 保留：
  - `source_type`
  - `source_id`
  - `conversation_key`
  - `source_message_id`
  - `source_path` 或等价引用
  - `mime_type`
  - `checksum`

---

## 4. Fragment / Searchable Ref Rules

- 附件本体不应直接塞进主 Memory 文本内容
- fragment 或 searchable ref 只保存：
  - 摘要
  - 转录文本（如果可用）
  - caption / link / filename
  - 指向 artifact 的引用

---

## 5. MemU Integration Rules

允许的最小状态：

- `pending`
- `synced`
- `degraded`
- `skipped`

### 规则

- MemU sync 是 `best-effort`
- MemU unavailable 时，导入主流程不得失败
- MemU degraded 必须落 warning，并反映到 `ImportRunDocument.memory_effects`

---

## 6. Failure / Partial Success Rules

### 6.1 附件 materialization 失败

- 主消息可继续导入
- 当前 run 必须记录 warning/error
- 状态可进入 `partial_success`

### 6.2 MIME 不可识别

- 允许继续 artifact 化
- 以 `application/octet-stream` 或等价 degraded metadata 表达

### 6.3 MemU sync 失败

- 只影响 `memu_sync_state`
- 不影响 artifact/proposal 主路径

---

## 7. 禁止行为

- 不允许把附件原始二进制内容直接塞进 `ImportedChatMessage.text`
- 不允许附件绕过 artifact 直接进入 Memory 权威事实层
- 不允许 MemU sync 失败被静默吞掉
