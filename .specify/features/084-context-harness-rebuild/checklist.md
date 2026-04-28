# Feature 084 - 质量检查清单

**特性**: 084 - Context + Harness 全栈重构
**检查时间**: 2026-04-27
**检查者**: Spec Driver 质量检查表子代理
**规范版本**: Draft (2026-04-27)

---

## Content Quality（内容质量）

| # | 检查项 | 状态 | Notes |
|---|--------|------|-------|
| CQ-1 | 无实现细节（未提及具体语言、框架、API 实现方式） | [ ] | FR-1.1 明确提及"AST 扫描"、FR-2.2 明确提及 `tempfile.mkstemp`、`os.replace()`、`fcntl.flock`，FR-6.1 明确提及 `asyncio.Task`、`asyncio.Task.cancel()`。这些是 Python 标准库实现细节，超出业务需求描述范围，属于实现细节泄漏。 |
| CQ-2 | 聚焦用户价值和业务需求 | [x] | 用户故事 J1-J9 清晰描述用户场景与业务价值；背景章节解释根因与用户可感知问题。 |
| CQ-3 | 面向非技术利益相关者编写 | [ ] | 大量 FR 条款使用 `AST 扫描`、`fcntl.flock`、`atomic rename`、`asyncio.Task`、`threading.RLock` 等技术实现术语。非技术利益相关者无法理解功能意图。注：此为单用户 owner 技术项目，利益相关者即 Connor（工程师），可接受程度较高，但按标准仍属不通过。 |
| CQ-4 | 所有必填章节已完成 | [x] | 包含：背景、用户故事、边界场景、功能需求、不变量、Scope Lock、风险盘点、验收准则、关键实体、交付计划、复杂度评估，章节完整。 |

**Content Quality 小结**：4 项中 2 项通过，2 项未通过（CQ-1、CQ-3）。

---

## Requirement Completeness（需求完整性）

| # | 检查项 | 状态 | Notes |
|---|--------|------|-------|
| RC-1 | 无 [NEEDS CLARIFICATION] 标记残留 | [x] | 全文无 `[NEEDS CLARIFICATION]` 标记。 |
| RC-2 | 需求可测试且无歧义 | [x] | 每个 FR 条款使用 MUST/SHOULD/MAY 分级，验收场景使用 Given/When/Then 格式，具备可测试性。 |
| RC-3 | 成功标准可测量 | [x] | 验收准则章节定义了 4 个实测场景 + 11 个技术指标（SC-001 至 SC-011），均有明确判定标准。SC-001 明确代码行数范围，SC-003/004/005 使用 grep 零结果为判定标准。 |
| RC-4 | 成功标准是技术无关的 | [ ] | SC-001（代码变更量"删除 ≥ 2000 行 / 新增 ≥ 3500 行"）是实现层面指标，不是业务成功标准。SC-003/004/005 的 grep 结果为零亦为实现层技术指标。成功标准应以用户可观察行为（系统正确响应、功能可用）为主，而非代码行数。 |
| RC-5 | 所有验收场景已定义 | [x] | J1-J9 每个用户故事均有 Given/When/Then 格式的验收场景，验收准则章节有 4 个端到端实测场景。 |
| RC-6 | 边界条件已识别 | [x] | 边界场景章节覆盖：外部修改 USER.md、并发写竞争、Threat Scanner 误杀、observation routine 无活跃会话、bootstrap 重入、sub-agent 返回超时，共 6 个边界场景。 |
| RC-7 | 范围边界清晰 | [x] | Scope Lock 章节明确列出"本次不动的模块"和"不做的事项"，约束清晰。 |
| RC-8 | 依赖和假设已识别 | [x] | 风险盘点章节识别了 8 个风险（R1-R9）；Scope Lock 明确了对 ProviderRouter、Memory backend、Telegram channel 的不动约束；零新外部依赖假设在复杂度评估中明确（依赖新引入数：0）。 |

**Requirement Completeness 小结**：8 项中 7 项通过，1 项未通过（RC-4）。

---

## Feature Readiness（特性就绪度）

