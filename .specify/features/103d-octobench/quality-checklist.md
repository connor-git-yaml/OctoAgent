# F103d OctoBench — Spec 质量检查清单

**生成时间**: 2026-05-27
**Spec 版本**: Draft（commit a69fe9c baseline）
**检查范围**: spec.md §0-§8

---

## 一、Spec 完整性

| # | 检查项 | 状态 | 说明 |
|---|--------|------|------|
| 1.1 | 6 个 User Story 均有 Acceptance Scenarios | ✓ | US1~US6 各含 2-4 条 AC |
| 1.2 | 每个 User Story 至少对应 1 个 SC | ✓ | US1→SC-001/003/008；US2→SC-002；US3→SC-006；US4（pass rate 分层）→SC-003/005；US5→SC-007；US6→SC-008 |
| 1.3 | 每个 SC 至少被 1 个 FR 落地 | ✓ | SC-001/006/007 ← FR-A 系列；SC-002 ← FR-G；SC-003/005 ← FR-C/D/E/F；SC-004 ← FR-H03；SC-008 ← FR-C01/C03；SC-009 ← FR-H01；SC-010 ← FR-F01~F03 |
| 1.4 | FR 总数（31）分布覆盖完整 | ⚠️ | FR-A07、FR-B01~B04 覆盖了 429/scorer，但 **FR-B02 效率评分的 `efficiency_baseline_tokens` 来源未在 SC 中对应测量**；ScoringRubric 实体中有 `efficiency_baseline_tokens` 字段但 SC 无"效率基准如何确定"的可验收标准 |
| 1.5 | 所有必填章节已完成（§0-§8） | ✓ | 共 8 节，包含 PoC 假设/范围决策/用户故事/边界/FR/实体/SC/Phase 拆分 |
| 1.6 | 无 [NEEDS CLARIFICATION] 残留 | ✓ | 未见此标记；[AUTO-RESOLVED] 标记已说明来源 |

---

## 二、可验证性

| # | 检查项 | 状态 | 说明 |
|---|--------|------|------|
| 2.1 | SC-001 耗时 ≤ 1 hour 可量化 | ✓ | wall clock 实测，明确可测量 |
| 2.2 | SC-003 pass rate"被记录"而非预设目标值 | ✓ | spec 明确"真实数值，不预设目标"——正确的 benchmark 哲学 |
| 2.3 | SC-006 "0 个 task hard fail"可验证 | ✓ | QUOTA_SKIP / 重试成功均有 result 字段可查 |
| 2.4 | SC-007 "≥ 95% 已完成 task 不重复执行"可测量 | ✓ | SQLite run 记录 + iteration 字段可精确统计 |
| 2.5 | SC-009 "grep 确认 0 文件变更"可自动验证 | ✓ | `git diff` 精确可执行 |
| 2.6 | AC1-4 "vs M5 baseline diff 视图"的验收标准清晰 | ⚠️ | AC1-4 描述了报告含 delta 视图，但 **未说明 delta 精度/格式要求**（如 pass rate 变化到小数点几位？regression 列表最大条目数？）FR-C03 有 delta 结构定义但与 AC1-4 未交叉引用 |
| 2.7 | FR-B03 LLM judge fallback 边界可测 | ⚠️ | FR-B03 说明 LLM judge 用于 Tier 1 partial 评分，但 **未定义何种情况触发 judge vs. 纯 EventStore 断言**；"EventStore 无法完全断言的语义场景"定义模糊，可能导致 scorer 实现偏差 |
| 2.8 | INCONSISTENT 结果（AC3-4 变体）的 pass rate 计算规则明确 | ✓ | §2 Edge Cases 明确：PASS=1 FAIL=1 时记 INCONSISTENT，不算入 pass rate |
| 2.9 | Tier 2 τ-bench Pass@1 的"对照 user_simulator 期望 actions"评分标准具体 | ⚠️ | §0.3 说明 τ-bench 走 Pass@1 对照期望 actions，但 **spec 未说明 user_simulator 的期望 actions 文件格式/来源**；FR-E01~E02 也未提 |

---

## 三、范围一致性

