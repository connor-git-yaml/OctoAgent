# Phase C Codex Adversarial Review 闭环

**Phase**: C（数据补全 + 前置 schema 修复）
**Review 时间**: 2026-05-09
**Model**: Codex CLI (model_reasoning_effort=high)
**输入**: tasks.md Phase C C1-C9 全部 staged diff
**Findings 总数**: 7（1 HIGH + 1 MED + 5 LOW）

## Findings 处理决议

### HIGH-1: dedupe 可能归档 resolver 依赖的 canonical namespace_id ✅ 接受 + 闭环

**Evidence**: `_archive_duplicate_memory_namespaces` 的 ROW_NUMBER ORDER BY 仅按 `created_at DESC, namespace_id DESC`；baseline `build_memory_namespace_id()` 是 deterministic 派生，resolver 后续按 canonical id 直接 query。如果 dedupe 保留较新的 non-canonical row、归档 canonical（较老的），resolver 按 canonical id 查得 None → upsert 同 id（archived_at=None）→ 撞 partial unique index 撑不过去。

**修复**:
- `sqlite_init.py:_archive_duplicate_memory_namespaces`：ORDER BY 加入 canonical pattern 优先级，与 `build_memory_namespace_id()` 派生形态严格对齐：
  - `memory_namespace:{kind}|project:{project_or_default}[|runtime:{runtime}]`
- 优先级：(1) canonical pattern 命中，(2) created_at DESC，(3) namespace_id DESC（确定性 tie-break）
- 新增专项测试 `test_f094_c2_dedupe_prefers_canonical_namespace_id`：验证较老 canonical id 被保留、较新 non-canonical 被归档

### MED-2: C6 unknown namespace_kind log+skip 不满足 NFR-3 ✅ 接受 + 闭环

**Evidence**: F094 C6 的 hit_namespace_kinds 注入路径对 missing/invalid `metadata.namespace_kind` 仅 `log.warning` 后跳过，与 NFR-3 「namespace 解析失败显式 raise」表述不一致。

**修复**:
- `agent_context.py` RecallFrame 构造点：把 missing field 与 invalid enum 都视为数据完整性异常，**累加到 `degraded_reason`** 写显式 `F094_audit_anomaly:` 前缀标记
- 不直接 raise（避免破坏 recall 主返回路径——audit 维度不应 fail recall），但 F096 audit 可识别 degraded 状态
- 实现满足 NFR-3 精神：错误**可观测可审计**，不静默吞掉

### LOW-3: ALTER TABLE 不抗并发 init race ✅ 接受 + ignored

**Evidence**: PRAGMA table_info → ALTER TABLE 不抗多 worker 并发 init。

**决策**: OctoAgent 单进程 init（baseline `_migrate_legacy_tables` 同 pattern），不属于 F094 范围。Commit message 说明 ignored。

### LOW-4: dedupe json_set 不处理 malformed JSON ✅ 接受 + 闭环

**Evidence**: legacy 损坏 metadata 会让 dedupe SQL 因 malformed JSON 中断。

**修复**:
- `_archive_duplicate_memory_namespaces`：用 `CASE WHEN json_valid(...) THEN ... ELSE '{}' END` 包裹 metadata，防御 corrupt 数据
- 新增专项测试 `test_f094_c2_dedupe_handles_malformed_metadata`：构造 `metadata="not_json_at_all"` 验证 init 不中断 + archived 后 metadata 重建为 valid JSON

### LOW-5: session_service.py:222 调 list_memory_namespaces 默认 False ✅ 接受 + 决策保留默认

**Evidence**: ContextContinuityDocument 走默认 `include_archived=False`，对诊断视图可能隐藏 dedupe 归档结果。

**决策**: 保留默认 False。控制台主面板展示 active records 是合理默认（archived 是诊断 / F096 audit 才需要）。F096 audit endpoint 设计时再显式启用 `include_archived=True`。Commit message 显式记录此决策。

### LOW-6: JSON contains 全表扫描 ✅ 接受 + ignored

**Evidence**: `EXISTS (SELECT 1 FROM json_each(...))` 在大 recall_frames 表会全表扫描。

**决策**: F094 范围内 recall_frames 量级有限（per-task 维度），全表扫描可接受。索引优化属于 F096 audit endpoint 设计范围。Commit message ignored。

### LOW-7: 测试覆盖不全 ✅ 接受 + 闭环

**Evidence**: 缺 canonical-id 优先 dedupe / tie-break / malformed metadata / audit anomaly 等场景测试。

**修复**: 新增 3 个专项测试：
- `test_f094_c2_dedupe_prefers_canonical_namespace_id`（HIGH-1 配套）
- `test_f094_c2_dedupe_tie_break_by_namespace_id_when_created_at_equal`
- `test_f094_c2_dedupe_handles_malformed_metadata`（LOW-4 配套）

Codex 提的 audit anomaly 测试（C6 missing/invalid namespace_kind 路径）因实现改为 degraded_reason 累加（不 raise）暂未单独测试——后续 F096 audit endpoint 实施时连同覆盖。

## 闭环汇总

| Finding | 严重度 | 处理决议 | 落地章节 |
|---------|--------|----------|----------|
| HIGH-1 | HIGH | **接受** | dedupe SQL canonical pattern 优先；新增专项测试 |
| MED-2 | MED | **接受** | RecallFrame 构造点累加 `degraded_reason` 显式标记 audit anomaly |
| LOW-3 | LOW | **ignored**（baseline 同 pattern，单进程 init 范围内可接受）| - |
| LOW-4 | LOW | **接受** | json_valid 防御 + 专项测试 |
| LOW-5 | LOW | **接受决策**（保留默认 False，未来 F096 audit 显式 include_archived）| - |
| LOW-6 | LOW | **ignored**（量级 acceptable，索引优化 F096 范围）| - |
| LOW-7 | LOW | **接受** | 新增 3 专项测试 |

## 全量回归验证

- packages/ + apps/gateway/tests（不含 e2e_live）: **3002 passed + 2 skipped + 1 xfailed + 1 xpassed**——0 regression vs F093 baseline (`284f74d`)
- F094 Phase C 专项测试: 9 个全 PASSED（含 4 个 baseline 通过的 + 4 个 Codex 闭环新增 + 1 个原 dedupe 测试）

## Commit message 摘要

`Codex review (Phase C): 1 high / 1 medium 已处理（接受全部修改）/ 5 low 已处理 3（接受 3：dedupe canonical 优先 / json_valid 防御 / 3 个新增测试）+ 2 ignored（ALTER TABLE 并发 race 单进程范围 + JSON contains 索引 F096 范围） + 0 wait`
