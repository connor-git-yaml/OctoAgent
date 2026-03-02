# Requirements Checklist: Feature 006 — Policy Engine + Approvals + Chat UI

**Purpose**: 验证 spec.md 是否满足需求规范的所有质量标准，作为进入技术规划阶段的质量关卡。
**Created**: 2026-03-02
**Feature**: `.specify/features/006-policy-engine-approvals/spec.md`

---

## Content Quality（内容质量）

- [x] CHK001 无实现细节（未提及具体语言、框架、API 实现方式）
  - Notes: spec 中提到了 "asyncio.Event"（EC 等处隐含）和 "Pydantic" 等技术细节，但这些仅出现在 Appendix Scope Boundaries 的 Resolved Ambiguities 中作为决策记录，不影响 FR 的技术无关性。FR 本身使用"系统 MUST"等行为描述，未绑定实现。Accepted.

- [x] CHK002 聚焦用户价值和业务需求
  - Notes: 9 个 User Stories 均以"作为 OctoAgent 的用户/系统管理员"开头，描述用户视角的期望行为和价值。每个 Story 附带 "Why this priority" 说明业务价值。

- [x] CHK003 面向非技术利益相关者编写
  - Notes: User Stories 使用自然语言描述场景，无需技术背景即可理解。FR 部分虽涉及技术概念（如 Pipeline、幂等注册），但使用了中文解释和场景化描述。

- [x] CHK004 所有必填章节已完成
  - Notes: 包含以下章节：Feature 元数据 / User Scenarios & Testing（含 9 个 US + Edge Cases）/ Requirements（含 27 个 FR + Key Entities）/ Success Criteria / Appendix: Scope Boundaries（含 M1 范围、M2+ 延伸、Resolved Ambiguities）。结构完整。

## Requirement Completeness（需求完整性）

- [x] CHK005 无 [NEEDS CLARIFICATION] 标记残留
  - Notes: 全文搜索未发现 `[NEEDS CLARIFICATION]` 标记。存在 2 个 `[AUTO-RESOLVED]` 标记（Pipeline 层数、审批等待实现方式），均已明确记录决策理由。

- [x] CHK006 需求可测试且无歧义
  - Notes: 全部 27 条 FR 均包含"测试"字段，描述了具体的验证方法。User Stories 使用 Given-When-Then 格式的 Acceptance Scenarios，条件和预期结果明确。

- [x] CHK007 成功标准可测量
  - Notes: 9 条 Success Criteria 均为可量化指标：SC-001/002 为 100% 通过率；SC-003/004/005/008 为延迟上限（3s/2s/5s/1s）；SC-006 为恢复时间上限（30s）；SC-007 为覆盖率 100%；SC-009 为实时性指标。

- [x] CHK008 成功标准是技术无关的
  - Notes: 所有 Success Criteria 以用户可感知的行为度量（延迟、覆盖率、恢复时间）为标准，未绑定特定技术实现。如 SC-008 描述"首字节显示延迟"而非 SSE chunk 到达时间。

- [x] CHK009 所有验收场景已定义
  - Notes: 9 个 User Stories 共定义 24 个 Acceptance Scenarios，覆盖正常流程（放行/审批/拒绝）、异常流程（超时/并发/重启恢复）、前端交互（面板展示/按钮操作/实时更新/SSE 流式）。每个 Story 均有 2-4 个场景。

- [x] CHK010 边界条件已识别
  - Notes: 8 个 Edge Cases（EC-1 到 EC-8）覆盖：进程崩溃恢复（EC-1）、并发审批竞态（EC-2）、Pipeline 异常（EC-3）、连续调用防重放（EC-4）、allow-always 后续行为（EC-5）、SSE 断连恢复（EC-6）、allowlist 意外清空（EC-7）、超时冲突（EC-8）。每个 EC 均标注关联的 FR 和 US。

- [x] CHK011 范围边界清晰
  - Notes: Appendix: Scope Boundaries 明确划分了 M1 范围内（9 项）和 M2+ 延伸（7 项），界线清晰。如"Policy Pipeline 前 2 层"在 M1，"Layer 3/4"在 M2+。

