# Tasks: F124 工具结果威胁扫描（context-scope tool-result scan）

**Input**: [spec.md](./spec.md)（v0.6）+ [plan.md](./plan.md)（v5，Codex plan review r1-r4 闭环）
**Tests**: **强制**（spec §9 AC↔test 绑定 + SDD 强化规则）——非可选。
**组织方式**: 按 plan 依赖相位（A→B→C→D1→E1→D2→F→G）。F124 是**中央防护**：US1/US2/US3（web/MCP/terminal）共用同一套机制，无 per-story 独立实现 → 用户故事映射到**验证任务**（[US*] 标签），实现集中在 Foundational + US1 MVP。

**格式**: `[ID] [P?] [Story] 描述（含文件路径）`。[P]=不同文件可并行。

**Baseline**: d2936e0（≥3674 passed）。每 Phase 后回归 0 regression。

---

## Phase 1: Setup

- [x] T001 baseline 确立：worktree .venv symlink→主仓，须 `PYTHONPATH="apps/gateway/src:packages/tooling/src:packages/core/src:packages/skills/src" uv run --no-sync python -m pytest`（`timeout` macOS 无，用 Bash 工具 timeout）。threat_scanner 焦点 baseline 75 passed/1 xfailed
- [ ] T002 [P] 建测试骨架文件 `apps/gateway/tests/harness/test_tool_result_threat_scan.py` + `test_tool_result_threat_scan_false_positive.py`（空壳 + markers）

---

## Phase 2: Foundational（plan A+B）⚠️ BLOCKS 所有 US

**Purpose**: scanner scope 维度 + 跨边界类型 + DI 抽象。无行为改变（MEMORY 零回归），先建信心。

### A — scanner 底座（`apps/gateway/.../harness/threat_scanner.py`）

- [x] T003 加 `ScanScope(str,Enum)` 枚举（MEMORY/CONTEXT，gateway 内部）+ `ThreatPattern.scopes: frozenset[ScanScope]`（default {MEMORY}）+ `_p` scopes 参数（default {MEMORY}）；17 条 baseline 不传 → MEMORY-only（FR-1.1/1.3）✅ 零回归 116 passed
- [x] T004 `scan(content, scope=ScanScope.MEMORY)` + 循环 `if scope not in tp.scopes: continue`；默认 MEMORY 字节级等价（FR-1.2）✅
- [x] T005 ReDoS 审计：现有 17 条全线性（`\b…\b`/负字符类，无 nested 量词灾难）→ **无需重写保零回归**；新 CONTEXT pattern 用有界量词 `{0,N}` ✅
- [x] T006 有界扫描：`_MAX_SCAN_INPUT=2MB`；MEMORY 超限→`_DEGRADED_BLOCK_RESULT`（blocked=True，PolicyGate 现有路径自动拒绝，**无需改 PolicyGate**）；`scan_context(content, source_field)` CONTEXT 入口——≤上限带 overlap(=max max_span=256) 分块全覆盖 first-hit annotate、超限→degraded finding（never silently clean）；advisory 固定不回显恶意片段。✅ 跨块 66KB payload 命中验证 + 323 passed 零回归（FR-1.5/2.4，plan §6）
- [x] T007 CONTEXT pattern 族：8 条注入/角色劫持现有 pattern 升 `_MEM_CTX` 双 scope + 10 条新 CONTEXT-only（role-play/C2/fake-update/hidden-HTML/deception/leak，有界量词 + max_span）✅；`_MEMORY_THREAT_PATTERNS`→`_THREAT_PATTERNS` 重命名（去命名失真）；CONTEXT 检测功能完整、MEMORY 零回归 109 passed（FR-1.4/1.5）

### B — 跨边界类型 + DI 抽象（`packages/tooling`）

- [x] T008 `ToolSecurityFinding`（tooling/models.py，JSON-native：pattern_id/scope(str)/severity/advisory/source_field/degraded）✅ model_dump(mode=json) 验证为纯 dict（plan PR2-F1，FR-3.4）
- [x] T009 `ContentThreatScanProtocol`（tooling/protocols.py，@runtime_checkable，scope-free `scan_tool_context(content)->list[ToolSecurityFinding]`）✅（plan PR2-F1）
- [x] T010 `ToolResult.security_findings`（tooling）+ `ToolFeedbackMessage.security_findings`（skills，import tooling）默认 []，向后兼容 ✅ 261 passed（FR-2.5/3.4）

### Phase 2 验证

- [x] T011 `TestF124MemoryScopeBaselineEquivalence`（4 测试）：MEMORY 已知样本等价 + 默认 scope=MEMORY + CONTEXT-only pattern 不污染 MEMORY + MEMORY 集无 CTX-*（FR-5.2）✅
- [x] T012 `TestF124ContextScopeDetection`（4）+ `TestF124BoundedScan`（5）：CONTEXT 检出双 scope/CONTEXT-only + clean 空 + advisory 不回显 + MEMORY/CONTEXT 超限 degraded + 阈值边界 + **payload 过首块仍命中** + ReDoS 有界（FR-1.5/SC-006）✅
- [x] T013 `TestF124NoCircularImport`：源码扫 `packages/tooling` 无 `octoagent.gateway` import（plan PR2-F1）✅ 342 passed

