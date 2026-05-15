# F100 Decision Loop Alignment — Plan（v0.3 Post-PhaseC-Audit）

**Spec**: [spec.md](spec.md) v0.3（Phase C audit 修订后）
**Recon**: [phase-0-recon.md](phase-0-recon.md)
**Codex Review**: [codex-review-pre-impl.md](codex-review-pre-impl.md)
**Baseline**: F099 049f5aa (3450 passed)

---

## 0. v0.2 修订说明

v0.1 plan 被 Codex pre-impl review 抓到 3 HIGH + 2 MED：

| Finding | v0.1 plan 问题 | v0.2 修订 |
|---------|---------------|-----------|
| HIGH-1 无 production producer | Phase D 只改 helper + 单测，无人写 force_full_recall=True 到主路径 | Phase D 任务扩展：新增 FR-H metadata hint → runtime_context 接入（orchestrator._prepare_single_loop_request 读取 `metadata["force_full_recall"]`）|
| HIGH-2 unspecified 是 pre-decision 状态 | Phase C 要求所有构造点显式 → 会让 chat.py seed 炸掉 | Phase C 缩范围到 consumed 时点 audit；不改构造点；保留 pre-decision unspecified |
| HIGH-3 + MEDIUM-1 Phase D→E→F 顺序不安全 | E 先删 fallback 再 F 验证 = destructive | **Phase 顺序改为 C→F→D→E1→E2**，F 前置 + E 拆分 |
| MEDIUM-2 5% perf gate 测量不足 | e2e_smoke 5x P50/P95 噪声大 | mock-based 控制变量测量（1000+ 样本，可重复）|
| LOW-1 bool vs Literal | 接受，handoff 给 F107 |

---

## 1. Phase 拆分与精细任务（v0.2 修订顺序：C→F→D→E1→E2→G→H）

### Phase C — consumed 时点 audit + 测试 fixture 准备

**目标**：sanity audit + 测试 fixture 准备，**不引入运行时行为变化**。

**Task C-1**：grep audit 所有 helper consumed 时点
```bash
grep -rn 'is_recall_planner_skip\(\|is_single_loop_main_active\(' \
  octoagent/apps octoagent/packages --include='*.py'
```
预期 hit 数：≤ 10 处（task_service / orchestrator / dispatch_service 等）

**Task C-2**：沿每个调用点向上追溯 RuntimeControlContext 来源：
- 来源 1：caller 已 patch（如 orchestrator._with_delegation_mode 路径）→ delegation_mode 显式
- 来源 2：caller 直接传递 metadata 给 helper（如 runtime_context_from_metadata）→ delegation_mode 取决于 metadata 中编码的值
- 来源 3：caller 不传 runtime_context（None） → fallback 路径
- 标注每个调用点的 delegation_mode 显式性预期

**Task C-3**：grep audit RuntimeControlContext 构造点（**仅记录，不改**）
```bash
grep -rn 'RuntimeControlContext(' octoagent/apps octoagent/packages \
  --include='*.py' | grep -v /tests/
```
- 分类：pre-decision seed（chat.py）/ post-decision patched（orchestrator）/ helper internal
- 标注 pre-decision seed 路径必经的 patch 时点

**Task C-4**：准备测试 fixture 骨架（Phase D-G 复用）：
- `tests/test_runtime_control_f100.py`（新文件框架）：
  - fixture：`unspecified_rc()` / `main_inline_rc()` / `worker_inline_rc()` / `main_delegate_rc()` / `subagent_rc()`
  - fixture：`force_full_recall_rc()`（Phase D 后用）
- audit doc：`.specify/features/100-decision-loop-alignment/phase-c-audit.md`
  - consumed 时点清单 + delegation_mode 来源标注
  - RuntimeControlContext 构造点分类

