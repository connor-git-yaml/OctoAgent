# Implementation Plan: F124 工具结果威胁扫描（context-scope tool-result scan）

**Branch**: `feature/124-tool-result-threat-scan` | **Date**: 2026-06-08 | **Spec**: [spec.md](./spec.md)
**Input**: `.specify/features/124-tool-result-threat-scan/spec.md`（v0.5，Codex review round 1-4 全闭环）
**Status**: ✅ 实现完整（Codex Plan review r1-r4 + **implement final review r1-r3 全闭环 0 HIGH**）。final review：r1 2H+2M（finalize final-output 重扫 / 去 chunk 单遍 / research-handoff via service / 有界 hash）+ r2 1H（error_summary 扫完整 block）+ r3 1H（**octoagent-sdk 独立运行面用户拍板范围外**，spec §2.2 文档排除）。broad 2765 passed + e2e_smoke 8 passed。详见 completion-report.md。待用户拍板 master 合入

---

## 1. Summary

把已有 ThreatScanner 接到今天裸奔的 tool 结果管道（web.fetch/search/MCP/terminal），**检测命中 → 不 block、只在 LLM 看到的内容前加 `[security-warning]` 标注 + 写审计事件**。spec 经 4 轮对抗 review 收敛出的核心架构：

- **检测/标注分层**：检测在 broker 两阶段扫描（不碰 raw 字段），标注在 LLM 物化层从持久化 finding 派生。
- **scope 显式成员**：MEMORY 集冻结（17 条 baseline，PolicyGate 零回归），CONTEXT 集为 tool 结果新增族。
- **单一 scanner 入口**：`ContentThreatScanService`（PolicyGate + 检测共用，C10 字面成立）。
- **replay-safe**：`ToolSecurityFinding` JSON-native 全链路持久化，4 条 LLM 再入口经唯一 render helper 重渲染标注。

**定级 L**：横跨 ①工具执行（broker）②会话持久化（turn metadata）③provider client + replay/compaction/memory-extraction（渲染）三层。

## 2. Technical Context

**Language/Version**: Python 3.12+
**Primary Dependencies**: FastAPI / Pydantic（ToolResult/ToolFeedbackMessage/新 ToolSecurityFinding 模型）；纯 `re`（scanner，无新依赖，C6 离线）
**Storage**: SQLite WAL（AgentSessionTurn.metadata，新 `security_findings` 字段；EventStore 新 `TOOL_RESULT_THREAT_FLAGGED`）
**Testing**: pytest（unit + e2e_smoke）；新增 `apps/gateway/tests/harness/test_tool_result_threat_scan*.py` + 扩展 `test_threat_scanner.py` / `test_constitution_compliance.py`
**Project Type**: web（apps/gateway + packages/tooling + packages/skills）
**Performance Goals**: 检测 chunk 全覆盖 P95 < 20ms（含 ≥1MB 病理样本）；fail-open（scanner 异常）/ fail-closed-to-degraded（预算超限）
**Constraints**: PolicyGate memory 路径**字节级零回归**；raw `output`/`error` 全程不改写（机器消费者 tool_search → json.loads 读原值）；标注确定性（replay/prefix-cache 稳定）
**Scale/Scope**: 单用户深度（Blueprint §0）；改动跨 3 包，新增 1 service + 1 model + 1 EventType + 1 render helper

## 3. Constitution Check（GATE）

| 规则 | Gate | 满足方式 |
|------|------|----------|
| **C1 Durability** | 标注必须扛重启/replay | `ToolSecurityFinding` JSON-native 持久化进 turn metadata + 各再入口从持久化重渲染（Phase E）|
| **C2 Everything is Event** | 命中写事件 | `TOOL_RESULT_THREAT_FLAGGED`（Phase C）|
| **C5 Least Privilege** | 不泄恶意原文 | 事件存 sha256 hash；标注仅 pattern_id + 固定 advisory，不回显（Phase C/D）|
| **C6 Degrade Gracefully** | scanner 故障不拖垮工具 | scanner 异常 fail-open；纯正则离线；ReDoS-safe + chunk 有界（Phase A/C）|
| **C8 Observability** | 命中可查 | 事件 + in-context 标注（Phase C/D）|
| **C9 Agent Autonomy** | 不替 LLM 决策 | 只标注不 block 不删改；LLM 自判（全程）|
| **C10 Policy-Driven Access** | 单一权限/扫描入口 | `ContentThreatScanService` 唯一 scanner 入口；同步 C10 文档/测试/Blueprint 区分"权限入口（PolicyGate）vs 标注入口（检测）"（Phase A/G）|
| **H1/H2/H3** | 不改协作模型 | 工具层护栏；主 Agent/Worker 经同一 broker+provider client 同获保护；不触碰委托/发声 |

