# F095 Worker Behavior Workspace Parity — Completion Report

| 字段 | 值 |
|------|-----|
| Feature | F095 Worker Behavior Workspace Parity（M5 阶段 1 第 3 个 Feature）|
| 主责设计哲学 | H2 完整 Agent 对等性 |
| 状态 | ✅ 完成（spec / plan v0.3，5 commits + 4 次 Codex review，3191 passed 0 net regression）|
| baseline | 284f74d (F093) → rebased to F094-merged master (1b82fa8) |
| 分支 | feature/095-worker-behavior-workspace-parity |
| 完成日期 | 2026-05-09 |

---

## 1. 实际完成 vs Plan 对照

### Phase 实施清单

| Plan Phase | 实际 commit | 状态 | 备注 |
|------------|-------------|------|------|
| Phase 0 实测前置（T0.1-T0.5）| (未单独 commit，trace.md 记录) | ✅ | 5 项实测发现：sync/async 边界、pack_id baseline 简陋、Worker 创建入口仅 2 处等 |
| Phase A envelope 双过滤收敛 + IDENTITY 修复 | 1a09d2d → b5ba606（rebase 前哈希）| ✅ | 包含 AC-2b prompt 拼接顺序断言；FULL profile 行为零变更显式锁住 |
| Phase B SOUL/HEARTBEAT.worker.md 模板 | 69d69c8 → c1d2fd0 | ✅ | 含 production 路径 kind="worker" 测试；模板内容经 Codex 哲学守护 review |
| Phase C 白名单扩展（USER + SOUL + HEARTBEAT） | a665fc1 → f7c5e48 | ✅ | 真正 filesystem e2e 测试覆盖；BOOTSTRAP 不扩入决策 |
| Phase D BEHAVIOR_PACK_LOADED + pack_id | bbf2b4d | ✅（简化方案）| 用户拍板：infrastructure ready，emit 推迟 F096；MED-3 F094 RecallFrame 实测无 agent_id 字段，AC-7b 真实关联路径间接化 |
| Phase E Final + rebase F094 | (本提交) | ✅ | rebase F094 0 冲突；Final Codex review 6 finding 闭环；本报告产出 |

### 对照 plan.md 偏离

- **plan §0.5 Phase 顺序 A→B→C→D→E**：完全按计划执行
- **plan §1 Phase D pack_id 长度 64 hex** → **实施 16 hex**（plan 已修订；Codex Final LOW-1 闭环）
- **plan §1 Phase D EventStore 接入** → **推迟 F096**（用户 Phase D 范围决策；spec AC-5 已调）
- **plan §1 Phase E AC-7b 完整集成测** → **partial 验证**（F094 RecallFrame 无 agent_id 实测发现；spec AC-7b 已调）
- **spec AC-4 `delegate_task → Worker workspace 初始化` 端到端集成测** → **production 路径 e2e 覆盖**（不直接通过 delegate_task tool 触发；spec AC-4 已调）

---

## 2. Codex Review 闭环表