**Task C-5**：commit
```
chore(F100-Phase-C): consumed 时点 audit + 测试 fixture 准备（v0.2 修订：不改构造点）

- grep is_recall_planner_skip / is_single_loop_main_active 调用点
- 沿调用链向上追溯 delegation_mode 来源
- audit RuntimeControlContext 构造点分类（pre-decision seed / patched / internal）
- 准备 tests/test_runtime_control_f100.py fixture 骨架
- 产出 phase-c-audit.md
- 零运行时行为变化（baseline 已通）
```

**Codex per-Phase review (Phase C)**：foreground，**重点检查**：是否漏掉某 consumed 时点；audit 分类是否准确

**回归门**：`pytest octoagent` ≥ 3450 passed + e2e_smoke 1x PASS

---

### Phase F — ask_back resume 真实恢复机制实测 + 单测 + 文档修正

**目标**：HIGH-3 修复——验证 ask_back resume 后 turn N+1 的 runtime_context 真实来源；修正 spec 叙述。**在 destructive E 之前完成**。

**Task F-1**：实测追踪 ask_back resume 调用链
- 起点：`task_runner.attach_input` → `_spawn_job` → `_run_job`
- 中段：`process_task_with_llm` → orchestrator dispatch
- 终点：是否调 `_prepare_single_loop_request` 或 `_with_delegation_mode` 重新 patch runtime_context？

**Task F-2**：grep `get_latest_user_metadata` 返回字段
- 验证 TASK_SCOPED_CONTROL_KEYS / TURN_SCOPED_CONTROL_KEYS 不含 RUNTIME_CONTEXT_JSON_KEY
- 验证 resume 时 dispatch_metadata 是否走另一条路径恢复 runtime_context

**Task F-3**：验证 turn N+1 派发时 orchestrator 重新派生 delegation_mode 的机制
- 若是：通过 orchestrator dispatch 路径重新 patch（worker_inline）→ HIGH-3 修复方向 C
- 若否：runtime_context 在 resume 后**默认值**（unspecified + recall_planner_mode=full + force_full_recall=False）→ 触发 Phase E2 移除 fallback 后 raise → HIGH-3 必修

**Task F-4**：基于 F-1/F-2/F-3 实测结论，更新 spec.md FR-E1/E3 + AC-5 的叙述（如有误）

**Task F-5**：新增 e2e 单测覆盖 ask_back resume 路径
- `tests/test_ask_back_recall_planner_resume.py`（新或并入现有）
- 测点 1：ask_back → attach_input → turn N+1 派发时 helper 调用得到合法 runtime_context
- 测点 2：F099 is_caller_worker_signal 透传不被破坏
- 测点 3：ask_back resume 后 turn N+1 默认行为：worker_inline → skip recall planner

**Task F-6**：commit
```
test(F100-Phase-F): ask_back resume 真实恢复机制实测 + 单测 + 文档修正

- 实测 attach_input → resume → turn N+1 派发链路
- 验证 connection_metadata.TASK_SCOPED_CONTROL_KEYS 不含 runtime_context_json
- 确认 orchestrator 在 turn N+1 派发时重新 patch delegation_mode（HIGH-3 修复方向 C）
- 新增 test_ask_back_recall_planner_resume.py：3 测点
- 更新 spec.md FR-E1/E3 + AC-5 叙述（去除"runtime_context 透传"误描述）
- 行为零变化，baseline 已通
```

**Codex per-Phase review (Phase F)**：foreground，重点检查实测结论是否正确反映在 spec/plan

**回归门**：`pytest octoagent` ≥ 3450 + F 新增测试 PASS

**若 F-3 实测发现 turn N+1 不重新 patch（高风险情境）**：
- 立即暂停 Phase D/E
- 升级方案：F100 范围扩大——把 runtime_context_json 加入 TASK_SCOPED_CONTROL_KEYS（结构性 invasive change），或在 resume 路径显式 patch
- 与用户沟通范围调整

---

### Phase D — RuntimeControlContext 加 `force_full_recall` 字段 + 启用 AUTO 决议 + FR-H minimal trigger

**目标**：核心新行为——AUTO 决议 + H1 override flag + production producer 接入。