**无 Constitution 违规**（§11 Complexity Tracking 空）。唯一需主动维护的边界：C10——检测层直接调 scanner 会与现有"统一经 PolicyGate"契约冲突，故抽 `ContentThreatScanService` 让二者同源 + 同步契约文本（非违规，是契约演进）。

## 4. 跨层架构与集成点（核心）

数据流（命中路径）：

**依赖方向（P-F1 + PR2-F1）**：broker 在 `packages/tooling`（下层，零依赖 gateway——实测核实）。故 **scanner 经 DI 注入，且跨边界类型全落 tooling**：
- `ContentThreatScanProtocol` 定义在 tooling，方法 **scope-free**：`scan_tool_context(content: str) -> list[ToolSecurityFinding]`——**CONTEXT scope 是 service 内部事，`ScanScope` enum 不跨边界**（避免 tooling 反向 import gateway 或常量分叉）。
- `ToolSecurityFinding` DTO 落 **tooling**（本就要挂 `ToolResult.security_findings`，ToolResult 在 tooling/models.py）。
- `ToolBroker.__init__` 加 `content_scanner: ContentThreatScanProtocol | None = None`（复用既有 `event_store`/`artifact_store` DI 模式）。
- gateway 的 `ContentThreatScanService` 实现 protocol，内部 `scan(scope=CONTEXT)`（`ScanScope` 留 gateway scanner 内部）；gateway 装配时注入**单实例**给 broker + PolicyGate（C10 单一入口）。
- 加 **no-circular-import 测试**（断言 tooling 不 import gateway）。

```
tool handler 执行
  │
  ├─[Phase C 阶段1] handler 成功后捕获 raw output → content_scanner.scan(CONTEXT) → 挂 finding（不碰 output）
  │
  ├─ after-hook 链（LargeOutputHandler 截断 / fail-closed hook 重建 ToolResult）
  │
  ▼[Phase C] finalize_result(result)  ← ToolBroker.execute 的【所有】return 分支统一汇入（P-F2）
     · 早退矩阵（实测 ~8 分支）：not-found / start-event-fail / permission-denied / before-hook-reject / before-hook-fail-closed / timeout / exception(str(e)→error) / after-hook-fail-closed / success(+truncated)
     · 显式保留 phase1 已挂 finding（防 after-hook 重建丢失）
     · 扫最终 error + 最终 output(若 final_output≠phase1_raw) → 去重键 (source_field,pattern_id,content_hash)
     · 命中 → emit TOOL_RESULT_THREAT_FLAGGED（hash，无原文）
  │
  ▼ ToolResult.security_findings
  │
  ▼[Phase D1] live: provider_model_client._append_feedback_to_history 经唯一 helper 从 finding 加标注（不碰 fb.output/error）
  │
  ▼[Phase E1] 持久化读模型: ToolFeedbackMessage.security_findings → record_tool_result_turn → AgentSessionTurn.metadata.security_findings（JSON-native）+ 读取 DTO
  │
  ▼[Phase D2] 从【持久化 finding】重渲染（这些路径派生自持久化 turn 数据，非 live ToolFeedbackMessage）：
     ② replay     ← AgentSessionTurn.summary 派生 → 须带 metadata.security_findings
     ③ compaction ← 事件 / ConversationTurn.content 派生
     ④ memory     ← session_memory_extractor._build_extraction_input（summary 派生，喂提取 LLM）
  │
  ▼ no-bypass: sink 正向约束测试（禁 raw turn/event/projection 内容直接进 LLM message，非仅扫属性名，P-F5）
```

