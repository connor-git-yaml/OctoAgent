# F150 Plan / Tasks Gate Review

## 当前结论

`PASS`，`GATE_TASKS=true`。

Design Gate 已通过。Tasks 已收敛为一个 Cloudflare named tunnel 网络基础设施、桌面 Web Access 产品入口、原生 iOS handoff；没有手机浏览器产品、第二 Web session/device、iOS service token、第二 parser/guard/registry 或 provider abstraction。

## 主审结果

1. **PASS**：T001 在任何 production 改动前扩展 F151 authority；正向只含批准 path/symbol，负向覆盖 sibling、第二 parser/guard/registry、public bind、mobile route、Bypass、service token 与 device/session。
2. **PASS（实施真值修订）**：18 个 task ID 唯一；原计划 13 个行为任务。T013 的测试路径前置修正后首次有效浏览器执行即被现有 production 2/2 满足，故实施时如实改列 L1 characterization；当前为 12 个行为任务、36 个 R/G/R command/oracle fields，加 1 个独立 L1 characterization command。没有为了维持计划计数制造假 RED。
3. **PASS**：T003/T015 明确需要用户当次授权，使用脱敏 live attestation，不计行为 RED。
4. **PASS**：F150 只拥有 backend contract、API projection 与最小 Settings entry；F149 拥有 Claude Design 基线上的最终 Web composition，两者不复制状态机。
5. **PASS**：390px 只作为 Web 窄窗口健壮性；F150 iOS production task/path=0，只有 T016 handoff。
6. **PASS**：FR-1～FR-13 全覆盖；Python profile 统一为 post-F151 七包+Gateway、SDK path=0、`uv sync`=0、`-k` selector=0；不存在未声明的 npm lint 命令。
7. **PASS**：JWT、Cookie、Cloudflare credential、service token 被禁止进入日志、响应、evidence 与 App；secret-negative gate 有独立行为任务。

## Gate 决定

只放行 T000 与 T001。T001 未形成真实 authority RED→GREEN→REFACTOR 前，不得修改 F150 protected production symbol。Cloudflare 账户、DNS、tunnel、Access application 与系统 service 仍需每次单独授权；Tasks Gate 不推导外部权限。

## 仍未授权

- F150 production 修改（T001 通过前）
- T002+ tests/implementation
- 外部 Cloudflare/DNS/tunnel/Access/service 变更
- stage/commit/push
- F149 T000 / F153 Implement
