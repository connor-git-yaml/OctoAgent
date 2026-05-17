# 质量检查表：F101 Notification + Attention Model

**用途**: 对 spec.md 进行 8 维度质量验证，确认规范可进入 plan 阶段
**生成日期**: 2026-05-15
**Feature**: F101 Notification + Attention Model
**输入文件**: spec.md + tech-research.md（baseline `182e9ed`）

---

## 总体评分：6 PASS / 2 WARN / 0 FAIL

spec.md 整体质量良好，无阻塞性 FAIL，2 项 WARN 均有明确修复方向，可带 WARN 进入 plan 阶段（建议在 GATE_DESIGN 时同步确认）。

---

## CHK-1 用户场景完整性

**检查项**：每条 FR/AC 是否归属某个 User Story（无孤立需求）

**状态**: [x] PASS

**验证明细**：

| 块 | FR/AC | 归属 User Story |
|----|-------|----------------|
| B | FR-B1~B7, AC-B1~B6 | US3（完成通知）/ US2（quiet hours）/ US1（审批）|
| C | FR-C1~C7, AC-C1~C7 | US1（审批修复）/ US5（guard 异常）|
| D | FR-D1~D4, AC-D1~D3 | US4（长 prompt recall）|
| E | FR-E1, AC-E1 | US1/US3（notification 集成路径）|
| F | FR-F1, AC-F1 | 决策点 1，§8 已有明确决策，不是孤立需求 |
| FR-C7 | AC-C7 | style 清洁，显式标注 N/A，合理豁免 |

所有 FR/AC 有明确的 User Story 归属或合理豁免说明，无孤立需求。

---

## CHK-2 AC 可独立测试性

**检查项**：AC 是否有完整 Given/When/Then 三段结构，每条可独立执行

**状态**: [x] WARN

**验证明细**：

大多数 AC 结构完整，以下两条存在问题：

**问题 1：AC-C4（ask_back integration test）缺 Given 段**

spec §5 AC-C4 原文：
> Given: Worker 发出 ask_back 请求的完整测试场景
> When: ask_back_handler 执行 → WAITING_INPUT → 用户 attach_input → resume
> Then: 完整事件链可验证...

"完整测试场景"不是可执行的前置条件——Given 段应明确测试环境（mock/integration）、Worker 初始状态、ask_back 触发方式。

**问题 2：AC-F1（ask_back resume runtime_context）Then 条件过弱**

> Then: 按选项 C（推荐），recall planner 在 turn N+1 正常运行；系统不报错，任务正常继续

选项 C 是"保持 baseline 行为"，AC-F1 的本质是"验证 baseline 不被破坏"。但 Then 没有指定任何可量化的验证点（如检查哪个函数被调用，或哪个日志/事件被 emit）。

**修复方向**：
- AC-C4 Given 改为：`Given: integration test 环境，Worker runtime 已 dispatch，task 处于 RUNNING 状态，mock TaskStore 和 EventStore 已初始化`
- AC-F1 Then 补充：`then: is_recall_planner_skip 返回 False 可验证（可通过 spy recall_planner 调用确认）`

---

## CHK-3 引用准确性

**检查项**：spec 引用 tech-research.md 的行号是否真实有效

**状态**: [x] WARN

**验证明细**：

逐项核查 spec §12 引用索引表（13 条引用）：

| 引用编号 | spec 标注行号 | tech-research 实际行号 | 状态 |
|---------|-------------|----------------------|------|
| ref-1（A-1-1）| 行 20-32 | A-1-1 表格第 20 行开始，至行 31 | PASS |
| ref-2（A-1-2）| 行 36-44 | A-1-2 从行 34 开始，代码片段在行 39-41 | PASS（误差 2 行可接受）|
| ref-3（A-2-1）| 行 50-64 | A-2-1 从行 50 开始，结论表在行 62-64 | PASS |
| ref-4（A-2-2）| 行 68-78 | A-2-2 从行 66 开始，实测发现在行 70-78 | PASS（误差 2 行可接受）|
| ref-5（A-2-3）| 行 82-108 | A-2-3 从行 80 开始（误差 2 行），但 spec 标注 `octo_harness.py:700-703` 是代码行而非 tech-research 行号 | **WARN**（说明文字混淆了 tech-research 行号与源代码行号）|
| ref-6（A-2-3 选项 1）| 行 190-212 | A-3 推荐方案在行 190-212 | PASS |
| ref-7（A-2-4）| 行 110-121 | A-2-4 从行 110 开始至行 121 | PASS |
| ref-8（A-2-5）| 行 125-134 | A-2-5 从行 123 开始，内容在行 125-134 | PASS |
| ref-9（A-2-6）| 行 138-150 | A-2-6 从行 136 开始，位置在行 138-150 | PASS |
| ref-10（A-2-7）| 行 154-160 | A-2-7 从行 152 开始，内容在行 154-160 | PASS |
| ref-11（A-4）| 行 220-262 | A-4 从行 216 开始，关键代码在行 220-262 | PASS |
| ref-12（A-5）| 行 266-310 | A-5 从行 264 开始，实测在行 266-310 | PASS |
| ref-13（风险 R1-R5）| 行 314-342 | 关键风险部分从行 312 开始至行 342 | PASS |

