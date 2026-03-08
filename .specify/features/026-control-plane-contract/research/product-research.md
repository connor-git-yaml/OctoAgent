# Feature 026 产品调研：Control Plane Delivery

**日期**: 2026-03-08  
**调研模式**: full / product  
**核心参考**:
- `docs/m3-feature-split.md`
- `docs/blueprint.md`
- `_references/opensource/openclaw/docs/web/dashboard.md`
- `_references/opensource/openclaw/docs/automation/cron-jobs.md`
- `_references/opensource/openclaw/docs/cli/approvals.md`
- `_references/opensource/agent-zero/python/helpers/task_scheduler.py`
- `_references/opensource/agent-zero/python/websocket_handlers/state_sync_handler.py`

## 1. 产品问题

当前 OctoAgent 的 Web 面仍是“任务页 + 两张控制卡片”，Telegram 也只有消息 ingress 与 operator callback。M3 想实现的不是更多零散入口，而是一个 operator 真能长期依赖的控制面：

- 有正式 dashboard shell
- 有 project/session/automation/diagnostics/config/channels 主导航
- 有统一动作语义
- 有可恢复的 automation / runtime / approvals / recovery 入口

## 2. 参考产品信号

### OpenClaw

- Dashboard 被定义为 browser Control UI，而不是 task list 的附属页。
- approvals、cron、config、gateway doctor、channels 都是同一产品族。
- 关键启示：控制面首先是“信息架构与统一对象”，其次才是某个具体页面。

### Agent Zero

- Scheduler、projects、memory dashboard 被提升为 operator-facing 对象。
- WebSocket state sync 说明“控制台需要统一状态快照 + 增量更新”。
- 关键启示：session/runtime/automation 应当作为产品对象被管理，而不是藏在底层 helper。

## 3. 对 026 的产品结论

- 026 的 Web 不能继续以 Task 为首页心智；Task 只是 Session / Runtime 的一个视图。
- Operator 面必须成为“统一控制台入口”，而不是 approvals 单页。
- Config、Channels、Diagnostics、Automation 需要各自成为明确导航项，而不是若干卡片和杂项按钮。
- Telegram 的价值不是复刻整个 Web，而是提供统一动作的轻量 command surface。

## 4. 产品落点

建议的 Web IA：

- Dashboard：总览当前 project、operator pending、latest runtime / update / recovery / automation
- Projects：selector + workspace + bindings 摘要
- Sessions：session center + execution/task detail refs
- Operator：approvals / retry / cancel / pairing / backup / restore / import / update
- Automation：jobs + run history
- Diagnostics：health / degraded / recent failures / control-plane events
- Config：schema + uiHints renderer
- Channels：Telegram pairing / approved users / allowlists / readiness

## 5. 风险

- 若直接在现有 TaskList 上继续堆卡片，会把 026 变成“更大的任务页”，而不是 control plane。
- 若 Telegram 继续只走 surface-private callback 协议，会削弱 action registry 的价值。
- 若 Config Center 没有 schema-driven form，后续 Secret Store/Wizard 很难直接复用。