**关键集成点清单（已实测定位，session 内核实）**：

| 层 | 文件:符号 | 改动 |
|----|-----------|------|
| scanner | `apps/gateway/.../harness/threat_scanner.py` | 加 `ScanScope` 枚举 + `ThreatPattern.scopes` + `scan(content, scope=MEMORY)` + chunk 全覆盖 + ReDoS-safe + 新 CONTEXT pattern 族 |
| protocol | `packages/tooling/.../`（新 `ContentThreatScanProtocol`）| 定义在 tooling，方法 scope-free `scan_tool_context(content)->list[ToolSecurityFinding]`，`ScanScope` 不跨边界（P-F1/PR2-F1）|
| service | `apps/gateway/.../services/`（新 `content_threat_scan.py`）| `ContentThreatScanService` 实现 protocol，包 scanner；gateway 构造**单实例**注给 broker（DI）+ PolicyGate（C10 单一入口）|
| broker DI | `packages/tooling/.../broker.py:61 __init__` | 加 `content_scanner: ContentThreatScanProtocol \| None=None`（复用既有 DI）|
| policy | `apps/gateway/.../services/policy.py:111` | PolicyGate 改经 service 调（scope=MEMORY），行为字节级不变 |
| model | `packages/tooling/.../models.py`（ToolResult）| 加 `security_findings: list[ToolSecurityFinding]`（默认空）|
| model | 新 `ToolSecurityFinding` 落 **`packages/tooling`**（JSON-native：pattern_id/scope/severity/advisory + degraded 标志；挂 ToolResult 须同包，PR2-F1）| |
| broker | `packages/tooling/.../broker.py:377-466` + 全 ~8 早退分支 | 收敛统一 `finalize_result(result)`，所有 return 汇入；阶段1 pre-hook raw output + 终态扫 error/changed-output；after-hook 重建保留 finding；emit 事件 |
| feedback | `packages/skills/.../runner.py:676 _build_tool_feedback` | `ToolFeedbackMessage` 加 `security_findings` 透传（**不改 output**）|
| persist | `apps/gateway/.../services/agent_context_turn_writer.py:204 record_tool_result_turn` | metadata 加 `security_findings`（`model_dump(mode="json")`，防 :138 broad except 吞 TypeError）|
| render | `packages/skills/.../provider_model_client.py:164 _append_feedback_to_history` | 经 helper 从 finding 加标注（不改 fb.output/error）|
| render | replay / compaction / `session_memory_extractor.py:516 _build_extraction_input` | 同 helper |
| event | `packages/core/.../models/enums.py` | 加 `TOOL_RESULT_THREAT_FLAGGED` |
| C10 | `apps/gateway/tests/constitution/test_constitution_compliance.py:14` | 更新契约表述为"两入口一 service" |

## 5. Phase 分解（依赖序，先简后难，先建零回归底座）

> 沿用 F091/F092 等"先简后难、先建 baseline 信心"Phase 序经验。每 Phase 后回归 0 regression vs d2936e0；末 Phase 前 Codex final cross-Phase review（CLAUDE.local.md 强制）。

