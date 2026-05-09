# F095 Worker Behavior Workspace Parity — Plan（Codex review #1 闭环版 v0.2）

| 字段 | 值 |
|------|-----|
| 上游 | spec.md v0.2（GATE_DESIGN passed + Codex review #1 闭环 + USER/BOOTSTRAP 决策翻转）|
| baseline | 284f74d |
| Baseline 测试 | `packages/core/tests/test_behavior_workspace.py` = 53 passed |
| F094 并行 | feature/094-worker-memory-parity（独立改动域）|
| Phase 顺序（v0.2）| A（envelope+IDENTITY 修复）→ B（worker variant 模板）→ C（白名单扩展）→ D（BEHAVIOR_PACK_LOADED + pack_id）→ E（Final + rebase F094）|
| 行为零变更对照 | 所有非-WORKER load_profile（FULL / MINIMAL / 代码内现存其他）行为 100% 等价 baseline 284f74d |

---

## 0. Plan 阶段补充实测（spec 阶段未覆盖，Codex review #1 触发）

### 0.1 测试断言数清点（影响 Phase A/C 工作量）

`packages/core/tests/test_behavior_workspace.py` 中需要更新的现有断言：

| 测试名 | 行号 | Phase | 改动 |
|--------|------|-------|------|
| `test_worker_profile_includes_5_files` | 276 | C | 改名 `..._includes_8_files`，expected 集扩到 8（含 USER, 不含 BOOTSTRAP）|
| `test_worker_profile_excludes_private` | 282 | C | 反转：BOOTSTRAP 仍 excluded；SOUL/HEARTBEAT/USER 不再 excluded |
| `test_worker_profile_returns_subset` | 318 | C | excluded 集只剩 `BOOTSTRAP.md` |

下游 envelope 测试搜索（baseline）：

```
rg -n "build_behavior_slice_envelope|shared_file_ids" octoagent/ --type py
```

baseline 命中 4 文件（agent_decision.py / behavior_workspace.py / models/__init__.py / models/behavior.py）+ 测试 0 直接断言 → Phase A/C 必须新增 envelope 单测覆盖。

### 0.2 BehaviorPack 字段层覆盖核实（v0.2 修订）

`build_behavior_layers` 接收 `pack.files`，按 `BehaviorLayerKind` 顺序展开：
- ROLE：AGENTS + IDENTITY
- COMMUNICATION：USER + SOUL（v0.2 含 USER；之前 v0.1 仅 SOUL）
- SOLVING：PROJECT + KNOWLEDGE
- TOOL_BOUNDARY：TOOLS
- MEMORY_POLICY：（无 default 文件）
- BOOTSTRAP：HEARTBEAT 仅（v0.2 不含 BOOTSTRAP.md；之前 v0.1 含 BOOTSTRAP + HEARTBEAT）

Worker 8 文件覆盖 5 个非空 Layer，符合 spec AC-1 要求的"4 层 H2 核心 + BOOTSTRAP lifecycle layer"（BOOTSTRAP layer 仅含 HEARTBEAT）。

### 0.3 Cache 行为分析（影响 Phase D BEHAVIOR_PACK_LOADED emit 时机）

`_behavior_pack_cache` key = `(profile_id, project_slug, project_root, load_profile.value)`：
- cache hit 不进 emit 路径 — 符合 spec §6.5 "仅 cache miss emit"
- cache 失效条件：mtime 变化（filesystem 路径）；default 路径 cache 永不失效（mtime=0）
- 单进程内一个 Worker 一次 LLM context 重建 = 一条 BEHAVIOR_PACK_LOADED（除非热更新 / 重启）

### 0.4 与 F084 SnapshotStore 的 prefix cache 关系

F084 SnapshotStore 冻结快照 = behavior layer 内容快照；F095 改 envelope 内容会**导致下次 dispatch 的 prefix cache 失效一次**（IDENTITY/SOUL/HEARTBEAT/USER 新增进上下文）。这是 spec §8 已识别风险，无额外缓解动作。

### 0.5 已存在模板再核实

