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
- [x] T018 [US1] `TestBrokerExitBranches`（exception error 通道扫描 / not-found finalize / scan fail-open）✅（FR-2.1）
- [x] T019 [US1] raw 不改写 + fail-open（`test_injection_detected_and_raw_unmodified` + `test_scan_failure_fails_open`）✅（FR-2.5/2.6/5.3）

### D1 — live 渲染 helper

- [x] T020 `render_tool_result_for_llm`（`tooling/security_render.py`，唯一 helper，从 finding.advisory 派生确定性 `[security-warning]`，不回显恶意片段、不碰 raw）（FR-3.1/3.2）✅
- [x] T021 接 live `_append_feedback_to_history`（call_id + no-call_id 两路径经 helper）+ `runner._build_tool_feedback` 透传 `security_findings`（ToolResult→Feedback 链路）✅ 端到端验证 LLM content 含标注 + 原文 + raw 不变；532 passed（FR-3.1/3.3）
- [x] T022 [US1] `TestLiveRenderD1`（render 加标注保原文 + _append_feedback_to_history 标注 history 不改 raw）（US1-AC1/AC2）✅

### E1 — 持久化 + 读模型

- [x] T023 `record_tool_result_turn` 加 `security_findings` 参数 + metadata 写 **JSON-native** `model_dump(mode="json")`；`agent_session_turn_hook.after_tool_execute` 透传 `feedback.security_findings`；`findings_from_turn_metadata` 读 DTO（FR-3.4）✅
- [x] T024 [US1] persistence roundtrip 测试（`TestPersistenceRoundtripE1`：JSON-native dump + findings_from_turn_metadata 还原 + 空 metadata）✅
- [x] T025 D2 再入口经 `render_persisted_tool_turn_for_llm` 从**持久化 finding** 重渲染：①memory-extraction `_build_extraction_input`；②replay `build_agent_session_replay_projection`（tool_exchange_lines，截断后 render）。**compaction 经核实非 tool-result sink**（`_load_conversation_turns` 仅 USER_MESSAGE/MODEL_CALL_COMPLETED，无 tool 结果）→ 无需 wire（FR-3.1/3.4）✅
- [x] T026 research handoff sink（plan PR4-F1）：`_build_research_handoff_block` 边界重扫 `research_result_summary/_text` 经 `render_tool_result_for_llm`（第 5 类 dict-payload sink）（FR-3.5）✅
- [x] T027 [US1] no-bypass 契约测试（`TestNoBypassContract`：源码扫已知 LLM-bound sink 模块均引用 render helper，有界保证 FR-3.5）✅
- [x] T028 [US1] replay-survival 测试（`TestReplaySurvivalD2`：持久化 finding 重渲染保标注；US1-AC3/SC-004）✅；US2/US3 中央覆盖参数化（web.search/mcp/terminal）✅

**Checkpoint (MVP)**: web.fetch 注入被标注、原文保留、不 block、扛过 replay/compaction/memory-extraction。US1 独立可交付。

---

## Phase 4: US2（P2）— MCP / web.search 中央覆盖（验证为主，复用中央机制）

- [x] T029 [US2] `TestCentralCoverageUS2US3`（参数化 web.search/mcp/terminal → 中央 finalize 检出 + 事件，无 per-tool 特判）（US2/US3，SC-003）✅

---

## Phase 5: US3（P3）— terminal + error/异常通道

- [x] T030 [US3] terminal 覆盖（`TestCentralCoverageUS2US3` 参数化含 terminal.run）（US3-AC1）✅
- [x] T031 [US3] error/异常通道（`TestBrokerExitBranches::test_exception_error_channel_scanned`：raise 异常 error 含注入 → 终态扫描 source_field=error）（US3-AC2）✅

---

## Phase 6: F — 误报门槛（MUST，与 D1/E1/D2 可并行）

- [x] T032 CONTEXT pattern scope 归类定稿（T007 已定：8 双 scope + 10 CONTEXT-only）✅
- [x] T033 `test_tool_result_threat_scan_false_positive.py`：9 负样本（合法技术/安全讨论）0 误报 + 4 正样本全检出 + 安全博客不引字面 pattern 不命中（FR-6.1/SC-007/SC-008）✅
- [x] T034 [US1] clean 不标注（`test_clean_output_no_finding_no_event`）+ 安全讨论负样本（US1-AC4/AC5）✅

---

## Phase 7: G — C10 契约 + 收口

- [x] T035 PolicyGate 改经 `ContentThreatScanService.scan_memory`（模块级单例，C10 单一入口，字节级等价）+ `test_constitution_compliance.py` C10 表述更新为"两入口一 service"（FR-5.4/6.3）✅ 91 passed 零回归
- [x] T036 Blueprint 同步：`docs/codebase-architecture/harness-and-context.md` 2.3 ThreatScanner（scope 维度 + tool 结果管道）+ 2.6 改"两入口一 service"（ContentThreatScanService / PolicyGate 拦截 / ToolBroker 标注）✅。`docs/blueprint/` 索引级文档（core-design/module-design/architecture-audit）的 ThreatScanner 提及 → completion-report living-docs drift 项
- [x] T037 broad 回归 2765 passed 0 失败（apps/gateway 除 e2e_live + tooling/skills/core）+ e2e_smoke 8 passed；MEMORY 字节级等价（FR-5.5/SC-005）✅
- [x] T038 **Codex final review 3 轮闭环**：r1 2H+2M（finalize final-output / 去 chunk / research-handoff via service / 有界 hash）+ r2 1H（error_summary 扫完整 block）+ r3 1H（**SDK 范围外，用户拍板 + 文档排除**）。0 HIGH 残留 ✅
- [x] T039 completion-report.md（Phase 实际 vs 计划 + 11 轮 Codex 闭环 + limitations + master 合入建议）✅

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