**Checkpoint**: scanner scope + 类型 + DI 就位，MEMORY 零回归；现有 scan 测试全绿。

---

## Phase 3: US1（P1）🎯 MVP — 中央检测 + live 标注 + 持久化 + 再入口（plan C+D1+E1+D2）

**Goal**: web.fetch 注入被标注、放行、且扛过 replay。**这是 F124 的 MVP，建成后 US2/US3 主要靠验证。**
**Independent Test**: stub 注入 payload 的 web.fetch，经 broker + provider client + 模拟 replay。

### C — service + 检测 + 事件

- [x] T014 `ContentThreatScanService`（`apps/gateway/.../services/content_threat_scan.py`）实现 protocol：`scan_tool_context`（CONTEXT→scan_context）+ `scan_memory`（MEMORY→scan）双方法（FR-2.4/6.3）✅
- [x] T015 octo_harness 装配 `ContentThreatScanService()` 注入 ToolBroker（DI）✅。**PolicyGate 改经 service（scan_memory）留 T035/Phase G**（C10 契约同步，避免本轮动 policy.py）；MEMORY degraded→BLOCK 已由 scan() degraded 经 PolicyGate 现有 blocked 路径自动达成（无需改 PolicyGate）
- [x] T016 ToolBroker `_finalize_result(result, context, raw_output=)`：**全 8 退出分支**（not-found/start-fail/permission/hook-reject/hook-fail-closed/timeout/exception/success）均经此；成功路径传 pre-truncation `output_str`（after-hook 截断后仍扫全文）；扫 output+error 双字段；**不改 raw output/error**；scanner 异常 fail-open（FR-2.1/2.2/2.3/2.6）✅ 207 tooling passed 零回归。**去重**：scan_context first-hit 每字段≤1，output/error 各≤1 自然无重复
- [x] T017 broker DI `content_scanner: ContentThreatScanProtocol|None=None`；命中 emit `TOOL_RESULT_THREAT_FLAGGED`（新 EventType），payload 仅 hash + finding 元数据无原文（FR-4.1/4.2）✅ 冒烟验证
- [ ] T018 [P] [US1] `test_two_phase_scan_covers_all_exit_paths`：全 ~8 分支矩阵逐项断言（FR-2.1）
- [ ] T019 [P] [US1] `test_no_raw_mutation_fail_open` + `test_tool_search_promotion_reads_raw_json`：raw output/error 不改写、tool_search 提升读 raw JSON 不抛、scanner 异常 fail-open（FR-2.5/2.6/5.3）

### D1 — live 渲染 helper

- [ ] T020 `render_tool_result_for_llm(finding-aware)` 唯一 helper（从 security_findings 派生确定性 `[security-warning]`，不回显恶意片段，不碰 raw 字段）（FR-3.1/3.2）
- [ ] T021 接 live `provider_model_client.py:164 _append_feedback_to_history` 经 helper（覆盖 output + `ERROR: {error}` 两渲染路径）（FR-3.1/3.3）
- [ ] T022 [P] [US1] `test_injection_annotated_at_history_materialization` + `test_raw_output_unmodified_event_emitted`（US1-AC1/AC2）

### E1 — 持久化 + 读模型

- [ ] T023 `record_tool_result_turn`（agent_context_turn_writer.py:204）metadata 写 `security_findings`（**JSON-native** `model_dump(mode="json")`，防 :138 broad except 吞 TypeError）+ 读取 DTO（FR-3.4）
- [ ] T024 [P] [US1] `test_finding_propagated_to_session_turn_metadata`：ToolResult→Feedback→turn metadata save/read/backup roundtrip（FR-3.4）

### D2 — 持久化再入口渲染（从持久化 finding）

- [ ] T025 接 replay/compaction/memory-extraction 再入口经 helper 从**持久化 finding** 重渲染：`build_agent_session_replay_projection`/`render_agent_session_replay_block`/`_build_system_blocks`/`_summarize_turns`×2/`_call_summarizer`/`_build_compacted_messages`（plan §6 sink 清单）（FR-3.1/3.4）
- [ ] T026 **research handoff sink**（plan PR4-F1）：`agent_context.py:4128 _build_research_handoff_block` 读的 `dispatch_metadata.research_result_*` payload MUST 携带 findings 或边界重扫经 helper（FR-3.5）
- [ ] T027 [US1] no-bypass 权威测试 `test_no_render_path_bypasses_annotation_helper`：sink 正向约束跑真实代码，任何 tool-derived 内容进 LLM/system message 缺 finding-aware 渲染即失败（含 dict payload，FR-3.5）
- [ ] T028 [P] [US1] `test_annotation_survives_replay_compaction_and_memory_extraction`（US1-AC3/SC-004）

