# F158 双端 E2E 与视觉矩阵

## Web

| surface / journey | 功能 L4 | API/L3 | Browser L1 | desktop visual | 390 Web | 状态 |
|---|---:|---:|---:|---:|---:|---|
| FrontDoor / Access 登录、刷新、过期、登出、恢复 | required | required | required | required | robustness | L1 GREEN；主框架 visual GREEN |
| 对话主工作台 / 消息 / 委派 / 工件 / SSE | required | required | required | required | robustness | L1 GREEN；主框架 visual GREEN |
| Tasks list | required | required | required | required | required | L4/L1 + desktop visual GREEN |
| Task detail / Advanced / 403 / not-found / disconnected | required | required | required | required | required | L4/L1 + desktop visual GREEN |
| Approvals / accept / reject / 409 | required | required | required | required | required | L4 + fresh accept L1 + desktop visual GREEN |
| Automation / pause / resume / 409 | required | required | required | required | required | L4/L1 + desktop visual GREEN |
| Settings / provider / secret / maintenance | required | required | required | required | required | L4/L1 + desktop visual GREEN |
| Settings / F150 remote access status/actions/Advanced | required | required | required | required | robustness | production consumer + L4/L1/visual GREEN |
| Agents | required | required | required | required | required | L4/L1 + desktop visual GREEN |
| Memory | required | required | required | required | required | L4/L1 + desktop visual GREEN |
| Files / versions / diff | required | required | required | required | required | L4/L1 + desktop visual GREEN |
| Skills / file selection / install | required | required | required | required | required | L4/L1 + desktop visual GREEN |
| MCP / connect / status | required | required | required | required | required | L4/L1 + desktop visual GREEN |
| loading / empty / recoverable error / origin 403 / 409 | required | required | required | required | required | deterministic L4 GREEN；认证/断线 L1 GREEN；选定高风险 pixel state 待 completion audit |

Web visual fixtures MUST freeze time, locale, fonts, animations, viewport, project/session/task IDs,
message/event order and dynamic durations. Screenshot projects use one desktop viewport matching the
early frame canvas and one 390px Web robustness viewport.

## iOS

| journey | pure/unit | local integration | simulator UI | device | visual |
|---|---:|---:|---:|---:|---:|
| cold launch / foreground / background | required | required | required | sampled | required |
| device registration challenge | required | required | required | required | required |
| Keychain persistence / rotation / revoke | required | required | partial | required | state screenshots |
| `/ready` / minimal API / reconnect | required | required | required | required | required |
| conversation / tasks / approvals / memory candidate | required | required | required | sampled | required |
| disconnected / expired / revoked | required | required | required | required | required |
| HealthKit permission/no-readable-data/data-preview | required | required | simulator contract | required | required |
| EventKit full-access disclosure/read-only product behavior | required | required | simulator contract | required if retained | required |
| notification / deep-link | required | required | required | required | required |
| Dynamic Type / VoiceOver / Reduce Motion | required | n/a | required | sampled | required |

真机项不能由 simulator 或静态 mock 代替。F152→F153→F154→F155→F156 默认串行；
只有不接生产 transport/data 的视觉探索可并行。

## 证据格式

每条场景记录：

- commit/tree；
- exact command；
- runtime/browser/device 版本；
- selected/pass/fail/skip/rerun；
- screenshot expected/actual/diff SHA；
- trace/log；
- owner Feature/task；
- 是否允许进入 release completion set。
