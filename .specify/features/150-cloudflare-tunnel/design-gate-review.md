# F150 Design Gate Review

## 结论

`PASS`，`GATE_DESIGN=true`。

用户已明确纠正产品边界：电脑保留 Web，手机只走原生 iOS App。设计已按“一个 named tunnel 基础设施、两个产品入口和 trust contract”重写；F150 只交付电脑 Web Access，F153 负责原生 iOS 真机 transport/device proof。配置权威、origin JWT/JWKS、request classifier、mutation CSRF、F151 authority 交接、F149 UI 交接、SSE 数值门与外部权限边界均有单一答案。

## 审查结果

| 维度 | 结论 | 证据 |
|---|---|---|
| 产品 | PASS | 电脑 Web + 原生 iOS；手机浏览器不是产品入口；唯一的是 named tunnel 基础设施 |
| 安全 | PASS | exact JWT/JWKS/owner/time、Host/Origin、fail-closed、secret boundary |
| 架构 | PASS | canonical config → one manifest parser → one guard/projection；F151 scope 先行 |
| 数据 | PASS | 无数据库表；manifest 非敏感且 versioned；status 纯派生 |
| UI ownership | PASS | F150 backend/minimal entry；F149 Web 与未来 iOS 均以 Claude Design 初稿为视觉基线，实现适配设计 |
| 测试 | PASS | F150 L4/L3/电脑 Web L1/live；F153 iOS L1/真机 transport 独立 |
| 外部副作用 | PASS | 账户/DNS/tunnel/service 一律需单次用户授权 |
| 坏味道 | PASS | 无 network provider abstraction、Web 第二 auth/session、compat path 或隐藏 fallback；iOS device identity 不被错误删除 |

## 主审决定

接受当前方案：Octo 在 F150 只保存电脑 Web 的非敏感 manifest 和单一 owner email；Cloudflare 账户、DNS、credential 与系统 service 继续由官方工具和用户管理。iOS 不内置 service token，F153 以真机 spike 决定设备级 trust contract。这样牺牲“一键全自动开通”，换取显著更小的 secret/账户权限面和更可审计的失败边界。

Design Gate 通过只允许进入 Tasks Gate；所有外部 Cloudflare 变更仍需当次单独授权，不能从本 Gate 推导。第一项行为工作固定为 F151 authority gate 的 RED→GREEN→REFACTOR，不直接跳到 production 实现。
