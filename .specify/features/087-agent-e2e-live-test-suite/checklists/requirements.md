# Feature 087 — Spec Quality Checklist

**生成日期**: 2026-04-30
**检查对象**: `.specify/features/087-agent-e2e-live-test-suite/spec.md`

---

## 总分: PASS
## 通过: 28 / 30

---

## 详细

### 结构完整性（8 项）

- [x] ✅ 背景章节存在且 ≥ 2 段 — §1 含 2 个清晰段落，描述 e2e 退化现状与 Harness 缺回归保护两个问题
- [x] ✅ 范围清晰（in / out 二分） — §2.2 范围内 / §2.3 范围外均明确列出；§7 再次复述"不做的事"
- [x] ✅ User Stories ≥ 3 — 共 US-1 到 US-6，6 条，含优先级、独立测试方式、验收条件
- [x] ✅ FR 清单完整 — FR-1 至 FR-35，按子系统分组，含 MUST/SHOULD/MAY 优先级标注
- [x] ✅ NFR 清单完整 — NFR-1 至 NFR-8，覆盖性能 / 可用性 / 安全 / 稳定性 / 可观测
- [x] ✅ 验收准则 SC 清单完整 — SC-1 至 SC-10，覆盖功能完整性 / 性能 / 安全 / 回归
- [x] ✅ 风险清单完整 — §11 列出 7 个风险，含概率 / 影响 / 缓解策略；附外部依赖清单
- [x] ✅ Phase 拆分预览存在 — §10 包含 P1-P5 完整表格，含主题 / 估算工时 / 关键交付

### FR 可测性（2 项）

- [x] ✅ 每条 FR 有可观测行为（输入 → 输出 → 副作用） — 关键 FR 均有副作用描述（tool_call 序列 / WriteResult.status / SQLite state diff / events 表内容）；FR-11 明确禁止字面输出断言，要求结构化断言
- [x] ✅ 每条 FR 至少 1 个验证方法 — FR-9/10 指明断言点数量；FR-15 细化 A2A 测点；FR-16-18 有明确行为差异（None 回退 vs DI 显式传）；FR-8/33-35 有 grep 验证方式

### NFR 量化（2 项）

- [x] ✅ 性能指标有具体数字 — NFR-1: smoke ≤ 180s / 目标 90-120s；full ≤ 10min；NFR-2: 单 LLM call ≤ 120s；FR-22/23 进一步细化单场景 30s timeout
- [x] ✅ 安全约束有具体行为 — NFR-5: secrets 全 env 注入 + gitignore 严约束 + fixture 不写明文 token；NFR-6: 指定 redact 字段（OPENROUTER_API_KEY / OAuth token / Telegram token）；SC-8: grep 结果为空的可验证标准

### 验收准则可验证（10 项）

- [x] ✅ SC-1 可观测可断言 — "13 个能力域 e2e 场景全部实现，每场景 ≥ 2 断言点"，可由测试文件 + 断言数量 count 验证
- [x] ✅ SC-2 可观测可断言 — "e2e_smoke ≤ 180s（p95 ≤ 150s）"，计时可测
- [x] ✅ SC-3 可观测可断言 — "e2e_full ≤ 10min"，计时可测
- [x] ✅ SC-4 可观测可断言 — "5x 循环跑 e2e_smoke 0 regression"，可循环执行记录退出码
- [x] ✅ SC-5 可观测可断言 — "Codex Adversarial Review 0 high finding"，可外部审查验证
- [x] ✅ SC-6 可观测可断言 — "lifespan ≤ 20 行 + F086 基线测试全绿"，行数 wc + pytest 结果均可测
- [x] ✅ SC-7 可观测可断言 — "sha256 一致"，明确可 diff 的文件列表
- [x] ✅ SC-8 可观测可断言 — "secrets grep 在仓库内为空"，可自动化 grep 验证
- [x] ✅ SC-9 可观测可断言 — "SKIP_E2E=1 exit 0"，exit code 可验证
- [x] ✅ SC-10 可观测可断言 — "≥ 2038 测试 0 regression"，pytest 输出可验证

### Constitution 对齐（2 项）