**Task D-1**：在 `packages/core/src/octoagent/core/models/orchestrator.py` 的 `RuntimeControlContext` 加字段：
```python
force_full_recall: bool = Field(
    default=False,
    description=(
        "F100：H1 完整决策环 override flag。"
        "True → 强制走完整 recall planner phase，覆盖 recall_planner_mode 的决议结果。"
        "默认 False（行为兼容 F091 baseline）。"
        "上层（typical: chat 路由 / API 参数 / 调试工具）判断主 Agent 自跑长 context 复杂查询时设 True。"
    ),
)
```

**Task D-2**：encode/decode round-trip 测试（pydantic 默认行为，仅需测试覆盖）

**Task D-3**：`runtime_control.py:is_recall_planner_skip` 启用 AUTO 决议（保留 fallback 此 Phase 不动，Phase E2 再移）

```python
def is_recall_planner_skip(
    runtime_context: RuntimeControlContext | None,
    metadata: Mapping[str, Any] | None,
) -> bool:
    # F100 FR-A2：override 优先级最高（早于 delegation_mode 判断）
    if runtime_context is not None and runtime_context.force_full_recall:
        return False  # 强制 full，覆盖所有决议

    if runtime_context is not None and runtime_context.delegation_mode != "unspecified":
        if runtime_context.recall_planner_mode == "skip":
            return True
        if runtime_context.recall_planner_mode == "full":
            return False
        # F100 FR-A1：AUTO 决议启用
        if runtime_context.recall_planner_mode == "auto":
            if runtime_context.delegation_mode in {"main_inline", "worker_inline"}:
                return True  # AUTO + inline → skip（兼容 F051 性能优势）
            if runtime_context.delegation_mode in {"main_delegate", "subagent"}:
                return False  # AUTO + delegate/subagent → full
            # defense-in-depth
            raise ValueError(
                f"AUTO recall_planner_mode 遇到未预期的 delegation_mode: "
                f"{runtime_context.delegation_mode}"
            )
        raise ValueError(
            f"Unknown recall_planner_mode: {runtime_context.recall_planner_mode}"
        )

    # Phase E2 移除此 fallback
    return metadata_flag(metadata, "single_loop_executor")
```

**Task D-4**：`is_single_loop_main_active` **不动**（不涉及 AUTO 决议或 force_full_recall）

**Task D-5**：FR-H metadata hint → runtime_context.force_full_recall 接入（HIGH-1 修复，minimal trigger）

定位 `orchestrator._prepare_single_loop_request`（约 line 770-880）：
- 在构造 patched runtime_context 前：`force_full_recall_hint = self._metadata_flag(metadata, "force_full_recall")`
- 传给 `_with_delegation_mode`（新增参数 `force_full_recall: bool = False`）
- `_with_delegation_mode` 把该值写入 patched runtime_context

同时 audit 是否需要在其他 helper（`_build_runtime_context_for_*`）也接入 FR-H —— **仅 single_loop main 路径接入即可**（OD-1=C 锁定 minimal trigger）

**Task D-6**：单测覆盖（新增 `tests/test_runtime_control_f100.py`）
- AC-1：`force_full_recall=True + delegation_mode=main_inline` → False
- AC-2：force_full_recall=True 对所有 4 个 delegation_mode 都返回 False
- AC-3：AUTO 决议 4 case：main_inline/worker_inline→True, main_delegate/subagent→False
- AC-4：worker_inline + skip baseline 行为不变
- AC-11：supports_single_loop_executor 类属性保留 + duck-type 检测正确
- AC-H1：metadata["force_full_recall"]=True → patched runtime_context.force_full_recall == True
- AC-H2：metadata 不含 hint → patched runtime_context.force_full_recall == False