| Phase | 内容 | 依赖 | 零回归校验 |
|-------|------|------|-----------|
| **A — scanner 底座** | `ScanScope`（gateway 内部）+ `ThreatPattern.scopes` + `scan(scope=MEMORY)` 默认等价；**ReDoS 审计全部 pattern**（catastrophic backtracking 等价重写，MEMORY 重写须 FR-5.2 corpus 验证不改命中）；**输入硬上限 + degraded** 机制（MEMORY 上限内全量单遍/字节级等价，CONTEXT chunk+max_span）；新 CONTEXT pattern 族（有界，先不接 tool 路径）。MEMORY 集冻结断言 | — | FR-5.2 memory 样本字节级等价 + 阈值±1 degraded + ReDoS 病理样本 + 现有 scan 测试全绿 |
| **B — 数据模型** | `ToolSecurityFinding`（JSON-native）+ `ToolResult.security_findings` + `ToolFeedbackMessage.security_findings`（默认空，向后兼容）| — | 模型序列化/默认值测试；现有工具调用不受影响 |
| **C — protocol + service + 检测 + 事件** | `ContentThreatScanProtocol`(tooling) + `ContentThreatScanService`(gateway) + broker DI 注入；PolicyGate 改经 service（scope=MEMORY），**MEMORY degraded（超上限）→ BLOCK 该写入**（fail-closed，复用 PolicyCheckResult.allowed=False 路径）；broker 收敛统一 `finalize_result`（所有 ~8 早退分支汇入 + 阶段1 raw output + 终态 error/changed-output + 去重 + after-hook 重建保留 finding）；emit `TOOL_RESULT_THREAT_FLAGGED` | A,B | PolicyGate 上限内字节级等价 + >上限 degraded-block；broker 既有测试全绿；**全分支矩阵**逐项测试 |
| **D1 — live 渲染** | 唯一 `render_tool_result_for_llm` helper；接 **live** `_append_feedback_to_history`（从 live ToolFeedbackMessage.security_findings）；raw 字段不改 | B,C | tool_search 提升读 raw JSON 不受影响（FR-5.3）；live 标注确定性 |
| **E1 — 持久化 + 读模型** | `record_tool_result_turn` metadata 写 `security_findings`（`model_dump(mode="json")`）+ 读取 DTO；明确要改的 turn metadata / projection 结构 | C,D1 | turn metadata save/read/backup roundtrip（JSON-native，防 :138 吞 TypeError）|
| **D2 — 持久化再入口渲染** | replay / compaction / memory-extraction 从【持久化】finding 经同 helper 重渲染（这些路径派生自 `AgentSessionTurn.summary` / `ConversationTurn.content`，非 live feedback）；sink 正向 no-bypass 测试 | E1 | 重启/replay/compaction/memory 后标注不丢（SC-004）|
| **F — 误报门槛** | CONTEXT pattern scope 归类定稿；真实样本 + 安全技术负样本集 + 标注率阈值；每 pattern 正负样本覆盖 | A,D | SC-008 负样本标注率 ≤ 阈值 |
| **G — C10 契约 + 收口** | 更新 `test_constitution_compliance.py` C10 表述；Blueprint 同步（harness-and-context / 安全模型）；全量回归 + e2e_smoke | A-F | 全量 0 regression vs d2936e0；e2e_smoke 全过 |
| **Verify** | Codex final cross-Phase review（输入 plan + 全 Phase diff）；completion-report + handoff | A-G | 0 HIGH 残留 |

**Phase 序理由（P-F3 重排）**：A/B 无行为改变底座先建零回归信心；C 接检测（产生 finding + 事件，未渲染）；**D1 仅接 live 渲染**（live ToolFeedbackMessage 有 finding，可独立验证）；**E1 先落持久化 + 读模型**（D2 的前置）；**D2 再接 replay/compaction/memory**（依赖 E1 的持久化 finding）。原 v1 把 D 整体排在 E 前是**循环依赖**（replay/memory 从持久化 turn 数据派生，需 E1 先就位）——已拆为 C → D1 → E1 → D2。核心链 A→B→C→D1→E1→D2 串行；F 误报可与 D1/E1/D2 并行；G 收口。

## 6. §10 精度项的 plan 级解决（round 4 残留 + plan 决策）

