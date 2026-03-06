# Feature 015 产研汇总：Octo Onboard + Doctor Guided Remediation

## 输入材料

- 产品调研: `research/product-research.md`
- 技术调研: `research/tech-research.md`
- 在线补充: `research/online-research.md`
- 上游约束: `docs/blueprint.md` §12.9 / `docs/m2-feature-split.md` Feature 015

## 1. 产品×技术交叉分析矩阵

| MVP 功能 | 产品优先级 | 技术可行性 | 实现复杂度 | 综合评分 | 建议 |
|---|---|---|---|---|---|
| `octo onboard` 统一入口 | P1 | 高 | 中 | ⭐⭐⭐ | 纳入 MVP |
| onboarding session 恢复 | P1 | 高 | 中 | ⭐⭐⭐ | 纳入 MVP |
| doctor action-oriented remediation | P1 | 高 | 中 | ⭐⭐⭐ | 纳入 MVP |
| channel verifier contract | P1 | 高 | 中 | ⭐⭐⭐ | 纳入 MVP |
| 真正的 Telegram transport/pairing 实现 | P1 | 中 | 高 | ⭐⭐ | 由 Feature 016 承担，不纳入 015 |
| Web 向导页面 | P2 | 中 | 高 | ⭐ | 推迟 |

## 2. 统一结论

1. 015 的核心不是“再做一个 CLI 命令”，而是把 `config -> doctor -> channel -> first message` 串成单一流程。
2. 当前代码已经有足够的 CLI 和配置基础，不需要新建包；缺的是编排层、持久化 session 和结构化 remediation。
3. 为了保持并发，015 必须只拥有 channel onboarding contract，不拥有 Telegram 传输实现。
4. 用户真正关心的是“系统现在能不能用、如果不能我下一步做什么”，因此终态和下一步动作必须成为一等输出。

## 3. 方案决策

### 选型：新增 `octo onboard` 编排层 + 复用 `config/doctor`（采纳）

- 以 `octo config` 为 provider/runtime 真实配置入口
- 以增强后的 `DoctorRunner` 输出 remediation summary
- 以 `OnboardingSession` 保持中断恢复能力
- 以 `ChannelOnboardingVerifier` 为 016 留接入口

### 不采纳方案

- 继续在 `octo init` 上叠加能力
- 只增强 `octo doctor` 不做向导
- 在 015 内直接实现 Telegram 传输与 pairing 状态机

## 4. MVP 范围锁定

### In

- `octo onboard` CLI 入口
- provider/runtime/doctor/channel/first-message 五阶段状态机
- onboarding session/checkpoint 持久化与 resume
- doctor remediation 结构化输出
- verifier 缺位时的 blocked state 与修复建议
- CLI E2E：中断 -> 修复 -> 恢复 -> ready

### Out

- Telegram transport / ingress / reply routing 本体（Feature 016）
- Web inbox / mobile controls（Feature 017）
- backup/export/restore 产品入口（Feature 022）
- Web onboarding UI

## 5. 风险矩阵

| 风险 | 等级 | 缓解 |
|---|---|---|
| 015 吞掉 016 的渠道职责 | 高 | 以 verifier contract 解耦，015 不拥有 Telegram transport |
| doctor 仍只是字符串 fix_hint，无法驱动向导 | 高 | 新增 remediation action 模型和 stage summary |
| 中断后仍需重跑全部步骤 | 高 | 引入 onboarding session/checkpoint 持久化 |
| 用户把“部分通过”误判为“系统可用” | 中 | 明确定义 blocked/action_required/ready 终态 |
| CLI 逻辑耦合 Click 难以测试 | 中 | 抽离独立 orchestration service |

## 6. Gate 结论

- `GATE_RESEARCH`: PASS（离线调研 + 在线调研均完成，points=2）
- `GATE_DESIGN`: READY（可进入 spec / clarify / checklist）

## 7. 执行建议

1. 先冻结 `OnboardingSession` / `DoctorRemediation` / `ChannelOnboardingVerifier` 三个核心概念。
2. 先确保“blocked 也可恢复”，再追求“全部一步成功”。
3. 把“系统已可用”做成明确终态和摘要输出，避免用户自行解释状态。