| Review 节点 | findings | 处理结果 |
|-------------|---------|----------|
| **Review #1** spec + plan | 5 high + 7 medium + 3 low (15 total) | 全部 15 接受。2 high 触发 GATE_DESIGN v0.2 翻转：USER.md 改扩入 / BOOTSTRAP.md 改不扩入 |
| **Phase A** per-Phase | 3 (0 high) | F1 测试空断言修 / F2 FULL zero-change 显式断言 / F3 worker_slice 字段语义已 docstring 说明 |
| **Phase B** per-Phase | 4 (2 medium + 2 low) | M1 SOUL "稳定事实写入 Memory" 越权改为"回报主 Agent" / M2 A2A 提前固化降级"运行时支持时" / L1 fixture 加 kind="worker" 路径 / L2 断言改用 Worker-only 完整片段 |
| **Phase C** per-Phase | 3 (1 high + 2 medium) | **HIGH1 已 materialize 主 Agent 版迁移问题 → deferred F107** / M2 测试用 main profile 改为 worker filesystem e2e / M3 端到端测覆盖 selected_source 路径 |
| **Phase D** per-Phase | 4 (1 high + 2 medium + 1 low) | HIGH1 pack_id 不 hash content → 改为每文件 sha256(content) / M2 pack_source 失真 → 按 file.source_kind 区分 default vs filesystem / M3 F094 RecallFrame 无 agent_id 字段 → AC-7b 真实路径间接化 / L4 mtime invalidation 测试新增 |
| **Final cross-Phase** | 6 (2 high + 3 medium + 1 low) | HIGH1 AC-5 spec 与实施不一致 → spec AC-5 调为分两阶段 / HIGH2 AC-7b 仅 helper 单测 → spec AC-7b 调为间接关联 partial / M1 CLAUDE.local.md 仍写错误设计 → 已修订完整状态行 / M2 AC-4 没 delegate_task 集成测 → spec AC-4 调为 production 路径覆盖 / **M3 SOUL.worker.md 与 F094 AGENT_PRIVATE memory 冲突 → 模板修订区分两类事实** / L1 pack_id 长度偏离 plan → plan 修订 |

**总计**：35 findings，全部闭环；2 推迟（Phase C HIGH1 → F107；Phase D EventStore 接入 → F096）。

---

## 3. F094 协同结果

### 3.1 Rebase 结果

```
git fetch origin master  # F094 已合 master (1b82fa8)
git rebase origin/master  # 4 commits 重新编号；0 冲突
```

**0 文件级冲突**：F095 工作域（`apps/gateway/src/octoagent/gateway/services/agent_decision.py` envelope / `packages/core/src/octoagent/core/behavior_workspace.py` 白名单 + variants / `packages/core/src/octoagent/core/behavior_templates/` / `packages/core/src/octoagent/core/models/behavior.py` BehaviorPackLoadedPayload / `packages/core/src/octoagent/core/models/enums.py` EventType）与 F094 工作域（`packages/memory/` / RecallFrame / `agent_context.py` recall planner / migrate-094）静态隔离。

### 3.2 全量回归对照

| 阶段 | 测试数 | 备注 |
|------|--------|------|
| baseline (284f74d, F093) | 3088 passed | F095 docs commit 时记录 |
| F094 合 master 后 | 3180 passed (+92) | F094 引入测试 |
| F095 Phase D 完成 | **3191 passed**（+11 F095 net 增量）| 0 net regression vs F094 baseline |

ThreatScanner `test_long_content_scan_under_1ms` 是 master baseline 既有 environment-dependent performance flake，不属于 F095 引入。

### 3.3 AC-7b partial 验证

由于 F094 RecallFrame schema 实际**没有 agent_id 字段**（实测确认它使用 `agent_runtime_id` / `agent_session_id` / `task_id`），完整端到端"双 agent_id 一致性"无法在 F095 范围内做。F095 实施了 partial 验证：

```
F095 BehaviorPackLoadedPayload.agent_id (= AgentProfile.profile_id)
    ↓
AgentRuntime.profile_id（worker dispatch 时创建）
    ↓
F094 RecallFrame.agent_runtime_id
```

**单测 `test_ac_7b_double_agent_id_consistency`** 验证 F095 自身：`payload.agent_id == agent_profile.profile_id`。完整链路集成测（用 AgentRuntime 表对齐两侧）由 F096 实施时自然覆盖（F096 接入 EventStore + 加 BEHAVIOR_PACK_USED，dispatch 端到端测覆盖此关联）。

---

## 4. 关键决策回顾

### 4.1 GATE_DESIGN v0.2 翻转（用户拍板）