| 项 | 决策 |
|----|------|
| **no-bypass = 原则 + 权威测试（FR-3.5，P-F5 + PR2-F3 + PR3-F2 + PR4-F1）** | **结构升级（PR4-F1，r4 印证手工枚举是 whack-a-mole）**：no-bypass 不再以"枚举清单"为封闭依据，而是 **原则 + 权威测试**——「**任何 tool-derived 内容进入 LLM/system message MUST 携带（并持久化）`security_findings` 且经 `render_tool_result_for_llm` 渲染**；handoff/派生 payload 在边界要么传递 findings、要么重新 CONTEXT scan 经同一 helper 输出」。权威测试是 sink 正向约束**跑真实代码**，对**任何** tool-derived sink（含 dict payload，非仅 turn/event/projection）缺 finding-aware 渲染即失败。**已知 sink 起点集（实测核实）**：①`skills/provider_model_client.py:164 _append_feedback_to_history`；②`services/session_memory_extractor.py:516 _build_extraction_input`；③`agent_context.py:1824 build_agent_session_replay_projection`+`:1972 render_agent_session_replay_block`；④`:3823 _build_system_blocks`；⑤`:4525 _summarize_turns`；⑥`context_compaction.py:847 _summarize_turns`+`:941 _call_summarizer`+`:1256 _build_compacted_messages`；⑦**`agent_context.py:4128 _build_research_handoff_block`（读 `dispatch_metadata.research_result_summary/_text` dict payload，r4 漏的第 5 类 sink）→ research handoff payload MUST 携带 findings 或边界重扫**。**封闭由权威测试在实施期对真实代码兜底**（枚举是起点非全称），残留 sink 实施期机械暴露 |
| **两阶段去重（FR-2.7）** | 去重键 `(source_field, pattern_id, content_hash)`；`content_hash`=sha256(命中片段所在字段全文)。终态仅当 `final_output != phase1_raw_output` 重扫 output |
| **MEMORY + CONTEXT 双 scope 有界扫描（FR-1.5，P-F4 + PR2-F2 + PR3-F1 全接受硬化 MEMORY）** | **删除原"MEMORY 受上游字符上限约束"假声明**——实测 `policy.py:12` 明文 PolicyGate **不做**字符上限，`promote.fact_content`(memory_candidates.py:53)/user_profile.update content 在 scan 前无 max_length，故 MEMORY 也会被大输入打。**用户拍板硬化 MEMORY**，二者都有界：<br>① **ReDoS 审计全部 pattern**（MEMORY+CONTEXT）：catastrophic backtracking 小输入也能爆炸，故审 pattern 本身（实测 17 条多为线性 `\b…\b`/`[^;\|\n]*`，大概率已安全无需重写→保零回归；若有 catastrophic 个案等价重写并经 FR-5.2 corpus 验证不改命中）。<br>② **CONTEXT scope**：chunk 全覆盖 + 每 pattern finite `max_span`（禁 unbounded 跨 chunk）+ overlap = max(max_span) + 跨边界正负样本；超硬上限 → degraded **annotate**（不 fail-open clean）。<br>③ **MEMORY scope**：输入上限内 = baseline 全量单遍扫描（**字节级等价**，不 chunk 避免改命中）；超上限 → degraded **= BLOCK 该写入**（fail-closed，"内容过大无法安全扫描请拆分"，用户可重试）。**仅病理超大输入改变行为**（原 hang/ReDoS → 干净拒绝，改善非回归）。<br>④ 硬上限阈值（建议 2MB，实测调）+ 阈值±1 测试（MEMORY 侧断言 degraded→block，CONTEXT 侧断言 degraded→annotate）。`ContentThreatScanService` 统一入口、双 scope 分治：CONTEXT chunk-annotate / MEMORY full-block |
| **JSON-native 持久化（FR-3.4）** | `ToolSecurityFinding.model_dump(mode="json")` → `list[dict[str,str]]` 写 metadata；roundtrip 测试覆盖 save/read/backup/replay |
| **三层落点影响面** | Phase C/D/E 各自跑既有测试子集（broker / skills runner / agent_context turn / provider client）确认无破坏；是否抽统一 helper = **是**（FR-3.1 强制单一 helper） |

## 7. 测试矩阵（对齐 spec §9 AC↔test 绑定）

落点 `apps/gateway/tests/harness/test_tool_result_threat_scan.py`（除标注外）：

- `test_injection_annotated_at_history_materialization`（US1-AC1）
- `test_raw_output_unmodified_event_emitted`（US1-AC2）
- `test_annotation_survives_replay_compaction_and_memory_extraction`（US1-AC3 / SC-004）
- `test_security_blog_negative_sample`（US1-AC4）
- `test_clean_output_no_annotation`（US1-AC5）
- `test_central_coverage_mcp_and_search`（US2）
- `test_terminal_output_annotated` + `test_error_and_exception_channel_scanned`（US3）
- `test_two_phase_scan_covers_all_exit_paths`（FR-2.1）
- `test_full_coverage_payload_past_window_and_redos`（FR-1.5/SC-006）
- `test_finding_propagated_to_session_turn_metadata`（FR-3.4）
- `test_no_render_path_bypasses_annotation_helper`（FR-3.5，AST allowlist）
- `test_tool_search_promotion_reads_raw_json`（FR-5.3）
- `test_memory_scope_baseline_equivalence`（→ `test_threat_scanner.py`，FR-5.2）
- C10 契约更新（`test_constitution_compliance.py`，FR-5.4）
- `test_tool_result_threat_scan_false_positive.py`（FR-6.1/SC-008）