**Checkpoint (MVP)**: web.fetch 注入被标注、原文保留、不 block、扛过 replay/compaction/memory-extraction。US1 独立可交付。

---

## Phase 4: US2（P2）— MCP / web.search 中央覆盖（验证为主，复用中央机制）

- [ ] T029 [P] [US2] `test_central_coverage_mcp_and_search`：stub 含 CONTEXT pattern 的 MCP/web.search → 被标注 + 写事件，**无 per-tool 特判**（US2，SC-003）

---

## Phase 5: US3（P3）— terminal + error/异常通道

- [ ] T030 [P] [US3] `test_terminal_output_annotated`（US3-AC1）
- [ ] T031 [P] [US3] `test_error_and_exception_channel_scanned`：raise 异常/is_error=True 且 error 含注入 → finalize 终态扫描 + 渲染标注（US3-AC2，验证 T016 异常分支覆盖）

---

## Phase 6: F — 误报门槛（MUST，与 D1/E1/D2 可并行）

- [ ] T032 CONTEXT pattern scope 归类定稿（17 条哪些同属 CONTEXT + 新增族）；每条配正负样本
- [ ] T033 [P] `test_tool_result_threat_scan_false_positive.py`：真实 web/search/MCP/terminal 样本 + 安全技术负样本集（prompt-injection 博客/jailbreak 防御文）；标注率 ≤ 阈值；每新 CONTEXT pattern 正负覆盖（FR-6.1/SC-007/SC-008）
- [ ] T034 [P] [US1] `test_security_blog_negative_sample` + `test_clean_output_no_annotation`（US1-AC4/AC5）

---

## Phase 7: G — C10 契约 + 收口

- [ ] T035 更新 `test_constitution_compliance.py` C10 表述为"内容威胁扫描统一经 ContentThreatScanService；PolicyGate=权限/拦截入口、tool 检测=标注入口"（FR-5.4/6.3）
- [ ] T036 Blueprint 同步：`docs/blueprint/`（harness-and-context / 安全模型 / C10）+ `docs/codebase-architecture/harness-and-context.md`（CLAUDE.md Blueprint 同步规则）
- [ ] T037 全量回归 0 regression vs d2936e0 + `pytest -m e2e_smoke` 全过（FR-5.5/SC-005）
- [ ] T038 **Codex final cross-Phase review**（输入 plan + 全 Phase diff；命中重大架构变更，CLAUDE.local.md 强制）；含 **no-bypass 权威测试对真实代码暴露的残留 sink**（plan PR4-F1 收口）
- [ ] T039 产出 completion-report.md（对照 plan Phase 实际 vs 计划）+ handoff.md + living-docs drift 检查

---

## Dependencies & Execution Order

```
Setup(T001-2) → Foundational(A: T003-7 串行同文件 / B: T008-9[P] → T010 / 验证 T011-13[P])
  → US1 MVP: C(T014→T015→T016→T017, 验证 T018-19[P]) → D1(T020→T021, T022[P]) → E1(T023→T024[P]) → D2(T025→T026→T027, T028[P])
  → US2(T029) / US3(T030-31) 可并行（中央机制已就位）
  → F(T032→T033-34[P]) 可与 D1/E1/D2 并行
  → G(T035→T036→T037→T038→T039) 收口串行
```

- **Foundational BLOCKS 一切**（scanner+类型+DI）。
- **US1 是 MVP**（C+D1+E1+D2 = F124 核心机制）；建成后 US2/US3 几乎纯验证。
- **核心串行链**：T016（finalize）→ T020-21（render）→ T023（persist）→ T025-27（re-entry）——因 D2 依赖 E1 持久化（plan PR2-F1/P-F3）。
- A 组（T003-7）同文件 threat_scanner.py 须串行；B 组 T008/T009 不同文件可 [P]。

## MVP / 增量交付

1. Setup + Foundational → scanner+类型+DI 就位（MEMORY 零回归）
2. US1（C+D1+E1+D2）→ web 注入标注 + replay-safe → **MVP，可 demo**
3. US2/US3 验证 → 证明中央覆盖 MCP/search/terminal/error
4. F 误报门槛 → 护栏可信
5. G → C10 契约 + Blueprint + 回归 + Codex final + report

## Notes

- 测试强制（spec §9）；先写测试断言失败再实现（TDD where practical）。
- `[P]` = 不同文件无依赖。
- commit 按 task / 逻辑组；commit message 不加 Co-Authored-By（CLAUDE.local.md）。
- **plan PR4-F1 收口**：no-bypass 残留 sink 由 T027 权威测试对真实代码机械暴露 + T038 Codex final 兜底——不靠手工枚举穷举。
- 每 Phase 后回归 0 regression vs baseline。