| file_id | 主模板 | worker variant | F095 待新建 |
|---------|--------|----------------|-------------|
| IDENTITY.md | IDENTITY.main.md ✓ | IDENTITY.worker.md ✓（14 行）| — |
| SOUL.md | SOUL.md ✓（24 行通用，主 Agent 用）| ❌ | **SOUL.worker.md** |
| HEARTBEAT.md | HEARTBEAT.md ✓（36 行通用）| ❌ | **HEARTBEAT.worker.md** |
| BOOTSTRAP.md | BOOTSTRAP.md ✓（spec §6.2 不扩入 Worker；不新建 variant）| — | — |

最终 `_BEHAVIOR_TEMPLATE_VARIANTS` 含 **3 个 worker variant 条目**（IDENTITY 旧 1 + SOUL/HEARTBEAT 新 2）。

### 0.6 Worker AgentProfile 创建入口审计（Codex M7 推动）

实施 Phase C 前必须先 grep 列出所有 Worker AgentProfile 创建路径（含 `kind=worker` 或 `_is_worker_behavior_profile=True`）：

```
rg -n "kind=\"worker\"|kind='worker'|AgentKind\\.WORKER|_is_worker_behavior_profile" octoagent/ --type py
```

Phase C 期初先记录所有命中（worker 创建路径 / 测试 fixture / 迁移路径），保证至少：
- main 创建路径 → IDENTITY.main / SOUL / HEARTBEAT 通用模板
- worker 创建路径 → IDENTITY.worker / SOUL.worker / HEARTBEAT.worker variant
- 至少一条 `delegate_task → Worker workspace 初始化` 端到端集成测试覆盖

### 0.7 envelope `shared_file_ids` contract audit（Codex H4 推动）

Phase A 前必做：

```
rg -n "shared_file_ids|shared_file_count|private_file_count|envelope\\.layers\\b|build_behavior_slice_envelope" octoagent/ --type py
```

记录所有命中：是否消费者依赖"share_with_workers=True"语义，还是只把它当"profile 白名单"使用。如有依赖旧语义的代码：
- 选项 A：保留 `shared_file_ids` 字段名 + docstring 说明语义变更
- 选项 B：新增 `profile_file_ids` 字段，旧字段保留向后兼容
- 选项 C：rename 字段（破坏 contract）

baseline 期望：消费者主要在 metadata 字段（`shared_file_count` / `private_file_count` / `load_profile`），不依赖 share_with_workers 语义；可选项 A。Phase A 实施时实测确认。

### 0.8 EventStore.record_event 接口（Codex M10 推动）

Phase D 必须用 EventStore（不能用 structlog）。先看 `octoagent/apps/gateway/src/octoagent/gateway/events/` 或 `octoagent/packages/core/src/octoagent/core/events/` 实际 API：

```
rg -n "class EventStore|def record_event|EventStore\\.record_event" octoagent/ --type py
```

记录 EventStore 注入路径（DI / 全局单例 / context manager）+ event payload schema 是 BaseModel 还是 dict。Phase D 实施前必须确认。

### 0.9 BehaviorPack.pack_id schema 演进（Codex H5 推动）

Phase D 必须给 `BehaviorPack` 加 `pack_id` 字段。决策点：
- pack_id 生成策略：UUID4 / hash(profile_id + load_profile + source_chain + content) / Composite
  - 推荐：`hash(profile_id + load_profile.value + source_chain joined + sha256(layers content))`，让 cache hit 时如果有 emit 也能引用相同 pack_id
- pack_id 类型：str，格式 `behavior-pack:{profile_id}:{load_profile}:{16-char hex digest}`（实施时定为 16 hex；碰撞概率 2^-64 对单用户单 worktree 可忽略；如 F096 audit 跨用户场景需要扩到 32/64 hex 再调）
- 是否进 cache key？否（cache key 已是 profile_id + slug + root + load_profile）
- 与 BehaviorPack.pack_id 已有字段冲突？grep 确认：

```
rg -n "BehaviorPack\\(\\s|pack_id" octoagent/packages/core/src/octoagent/core/models/behavior.py
```

Phase D 实施前必须把 schema 演进路径定下来。

---

## 1. 实施 Phase 详细分解（v0.2 顺序）