## 8. 风险 & rollout

- **R1 渲染路径漂移**：已用 no-bypass AST allowlist 封闭（有界）；新增渲染路径绕过即测试失败。
- **R2 两阶段复杂度**：去重键 + changed-output 判定增加 broker 复杂度；Phase C 充分单测全分支。
- **R3 持久化兼容**：turn metadata 加字段对既有 replay/compaction/memory-extraction 消费者——Phase E roundtrip + 消费者兼容测试。
- **R4 误报"狼来了"**：Phase F 误报门槛 MUST，负样本阈值未达不进默认集。
- **rollout**：纯增量（新字段默认空 / 新事件 / 新 service）；scanner 异常 fail-open 保证不拖垮工具；MEMORY 冻结保证 PolicyGate 零回归。无 feature flag（护栏应默认开，fail-safe）。

## 9. Complexity Tracking

无 Constitution 违规需 justify。唯一抽象新增 `ContentThreatScanService` 是为满足 C10 单一入口（非过度抽象，是契约要求）。

## 10. Plan review gate（CLAUDE.local.md 强制）

本 plan 命中"重大架构变更"（工具系统 + 安全模型 + 跨层数据库 schema/持久化）→ **commit 前必走 Codex adversarial review**。通过后再 `/spec-driver:spec-driver-tasks` 拆 tasks。

## 11. Codex Plan Review 闭环

### round 1（job bjcko31ym，4H+1M，全接受 → v2；核实方法：grep tooling 无 gateway import + broker.execute ~8 早退分支 + unbounded 正则形态）

| F | sev | 核实 | 处理 |
|---|-----|------|------|
| P-F1 scanner service 在 gateway → tooling broker 反向依赖 | HIGH | 成立（tooling 0 import gateway；broker 已有 DI）| §4 `ContentThreatScanProtocol` 定 tooling + broker DI 注入 + gateway 装配单实例 |
| P-F2 两阶段漏 broker ~8 早退分支 + after-hook 重建丢 finding | HIGH | 成立（~8 个 `return ToolResult(is_error=True)`）| §4/§5-C 收敛统一 `finalize_result` 全分支汇入 + 重建保留 finding + 分支矩阵测试 |
| P-F3 D/E 循环 + replay/memory 从持久化派生非 live | HIGH | 成立（replay/memory 读 turn.summary）| §5 重排 C→D1（live）→E1（持久化+读模型）→D2（持久化再入口）|
| P-F4 chunk overlap"最长 regex span"不可实现 | HIGH | 成立（unbounded 正则无有限宽度）| §6 禁 unbounded CONTEXT pattern + 每 pattern 声明 finite max_span + 跨边界样本 |
| P-F5 AST allowlist 占位 + 属性扫漏间接 sink | MED | 成立（dict payload/ConversationTurn.content 等 sink）| §6 改 sink 正向约束 + 枚举精确模块 + 软化为有界保证 |

**spec 影响**：仅 FR-1.5 措辞从"overlap ≥ 最长 pattern 跨度"微调为"per-pattern finite max_span + CONTEXT 禁 unbounded"（契约"全覆盖/never-clean"不变）。其余 4 条纯 plan（HOW）层，spec 契约不动。

### round 2（job bajdtyrbm，2H+1M，全接受 → v3；Codex 明确确认 C/D/E 循环 + finalize 已封 = P-F2/P-F3 sealed）