| # | 检查项 | 状态 | Notes |
|---|--------|------|-------|
| FR-A | 所有功能需求有明确的验收标准 | [x] | FR-1 至 FR-10 共 10 组需求，每组在验收准则章节均有对应的技术指标（SC-001 至 SC-011）和实测场景覆盖；FR-10 的 9 个事件类型由 SC-006 覆盖。 |
| FR-B | 用户场景覆盖主要流程 | [x] | J1（档案初始化）、J2（写入回显）、J3（Threat Scanner）、J4（档案修改）、J5（重装路径）涵盖 P0 Must 的全部主流程；J6-J8（P1 Nice）覆盖扩展流程；J9（Approval Gate）覆盖安全路径。 |
| FR-C | 功能满足 Success Criteria 中定义的可测量成果 | [x] | 验收 1-4 场景与 J1/J3/J6-J7/J5 直接对应；SC-001 至 SC-011 的技术指标覆盖 Constitution 合规、测试回归、代码退役等可测量目标。 |
| FR-D | 规范中无实现细节泄漏 | [ ] | 同 CQ-1：FR-1.1（"AST 扫描"、"动态 import"）、FR-2.2（"tempfile.mkstemp"、"os.replace()"、"fcntl.flock(LOCK_EX)"）、FR-3.2（"`_MEMORY_THREAT_PATTERNS`"）、FR-6.1（"asyncio.Task"、"asyncio.Task.cancel()"）均包含具体 Python API 和标准库调用，属于实现细节泄漏。 |

**Feature Readiness 小结**：4 项中 3 项通过，1 项未通过（FR-D）。

---

## 总体结果

| 维度 | 通过 | 未通过 | 合计 |
|------|------|--------|------|
| Content Quality | 2 | 2 | 4 |
| Requirement Completeness | 7 | 1 | 8 |
| Feature Readiness | 3 | 1 | 4 |
| **合计** | **12** | **4** | **16** |

**总体状态**：❌ 未通过（存在 4 项未通过）

---

## 未通过项汇总与修复建议

### CQ-1 / FR-D：实现细节泄漏（重叠项，同根原因）

**问题条款**：
- FR-1.1：`AST 扫描`、`registry.register()`、`threading.RLock`
- FR-2.2：`tempfile.mkstemp`、`os.replace()`、`fcntl.flock(LOCK_EX)` 原子替换
- FR-3.2：`_MEMORY_THREAT_PATTERNS`（Hermes 内部变量名）
- FR-6.1：`asyncio.Task`、`asyncio.Task.cancel()`

**修复建议**：将上述实现细节移入技术规划文档（如 TDD / ADR），或移至 spec 的"实现注记"附录区域，保持 FR 条款层面只描述可观察行为。例如：
- FR-1.1 可改为"Tool Registry 在启动时执行一次工具扫描，耗时 < 200ms"
- FR-2.2 可改为"写入操作保证原子性，并发写入时只有一个操作成功，无数据损坏"
- FR-6.1 可改为"Observation Routine 作为独立后台任务运行，支持随时停止，不阻塞主 Agent 会话"

### CQ-3：部分内容面向技术读者

**问题**：FR 章节大量技术缩写和标准库 API 对非技术读者不友好。

**修复建议**：考虑到本项目是单用户技术系统（Connor 即利益相关者），此问题优先级低于 CQ-1/FR-D。若确认"利益相关者仅为工程师"，可在 spec 头部加注说明，并将此检查项豁免。

### RC-4：成功标准包含实现层指标

**问题**：SC-001（代码行数）是实现约束，不是用户可观察的成功标准。

**修复建议**：将 SC-001 改为可测量的业务成果指标，例如"F082 引入的 bootstrap 工具集不再对任何入口可见（全量 grep 结果为零）"，或将 SC-001 移至"技术约束"小节而非"验收准则"。SC-003/004/005 的 grep 指标可保留，因其直接对应功能退役的业务目标（bootstrap.complete 退役是用户可感知的架构改善）。

---

*检查基于 spec.md（2026-04-27 Draft 版本）执行。*