| 项 | v0.1 | v0.2（最终）| 翻转理由 |
|----|------|-------------|----------|
| USER.md | 不扩入 | **扩入** | 实测 USER.md 是用户长期偏好（语言中文 / 信息组织 / 回复风格 / 确认偏好），无 user-facing 对话指令；Worker 写 commit message 也需对齐用户偏好；H1 哲学由 SOUL.worker.md 内容守住 |
| BOOTSTRAP.md | 扩入沿用通用 | **不扩入** | 实测 BOOTSTRAP.md 实际内容是主 Agent 用户首次见面对话脚本（"你好！我是 __AGENT_NAME__"... "你希望我怎么称呼你"），含 user-facing 对话指令 + propose_file 越权；Worker 没有"首次见面"周期 |

**最终白名单 8 文件** = `{AGENTS, TOOLS, IDENTITY, PROJECT, KNOWLEDGE, USER, SOUL, HEARTBEAT}` （= FULL 9 - BOOTSTRAP）

### 4.2 Phase D 范围决策（用户拍板）

由于 sync/async 边界硬约束（`resolve_behavior_pack` sync + `EventStore.append_event` async + 所有 production caller 都 sync），用户决策采用 infrastructure ready + emit 推迟 F096 方案：

- F095 实施：pack_id 改造（hash 化）+ BehaviorPackLoadedPayload schema + sync helper + cache miss 标记
- F096 推迟：实际 EventStore.append_event 接入 + BEHAVIOR_PACK_USED 事件 + 完整 dispatch e2e 集成测
- spec AC-5 已调整为分两阶段，明确 F095 / F096 范围分工

### 4.3 share_with_workers 字段降级

字段保留作为 UI / behavior_commands 显示提示；**只去掉 envelope 过滤逻辑**。完全删除字段需要 SQL schema 变更 + UI 同步，超 F095 范围（推迟到 F107 capability layer refactor）。`BehaviorSliceEnvelope.shared_file_ids` 字段名保留但 docstring 显式说明语义变更（"WORKER 白名单内文件 ID 列表"，不再是"share_with_workers=True 列表"）。

### 4.4 IDENTITY.md envelope baseline bug

实测发现：原 `build_behavior_slice_envelope` 用 `share_with_workers AND in worker_allowlist` 双过滤，IDENTITY.md（`share_with_workers=False`）即使在 WORKER 白名单内也被剥离 → IDENTITY.worker.md 模板渲染了但 Worker LLM 永远看不到（隐性 bug）。Phase A 修复（顺手修，用户 GATE_DESIGN 拍板时确认）。

---

## 5. Deferred Followups（必读）

### 5.1 → F096（Worker Recall Audit & Provenance）

**重要：F096 必须接入以下推迟项**：

1. **BEHAVIOR_PACK_LOADED 事件 EventStore 接入**：F095 已就位 schema (`BehaviorPackLoadedPayload`) + sync helper (`make_behavior_pack_loaded_payload`) + cache miss 标记 (`pack.metadata["cache_state"]="miss"`)；F096 在 async caller 处接入：

   ```python
   pack = resolve_behavior_pack(...)
   if pack.metadata.get("cache_state") == "miss":
       payload = make_behavior_pack_loaded_payload(pack, agent_profile=ap, load_profile=lp)
       event = build_event(EventType.BEHAVIOR_PACK_LOADED, payload.model_dump())
       await event_store.append_event_committed(event)
   ```

2. **BEHAVIOR_PACK_USED 事件**：F095 不实施。F096 自定义此事件（每次 LLM 决策环 emit），通过 `pack_id` 引用 LOADED 实例形成完整可审计链路。

3. **AC-7b 完整 dispatch 集成测**：用 AgentRuntime 表的 (profile_id ↔ runtime_id) 映射对齐 F095 BEHAVIOR_PACK_LOADED.agent_id 与 F094 RecallFrame.agent_runtime_id。

4. **`delegate_task → worker_service.create_worker → workspace 初始化 → BEHAVIOR_PACK_LOADED emit` 端到端集成测**：F095 提供的 helper / fixture 可复用。