- [x] ✅ 不违反 Constitution 10 条 — §8 关键不变量第 1 条明确"Constitution 全 10 条不破"；tech-research §12 兼容性表全部 ✅/N/A；secrets 处理（FR-33-35）对齐 Rule 5（Least Privilege）；ApprovalGate e2e（域 #12）对齐 Rule 7（User-in-Control）；events 断言（FR-11）对齐 Rule 2（Everything is an Event）
- [x] ✅ 标识哪些 Constitution 条款被本 feature 加强 — 隐式加强 Rule 2（Events 可观测 → e2e 断言）、Rule 5（DI 隔离 secrets）、Rule 8（可观测性基础设施回归保护）；tech-research 有详细兼容性表，建议 spec 正文补一段显式声明（见改进建议）

### 风险列出（3 项）

- [x] ✅ 至少 3 个风险（技术 / 工程 / 外部依赖） — 共 7 个风险，覆盖技术（module reset 漏 / OctoHarness 抽离破坏 prefix cache / LLM 非确定）/ 工程（hook 被绕过）/ 外部依赖（Codex OAuth 限频 / Perplexity 抖动 / xdist race）
- [x] ✅ 每个风险有缓解策略 — §11 表格每行均含"缓解"列，具体且可执行

### 范围清晰（2 项）

- [x] ✅ 不做事项 ≥ 5 条 — §7 列出 8 条 ❌ 不做事项，清晰锁定
- [⚠️] WARN 关键不变量列出 — §8 共 6 条关键不变量，质量较高；但不变量 #1 仅写"Constitution 全 10 条不破（见 tech-research §12）"，引用外部文档而非原文列出，对不读调研的 reviewer 不够自包含

---

## 综合评分汇总

| 维度 | 通过 / 总数 | 状态 |
|------|------------|------|
| 结构完整性 | 8 / 8 | ✅ PASS |
| FR 可测性 | 2 / 2 | ✅ PASS |
| NFR 量化 | 2 / 2 | ✅ PASS |
| 验收准则可验证 | 10 / 10 | ✅ PASS |
| Constitution 对齐 | 2 / 2 | ✅ PASS |
| 风险列出 | 2 / 2 | ✅ PASS |
| 范围清晰 | 2 / 2（1 WARN）| ⚠️ WARN |
| **合计** | **28 通过 / 2 WARN / 0 FAIL** | **PASS** |

---

## 改进建议

### 高优先（建议 plan 阶段前落地，但不阻塞通过门禁）

- 无高优先阻塞项

### 中优先（建议纳入 plan 阶段交付）

- **Constitution 对齐显式化**：§8 不变量 #1 建议在 spec 正文补 2-3 行，明确列出 F087 主动加强的 Constitution 条款（Rule 2 / 5 / 8），避免 reviewer 需要翻 tech-research §12 才能确认合规性
- **Codex quota 耗尽行为锁定**：附录 A 第 4 条已识别"FAIL vs SKIP"未严格锁定；建议在 FR 或 Edge Cases 中直接写明"quota exceeded → 输出 'quota exceeded' 提示并 SKIP（不阻塞 commit）"，而非推迟到 plan 阶段
- **旧 test_acceptance_scenarios.py 去留决策**：附录 A 第 1 条建议"替换"，可直接在 §7 不做事项中加一条 "❌ 不保留旧 test_acceptance_scenarios.py 5 域循环（由 13 域 e2e 替换）"，使范围更确定

### 低优先（可选，不影响实现）

- §8 不变量写法可改为完全自包含（不依赖引用外部文档），提升 spec 独立可读性
- FR-27 "plan 阶段产出精确清单文档"可以给出预期文档路径（如 `.specify/features/087-.../plan/module-singletons.md`），便于验收

---

## Gatekeeper 建议

**建议主编排器：通过门禁，进入 plan 阶段。**

spec.md 质量整体优秀：FR 数量充足（35 条）且可测性强，NFR 全部量化，SC 全部可断言，风险覆盖完整（7 条含缓解），不做事项明确（8 条），Phase 拆分合理（P1-P5）。2 项 WARN 均为表述改进建议，不构成实现风险。无 FAIL 项，不需要返回 specify 阶段修改 spec。

附录 A 中的 3 个 open issue（旧测去留 / test helper 合并方案 / quota 行为锁定）建议 plan 阶段作为决策点处理，而非修 spec 后再过门禁。