**Task D-7**：commit
```
feat(F100-Phase-D): 引入 force_full_recall override 字段 + 启用 AUTO 决议 + FR-H metadata hint 接入

- RuntimeControlContext 加 force_full_recall: bool = False（H1 override）
- is_recall_planner_skip 启用 AUTO 决议（依 delegation_mode）+ 移除 line 124 NotImplementedError
- AUTO + main_inline/worker_inline → skip（F051 性能兼容）
- AUTO + main_delegate/subagent → full
- force_full_recall=True 覆盖所有决议
- FR-H 接入：orchestrator._prepare_single_loop_request 读取 metadata["force_full_recall"] hint
  → _with_delegation_mode 写入 patched runtime_context.force_full_recall
  → H1 minimal trigger 真实可达（上层可显式传 hint）
- 新增 tests/test_runtime_control_f100.py 覆盖 AC-1/2/3/4/11/H1/H2
- 全量回归 0 regression
```

**Codex per-Phase review (Phase D)**：foreground，**重点检查**：
- AUTO 决议 switch 是否覆盖所有 DelegationMode 取值（不含 unspecified）
- force_full_recall 优先级是否正确（早于 delegation_mode 判断）
- pydantic round-trip 是否被破坏
- FR-H 接入是否真触达 production 路径（H1 是否真可达）

**回归门**：`pytest octoagent` ≥ 3450 + 新增 D 测试

---

### Phase E1 — 移除 orchestrator metadata 写入

**目标**：移除 F090 D1 双轨写入侧。**先做（小影响），方便 bisect**。

**Task E1-1**：定位 metadata 写入点
```bash
grep -n '"single_loop_executor"' octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py
```
预期：约 line 827-854 的 `_prepare_single_loop_request` 内

**Task E1-2**：移除写入
- 移除 `updated_metadata["single_loop_executor"] = True`
- 移除 `updated_metadata["single_loop_executor_mode"] = ...`
- 保留 `_with_delegation_mode` 调用（runtime_context 写入）

**Task E1-3**：grep 仓库其他 production 写入点
```bash
grep -rn '"single_loop_executor"\s*[:=]' octoagent/apps octoagent/packages \
  --include='*.py' | grep -v /tests/
```
预期：0 hit（除上述 orchestrator 一处已移除）。如有其他，一并清。

**Task E1-4**：commit
```
refactor(F100-Phase-E1): 移除 orchestrator metadata["single_loop_executor"] 写入

- 移除 _prepare_single_loop_request 内 metadata flag 写入
- 保留 runtime_context.delegation_mode/recall_planner_mode 写入（F091 baseline）
- 此 Phase 后 metadata 不再含 single_loop_executor / single_loop_executor_mode 字段
- helper fallback 仍生效（Phase E2 才移除）
- 测试 fixture 暂不动（依赖 unspecified fallback 的测试 E2 阶段一并迁移）
- 全量回归 0 regression
```

**Codex per-Phase review (Phase E1)**：foreground

**回归门**：`pytest octoagent` ≥ 3450 + e2e_smoke 1x PASS

---

### Phase E2 — 移除 helper metadata fallback + unspecified → return False + 测试 fixture 迁移

**目标**：F090 D1 双轨彻底关闭。**v0.3 修订**：unspecified → return False（与 baseline 兼容），不 raise。

**v0.3 修订原因**：Phase C audit 实测发现 4 个 production consumed 时点中 3 个是 pre-decision，v0.2 "consumed raise" 会破坏 chat 主链。详见 [phase-c-audit.md](phase-c-audit.md)。

**Task E2-1**：`runtime_control.py:is_recall_planner_skip` 移除 fallback
```python
# F100 v0.3：移除 metadata fallback，unspecified → return False（与 baseline 默认行为等价）
def is_recall_planner_skip(runtime_context, metadata):
    if runtime_context is not None and runtime_context.force_full_recall:
        return False
    if runtime_context is not None and runtime_context.delegation_mode != "unspecified":
        # ... 显式 mode 决议（F100 Phase D 加入的 AUTO 决议）...
    # F100 v0.3：runtime_context None 或 unspecified → return False
    # 不再 fallback metadata_flag；不再 raise ValueError
    return False
```