### Phase A — envelope 双过滤收敛 + IDENTITY 修复

**目标**：移除 envelope 二次过滤，IDENTITY.md 进 Worker LLM context；最小风险，先做。

**前置条件**：§0.7 envelope contract audit 完成。

**改动文件**（4 个）：

1. `octoagent/apps/gateway/src/octoagent/gateway/services/agent_decision.py`
   - `build_behavior_slice_envelope` 行 327-330：移除 `share_with_workers AND` 子句，改为只按 `worker_allowlist` 过滤
   - 注释更新：明确"白名单是唯一过滤源；share_with_workers 字段保留作为 UI 提示"
   - 函数 docstring 显式说明：`shared_file_ids` 字段名保留但语义变更（"profile 白名单内文件 ID 列表"）
   - metadata 字段保留（`shared_file_count` / `private_file_count` / `load_profile`），language 同步

2. `octoagent/apps/gateway/tests/services/test_agent_decision.py`（如不存在则创建）
   - 新增 `TestBuildBehaviorSliceEnvelope` 类（覆盖：
     - `IDENTITY.md` 在 envelope 内（Worker profile）
     - `share_with_workers=False` 不再剥离白名单文件
     - `private_file_count` 计算与白名单一致
     - 主 Agent FULL profile envelope 行为零变更（如 envelope 也对 main 生成）

3. `octoagent/packages/core/tests/test_behavior_workspace.py`
   - 新增 `TestBehaviorSliceEnvelope` 类（5 个 unit 测试）：
     - IDENTITY.md 进 envelope
     - SOUL.md / HEARTBEAT.md / USER.md / BOOTSTRAP.md 不在 envelope（Phase A 阶段，白名单未扩前）
     - `shared_file_ids` 字段语义为 "profile 白名单内文件 ID 列表"

4. **可能新增** `octoagent/apps/gateway/src/octoagent/gateway/services/agent_decision.py` 或 spec 同位置：
   - AC-2b "prompt 拼接顺序" 的辅助函数 / 断言（如已有 `build_decision_system_prompt` 则用现有 API 加测试）

**Phase A 不动**：
- `_PROFILE_ALLOWLIST[WORKER]`（Phase C 改）
- `models/behavior.py` `share_with_workers` 字段（spec §6.3）
- `behavior_commands.py`（UI 路径仍读 share_with_workers）
- `behavior_templates/` 目录（Phase B 改）
- `_BEHAVIOR_TEMPLATE_VARIANTS`（Phase B 改）
- BehaviorPack schema（Phase D 改）

**Phase A 验收**：
- [ ] `pytest packages/core/tests/test_behavior_workspace.py -k "envelope|slice"` PASS
- [ ] 新增 envelope unit 测试 PASS（5+ 个）
- [ ] 主 Agent FULL profile 行为零变更（grep 主 Agent 走的代码路径，断言不命中 envelope 的 worker 分支）
- [ ] e2e_smoke PASS
- [ ] 全量回归 vs baseline 284f74d 0 regression
- [ ] §0.7 contract audit 闭环（无消费者依赖旧语义；或必要时新增 `profile_file_ids` 字段）
- [ ] Codex per-Phase review 0 high 闭环

---

### Phase B — Worker 私有模板 + variant 注册

**目标**：新增 `SOUL.worker.md` / `HEARTBEAT.worker.md`；扩 `_BEHAVIOR_TEMPLATE_VARIANTS`；模板内容 commit 前 Codex review。

**前置条件**：Phase A 通过；§0.5 模板清单确认。

**改动文件**（5 个）：

1. **新建** `octoagent/packages/core/src/octoagent/core/behavior_templates/SOUL.worker.md`
   - 内容要点（基于 spec §6.4 + IDENTITY.worker.md 风格）：
     - 服务对象：主 Agent（Butler）；通过 A2A 状态机回报，不直接对话用户
     - 输出风格：结论优先 / 聚焦专业领域 / 简洁高效（继承通用 SOUL "结论优先" 原则）
     - 用户决策需求：不主动对话，**escalate 给主 Agent**
     - 认知边界：信息不足 / 工具不足 / 多方案不确定 → A2A 回报主 Agent，由主 Agent 与用户对话
   - 内容长度：≤30 行（与通用 SOUL.md 24 行同量级）
   - 顶部注释引用 spec §6.1（USER.md 扩入决策）+ §6.2（不主动对话）