**问题**：ref-5 的描述混淆了两个不同层次的行号——括号中的 `octo_harness.py:700-703` 是源代码文件行号，而前面的 "行 82-108" 是 tech-research.md 的文档行号。这在交叉引用时容易产生歧义（plan 子代理可能误解 `行 82-108` 是 octo_harness.py 的行号）。

**修复方向**：ref-5 改为：`行 80-108（tech-research §A-2-3）| 说明: 此节引用 octo_harness.py:700-703 为实测代码位置`。或在引用索引表头部加注："行号列指 tech-research.md 文档行，括号内为对应源码文件行"。

---

## CHK-4 范围边界

**检查项**：Out of Scope 列表是否完整、与 FR 无重叠

**状态**: [x] WARN

**验证明细**：

Out of Scope §6 共 8 条，逐条检查无 FR 重叠。但发现以下边界模糊点：

**问题：FR-B7 与 §6 条 7 的边界不够清晰**

FR-B7 原文：
> `attention_work_count` 字段 SHOULD 作为 Attention Model 的输入信号，记录当前 Worker 并发数... F101 仅需确保该字段在 Worker 开始/结束时正确更新，不需要实现完整的 Attention Model 决策逻辑（推 F102）

§6 Out of Scope 第 7 条：
> 完整 Attention Model 决策逻辑：attention_work_count 维护是 F101 范围，但"根据 attention model 决定是否发通知的 LLM 决策路径"属于 F102 扩展范围

两段描述一致但各自独立，容易让实施者不确定 `attention_work_count` 正确更新到底包含哪些代码路径（Worker start/end hook 是否已存在？需要新增哪个 event？）。

**无 FR 重叠问题**：Out of Scope §6 的 8 条均与 FR-B/C/D/E/F 无实质性重叠。

**修复方向**：在 FR-B7 中补充一行"实现范围：在 WorkerRuntime dispatch 开始（+1）和任务终态（-1）时更新该字段，不超出此范围"，消除边界歧义。

---

## CHK-5 复杂度评估合理性

**检查项**：§11 复杂度评估是否与功能范围相符，维度数据是否有依据

**状态**: [x] PASS

**验证明细**：

| 评估维度 | spec 给出值 | 核查结论 |
|---------|-----------|---------|
| 新增/修改组件数 | 5 | 实测：NotificationService / ApprovalGate / task_runner / octo_harness / chat.py — 5 个，正确 |
| 新增/修改接口数 | 6 | 列举 6 个接口点，与 FR 清单对应，正确 |
| 引入新外部依赖数 | 0 | 全部内部组件扩展，tech-research 确认，正确 |
| 跨模块耦合 | 是（5 模块）| 5 模块名列清，与 FR 改动范围一致 |
| 复杂度信号 | 2（状态机 + 并发控制）| WAITING_APPROVAL 超时 + dismiss 幂等，合理 |
| 总体复杂度 | HIGH | 组件 5 + 跨模块耦合 + 2 个复杂度信号，HIGH 合理 |

HIGH 复杂度决议建议（6+ Phase、Codex pre-impl + Final review）与同规模 Feature 历史（F098：5 阶段 + 15 commits）相符。

---

## CHK-6 风险覆盖度

**检查项**：spec §9 的 6 个风险是否覆盖 tech-research 全部 5 个风险

**状态**: [x] PASS

**对照表**：

| tech-research 风险 | 严重度 | spec §9 对应风险 | 覆盖状态 |
|-------------------|-------|----------------|---------|
| 风险 1：SSEHub.broadcast 不支持 per-session_id 广播 | MED | R1 | 完整覆盖，缓解策略一致（plan Phase 0 侦察） |
| 风险 2：WAITING_APPROVAL 超时清理复杂度 | HIGH | R2 | 完整覆盖，严重度升级为 HIGH 合理 |
| 风险 3：NotificationService 未绑定到 task_runner | MED | R3 | 完整覆盖，缓解策略一致 |
| 风险 4：USER.md 活跃时段字段无解析逻辑 | MED | R4 | 完整覆盖，并新增结构化字段方案 |
| 风险 5：F3 HIGH 与 A-2-3 强耦合 | HIGH | R5 | 完整覆盖，FR-C2 联合约束条款对应 |

spec 额外新增 R6（D 块阈值配置）：属 FR-D3 范围的 LOW 风险扩展，合理补充，不与 tech-research 风险重叠。

tech-research 全部 5 个风险均在 spec §9 找到对应覆盖，且严重度标注一致。

---

## CHK-7 决策点完整性