**Task E2-2**：`runtime_control.py:is_single_loop_main_active` 同样移除 fallback + return False
```python
def is_single_loop_main_active(runtime_context, metadata):
    if runtime_context is not None and runtime_context.delegation_mode != "unspecified":
        return runtime_context.delegation_mode in _SINGLE_LOOP_DELEGATION_MODES
    # F100 v0.3：unspecified 或 None → return False
    return False
```

**Task E2-3**：测试 fixture 迁移
- `tests/test_runtime_control_f091.py` 内 unspecified + metadata 路径断言：
  - 原 `assert is_recall_planner_skip(unspecified_rc, {"single_loop_executor": "1"}) == True`
  - v0.3 改为 `assert is_recall_planner_skip(unspecified_rc, {"single_loop_executor": "1"}) is False`（fallback 移除后 metadata flag 不再生效）
- 类似断言全部迁移

**Task E2-4**：新增 unspecified return False 测试（AC-8/9）
- 单测：直接构造 unspecified RuntimeControlContext 调 helper → 验证 return False
- 单测：runtime_context=None 调 helper → 验证 return False
- 集成测试：模拟 chat.py 路径，验证 orchestrator patch 后 helper 调用得到预期值（非 False）

**Task E2-5**：grep production reader 兜底验证
```bash
grep -rn 'single_loop_executor' octoagent/apps octoagent/packages --include='*.py' \
  | grep -v /tests/ | grep -v supports_single_loop_executor
```
预期：0 hit（除可能的 docstring 提及）

**Task E2-6**：commit
```
refactor(F100-Phase-E2): 移除 helper metadata fallback + unspecified return False（v0.3 修订）

- is_recall_planner_skip / is_single_loop_main_active 移除 fallback 分支
- unspecified delegation_mode 或 runtime_context=None → return False（v0.3 修订）
  - 原 v0.2 方案 raise ValueError 会破坏 chat 主链（Phase C audit 实测）
  - v0.3 与 baseline metadata flag 缺失时的默认行为等价
- 测试 fixture：unspecified 路径断言从 True 迁移到 False（fallback 移除后效果）
- 新增 unspecified return False + chat.py path patch 测试
- F090 D1 双轨彻底关闭，F107 不再需要碰
- 全量回归 0 regression
```

**Codex per-Phase review (Phase E2)**：foreground，**重点检查**：
- 是否还有遗漏 production reader 读 metadata flag
- fixture 迁移是否完整
- chat.py pre-decision seed 路径在 orchestrator patch 前不再走 metadata flag，行为等价于 baseline
- `supports_single_loop_executor` 类属性是否被误改

**回归门**：`pytest octoagent` ≥ 3450 + 新增 D/E1/E2 测试 + e2e_smoke 1x PASS

---

### Phase G — mock-based perf 基准 + 全量回归 + e2e_smoke 5x sanity

**目标**：MEDIUM-2 修订后 perf 测量方法 + 全量回归。

**Task G-1**：mock-based perf 基准
- 实施工具：`pytest-benchmark` 或 stdlib `timeit`
- 测点 1：`is_recall_planner_skip` 调用耗时（baseline 4 个 mode + F100 force_full_recall flag 共 8 case，每 case 1000+ 样本均值）
- 测点 2：`is_single_loop_main_active` 同样测量
- 通过门：F100 commit vs F099 baseline 均值回归 ≤ 5%

**Task G-2**：override full recall 场景 perf
- mock fixture：`force_full_recall=True` 触发 recall planner phase 入口
- 测量 phase 入口决策延迟（不含 LLM 调用本身）

**Task G-3**：e2e_smoke 5x sanity check
```bash
for i in {1..5}; do pytest -m e2e_smoke octoagent/tests/e2e/ -v; done
```
通过门：5x 全 PASS（sanity，不作 perf hard gate）

**Task G-4**：全量回归
```bash
pytest octoagent
```
通过门：≥ 3450 + F100 新增测试数