2. **新建** `octoagent/packages/core/src/octoagent/core/behavior_templates/HEARTBEAT.worker.md`
   - 内容要点（基于通用 HEARTBEAT.md + worker 特化）：
     - 自检触发：连续 5 次工具调用 / 异常 / 偏转 / 超时（与主版同）
     - 自检清单：保留主版 5 项 + 新增"是否还在 objective 范围 / 是否需要 escalate 给主 Agent"
     - 进度回报：对象**改为主 Agent**（A2A），不直接面向用户
     - 收口标准：objective 达成或假设不成立 → A2A 回报主 Agent
   - 内容长度：≤40 行（与通用 HEARTBEAT.md 36 行同量级）

3. `octoagent/packages/core/src/octoagent/core/behavior_workspace.py`
   - `_BEHAVIOR_TEMPLATE_VARIANTS` 新增：
     - `("SOUL.md", True): "SOUL.worker.md"`
     - `("HEARTBEAT.md", True): "HEARTBEAT.worker.md"`
   - 最终含 **3 个 worker variant 条目**（IDENTITY + SOUL + HEARTBEAT）
   - `_default_content_for_file` 链路无需改（已通过 `_template_name_for_file` 派发）

4. `octoagent/packages/core/tests/test_behavior_workspace.py`
   - 新增 `TestWorkerVariantTemplates` 类（4 个测试）：
     - `is_worker_profile=True` 时 SOUL/HEARTBEAT/IDENTITY 派发 worker variant
     - `is_worker_profile=False` 时派发主 variant
     - 渲染断言：worker variant 不漏 placeholder（`__AGENT_NAME__` 等）
     - worker variant 内容含"主 Agent" / "A2A" 或同义词（哲学守护断言）

5. `octoagent/packages/core/tests/test_behavior_workspace.py`（同文件）
   - 新增 `TestWorkerWorkspaceFilesInit` 测试（2 个）：
     - `build_default_behavior_workspace_files(include_advanced=True, agent_profile=worker_profile)` 渲染 worker variant
     - 主 profile 渲染主 variant

**Phase B 不动**：
- 主版 SOUL.md / HEARTBEAT.md 模板（避免回归主 Agent 行为）
- IDENTITY.worker.md / IDENTITY.main.md（已就绪）
- `_PROFILE_ALLOWLIST[WORKER]`（Phase C 改）

**Phase B 验收**：
- [ ] SOUL.worker.md / HEARTBEAT.worker.md 已创建
- [ ] `_BEHAVIOR_TEMPLATE_VARIANTS` 含 3 个 worker variant 条目
- [ ] 新增 variant 测试 PASS（6+ 个）
- [ ] 主 Agent FULL profile 行为零变更（断言：FULL profile 渲染 IDENTITY.main.md / SOUL.md / HEARTBEAT.md 通用版）
- [ ] **模板内容 commit 前 Codex 单点 review**（哲学守护：不含 user-facing / 主动对话 / hire-fire 指令）
- [ ] e2e_smoke PASS
- [ ] 全量回归 vs F095-Phase-A baseline 0 regression
- [ ] Codex per-Phase review 0 high 闭环

---

### Phase C — Worker allowlist 扩展（v0.2 顺序：模板就位后再扩）

**目标**：`_PROFILE_ALLOWLIST[WORKER]` 扩到 8 文件（去 BOOTSTRAP 加 USER + SOUL + HEARTBEAT）；此时 worker variant 已就位，扩白名单不会让 Worker 看到通用模板。

**前置条件**：Phase B 通过；§0.6 Worker 创建入口列表已建立。

**改动文件**（2 个）：

