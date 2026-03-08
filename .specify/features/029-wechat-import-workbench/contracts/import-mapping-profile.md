# Contract: Import Mapping Profile

**Feature**: `029-wechat-import-workbench`  
**Created**: 2026-03-08  
**Traces to**: FR-007, FR-008, FR-009, FR-010, FR-011

---

## 契约范围

本文定义 source conversation 到 `project/workspace/scope/partition` 的 durable mapping contract。

---

## 1. Mapping Profile 结构

```json
{
  "mapping_id": "mapping-001",
  "source_id": "wechat-source-001",
  "source_type": "wechat",
  "project_id": "project-default",
  "workspace_id": "workspace-default",
  "conversation_mappings": [
    {
      "conversation_key": "wechat:chat:alice",
      "conversation_label": "Alice",
      "project_id": "project-default",
      "workspace_id": "workspace-default",
      "scope_id": "chat:wechat_import:alice",
      "partition": "chat",
      "sensitivity": "default",
      "enabled": true
    }
  ],
  "sender_mappings": [],
  "attachment_policy": "artifact-first",
  "memu_policy": "best-effort"
}
```

---

## 2. 规则

### 2.1 Project / Workspace 约束

- `project_id` / `workspace_id` 必须是当前可见且有效的选择目标
- `scope_id` 必须与目标 project/workspace 语义兼容
- 不允许把 source conversation 默认映射到未绑定或未知 project

### 2.2 Conversation 映射

- 每个 `conversation_key` 最多对应一个当前有效 mapping
- `enabled=false` 表示本次导入显式跳过该 conversation
- 未配置 mapping 的 conversation 不得进入真实导入

### 2.3 Sender 映射

- sender mapping 主要用于稳定 actor hint 和后续显示
- 缺失 sender mapping 不应阻断 preview，但可能降级为 warning

### 2.4 Attachment / MemU 策略

允许的最小值：

- `attachment_policy`
  - `artifact-first`
- `memu_policy`
  - `best-effort`
  - `skip`

---

## 3. Validation Rules

保存 mapping 时至少检查：

- source 是否存在
- project/workspace 是否存在
- conversation keys 是否属于 detect 结果
- `scope_id` 是否为空或冲突
- strategy 值是否在允许集合内

### Validation 失败结果码

- `IMPORT_MAPPING_SOURCE_NOT_FOUND`
- `IMPORT_MAPPING_PROJECT_INVALID`
- `IMPORT_MAPPING_WORKSPACE_INVALID`
- `IMPORT_MAPPING_CONVERSATION_UNKNOWN`
- `IMPORT_MAPPING_SCOPE_INVALID`

---

## 4. Durability Rules

- mapping profile 必须是 project-scoped durable object
- workbench 重载后必须能恢复最近使用的 mapping
- CLI 与 Web 必须读取同一 mapping 事实源

---

## 5. 禁止行为

- 不允许只把 mapping 保存在前端表单状态
- 不允许在真实导入时对未映射 conversation 自动猜 scope
- 不允许 sender/conversation 自定义字段绕过 validation 直接进入 021 import core
