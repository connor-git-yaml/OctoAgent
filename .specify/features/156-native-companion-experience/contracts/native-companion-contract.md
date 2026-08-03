# F156 Native Companion Contract

## Architecture flow

```text
Gateway canonical contract
  -> generated mobile adapter
  -> application coordinator
  -> pure finite projection
  -> SwiftUI composition
```

禁止 page/view 直接拼 URL、解析 raw JSON/SSE、持有 token 或推断 capability。

## Current API recon boundary

`inventories/mobile-api-recon.v1.json` 冻结当前事实：mobile hostname 的 exact allowlist
已有 F153 的 enrollment、token challenge、key rotation、token、ready、device profile
共 7 条路由，以及 F154 的 health review/analysis/delete 3 条 owner route。Health route
不属于 F156 Chat/Task/Inbox/APNs 合同，也不能用来冒充 Companion shell 已实现。
Chat、Task、Approval、Memory 与 `/api/notifications` 仍由 Web front door 保护，mobile
hostname 会在路由前拒绝；`/api/notifications` 也不是 APNs。F156 只能扩展现有 mobile
router、F153 request proof 与既有 application service，不得从 iOS 调 Web route 或新增
第二 transport。

当前实际签发的 capability 只覆盖 device ready/profile 与 F154 Health 三项操作；
`task.read` 只是声明但未签发的 F156 未来 capability，task read/control route 都不存在。
因此 task 列表、cancel/resume 等在新增 exact capability+route 前必须保持不可见/不可用。
Operator action 的 source 也只有 web/telegram/system，不得把 iOS 伪装成其中之一。

## Exact top-level navigation

```text
chat | tasks | inbox | settings
```

## Notification payload

```json
{
  "version": 1,
  "type": "task_state_changed | approval_requested | memory_candidate_ready",
  "object_id": "<opaque-id>",
  "collapse_id": "<opaque-dedup-id>"
}
```

extra/unknown field fail closed。payload 不含 title/body/owner/device/token/approval
details/Memory/Health/Calendar。

## Deep-link contract

1. parse finite payload；
2. validate current F153 credentials/device；
3. fetch object from authenticated Gateway；
4. validate capability/current state；
5. navigate to typed destination；
6. never execute approve/reject/cancel/send from notification.

## Visual contract

Canonical design source：

- `.specify/features/149-web-pages-v2/design-output/2026-07-28/OctoAgent Web.dc.html`
- SHA-256 `1d497d8cc4e8a06e9f2bff296784d4648e0bb0784a73c8fe4f8a7bd9812132f7`

该导出的 Claude 最早期视觉语言是唯一上游；后期大 Hero、radial glow、
大圆角低密度卡片墙与无来源的通用移动模板不得进入 active baseline。

每个 baseline 记录：

- design source SHA/scene id；
- app/test commit；
- device/runtime/scale/locale/content-size；
- fixture SHA；
- pixel threshold；
- semantic/geometry/a11y oracle；
- intentional divergence reason and impact。

同一实现运行后新建 baseline 只能作为候选，必须与 Claude early source/accepted
baseline 人工和机械双审后才能替换。