1. `octoagent/packages/core/src/octoagent/core/behavior_workspace.py`
   - `_PROFILE_ALLOWLIST[BehaviorLoadProfile.WORKER]` 改为 8 项：`{AGENTS, TOOLS, IDENTITY, PROJECT, KNOWLEDGE, USER, SOUL, HEARTBEAT}`
   - `BehaviorLoadProfile.WORKER` 文档串更新

2. `octoagent/packages/core/tests/test_behavior_workspace.py`
   - 修 §0.1 表中 3 个老断言（`includes_5_files` → `includes_8_files`，excluded 集只剩 BOOTSTRAP）
   - `TestBehaviorSliceEnvelope` 类（Phase A 创建）增 4 个新测试：
     - SOUL/HEARTBEAT/USER 在 Worker envelope 内（v0.2 关键变更）
     - BOOTSTRAP 不在 Worker envelope 内
     - Worker 加载 8 文件覆盖 ROLE / COMMUNICATION / SOLVING / TOOL_BOUNDARY / BOOTSTRAP 5 个 layer
     - SOUL 进 envelope 时 content 来自 SOUL.worker.md（用 placeholder 区分 marker）

3. **集成测试**（新增或现有 e2e）
   - 一条 `delegate_task → Worker workspace 初始化 → Worker LLM 决策环加载 8 文件` 端到端集成测
   - 覆盖 §0.6 Worker 创建入口审计的至少一个真实路径（非 helper 函数）

**Phase C 不动**：
- BehaviorPack schema（Phase D 改）
- BEHAVIOR_PACK_LOADED 事件（Phase D 改）

**Phase C 验收**：
- [ ] `_PROFILE_ALLOWLIST[WORKER]` 8 项（去 BOOTSTRAP 加 USER + SOUL + HEARTBEAT）
- [ ] 测试断言更新（3 个老断言 + 4 个新 envelope + 1 个集成测）PASS
- [ ] Worker LLM context 真能看到 IDENTITY/SOUL/HEARTBEAT/USER 内容（用 placeholder 区分 marker 验证）
- [ ] BOOTSTRAP 不在 Worker envelope（保护 H1）
- [ ] 主 Agent FULL profile 行为零变更
- [ ] e2e_smoke PASS
- [ ] 全量回归 vs F095-Phase-B baseline 0 regression
- [ ] Codex per-Phase review 0 high 闭环

---

### Phase D — BEHAVIOR_PACK_LOADED 事件 + BehaviorPack.pack_id

**目标**：BehaviorPack 加 `pack_id` 字段；`resolve_behavior_pack` cache miss 三条路径 emit `BEHAVIOR_PACK_LOADED` 事件；sink = EventStore.record_event。

**前置条件**：§0.8 EventStore 接口确认；§0.9 pack_id schema 决策。

**改动文件**（3-4 个）：

1. `octoagent/packages/core/src/octoagent/core/models/behavior.py`
   - `BehaviorPack` model 增 `pack_id: str` 字段
   - 字段生成策略：`hash(profile_id + load_profile.value + source_chain joined + per-file sha256(content))`，**16-char hex digest**（实施时定，对应单用户场景；F096 跨用户需要时再扩位宽）
   - 字段在所有构造点填充（`resolve_behavior_pack` 三条路径）
   - SQL schema 影响检查：grep `BehaviorPack` 是否进 SQL（如不进则免迁移）

2. `octoagent/apps/gateway/src/octoagent/gateway/services/agent_decision.py`
   - `resolve_behavior_pack` 在三条 cache miss 返回路径之前 emit：
     - filesystem_pack 路径（line 159 之前）
     - metadata raw_pack 路径（line 174 之前）
     - default fallback 路径（line 210 之前）
     - **cached 路径不 emit**（line 144-145）
   - emit 接口：`event_store.record_event(BEHAVIOR_PACK_LOADED, payload=...)`

3. **可能新增** `octoagent/packages/core/src/octoagent/core/events/behavior_events.py`（依 §0.8 实测决定 — 若现有 events 模块有合适位置则不新增）
   - 定义 `BEHAVIOR_PACK_LOADED` event payload schema（pydantic BaseModel）：
     - `pack_id: str`
     - `agent_id: str`（来自 `agent_profile.profile_id`）
     - `agent_kind: str`（main / worker / subagent）
     - `load_profile: str`（`load_profile.value`）
     - `pack_source: str`（"filesystem" / "default" / "metadata_raw_pack"）
     - `file_count: int`
     - `file_ids: list[str]`
     - `source_chain: list[str]`
     - `cache_state: str`（恒为 "miss"，预留 future）
     - `is_advanced_included: bool`

