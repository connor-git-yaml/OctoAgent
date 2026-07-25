# F150 Design Requirements Checklist

## 产品与范围

- [x] named tunnel 是唯一远程网络基础设施；无 provider/profile selector 或第二公网入口。
- [x] 电脑保留 Web，手机只走原生 iOS App；手机浏览器不计产品入口。
- [x] Gateway 永远只绑定 loopback；quick tunnel 与公网直绑被禁止。
- [x] 电脑 Web 不接收 bearer/service token，不新增 Octo browser session/device 表；该约束不抹掉 F153 的原生设备身份。
- [x] F150 与 F149 的 backend/UI 所有权边界已冻结。
- [x] Web 与 iOS 均以 Claude Design 初稿为视觉基线；实现适配设计，非必要不改层级、留白、卡片节奏和排版。

## 安全合同

- [x] manifest exact schema、path containment 与 secret-negative contract 已冻结。
- [x] JWT header、algorithm、issuer、audience、required claims、owner 与 time contract 已冻结。
- [x] JWKS derived URL、TTL、key bound、timeout、rotation refresh 与 fail-closed 已冻结。
- [x] direct-local / cloudflare-access request classifier 已冻结。
- [x] Host/Origin/JSON mutation gate 已冻结；无第二 CSRF secret。
- [x] HTTP/SSE 共用 verifier；日志与 live evidence secret boundary 已冻结。
- [x] iOS 禁止内置 Cloudflare service token；F153 真机 spike 前不启用 mobile route/Access Bypass。

## 架构与实施

- [x] F151 stable commit 与 protected-scope 更新前置已冻结。
- [x] 单 config loader、单 manifest parser、单 guard、单 API projection；禁止第二 registry/state。
- [x] F150 L4/L3/电脑 Web L1/live 与 F153 iOS L1/真机边界已冻结。
- [x] 外部 Cloudflare/系统变更需要用户单次授权。
- [x] SSE live 数值门与脱敏证据 schema 已冻结。

## Gate

- [x] Spec、Plan、Research、Design Forks、Data Model、Origin Contract 互相一致。
- [x] 不存在未决产品/安全设计问题。
- [x] 用户明确产品边界，main 完成 Design Gate 主审。
- [x] Tasks 18/18 unique；13 个行为任务各有完整 RED→GREEN→REFACTOR command/oracle。
- [x] FR-1～FR-13 全部有 task owner；Python profile 无 SDK/uv sync/bare pytest。
- [x] external live、F149 Web UI、F153 iOS、F151 authority 的执行与权限边界均有唯一 owner。
- [x] main 完成 Tasks Gate 主审。
- [x] T015 production live通过；脱敏attestation只含散列/PASS/T003引用，raw identity与secret扫描为0。
- [x] T016机械确认F150无iOS production path、F153唯一owner及Claude Design/SwiftUI handoff完整。
- [x] T017本地验证项目通过：Python 363/363有效执行项通过（另1项条件跳过）、F150精确安全门22/22、frontend unit 17/17、Playwright 2/2、complexity/build/静态门均通过。
- [x] manifest exact 7字段、changed migration=0、T003/T015 SHA/size与secret-negative均复算一致；must-fix=0。
- [x] 已形成可供F149 T000消费的F150 stable commit：产品实现`bf29d6be7d7a86c298cd45699488a8640065a566`，仓库级门禁配套提交及当前稳定点`5e6f4846703b7126cd104c8b9678e0c2f5300cc8`。

当前：`GATE_DESIGN=true`、`GATE_TASKS=true`。T000～T017全部完成，F150 stable；F149 T000已解锁，禁止force push。