**Task G-5**：commit
```
test(F100-Phase-G): mock-based perf 基准 + 全量回归 + e2e_smoke 5x sanity

- is_recall_planner_skip / is_single_loop_main_active helper mock-based perf：
  - F100 vs F099 baseline 均值回归 X% （< 5% hard gate 通过）
- override full recall 场景 phase 入口延迟 Xms
- e2e_smoke 5x 循环 PASS（sanity）
- 全量回归 N passed (vs F099 baseline +M)
- 性能基准报告：phase-g-perf-report.md
```

**Codex per-Phase review (Phase G)**：与 Final review 合并

**回归门**：全量 + e2e_smoke 5x PASS + mock-based perf hard gate 通过

---

### Phase H — Verify：Final cross-Phase Codex review + completion-report + handoff

**目标**：完成验收 + Final review 闭环 + 产出 F101 handoff。

**Task H-1**：Final cross-Phase Codex review
- 输入：spec.md v0.2 + plan.md v0.2 + Phase C/F/D/E1/E2/G 全部 commit diff
- 范围：检查"是否漏 Phase / 是否偏离原计划且未在 commit message 说明 / 大改动后是否有 N-H1 类隐性问题 / Codex pre-impl 4 finding 是否真正闭环"
- F099 实证：Final review 后可能抓 1 新 HIGH → 必须 re-review

**Task H-2**：处理 Codex finding
- HIGH：必须当 Phase 修复 + re-review
- MEDIUM：处理或归档 F101
- LOW：可 ignored，commit message 列出

**Task H-3**：产出 completion-report.md
- 模板沿用 F099 / F098
- 必含章节：
  - 实际 vs 计划对照表（Phase C/F/D/E1/E2/G/H）
  - Codex finding 闭环表（pre-impl 4 + per-Phase + Final + re-review）
  - 性能基准对比（mock-based perf vs F099 baseline）
  - Phase 跳过显式归档（若有）
  - 测试通过数：F99 baseline (3450) → F100 (N)，回归 0

**Task H-4**：产出 handoff.md（给 F101）
- §1 F100 落地状态摘要
- §2 决策环改造后 Notification 触发点（H1 完整决策环 override 触发点：`metadata["force_full_recall"] = True`）
- §3 Attention Model 信号源（force_full_recall 状态可能成为输入；OD-1=C 的 minimal trigger 在哪些场景应触发）
- §4 F099 7 项推迟项的当前状态评估
- §5 RecallPlannerMode 演进路径（F107 partial 中间档允许破坏式升级；LOW-1 闭环）
- §6 HIGH-1 minimal trigger 演进（F101 / 独立 Feature 接力实现 producer）

**Task H-5**：commit（多个）
```
docs(F100-Verify): completion-report + handoff + Codex Final review 闭环

- N HIGH / M MEDIUM 处理 / K LOW ignored
- Phase 跳过显式归档（若有）
- F101 handoff 6 章节
- Codex pre-impl 4 finding 真闭环验证
```

**回归门**：0 HIGH 残留 + completion-report 全填 + handoff 完整

---

## 2. 风险管理（v0.2）

### 2.1 Phase F 是 v0.2 关键风险拦截点

若 Phase F-3 实测发现 turn N+1 不重新 patch runtime_context：
- 立即暂停 Phase D/E
- 升级方案 1：F100 范围扩大——把 runtime_context_json 加入 TASK_SCOPED_CONTROL_KEYS
- 升级方案 2：resume 路径显式 patch runtime_context
- **必须与用户沟通范围调整**

### 2.2 Phase D FR-H 接入风险

`_metadata_flag` helper 已存在（task_service.py:_metadata_flag）但在 orchestrator 是否暴露？若不暴露需新增 helper。Phase D-5 实施前需 grep 确认。

### 2.3 Phase E2 chat.py 路径 destructive 风险

若 orchestrator 在某个 chat.py 派生路径上漏 patch（unspecified 残留），Phase E2 后会 raise。Phase F 必须验证 chat.py 全部派生路径 + Phase E2 必须有 chat.py path patch 测试。

### 2.4 性能 hard gate

mock-based perf 测量本身可重复性高，但 < 5% 仍是统计要求——若 baseline 噪声 > 1%，需扩大样本到 10000+。