4. `octoagent/packages/core/tests/test_behavior_workspace.py` 或新建 `test_behavior_events.py`（依 §0.8 实测）
   - 单测（5+ 个）：
     - 单次 dispatch emit 一次 BEHAVIOR_PACK_LOADED
     - cache hit 不 emit（重复 resolve 同 key）
     - 三条 miss 路径 `pack_source` 字段正确（filesystem / metadata_raw_pack / default）
     - payload 字段完整性（10 个字段）
     - BehaviorPack.pack_id 在三条路径生成一致（同 input → 同 pack_id；不同 input → 不同 pack_id）
   - 集成测：Worker dispatch 端到端 emit 一次 BEHAVIOR_PACK_LOADED 含 `agent_kind="worker"`

**Phase D 验收**：
- [ ] BehaviorPack.pack_id schema 演进（model + 三条构造点 + SQL 检查）
- [ ] BEHAVIOR_PACK_LOADED 事件能在 unit / 集成测中被观测
- [ ] cache hit 不重复 emit
- [ ] payload 字段对齐 spec AC-5 + F096 接口
- [ ] 主 Agent dispatch 也 emit（main kind），Worker dispatch 也 emit（worker kind），可区分
- [ ] sink 是 EventStore.record_event（非 structlog 单写）
- [ ] e2e_smoke PASS
- [ ] 全量回归 vs F095-Phase-C baseline 0 regression
- [ ] Codex per-Phase review 0 high 闭环

---

### Phase E — Final cross-Phase + rebase F094 + AC-7b 集成测

**目标**：F094 完成合 master 后，F095 final 闭环（spec AC-6 + AC-7）。

**步骤**：

1. **rebase F094 完成的 master**
   ```bash
   cd /Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/.claude/worktrees/F095-worker-behavior-workspace-parity
   git fetch origin master
   git rebase origin/master
   ```
   - 冲突预期：spec/plan §0.6 静态隔离假设——预期无文件级冲突
   - 若有意外冲突：Codex M9 finding 已警示——必须停下来评估，不强推

2. **AC-7b 集成验证**（Codex M9 推动）
   - rebase 完成后跑：Worker dispatch 端到端，断言：
     - F095 BEHAVIOR_PACK_LOADED 事件含正确 `agent_id`
     - F094 RecallFrame 持久化含正确 `agent_id`
     - 两者 `agent_id` 一致
   - 这是**新增集成测**，验证 F094/F095 协同

3. **全量回归** 0 regression vs F094 完成后的新 baseline
   - `pytest` 全套 + e2e_smoke + e2e_full smoke 5 域

4. **Final cross-Phase Codex review**（按 CLAUDE.local.md 强制规则）
   - 输入：plan.md（本文，v0.2）+ Phase A + B + C + D 全部 commit diff + AC-7b 集成测结果
   - 重点：是否漏 Phase / 是否偏离 plan / spec 一致性 / 与 F094 协同 / Codex review #1 闭环结果是否真落地
   - high / medium 闭环；low 可推迟到后续 Feature

5. **completion-report.md 产出**（按 CLAUDE.local.md 强制规则）
   - 实际 vs 计划对照（Codex review #1 闭环 + Phase 顺序调整）
   - Codex review #1 + per-Phase + Final 全部 finding 闭环表
   - F094 并行合并结果（rebase 是否冲突 / AC-7b 是否通过 / 全量回归是否 0 regression）
   - 对 F096 / F097 接口点的留言（pack_id schema / BEHAVIOR_PACK_USED 事件接口 / Worker behavior 4 层）

6. **handoff.md**（沿用 F093 pattern）
   - 留给 F096 的接口点：BEHAVIOR_PACK_LOADED 字段语义、pack_id 引用方式、Worker memory 与 worker behavior 关系