| # | 检查项 | 状态 | 说明 |
|---|--------|------|------|
| 3.1 | §7 排除"改动 production 代码"与 FR-H01 一致 | ✓ | FR-H01 为强制 [必须]，§7 明确排除 |
| 3.2 | §0.3 三层 task 数（25+20+5=50）与 FR/SC 一致 | ✓ | FR-D03 25 个；FR-E02+E04 合计 20 个（15+5）；FR-F01 5 个；SC-005 引用 50 task |
| 3.3 | Tier 1 域分布 task 数之和等于 25 | ✓ | 3+3+2+3+2+4+1+1+2+4=25，与表格一致 |
| 3.4 | Phase D 注册 CLI 命令路径一致性 | ⚠️ | Phase D 内容提到 `apps/gateway/src/.../cli/bench_commands.py`，**即修改了 `apps/gateway/` 下文件**，与 FR-H01"不修改 apps/gateway/ 下任何现有文件"存在潜在矛盾（新建文件 vs. 修改现有文件的边界未在 spec 中澄清；FR-H01 说"现有文件"，新增文件可能合规，但措辞有歧义）|
| 3.5 | §7 排除 Full Bench 150 task 与 CLAUDE.local.md M5→M6 规划一致 | ✓ | CLAUDE.local.md 明确 Full Bench 推 M6 中段 |
| 3.6 | PoC 5 task 构成（§0.4 + Phase 0 + AC2-1）互相一致 | ⚠️ | §0.4 说"1 Tier 1 / 2 Tier 2 / 1 Tier 3 / 1 混合"（合计 5）；AC2-1 说"Tier 1×1 / τ-bench×2 / GAIA×1 / Tier 3×1"（合计 5）；Phase 0 内容说"1 Tier 1 基础工具 / 1 τ-bench airline / 1 GAIA Level 2 / 1 Tier 3 H1 / 1 **并发压测用**"（合计 5）—— **"并发压测用"在 AC2-1 中映射不明确**（AC2-1 五项无"并发压测"，推断是"混合"，但未说明） |

---

## 四、依赖关系

| # | 检查项 | 状态 | 说明 |
|---|--------|------|------|
| 4.1 | PoC-H1（HF GAIA 访问）有降级方案 | ✓ | 降级：arxiv 附录公开样本 + [GAIA-FALLBACK] 标注 |
| 4.2 | PoC-H2（τ-bench task 数 ≥ 15）有降级方案 | ✓ | 降级：补入 retail domain |
| 4.3 | PoC-H3（SQLite WAL contention 可接受）有降级方案 | ✓ | 降级：共享 store 方案 |
| 4.4 | PoC-H4（τ-bench mock DB reset 无污染）有降级方案 | ✓ | 降级：file-based isolation（tmpdir copy）|
| 4.5 | Phase 顺序依赖关系合理（D 依赖 A/B/C，E 依赖 D）| ✓ | §0.4 Phase 顺序表明确说明 |
| 4.6 | Tier 3 T3-4 依赖 F099 N-H1 修复已在 master | ✓ | F099 已合入 master（CLAUDE.local.md 记录），依赖满足 |
| 4.7 | OctoHarness DI（F087）依赖已就绪 | ✓ | F087 已完成，4 个 DI 钩子可用 |
| 4.8 | Connor 真实场景 4 个 task 内容推迟到 Phase A 定义 | ⚠️ | FR-D03 和 AC1-1 都包含 Tier 1 的 25 task，但**真实场景 4 个 task 内容在 spec 阶段未定义**；若 Phase A 与用户确认后场景变化，可能影响 SC-005 的可验收性。spec 应说明这 4 个 task 的边界约束（如"必须覆盖某类能力"）|

---

## 五、Constitution 合规

| # | 检查项 | 状态 | 说明 |
|---|--------|------|------|
| 5.1 | Durability First（规则 1）：benchmark 结果落盘 | ✓ | FR-A05 append-only SQLite；AC5-1 续跑 |
| 5.2 | Everything is an Event（规则 2）：不适用 benchmark 自身 | ✓ | benchmark 是外部观测工具，不要求 benchmark runner 本身走 EventStore |
| 5.3 | Least Privilege（规则 5）：benchmark 不接触 secrets | ✓ | OctoHarness 通过 DI 注入，credential_store 来自 harness（F087 已隔离） |
| 5.4 | Agent Autonomy（规则 9）：不用硬编码规则替代 LLM 决策 | ✓ | Tier 1 使用真实 LLM 路径；FR-B03 提供 LLM judge fallback |
| 5.5 | User-in-Control（规则 7）：PoC 门禁要求用户拍板 | ✓ | Phase 0 GATE 明确用户拍板后才进 Phase A |
| 5.6 | Observability（规则 8）：benchmark 状态可查 | ✓ | SQLite 持久化 + JSON/Markdown 报告 |
| 5.7 | CLAUDE.local.md Codex review 节点规则合规 | ✓ | §0.2 表明确列出 pre-impl + 每 Phase 末 + Final cross-Phase review |
| 5.8 | CLAUDE.local.md 远端分支精简规则：不强制 push | ✓ | spec 无违反 push 规则的描述 |