- [x] CHK012 依赖和假设已识别
  - Notes: 前序依赖明确标注为"Feature 004（ToolMeta + PolicyCheckpoint Protocol + BeforeHook Protocol）"。Resolved Ambiguities 记录了两个关键假设及其决策理由。Blueprint 对齐关系明确标注（FR-TOOL-3、FR-CH-1[M1]）。

## Feature Readiness（特性就绪度）

- [x] CHK013 所有功能需求有明确的验收标准
  - Notes: 27 条 FR 每条均包含"追踪"（关联 US）和"测试"字段。追踪字段确保 FR 来源于 User Story；测试字段确保 FR 可独立验证。

- [x] CHK014 用户场景覆盖主要流程
  - Notes: 主要流程覆盖情况：
    - 工具审批核心流（US-1: 不可逆触发审批 + US-2: 安全操作直接执行）
    - 策略管道流（US-3: 多层过滤 + 决策追溯）
    - 竞态安全流（US-4: 幂等 + 原子消费 + 宽限期 + 持久化恢复）
    - 前端交互流（US-5: Approvals 面板 + US-6: Chat UI + SSE）
    - 审计流（US-7: 全链路事件 + US-8: 配置变更审计）
    - 实时更新流（US-9: SSE 推送 + 轮询兜底）

- [x] CHK015 功能满足 Success Criteria 中定义的可测量成果
  - Notes: SC 与 FR/US 的映射关系：
    - SC-001 <- FR-005/015/016, US-1
    - SC-002 <- FR-005, US-2
    - SC-003 <- FR-018/020, US-5
    - SC-004 <- FR-012/019, US-1
    - SC-005 <- FR-010, US-1/EC-8
    - SC-006 <- FR-011, US-4/EC-1
    - SC-007 <- FR-006/026/027, US-7
    - SC-008 <- FR-024, US-6
    - SC-009 <- FR-022, US-9
    - 每条 SC 均有对应的 FR 支撑实现。

- [x] CHK016 规范中无实现细节泄漏
  - Notes: FR 层面使用行为描述（"系统 MUST 提供"、"系统 MUST 支持"），未指定具体编程语言、数据库表结构、API 框架等实现细节。Appendix Resolved Ambiguities 中提到 "asyncio.Event" 和 "PolicyCheckHook" 属于决策记录而非规范正文，可接受。

## Constitution 合规（扩展检查）

- [x] CHK017 C1 Durability First 对齐
  - Notes: FR-011 要求审批状态持久化到 Event Store + 重启恢复。EC-1 覆盖进程崩溃后恢复场景。SC-006 量化恢复时间上限（30s）。US-4 Scenario 4 明确要求重启后 pending 审批可见。

- [x] CHK018 C2 Everything is an Event 对齐
  - Notes: FR-006/026/027 定义了 5 种新事件类型（APPROVAL_REQUESTED / APPROVED / REJECTED / EXPIRED / POLICY_DECISION）。FR-012 定义了完整的审批事件流转。US-7 要求全链路事件可查询。SC-007 要求事件覆盖率 100%。

- [x] CHK019 C4 Side-effect Must be Two-Phase 对齐
  - Notes: FR-007 明确定义 Two-Phase Approval（注册 + 异步等待）。FR-015/016/017 确保 irreversible 工具必须经过 PolicyCheckpoint，不可绕过。EC-3 确保 Pipeline 异常时 fail-closed。US-1 描述完整的 Plan -> Gate -> Execute 流程。

- [x] CHK020 C7 User-in-Control 对齐
  - Notes: FR-008 提供三种审批决策（allow-once / allow-always / deny）。FR-018/019 提供 REST API 审批通道。FR-020/021 提供 Web UI 审批通道。US-8 支持策略配置可调。默认策略为 safe by default（irreversible -> ask）。

- [x] CHK021 C8 Observability is a Feature 对齐
  - Notes: FR-002 要求每层决策附带 label 可追溯来源。FR-006/026 要求策略决策事件写入 Event Store。FR-020 要求 Approvals 面板展示风险说明和剩余时间。US-7 要求全链路可审计。Key Entities 中 ApprovalRequest 包含 task_id 关联。

## 上游契约兼容（扩展检查）

