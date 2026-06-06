# F103d OctoBench — 总 Completion Report

> 生成：2026-05-31（Phase F 文档闭环）
> Feature：F103d OctoBench（混合 benchmark suite + M5 baseline）
> 终态 commit：`3b0bf74`（Phase 0/A/B/C/D/E + 3 个 production bug 修复全部合入 master）
> baseline 数据：`benchmarks/baselines/preliminary-m5-tier13-1iter-deepseek.{json,md}`
> **重要定性**：M5 baseline 是 **preliminary**（Tier1+3 / 1-iter / DeepSeek-V3.2 控变量），非 spec AC1-1 定义的完整版（50 task / 3-iter / ≤1h）。差额已被用户显式判定为独立 Feature（轻量 bootstrap + Tier2 真跑），不阻塞 M6。

---

## 1. 一句话总结

F103d 交付了**完整的 benchmark 基础设施**（三层 task 体系 + runner + scorer + reporter + CLI + SQLite 持久化 + 断点续跑），并跑出 **preliminary M5 baseline**。真跑过程**暴露并修复了 3 个 production bug**（benchmark 价值实证）。核心价值已交付；完整全量跑（Tier2 纳入 + 性能优化达 SC-001）作为独立 Feature 推后，不阻塞 M6。

---

## 2. Phase 实际执行 vs 计划

| Phase | 计划 | 实际 | 偏离说明 |
|-------|------|------|----------|
| 0 PoC | 5 task 实测 + 4 假设验证 | ✅ τ-bench airline=50 充裕 / GAIA gated→fallback / 8 并发 SQLite 0 lock / PoC-H4 推迟 Phase B | GAIA gated 访问失败 → 用户拍板走 fallback（arxiv 公开样本）|
| A Tier 1 | 25 私有 task + scorer | ✅ 25 task + EventStore scorer + LLM judge 触发常量化；Codex 4 HIGH + 5 MED 闭环 | HIGH-1 scorer async bug（原 Tier 1 全 FAIL）证明 review 价值 |
| B Tier 2 | τ-bench 15 + GAIA 5 adapter | ✅ adapter + preflight；Codex 2 HIGH + 7 MED 闭环 | HIGH-1 threading.Lock 跨 yield 阻塞 async event loop |
| C Tier 3 | H1/H2/H3 5 task + audit scorer | ✅ 5 task + score_tier3 audit_chain_assert；Codex 6 轮 17 finding 闭环 | — |
| D Runner/Scorer/Reporter | 完整 runner + CLI | ✅ store/worker/reporter/conftest/CLI；Codex 4 HIGH + 1 MED 闭环 | asyncio.Lock 短临界区不跨 await（避免 Phase B 陷阱）|
| E M5 baseline | 50 task × 3 × 8 ≤ 1h | ⚠️ **preliminary**：Tier1+3 / 1-iter，30 task / 44 min | runner 端到端验证可用；性能不达 SC-001（每 task 起完整 OctoHarness 太重）→ 独立 Feature |
| F 文档闭环 | completion-report + handoff | ✅ 本文 + handoff.md + milestones.md M6 同步 + 2 scorer 断言修正（1 修 1 归档）| memory 断言修正；threat_scanner 重设计归档 |

---

## 3. 逐 AC 验收

### AC1（M5 baseline）
- **AC1-1** 50 task × 3 × ≤1h：⚠️ **PARTIAL** — 跑通 30 task（Tier1+3）/ 1-iter / 44 min。基础设施支持 50×3，但每 task 起完整 OctoHarness 性能不达标 → 轻量 bootstrap 独立 Feature
- **AC1-2** JSON + Markdown 报告：✅ **PASS** — `preliminary-m5-tier13-1iter-deepseek.{json,md}`，含总/三层 pass rate + token + duration
- **AC1-3** SQLite ≥150 BenchmarkRun：⚠️ **PARTIAL** — 30 条（1-iter）；schema + append-only 就绪，3-iter 是规模问题非能力问题
- **AC1-4** vs M5 baseline diff 视图：✅ **PASS（代码就绪）** — reporter `--compare` 实现 + unit test；M6 真实对比时端到端验证

### AC2（PoC）
- **AC2-1 / 2-1b / 2-2 / 2-3**：✅ **PASS** — `phase-0-poc-report.md` 含 5 task 耗时 + 4 假设结论 + 8 并发压测 0 lock + 用户拍板进 Phase A

### AC3（429/timeout graceful）
- **AC3-1~3-4**：✅ **PASS（代码就绪 + 真跑验证部分）** — worker.py retry-after + exp backoff jitter + QUOTA_SKIP/TIMEOUT/INFRA_ERROR 三态；真跑 1 个 TIMEOUT 正确隔离

### AC4（报告结构）
- **AC4-1** summary + by_tier + by_domain：✅ **PASS** — JSON 三层结构齐全
- **AC4-2** τ-bench / GAIA 独立 pass rate：⚠️ **PARTIAL** — 结构支持，但 Tier2 未纳入真跑（by_tier.tier2 为空）
- **AC4-3** Tier 3 audit 信号断言可查：✅ **PASS** — score_tier3 逐条 audit_assertions + 失败断言报告

