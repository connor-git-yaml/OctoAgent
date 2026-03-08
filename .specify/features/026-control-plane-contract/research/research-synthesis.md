# Feature 026 调研综合结论

## 结论

Feature 026 的正确实现方式是：

1. 以 026-A frozen contract 作为唯一上游；
2. 用 backend canonical producer 把散落的 onboarding/config/project/task/execution/operator/ops 能力收拢成六类 resource documents；
3. 用统一 action registry 替代 surface-specific command/button/callback；
4. 用正式 Web control plane shell 取代当前最小 task 首页；
5. 用 control-plane events 和 automation persistence 让控制台具备真正的产品 durability。

## 为什么不是“继续扩旧 API + 旧 TaskList”

- 旧 API 是 route-specific payload，不能充当 canonical contract。
- 旧首页缺少 project/session/automation/diagnostics 心智。
- Telegram/Web 现状没有统一 request/result/event 语义。
- automation/scheduler 若没有独立 persistence / run history，不算正式产品对象。

## 架构选择

### 选择

- `packages/core` 定义 control-plane models / envelopes / event payloads
- `packages/provider/dx` 提供 project-root durable state / automation store
- `apps/gateway` 提供 canonical producer / action executor / routes / scheduler
- `frontend` 只消费 `/api/control/*`

### 不选择

- 不让 frontend 继续聚合旧 route payload
- 不让 Telegram 继续只走独立 callback codec
- 不让 automation 仅做 UI 壳而无持久化/恢复

## 对实现的直接约束

- 所有 action 必须先进入 registry，再被 surface alias 消费
- 所有高风险或副作用动作都必须有统一 request/result/event 语义
- 所有资源都必须有 `degraded/unavailable` 明示表达
- 所有新页面必须以 resource document 为数据源，而不是点状 API
