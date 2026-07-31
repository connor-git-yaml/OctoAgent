# F156 Research Synthesis

## 结论

F156 应在 F153 连接能力之上建立一个四区原生 shell，而不是把 Web 三栏缩放到手机。
当前可以完成产品/技术/视觉与场景设计草案，但 Design/Tasks Gate 仍需等待
F154/F155 边界确定和 Gateway recon。

```text
F153 identity + single URLSession
  -> generated mobile contracts
  -> application coordinators
  -> pure finite projections
  -> TabView(chat/tasks/inbox/settings)
  -> NavigationStack destinations
```

## 核心决定

1. 顶层四 tab：对话、任务、收件箱、设置。
2. approval 与 Memory candidate 同属收件箱，但保持不同 typed action。
3. Health/Calendar 是 owner slice 的导航入口，不在 F156 复制 ingestion。
4. APNs payload 不含敏感正文，只作 authenticated typed deep link。
5. background refresh 只更新摘要/badge，不承诺周期，不执行敏感动作。
6. Claude early 是视觉基线；SwiftUI 原生 navigation/a11y 是允许且必须记录的适配。
7. 功能与视觉 E2E 都必须先启动真实 Gateway 和 App；静态 preview 不是交付证据。

## Gate 结论

- Research：PASS
- Design Draft：完整
- Tasks Draft：完整
- Gate：false，等待 F153 Verify、F154 状态、F155 方案 A/B 与 current API recon