| F | sev | 核实 | 处理 |
|---|-----|------|------|
| PR2-F1 P-F1 残留：ScanScope 仍在 gateway，protocol 签名带它仍反向依赖 | HIGH | 成立（半修）| protocol 方法改 scope-free `scan_tool_context()`；`ToolSecurityFinding` 落 tooling；ScanScope 留 gateway 内部；no-circular-import 测试 |
| PR2-F2 P-F4 残留：只禁 CONTEXT unbounded，MEMORY unbounded 与零回归/chunk 冲突 | HIGH | 成立（MEMORY baseline 有 `[^;\|\n]*`/`.*`DOTALL）| MEMORY 保 baseline 全量（不 chunk，零回归）；chunk+max_span 仅 CONTEXT；MEMORY ReDoS 是既有现状+上游字符上限约束，F124 不引入 |
| PR2-F3 P-F5 残留：sink 模块清单仍占位 | MED | 成立（推到 D2）| plan 现枚举精确 sink（_append_feedback_to_history / _build_extraction_input / replay 投影 / compaction builder）+ 例外 + sentinel |

**收敛判断**：r2 确认 plan 最难的结构项（broker finalize 全分支 + C/D/E 无环）已封；剩 3 条是 P-F1/F4/F5 收尾（类型归位 / MEMORY-CONTEXT 分治 / sink 枚举），非新结构问题。spec 契约不动（FR-1.5 措辞已在 r1 调过，本轮 MEMORY 分治是 plan 内 HOW）。

### round 3（job bpbc0t5es，2H；Codex 确认 PR2-F1 依赖方向已闭合）。**用户拍板硬化 MEMORY（全接受 PR3-F1，不再部分拒绝）**

| F | sev | 核实 | 处理 |
|---|-----|------|------|
| PR3-F1 MEMORY"上游字符上限"假声明 + MEMORY 仍有 ReDoS 敞口 | HIGH | 成立（`policy.py:12` 明文 PolicyGate 不做字符上限；promote.fact_content 无 max_length）| **删假声明**；**全接受硬化 MEMORY**：输入上限内全量字节级等价 / 超上限 degraded→**BLOCK**（fail-closed）；ReDoS 审计全部 pattern。FR-5.1 零回归精确化为"上限内字节级等价 + 病理超限 degraded-block" |
| PR3-F2 replay/compaction sink 仍通配符不可 review | HIGH | 成立（实测 7 函数级 sink 真实存在）| §6 去通配符，枚举 7 个函数级精确符号（`build_agent_session_replay_projection`/`render_agent_session_replay_block`/`_build_system_blocks`/`_summarize_turns`×2/`_call_summarizer`/`_build_compacted_messages`）+ 每个正向测试 |

**spec 影响（本轮）**：FR-5.1 零回归精确化（上限内字节级等价 + 病理超限 degraded-block）；FR-1.5 有界扫描扩到 MEMORY（双 scope）；新增 MEMORY >上限 degraded-block 用户可见行为。已同步 spec。

### round 4（job b3oy6bxlq，1H；finding 4→2→2→1 严格递减，架构零翻案）。**用户拍板：修 + 进 tasks（不再刷 spec-review 轮）**

| F | sev | 核实 | 处理 |
|---|-----|------|------|
| PR4-F1 no-bypass 枚举漏第 5 类 sink：research handoff dict payload→LLM | HIGH | 成立（`agent_context.py:4128 _build_research_handoff_block` 读 `dispatch_metadata.research_result_summary/_text` 进主 Agent 上下文，不带 finding）| §6 加第 ⑦ sink；**no-bypass 结构升级**为「原则（任何 tool-derived 内容进 LLM message 必带 findings 经 helper）+ 权威测试跑真实代码」，handoff payload 传递 findings 或边界重扫；FR-3.5 同步 |

**收敛决策（用户拍板）**：8 轮 review 后架构全稳，finding 严格递减至 sink 枚举尾巴。手工 spec-review 穷举 sink 是 whack-a-mole（r3 预言、r4 印证，可能有第 6 个）；**真正封闭 = 实施期 no-bypass 权威测试对真实代码机械兜底 + tasks/implement 自带 Codex 闸**。故 plan 闸**实质达成**（架构 sound + 残留为实施期可机械暴露的 sink 尾巴），收口进 tasks。