**检查项**：块 F + 块 C-6 决策点是否都有推荐选项和理由

**状态**: [x] PASS

**验证明细**：

**决策点 1（§8 块 F — AC-5 ask_back resume runtime_context）**：
- 三选一表格，含侵入性 + 推荐度两个维度
- 推荐 C（保持 baseline），4 条推荐理由列举清楚
- 明确标注"最终决策待用户 GATE_DESIGN 确认，spec 默认选 C"
- 状态：有推荐 + 理由 + 确认机制。PASS

**决策点 2（§8 块 C-6 — N-H1 PARTIAL startup_recovery）**：
- 二选一表格，含理由列
- 推荐 F101 实施，3 条推荐理由：同文件同层次 / F107 范围不重叠 / 漏洞不应持续 2 Feature 周期
- 明确标注"最终决策：F101 实施，FR-C6 + AC-C6 已据此收入 spec"
- 状态：有推荐 + 理由 + 已作出最终决策。PASS

两个决策点均结构完整，无"待定"悬空状态。

---

## CHK-8 依赖前置完整性

**检查项**：§7 依赖表是否完整覆盖 FR 实施所需的前置条件

**状态**: [x] WARN

**验证明细**：

§7 依赖表 13 条逐条核查，覆盖主要前置：F064（Notification 基础设施）、F084（ApprovalGate / SSEHub / user_profile.update）、F098/F099（CONTROL_METADATA_UPDATED / source_runtime_kind）、F100（force_full_recall 字段和链路）均有覆盖。

**问题：FR-B5 dismiss 同步机制未在依赖表列明**

FR-B5 原文：
> Web SSE 通道和 Telegram 通道的 dismiss 语义 MUST 统一：任一通道 dismiss 后，通知标记为已处理，重复 dismiss 幂等不报错

tech-research §A-1-1 指出 TelegramNotificationChannel 在 `notification.py:318` 实现，但 dismiss 同步机制（跨通道状态共享）需要一个共享 store（内存 set 或持久化）。依赖表未列明"通知 dismiss 状态存储机制"的前置——内存 set 是否足够？是否需要 TaskStore 或独立表？这个前置未明确。

**其他小缺失（可接受）**：FR-C4（integration test）依赖测试 fixtures 基础设施，属内部实现细节，不要求在依赖表列明。

**修复方向**：在 §7 依赖表末尾新增一行：`通知 dismiss 状态存储 | F064（NotificationService 内存 set） | notification.py:92-229（内部 _sent_notifications set）` 并在 FR-B5 备注"dismiss 幂等由 NotificationService 内存 set 实现，重启后状态不保留——这是已知限制还是需要持久化，plan 阶段明确"。

---

## 建议新增检查项（超出 8 项范围，记录备用）

以下问题在 8 项框架内无对应检查，记录供后续改进：

1. **状态机完整性核查**：FR-C1/C2/C3 联合实施约束在 spec §10 实施顺序中再次强调，但未有独立的"状态机转移图"或"状态合法性验证"。HIGH 风险修复建议在 plan 阶段产出 WAITING_APPROVAL 完整状态机转移图作为前置验证。

2. **测试分层清晰度**：AC 中混用"可独立测试"/ "integration test" / "style 验证"三种标记，但未定义各层的 mock 策略边界。建议 plan 阶段补充测试分层说明。

---

## 摘要

| 检查项 | 状态 | 备注 |
|--------|------|------|
| CHK-1 用户场景完整性 | PASS | 全部 FR/AC 有 US 归属，FR-C7 合理豁免 |
| CHK-2 AC 可独立测试性 | WARN | AC-C4 缺具体 Given；AC-F1 Then 条件过弱 |
| CHK-3 引用准确性 | WARN | ref-5 混淆 tech-research 行号与源码行号，易导致 plan 子代理误解 |
| CHK-4 范围边界 | WARN | FR-B7 与 §6 第 7 条边界描述重复但不精确，attention_work_count 更新范围未明确 |
| CHK-5 复杂度评估合理性 | PASS | HIGH 复杂度自评与 FR 范围相符，历史 Feature 对比合理 |
| CHK-6 风险覆盖度 | PASS | tech-research 5 个风险全部覆盖，R6 为合理扩展 |
| CHK-7 决策点完整性 | PASS | 块 F 和块 C-6 均有推荐 + 理由 + 最终决策状态 |
| CHK-8 依赖前置完整性 | WARN | FR-B5 dismiss 同步机制缺 dismiss 状态存储前置说明 |

**总计**：6 PASS / 2 WARN / 0 FAIL

**结论**：无阻塞性 FAIL，可进入 plan 阶段。建议在 GATE_DESIGN 阶段同步确认以下 WARN 项修复方向，或在 plan Phase 0 侦察中明确：
- AC-C4 补 Given 段
- ref-5 行号描述歧义
- FR-B7 attention_work_count 更新范围精确定义
- FR-B5 dismiss 状态存储机制明确