- [x] CHK022 与 Feature 004 PolicyCheckpoint Protocol 兼容
  - Notes: FR-015 明确引用 Feature 004 的 PolicyCheckpoint Protocol，fail_mode 强制为 closed。FR-016 描述 PolicyCheckHook 在 BeforeHook 内部等待审批。FR-017 对齐 Feature 004 的 FR-010a（irreversible 工具无 PolicyCheckpoint 时强制拒绝）。tooling-api.md 中 PolicyCheckpoint.check() 签名、CheckResult 模型、FailMode 枚举均在 spec 中正确引用。

- [x] CHK023 ToolMeta / SideEffectLevel / ToolProfile 枚举值对齐
  - Notes: FR-001/004/005 中使用的 SideEffectLevel（none/reversible/irreversible）和 ToolProfile（minimal/standard/privileged）与 tooling-api.md 锁定值完全一致。

## 安全性（扩展检查）

- [x] CHK024 审批令牌防重放
  - Notes: FR-008 明确 allow-once 为一次性令牌，消费后不可再次使用。US-4 Scenario 2 验证二次消费返回失败。EC-2 覆盖并发解决竞态。

- [x] CHK025 secrets 不进 LLM 上下文
  - Notes: Key Entities 中 ApprovalRequest 描述"参数摘要（脱敏后）"。Constitution C5（Least Privilege）在 spec 元数据中虽未直接列出，但 C8 明确要求"敏感原文不得直接写入 Event payload"。FR-018 中审批请求返回"参数摘要"而非原始参数。

- [ ] CHK026 审批 payload 脱敏显式要求
  - Notes: **未通过**。虽然 Key Entities 中 ApprovalRequest 提到"参数摘要（脱敏后）"，但 FR 中没有一条 FR 显式要求对审批 payload 中的敏感参数进行脱敏处理。FR-018 仅要求返回"参数摘要"，未明确定义脱敏规则或脱敏机制。建议在 FR-018 或新增 FR 中明确："审批请求中的参数摘要 MUST 对标记为敏感的参数值进行脱敏（如 SecretStr 类型的值替换为 ***），确保审批面板和事件记录中不暴露原始密钥。"

## 可观测性（扩展检查）

- [x] CHK027 审批事件包含 trace_id/task_id
  - Notes: Key Entities 中 ApprovalRequest 包含"关联 task_id"。ExecutionContext（来自 Feature 004 契约）包含 task_id 和 trace_id。FR-006 要求策略决策事件包含工具名称等字段。FR-012 中审批事件与 Task 状态流转绑定（Task 有 task_id）。Constitution C8 要求"结构化日志必须绑定 trace_id / task_id"。

## 前端覆盖（扩展检查）

- [x] CHK028 Approvals 面板 FR 充分
  - Notes: FR-020（面板组件 + 信息展示）、FR-021（三按钮操作）、FR-022（SSE 实时 + 轮询兜底）三条 FR 覆盖了面板的展示、交互、实时性三个维度。US-5 有 4 个验收场景，US-9 有 2 个验收场景。

- [x] CHK029 Chat UI FR 充分
  - Notes: FR-023（消息输入 + 提交）、FR-024（SSE 流式输出）、FR-025（审批提示 + 引导跳转）三条 FR 覆盖了 Chat UI 的输入、输出、与审批系统联动三个维度。US-6 有 3 个验收场景。EC-6 覆盖 SSE 断连恢复。

---

## Summary

| 维度 | 检查项数 | 通过 | 未通过 |
|------|---------|------|--------|
| Content Quality | 4 | 4 | 0 |
| Requirement Completeness | 8 | 8 | 0 |
| Feature Readiness | 4 | 4 | 0 |
| Constitution 合规 | 5 | 5 | 0 |
| 上游契约兼容 | 2 | 2 | 0 |
| 安全性 | 3 | 2 | 1 |
| 可观测性 | 1 | 1 | 0 |
| 前端覆盖 | 2 | 2 | 0 |
| **Total** | **29** | **28** | **1** |

### 未通过项详情

| ID | 严重程度 | 描述 | 修复建议 |
|----|---------|------|---------|
| CHK026 | MEDIUM | 审批 payload 脱敏缺乏显式 FR | 在 FR-018 中补充脱敏要求，或新增 FR 明确脱敏机制（如 SecretStr 值替换为 `***`、长字符串截断等规则）。建议回到 specify 阶段修补。 |