### 2.5 Codex Final review 抓新 HIGH 应对

预留 0.3-0.5d。

---

## 3. 测试矩阵（v0.2 修订）

| 测试文件 | Phase | 覆盖 |
|----------|-------|------|
| `tests/test_runtime_control_f100.py`（新）| C 准备 / D 实施 / E2 加 | AUTO 决议 4 cases / force_full_recall 4 cases / unspecified raise / round-trip / FR-H AC-H1/H2 |
| `tests/test_orchestrator_f100.py`（新或并入）| E1 实施 | metadata 写入移除验证 + _prepare_single_loop_request runtime_context 设置 + FR-H hint 接入 |
| `tests/test_runtime_control_f091.py`（迁移）| C 准备 / E2 迁移 | unspecified 路径断言迁移到 raise |
| `tests/test_ask_back_recall_planner_resume.py`（新或并入）| F | ask_back resume turn N+1 行为 + is_caller_worker 透传 + worker_inline 默认 skip |
| `tests/test_chat_runtime_context_patch.py`（新或并入）| E2 | chat.py path orchestrator patch 必经路径验证 |

---

## 4. Commit 边界与 push 策略

**Commit 边界**：每 Phase 1 commit（C/F/D/E1/E2/G 各 1，H 多 commit 含 docs）。

**push 策略**：
- worktree 本地 commit + push origin/feature/100-decision-loop-alignment（保护工作）
- **不主动 push origin/master**（按 CLAUDE.local.md §Spawned Task 处理流程归总报告等用户拍板）

**禁止**：不 force push / 不 amend 已推送 commit / 不跳过 pre-commit hook。

---

## 5. Codex Review 触发点汇总

| Review 类型 | 时机 | 范围 | 模式 |
|-------------|------|------|------|
| Pre-impl review | **plan v0.2 完成后**（v0.1 review 已通过，v0.2 是否再 review 看修改面） | spec v0.2 + plan v0.2 整体 | foreground，已完成 |
| Per-Phase C review | Phase C commit 后 | audit 是否完整 | foreground |
| Per-Phase F review | Phase F commit 后 | 实测结论是否影响 spec/plan 假设 | foreground |
| Per-Phase D review | Phase D commit 后 | AUTO + force_full_recall + FR-H 接入 | foreground |
| Per-Phase E1 review | Phase E1 commit 后 | metadata 写入移除完整性 | foreground |
| Per-Phase E2 review | Phase E2 commit 后 | metadata 移除 + fixture 迁移 + chat.py 路径覆盖 | foreground |
| Per-Phase G review | Phase G commit 后 | 与 Final 合并 | foreground |
| **Final cross-Phase review** | Phase H 前 | 全 commit diff + 漏 Phase 检查 + 4 finding 真闭环验证 | foreground |
| Re-review（如有）| Final review 抓 HIGH 修复后 | 验证修复 | foreground |

---

## 6. 完成定义（Definition of Done，v0.2）

- [ ] Phase C/F/D/E1/E2/G 全部 commit + 回归门通过
- [ ] 全量回归 ≥ 3450 + F100 新增测试数 (vs F099 baseline 0 regression)
- [ ] e2e_smoke 5x 循环 PASS（sanity）
- [ ] mock-based perf hard gate 通过（helper 调用耗时回归 ≤ 5%）
- [ ] override full recall 软门通过（≤ 5s）
- [ ] Codex pre-impl 4 finding 真闭环（spec/plan/code 全部体现修复）
- [ ] Codex Final cross-Phase review 0 HIGH 残留
- [ ] completion-report.md 已产出（实际 vs 计划 + finding 闭环表 + 性能对比）
- [ ] handoff.md 已产出（6 章节，含 HIGH-1 minimal trigger 演进 + LOW-1 partial 升级路径）
- [ ] worktree 本地 commit + push origin/feature 分支
- [ ] **不 push origin/master**（等用户拍板）

---

**Status**: v0.2 Draft，准备进入 implement Phase C。