7. **文档同步**
   - `docs/codebase-architecture/harness-and-context.md`：worker behavior 章节更新（baseline 4 文件 → F095 后 8 文件，share_with_workers 字段语义降级，BEHAVIOR_PACK_LOADED 事件）
   - `CLAUDE.local.md` M5 阶段 1 表格 F095 行：`✅ 完成（日期，commits + 文档制品，X passed vs F094-rebased baseline +N regression）`
   - **`docs/blueprint.md` 同步审计**（Codex L15 推动）：
     ```
     rg -n "Worker.*behavior|BEHAVIOR_LOADED|share_with_workers|behavior.*sharing" docs/blueprint*
     ```
     有相关章节则同步；无则在 completion-report 显式说明"经 grep 确认无相关章节"

**Phase E 验收**：
- [ ] git diff vs F094 完成的 master 不重叠 F094 改动域
- [ ] AC-7b 集成测 PASS（双 agent_id 一致性）
- [ ] 全量回归 0 regression
- [ ] Final Codex review 通过
- [ ] completion-report.md / handoff.md 已产出
- [ ] 文档同步完成（含 blueprint 审计结果）
- [ ] **未** push origin/master（按 prompt 要求）

---

## 2. 测试策略

### 2.1 单测增量（v0.2 估算）

| 模块 | 现有 | 新增 | 总 |
|------|-----:|-----:|----:|
| `test_behavior_workspace.py` `TestBehaviorLoadProfile` | 4 | 0（修 1）| 4 |
| `test_behavior_workspace.py` `TestResolveWithLoadProfile` | 5 | 0（修 1）| 5 |
| `test_behavior_workspace.py` `TestBehaviorSliceEnvelope` (新) | 0 | 5 + 4 (Phase C) = 9 | 9 |
| `test_behavior_workspace.py` `TestWorkerVariantTemplates` (新) | 0 | 4 | 4 |
| `test_behavior_workspace.py` `TestWorkerWorkspaceFilesInit` (新) | 0 | 2 | 2 |
| `test_behavior_workspace.py` 或 `test_behavior_events.py` | 0 | 5+ | 5+ |
| 集成层（agent_decision / e2e Worker dispatch / AC-7b 双 agent_id）| 已有 | 2-3 | 2-3 |
| **总增量** | — | **22-23** | — |

baseline = 53 → 预期 = 75-76

### 2.2 不写哪类测试

- 不写 BEHAVIOR_PACK_LOADED 事件 sink 性能测（超 F095 范围）
- 不写 worker variant 模板内容文案审查（人工 + Codex review）
- 不写 prefix cache 失效次数测（F084 责任）
- 不写 BEHAVIOR_PACK_USED 事件（F096 责任）

### 2.3 e2e_smoke 覆盖

- F087 e2e_smoke 5 域已覆盖工具调用 / USER.md 全链路 / 冻结快照 / ThreatScanner / ApprovalGate
- F095 改动应不影响这 5 域 — 每 Phase 后必跑

---

## 3. 文档同步

- `docs/codebase-architecture/harness-and-context.md`：worker behavior 章节更新（baseline 4 文件 → F095 后 8 文件，share_with_workers 字段语义降级，BEHAVIOR_PACK_LOADED 事件，pack_id 接口）
- `CLAUDE.local.md` M5 阶段 1 表格 F095 行：完成状态记录
- `docs/blueprint.md`：Phase E 时 grep 审计（Codex L15）；有相关章节则同步，无则显式说明

---

## 4. 风险与应对（plan 维度补全 spec §8，Codex review 闭环加固）