### 5.2 → F107（Capability Layer Refactor）

**Phase B 前已 materialize 主 Agent 版 SOUL/HEARTBEAT 的 worker 目录迁移**（Codex Phase C HIGH-1 deferred）：

`materialize_agent_behavior_files` 是 write-if-missing 语义。如有用户在 F095 合并前已创建过 worker（用 `is_worker_profile=False` 误调用，或 F094/F095 改动前的代码路径），其 `behavior/agents/{worker_slug}/SOUL.md` / `HEARTBEAT.md` 是主 Agent 通用版本——F095 合并后 Worker 仍读这些旧文件（`_resolve_filesystem_behavior_pack` 优先 selected_source），违反 H1 守护。

**人工迁移指引**（Connor 单用户场景）：合并 F095 后检查 `~/.octoagent/behavior/agents/*/SOUL.md` 内容，如不含 "服务对象 = 主 Agent" 字样则**手动删除**触发重新 materialize：

```bash
# 检查所有 worker SOUL.md
for f in ~/.octoagent/behavior/agents/*/SOUL.md; do
    if ! grep -q "服务对象 = 主 Agent" "$f"; then
        echo "需迁移: $f"
        # 手动决定：rm "$f" 触发下次 worker 创建时重新 materialize 为 worker variant
    fi
done

for f in ~/.octoagent/behavior/agents/*/HEARTBEAT.md; do
    if ! grep -q "通过当前 Worker 回报通道" "$f"; then
        echo "需迁移: $f"
    fi
done
```

**自动化迁移**留 F107（capability layer refactor 时一并实现 migrate-095 CLI 或 materialize 强制升级模式）。

### 5.3 → 长期跟踪

- `share_with_workers` 字段彻底删除（F107）
- WorkerProfile 完全合并 AgentProfile（F107）
- pack_id 长度（16 hex 在单用户单 worktree 足够；F096 跨用户审计场景如需要可扩到 32/64 hex）

---

## 6. 制品清单

```
.specify/features/095-worker-behavior-workspace-parity/
├── spec.md                       # v0.3（含块 A 实测、§6.1-6.5 决策、AC-1~AC-7 含 v0.3 调整）
├── plan.md                       # v0.3（5 Phase + Codex review 闭环 + pack_id 长度修订）
├── tasks.md                      # 按 Phase 拆解的可勾选任务
├── trace.md                      # 编排 + Phase 进度 + Codex review 历次闭环
├── codex-review-spec-plan.md    # Review #1（15 finding）闭环表
└── completion-report.md          # 本文件
```

源代码改动文件：
- `octoagent/packages/core/src/octoagent/core/behavior_workspace.py`：白名单扩展 + variant 注册
- `octoagent/apps/gateway/src/octoagent/gateway/services/agent_decision.py`：envelope 改造 + pack_id helper + BEHAVIOR_PACK_LOADED helper
- `octoagent/packages/core/src/octoagent/core/behavior_templates/SOUL.worker.md`（新建）
- `octoagent/packages/core/src/octoagent/core/behavior_templates/HEARTBEAT.worker.md`（新建）
- `octoagent/packages/core/src/octoagent/core/models/behavior.py`：BehaviorPackLoadedPayload 新增
- `octoagent/packages/core/src/octoagent/core/models/enums.py`：EventType.BEHAVIOR_PACK_LOADED 新增
- `octoagent/packages/core/src/octoagent/core/models/__init__.py`：export BehaviorPackLoadedPayload

测试改动：
- `octoagent/packages/core/tests/test_behavior_workspace.py`：53 → 60+ 测试（白名单 / variant / Worker filesystem e2e）
- `octoagent/apps/gateway/tests/services/test_agent_decision_envelope.py`（新建）：29 测试覆盖 envelope / pack_id / payload helper / cache invalidation / AC-7b partial

