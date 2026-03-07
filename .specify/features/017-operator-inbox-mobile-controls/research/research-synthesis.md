# Feature 017 产研汇总：Unified Operator Inbox + Mobile Task Controls

## 输入材料

- 产品调研: `research/product-research.md`
- 技术调研: `research/tech-research.md`
- 在线补充: `research/online-research.md`
- 上游约束: `docs/blueprint.md` M2 / `docs/m2-feature-split.md` Feature 017

## 1. 产品×技术交叉分析矩阵

| MVP 功能 | 产品优先级 | 技术可行性 | 实现复杂度 | 综合评分 | 建议 |
|---|---|---|---|---|---|
| 统一 `OperatorInboxItem` projection | P1 | 高 | 中 | ⭐⭐⭐ | 纳入 MVP |
| Web inbox 视图 + 快速操作 | P1 | 高 | 中 | ⭐⭐⭐ | 纳入 MVP |
| Telegram inline keyboard 等价操作 | P1 | 中 | 中 | ⭐⭐⭐ | 纳入 MVP |
| 统一 `OperatorAction` contract + 幂等结果 | P1 | 高 | 中 | ⭐⭐⭐ | 纳入 MVP |
| operator action 审计事件 | P1 | 高 | 中 | ⭐⭐⭐ | 纳入 MVP |
| pairing quick action | P2 | 中 | 中 | ⭐⭐ | 若不显著拖慢主线，则并入 017 |
| 原生 mobile app / PWA | P3 | 中 | 高 | ⭐ | 推迟 |

## 2. 统一结论

1. 017 的问题不是“缺一个 approvals 页面”，而是缺一个统一的 operator control surface。
2. 当前代码已经有足够的数据源能力：approvals、journal、cancel、task_jobs、telegram-state。
3. 当前最大缺口不是 UI，而是统一动作契约和 Telegram callback 支持。
4. 为了保持并发和降低回归风险，017 必须复用已有 domain contract，而不是重做审批、watchdog 或 Telegram ingress 基础链路。

## 3. 方案决策

### 选型：统一 projection + 统一 action contract（采纳）

- `OperatorInboxItem` 聚合 approvals / alerts / retryable failures / pending pairings
- `OperatorActionRequest / Result` 作为 Web 与 Telegram 共用动作接口
- Web 提供 operator inbox 页面
- Telegram 提供 inline keyboard 与 callback action
- 所有动作写 operator action audit event

### 不采纳方案

- 继续在 approvals panel、task detail、Telegram 文本通知上分别叠按钮
- 只做 Web inbox，Telegram 继续只通知
- 重新实现 approvals / journal / pairing 的底层状态机

## 4. MVP 范围锁定

### In

- `OperatorInboxItem` 模型
- 统一 inbox API / projection
- Web inbox 视图
- Telegram inline keyboard 等价操作
- approve / deny / retry / cancel / acknowledge 的统一动作语义
- operator action 审计事件与最近动作结果

### Out

- 原生 mobile app
- Telegram transport / routing 基础链路重写
- JobRunner console 全功能面板
- 新的长期运维后台

## 5. 风险矩阵

| 风险 | 等级 | 缓解 |
|---|---|---|
| Telegram 只做到“通知升级”，没有真正 callback action | 高 | 把 callback query 支持列为 MVP，不延期 |
| retry 语义未冻结，导致实现层来回返工 | 高 | 在 plan 阶段先锁定“来源链路审计 + 重试 attempt 语义” |
| pairing request 被降级成只读信息 | 中 | 明确它是一等 inbox item；若动作实现超时，至少保证 Web 可见并指向统一处理入口 |
| 再造旁路日志导致审计链分叉 | 高 | 所有 operator action 必须落 Event Store，不新建独立 action log |
| Web 与 Telegram 并发点击导致结果混乱 | 中 | 动作结果模型必须包含 `already_handled / expired / stale_state` |

## 6. Gate 结论

- `GATE_RESEARCH`: PASS（离线调研 + 在线调研完成，points=3）
- `GATE_DESIGN`: READY（可进入 spec / clarify / checklist）

## 7. 执行建议

1. 先冻结 `OperatorInboxItem`、`OperatorActionRequest`、`OperatorActionResult` 三个核心概念。
2. 先统一动作语义，再做 Web/Telegram 两个表面，避免双套后端。
3. 把“最近动作结果 + pending 数量 + 到期时间”作为 inbox 的一等字段，不要事后补。
4. pairing request 若进入 MVP，优先做 Web 操作面；Telegram parity 复用同一动作 contract。