### AC5（断点续跑）
- **AC5-1 / 5-2**：✅ **PASS（代码就绪）** — store.get_pending_runs + worker resume；Phase D T-D-8 手工验证

### AC6（compare）
- **AC6-1** --compare delta：✅ **PASS（代码就绪）** — delta 区块 0.001 精度 + regression/improvement 列表
- **AC6-2** baseline not found 报错：✅ **PASS** — 不静默失败

### SC（成功标准）
- SC-001 ≤1h：⚠️ PARTIAL（30 task 44min，全量需轻量 bootstrap）
- SC-002 PoC report：✅ / SC-003 baseline 数值记录：✅（preliminary）/ SC-004 0 regression：✅（3674 passed）
- SC-005 三层 YAML 化：✅（Tier1 25 + Tier2 20 + Tier3 5）/ SC-006 429 graceful：✅
- SC-007 resume：✅ / SC-008 handoff：✅（本 Phase）/ SC-009 零侵入：✅（packages/apps 0 改动）
- SC-010 Tier3 H1/H2/H3-A/H3-B/H3 各 1：✅ / SC-011 INCONSISTENT ≤5%：✅（0 个，1-iter 无 majority vote）

---

## 4. benchmark 价值实证：真跑暴露 3 个 production bug（已修）

| commit | bug | 严重度 |
|--------|-----|--------|
| a6b51fc | provider_client double `/v1` → 404（含 /v1 api_base 拼出 `/v1/v1`）；此前靠 instance workaround，下个配 /v1 的 provider 会再踩 | 真缺陷（治本）|
| 3eabd58 | watchdog RepeatedFailureDetector offset-naive datetime 比较 TypeError | 日志污染 + 漂移静默失效 |
| 3b0bf74 | session memory extraction shutdown 竞态（fire-and-forget task 未注册 drain）→ 最后一轮提取可能丢失 | durability |

---

## 5. 已知 limitations（诚实记录）

| # | limitation | 处置 |
|---|-----------|------|
| L1 | **性能**：每 task 起完整 OctoHarness，30 task 44min，全量 50×3 不达 SC-001 | 独立 perf Feature（轻量 bootstrap，方案：tool registry importlib scan 抽到 runner 进程级跑一次，~1-1.5 天）|
| L2 | **Tier2 未真跑**：runner_fn Tier2 分派 + τ-bench env.step/user_simulator 接入未完成 | 独立 Feature（3-5 天）|
| L3 | **threat_scanner 2 task = false FAIL**：task prompt 直接发恶意内容给 Agent，但 production threat scan **仅在 user_profile_tools.py memory 写入路径触发**（不扫 chat prompt）；且断言用 `POLICY_DECISION` 但 production emit `MEMORY_ENTRY_BLOCKED` | **task 需重新设计**（prompt 诱导 memory 写入 + 断言改 MEMORY_ENTRY_BLOCKED）→ 归档 backlog，跑 M6 对比前必修 |
| L4 | **memory 域 = 控变量 + 原断言错位**：原断言 AGENT_PRIVATE，但 main direct 按 F094 默认 PROJECT_SHARED | ✅ Phase F 已修 t1_memory_001（AGENT_PRIVATE → PROJECT_SHARED）|
| L5 | **delegation/philosophy 全 0% = DeepSeek 控变量画像**：DeepSeek-V3.2 从不主动 delegate_task（0 SUBAGENT_SPAWNED）；捕获链 octo_runner.py:1107 已正确接入 | 非缺陷；换 production LLM 复跑才有真数据 |

---

## 6. M5 baseline 数据快照（preliminary）

```
total_tasks: 30 (Tier1 25 + Tier3 5)，1-iter，DeepSeek-V3.2 控变量
pass_rate: 0.276 | weighted_score: 0.300 | duration: 44.1 min
token: input=2,489,005 / output=39,078 | skipped: TIMEOUT=1 | inconsistent: 0
by_tier: tier1 0.333 (8/25) | tier3 0.000 (0/5) | tier2 (未跑)
扎实域: tool_call 100% / connor_real_world 75% / snapshot 50%
0% 域: delegation/max_depth/memory/philosophy_*/threat_scanner/skill_pipeline/routine
```

能力画像三分类见 handoff.md。

---

## 7. 零侵入 production 验证

`git diff <F103c baseline a69fe9c>..3b0bf74 -- packages/ apps/web/` = benchmark 模块 0 改动（benchmark 全在 `benchmarks/` + `apps/gateway` 仅 bench_commands.py 新增 + pyproject 3 行 entry point）。3 个 production bug 修复是**真跑发现的既有缺陷**，不是 benchmark 引入。

---

## 8. Final Review 状态

按 CLAUDE.local.md §工作流改进，纯文档 Phase 豁免 per-Phase review。F103d 各代码 Phase（A/B/C/D/E）均已 Codex 闭环（A 4H+5M / B 2H+7M / C 6 轮 17 finding / D 4H+1M / E 3 轮）。Phase F 为纯文档 + 2 个 YAML 断言修正（1 修 1 归档），低风险，主 session 自审。