---

## 六、零侵入约束验证

| # | 检查项 | 状态 | 说明 |
|---|--------|------|------|
| 6.1 | FR-H01 明确禁止修改 packages/ / apps/gateway/ / apps/web/ | ✓ | FR-H01 为 [必须] |
| 6.2 | Phase D CLI 注册路径与 FR-H01 有歧义 | ⚠️ | 见 3.4：Phase D 提到在 `apps/gateway/src/.../cli/bench_commands.py` 新建 CLI 文件。FR-H01 措辞为"不修改现有文件"，**新增文件是否属于违反"零侵入"未在 spec 中澄清**。pyproject.toml 中注册 CLI 命令是否需要修改现有文件也未说明 |
| 6.3 | τ-bench adapter 临时注册 Tool Registry 后是否修改 production 状态 | ⚠️ | FR-E01 要求"把 airline tools 临时注册到 benchmark-local Tool Registry（不修改 production registry）；跑完后清理注册"——但 spec **未说明"benchmark-local Tool Registry"与 production Tool Registry 的隔离机制**；若复用 production registry 单例，清理逻辑是否安全需要 plan 阶段明确 |
| 6.4 | SC-009 grep 验证具体化 | ✓ | SC-009 说"grep 确认无 production 文件被修改"，FR-H01 给出了三个目录范围 |

---

## 七、整体 Verdict

**整体结论：PASS-WITH-WARNINGS**

| 统计 | 数量 |
|------|------|
| 总检查项 | 29 |
| ✓ 通过 | 20 |
| ⚠️ 警告 | 9 |
| ✗ 失败 | 0 |

### 关键警告摘要（9 项，按优先级）

1. **[W1 范围歧义 - 高优先级]** Phase D CLI 路径 `apps/gateway/.../bench_commands.py` 是否违反 FR-H01"零侵入"措辞不明确（3.4 / 6.2）。建议 spec 或 plan 中明确"新增文件 ≠ 修改现有文件"，并说明 pyproject.toml / CLI 注册表改动是否属于例外。

2. **[W2 接口隔离 - 高优先级]** FR-E01 "benchmark-local Tool Registry"隔离机制未定义（6.3）。若 plan 阶段不明确，τ-bench adapter 临时注册可能污染 production Tool Registry 单例，违反 FR-H01 零侵入精神。

3. **[W3 FR 可验收性]** FR-B02 效率维度评分的 `efficiency_baseline_tokens`（per task domain）来源未在 SC 中量化（1.4）。SC 无"首次建立 baseline token 基准的方法"可验收标准。

4. **[W4 LLM judge 触发条件模糊]** FR-B03 未定义 EventStore 断言"无法完全断言"的判定条件（2.7）。可能导致 scorer 实现时 judge 触发过于随意或过于保守。

5. **[W5 τ-bench 评分标准细节]** τ-bench Pass@1 的"期望 actions 文件格式/来源"未在 spec 中说明（2.9）。FR-E01 只提"user_simulator 期望 actions"但未指向具体数据结构。

6. **[W6 PoC task 构成小出入]** Phase 0 content 的"1 并发压测用"在 AC2-1 中没有对应映射（3.6）。轻微但可能造成 PoC report 验收歧义。

7. **[W7 AC1-4 delta 视图格式未定义]** AC1-4 验收标准未说明 delta 精度/格式要求（2.6），与 FR-C03 未交叉引用。

8. **[W8 Connor 真实场景 task 延迟定义]** 4 个 task 推迟到 Phase A 定义，spec 缺少边界约束（4.8），SC-005 的可验收性依赖这 4 个 task 最终内容。

9. **[W9 ScoringRubric 实体利用率]** ScoringRubric 实体定义完整，但 spec 中无 FR 明确说明"ScoringRubric 以 YAML/DB 形式持久化"还是"硬编码在 scorer.py"——plan 阶段需要决策。
