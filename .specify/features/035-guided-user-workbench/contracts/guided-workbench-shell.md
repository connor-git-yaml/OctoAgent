# Contract: Guided Workbench Shell

## 1. Route Map

035 的页面路由冻结为：

- `/` -> `Home`
- `/chat` -> `ChatWorkbench`
- `/work` -> `WorkbenchBoard`
- `/memory` -> `MemoryCenter`
- `/settings` -> `SettingsCenter`
- `/advanced` -> `AdvancedControlPlane`

兼容：

- `/tasks/:taskId` 保留 deep link 或重定向入口
- 原 `ControlPlane` 深链接保留到 `Advanced`

## 2. Page-to-API Matrix

| 页面 | 用户问题 | Canonical 数据源 | 合法动作 | 允许 detail route |
|---|---|---|---|---|
| `Home` | 现在能不能用？下一步是什么？ | `/api/control/snapshot` | `wizard.refresh`、`wizard.restart`、`project.select`、`config.apply`、operator/channel actions | 无需额外 detail route |
| `Chat` | 我在和谁说话？系统现在在做什么？ | `sessions`、`delegation`、`memory`、033/034 resource | `session.focus`、`session.export`、`operator.approval.resolve`、`work.*` | `/api/chat/send`、`/api/stream/task/{task_id}`、`/api/tasks/{task_id}`、`/api/tasks/{task_id}/execution` |
| `Work` | 当前有哪些任务和 child works？ | `sessions`、`delegation` | `work.refresh`、`work.cancel`、`work.retry`、`work.split`、`work.merge`、`work.escalate` | `/api/tasks/{task_id}`、`/api/tasks/{task_id}/execution` |
| `Memory` | 系统记住了什么？为什么这样记住？ | `memory`、`memory-subjects/*`、`memory-proposals`、`vault-authorization` | `memory.query`、`memory.flush`、`memory.reindex`、`vault.*` | 无额外私有 API |
| `Settings` | 怎么改主 Agent / Work / Memory / Channel？ | `config`、`project-selector`、`wizard` | `config.apply`、`project.select`、wizard actions | 无额外私有 API |
| `Advanced` | 我要看原始控制面和诊断细节 | 全量 control-plane resource | 全量 registry actions | 现有 detail routes 全可引用 |

## 3. Global Shell Rules

- 首屏 MUST 先读 `/api/control/snapshot`
- 任意页面刷新 MUST 优先使用 `resource_refs` 指向的 canonical resource route
- 全局 project 切换 MUST 通过 `project.select`
- 全局“待你确认”数量 MUST 来自 `SessionProjectionDocument.operator_summary` 或其 canonical 扩展
- 全局降级/错误状态 MUST 引用真实 `status`、`warnings`、`degraded.reasons`

## 4. Progressive Disclosure Rules

- 小白默认文案使用“系统状态”“下一步”“待你确认”“当前工作”“记忆摘要”等人话表达
- `capability_pack`、`delegation_plane`、`pipeline replay`、`memory proposal audit` 等底层术语仅在：
  - detail drawer
  - raw view
  - `Advanced`
  中出现

## 5. Degraded / Pending Semantics

- wizard 未完成：显示 `继续完成设置`
- 033 未就绪：显示 `连续上下文能力待接入`
- 034 未触发：显示 `尚未触发上下文压缩`
- memory degraded：显示 `记忆暂时降级，当前仅展示安全摘要`
- channel 未就绪：显示 `当前渠道未连接`

禁止：

- 把 pending/degraded 伪装成完成状态
- UI 本地猜测 runtime truth

## 6. Security Rules

- frontend 不得保存 config secret 实值
- frontdoor token 行为继续复用现有 session/persistent 模式
- vault 相关内容默认只显示摘要与操作结果，不回显未授权 raw payload
