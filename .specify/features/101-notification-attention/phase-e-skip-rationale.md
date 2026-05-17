# F101 Phase E — D8 顺手清 SKIP 决策记录

> Date: 2026-05-17
> Plan reference: plan.md §6（Phase E — D8 顺手清，条件实施）
> Spec reference: spec.md FR-E1（[可选] ControlPlaneService notification_service 参数）+ AC-E1（条件验证）

## 决策：SKIP（不实施）

## 触发条件检查（plan §6.2）

> Phase E 条件：若 Phase C 已通过 task_runner 路径覆盖所有通知场景，Phase E 可降级为不实施（AC-E1 豁免）

### 实测验证（Phase D commit `98e658a` 后）

```bash
grep -rn "notification_service" octoagent/apps/gateway/src/octoagent/gateway/services/control_plane/
# 结果：无任何匹配 — control_plane domain services 完全不引用 notification_service
```

### Phase C 覆盖的通知场景（已验证 commit `ec2886f`）

1. **Worker 完成通知（SUCCEEDED/FAILED）**：`task_runner._notify_completion` → `notification_service.notify_task_state_change`（接通）
2. **WAITING_APPROVAL 通知**：`ask_back_tools.escalate_permission_handler` → `notification_service.notify_approval_request`（接通）
3. **Web list/dismiss API**：`gateway/routes/notifications.py` 独立路由（不通过 ControlPlaneService）
4. **Telegram callback dismiss**：`gateway/services/telegram.py._handle_dismiss_notification_callback`（直接通过 TelegramGatewayService.bind_notification_service 接通）

### control_plane 路径与 notification 的实际关系

ControlPlaneService（`_coordinator.py:93-109`）的 9 个 domain service：
- SessionDomainService / WorkDomainService / AgentProfileDomainService / AutomationDomainService / ImportDomainService / McpDomainService / MemoryDomainService / SetupDomainService / WorkerProfileDomainService

这些服务**全部不涉及通知发送**——通知职责由 task_runner（业务事件层）和 ask_back_tools（工具层）直接承担，不经过 control_plane。

## 决策依据

Phase 0 tech-research §A-5 已实测确认：
- D8 实测**不是隐性耦合**——ControlPlaneService 是显式 DI 14 参数（最佳实践形态）
- F101 仅需在需要时按需加 `notification_service` 参数，不重架
- 当前 Phase C 已通过 task_runner / routes / Telegram 三路径覆盖所有通知场景

GATE_DESIGN spec §10 第 5 条："FR-E1 与 FR-B 集成时可顺手清：不需要独立 Phase，在 NotificationService 集成到 ControlPlane 时加参数即可。"

实测：NotificationService 实际**未**集成到 ControlPlane（task_runner 路径已足够），所以"顺手清"的触发条件未满足。

## AC-E1 豁免理由

AC-E1 原文："ControlPlaneService 构造时 notification_service 作为显式参数传入"

- Phase E SKIP → ControlPlaneService 仍是 14 参数（无 notification_service）
- 当前所有通知功能正常工作（Phase C v3 验证 38 测试 + e2e_smoke 8/8）
- 添加 notification_service 参数是 YAGNI（you ain't gonna need it）—— 没有 control_plane domain service 需要触发通知

## 影响

- F101 Phase E 跳过 → 0 production 代码改动 / 0 测试改动
- Phase F 直接基于 Phase D commit `98e658a` 启动
- F107 Capability Layer Refactor 可重新评估 D8 一并清理（如有需要）

## 状态

- ✅ Phase E DECISION_RECORDED：SKIP
- ✅ tasks.md Phase E checkboxes 标注 `(SKIPPED, 条件不满足)`
- 进 Phase F