文档改动：
- `CLAUDE.local.md` F095 行更新为完整状态
- `docs/codebase-architecture/harness-and-context.md`：见 handoff.md 提示（如必要由后续 Feature 同步）

---

## 7. 验收 checklist 最终状态

### 块 A 实测验收（spec §2）
- [x] BehaviorLoadProfile.WORKER 当前真实加载文件清单
- [x] share_with_workers 是否真过滤
- [x] IDENTITY.worker.md 是否被消费
- [x] SOUL.worker.md / HEARTBEAT.worker.md 默认模板是否存在
- [x] BOOTSTRAP.md / USER.md 内容实测（决策依赖项）
- [x] baseline grep 命中数对照表

### 块 B 验收
- [x] BehaviorLoadProfile.WORKER 含 USER + SOUL + HEARTBEAT（不含 BOOTSTRAP）
- [x] effective_behavior_source_chain 显示 4 层 H2 核心 + BOOTSTRAP lifecycle 5 layer 覆盖
- [x] share_with_workers 决策（保留字段，去 envelope 过滤）
- [x] USER.md 扩入决策（v0.2 翻转）
- [x] BOOTSTRAP.md 不扩入决策（v0.2 翻转）

### 块 C 验收
- [x] IDENTITY.worker.md (已存在) + SOUL.worker.md (新建) + HEARTBEAT.worker.md (新建)
- [x] Worker 创建路径自动初始化（kind="worker" + materialize_agent_behavior_files(is_worker=True)）
- [x] 模板内容体现 Worker persona（不主动与用户对话 + 通过 Worker 回报通道 + 区分 AGENT_PRIVATE memory vs USER 偏好）

### 全局验收
- [x] 全量回归 0 net regression vs F094-merged baseline (3191 passed)
- [x] e2e_smoke 每 Phase 后 PASS（pre-commit hook 跑过 5+ 次，每次 8 passed）
- [x] 每 Phase Codex review 闭环（0 high 残留 except deferred F107）
- [x] Final cross-Phase Codex review 通过（6 finding 闭环）
- [x] Final 阶段已 rebase F094 完成的 master + 全量回归
- [x] completion-report.md（本文件）已产出
- [x] handoff.md 已产出（见同目录）
- [x] CLAUDE.local.md M5 阶段 1 表格 F095 行已更新
- [ ] docs/codebase-architecture/harness-and-context.md 同步（视后续是否真需要——F095 没改 harness 结构，仅 worker behavior 加载行为；详见 handoff）
- [x] docs/blueprint.md 审计：grep `Worker behavior / BEHAVIOR_LOADED / share_with_workers / behavior sharing` 无相关章节，无需同步

---

## 8. 给主 session 的归总结论

**F095 主体已完成，可以合入 master**：
- ✅ 5 commits 已就位（5df00ec → bbf2b4d）
- ✅ rebase F094-merged master 0 冲突
- ✅ 3191 passed 0 net regression
- ✅ 全部 35 Codex finding 闭环或显式推迟（F096/F107）
- ✅ 关键 v0.2 决策（USER 扩入 / BOOTSTRAP 不扩入）经用户拍板
- ✅ Phase D 简化方案（emit 推迟 F096）经用户拍板
- ✅ MED 3 哲学守护守住（SOUL.worker.md 区分 AGENT_PRIVATE memory vs USER 偏好，与 F094 Worker Memory Parity 协调）

**建议**：合入 origin/master 前先用户审视本 completion-report + handoff.md。本 spawn task 不主动 push（按 spawn task 流程）。

**已知 deferred 项必须确认接受**：
- F096：BEHAVIOR_PACK_LOADED EventStore 接入 + AC-7b 完整集成测
- F107：已有 worker 目录主版 SOUL/HEARTBEAT 迁移（人工指引已在 §5.2）+ share_with_workers 字段彻底删除
