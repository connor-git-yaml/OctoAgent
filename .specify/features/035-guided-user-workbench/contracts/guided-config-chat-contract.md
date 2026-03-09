# Contract: Guided Config + Chat Workbench Integration

## 1. Graphical Config Contract

### 1.1 Data Source

设置页只允许消费以下 canonical inputs：

- `GET /api/control/resources/config`
- `GET /api/control/resources/project-selector`
- `GET /api/control/resources/wizard`
- 033 完成后补充：
  - profile / bootstrap / context provenance resource

### 1.2 Field Grouping

设置页可以按用户语言分组，但字段真相源不变：

| 用户分组 | 字段来源 | 说明 |
|---|---|---|
| `Main Agent` | `config.schema` + `ui_hints` + 033 resources | provider/model/policy/profile/persona 相关 |
| `Work` | `config.schema` + delegation defaults | tool profile / approval / work routing / runtime defaults |
| `Memory` | `config.schema` + memory backend summary | backend、maintenance、vault policy、可见性 |
| `Channels` | `config.schema` + diagnostics/channel summary | Telegram mode、pairing、webhook/polling、allowlist |
| `Projects` | `project-selector` | current project/workspace 切换与继承 |

如果某个用户分组需要的字段不存在于 canonical resource 中：

1. 先补 backend `ui_hints` 或 canonical resource
2. 再做 UI

禁止在前端直接发明字段。

### 1.3 Save Flow

保存流程固定为：

1. 读取当前 `ConfigSchemaDocument.current_value`
2. 用户编辑 draft
3. 前端做 schema-friendly 的基础校验
4. 通过 `config.apply` action 提交
5. 按 `resource_refs` 刷新 snapshot/config/diagnostics
6. 反馈成功、失败或 degraded

禁止：

- 私有 `/save-settings`
- 直接写 YAML
- 跳过 backend validation

## 2. Chat Workbench Contract

### 2.1 Request / Stream Chain

聊天发送只允许以下链路：

1. `POST /api/chat/send`
2. `EventSource(/api/stream/task/{task_id})`
3. 读取 `/api/tasks/{task_id}` 获取 durable event/artifact truth
4. 读取 `/api/tasks/{task_id}/execution` 获取 execution truth
5. 读取 `sessions` / `delegation` / `memory` 获取上下文抽屉内容

### 2.2 Right Drawer Composition

右侧上下文抽屉必须按以下顺序组织：

1. `当前任务`
   - task status / latest event / artifact refs
2. `当前工作`
   - work status / child work / next actions
3. `待你确认`
   - approvals / execution input / pairing / retry
4. `记忆与上下文`
   - memory summary
   - 033 provenance
   - 034 compaction
5. `更多诊断`
   - advanced deep links

### 2.3 Action Rules

聊天页按钮只允许调用：

- `session.focus`
- `session.export`
- `operator.approval.resolve`
- `work.cancel`
- `work.retry`
- `work.split`
- `work.merge`
- `work.escalate`
- `POST /api/tasks/{task_id}/execution/input`

### 2.4 033 / 034 Dependency Contract

- 033 提供：
  - profile/bootstrap/context provenance
  - current context degraded reason
  - context frame ref
- 034 提供：
  - compaction summary
  - artifact refs
  - degradation / fallback signal

035 只能：

- 读取并展示
- 用人话解释
- 在 advanced/detail 中显示原始 refs

035 不能：

- 自己拼 profile/context prompt
- 自己判断是否发生 compaction
- 自己生成 provenance 记录

## 3. Fallback Semantics

- 如果聊天已可用但 033 未接好：聊天继续工作，右栏 context 显示 `pending/degraded`
- 如果 execution session 不存在：聊天仍显示任务状态，但隐藏 execution panel
- 如果 memory unavailable：右栏保留 memory card，显示安全降级说明
- 如果 approval/action 不支持当前 surface：明确显示 `请到 Advanced 继续` 或对应 action 不可用原因