| 风险 | 概率 | 影响 | 应对 |
|------|:---:|:---:|------|
| Phase A envelope 单测撞坏现有非 envelope 路径 | 低 | 中 | Phase A 之前 baseline `pytest` 跑一遍记录 PASS 数（已记录 53） |
| Phase B SOUL/HEARTBEAT.worker.md 内容偏离 H1 哲学 | 中 | 中 | 模板内容 commit 前 Codex 单点 review；spec §6 决策点摘录贴入模板顶部注释 |
| Phase C 扩白名单时 Worker 看到通用 SOUL（中间态）| **已消除**（v0.2 Phase 顺序：B 先于 C）| — | — |
| Phase D EventStore 接口选择错误 | 低 | 中 | §0.8 plan 阶段定 sink；implement 阶段先确认 record_event 签名 |
| Phase D BehaviorPack.pack_id schema 演进破坏 cache | 中 | 中 | §0.9 实施前 grep 现有 pack_id 引用 + 决策生成策略 |
| F094 rebase 冲突（违反静态隔离假设）| 极低 | 高 | spec §3.2 / §7.1 + plan §0 已多次校验改动域；若仍冲突，停下来评估 |
| F094 / F095 隐性 agent_id 耦合（Codex M9）| 中 | 中 | Phase E AC-7b 集成测必跑 |
| `shared_file_ids` 字段消费者依赖旧语义（Codex H4）| 中 | 中 | Phase A §0.7 contract audit 必做 |
| Worker 创建入口审计漏看（Codex M7）| 中 | 中 | Phase C 前 §0.6 grep 必做 |
| IDENTITY 修复破坏 prompt 拼接顺序（Codex M8）| 中 | 中 | Phase A 加 AC-2b prompt 拼接顺序断言 |
| Worker prefix cache 失效带来 token 成本增加 | 中 | 低 | 事件可观测；后续按 budget 决策是否截断（F095 范围外）|
| BEHAVIOR_PACK_LOADED cache hit 误 emit | 低 | 中 | Phase D 单测必含 "重复 resolve 同 key 不 emit" |

---

## 5. 与上下游 Feature 的接口

### 5.1 F094 并行（实操）

- F094 改 `packages/memory/` / RecallFrame / `agent_context.py` recall planner 区域 / migrate-094
- F095 改 `behavior_workspace.py` / `agent_decision.py` 中 envelope 函数 / `behavior_templates/` / `models/behavior.py` (BehaviorPack.pack_id)
- **文件级低冲突**（Codex M9 推动改"零冲突"为"低冲突 + 集成验证"）
- Phase E rebase 后必跑 AC-7b 集成测

### 5.2 F096 接口（继任 Feature）

- F096 (Worker Recall Audit & Provenance) 将消费 F095 引入的 `BEHAVIOR_PACK_LOADED` 事件 + `pack_id` 字段做 Worker 行为审计
- F096 自己实现 `BEHAVIOR_PACK_USED` 事件（每次 LLM 决策环 emit），通过 `pack_id` 引用 F095 LOADED
- F096 还会按 `agent_id` / `session_id` 维度审计 Worker memory recall（F094 提供）
- F095 BEHAVIOR_PACK_LOADED payload 字段（pack_id / agent_id / agent_kind / load_profile / pack_source / file_ids / source_chain / cache_state / is_advanced_included）已对齐 F096 预期（详见 plan §1 Phase D）
- F095 不在 BEHAVIOR_PACK_LOADED 加 `recall_id` / `decision_id` 等 — 那是 F094 / F096 责任

### 5.3 F098 / F107 边界

- `share_with_workers` 字段彻底删除：F107 capability layer refactor
- Worker → Worker spawn 解禁：F098（spec §3.2 已排除）

---

## 6. 入口验证 checklist（implement Phase A 开工前）

- [x] worktree clean，HEAD 在 baseline 284f74d
- [x] F094 状态：可能仍在跑（独立 worktree）；F095 不依赖 F094 完成
- [x] `pytest packages/core/tests/test_behavior_workspace.py` baseline = 53 passed
- [ ] e2e_smoke baseline PASS（Phase A 开工前补跑）
- [x] 工具：`rg` / `pytest` / `node` / `git worktree` 可用
- [x] Codex CLI 可用（per-Phase review 必跑；spec/plan review 已闭环 #1）
- [ ] §0.7 envelope `shared_file_ids` contract audit 完成（Phase A 之前必做）
- [ ] §0.8 EventStore 接口确认（Phase D 之前必做）
- [ ] §0.9 BehaviorPack.pack_id schema 决策（Phase D 之前必做）
- [ ] §0.6 Worker 创建入口审计（Phase C 之前必做）
