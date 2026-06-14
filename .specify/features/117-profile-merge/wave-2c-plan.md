# F117 Wave 2c 计划 — authoring 塌缩（Option B：直写 AgentProfile，用户拍板）

> 上游：recon agent 详尽报告（read path 已全在统一行；W2c = authoring 写 + 杀 worker_profiles + 镜像）。
> 用户拍板 Option B：authoring 直写 AgentProfile(kind=worker)，agent-only 派生移进写路径，删 materialize-on-read，本波改 WorkerProfile 用法。

## 关键约束（recon #1 结论）
运行时**只读** 2 个 agent-only 字段（worker 行）——**必须保留**：
- `instruction_overlays`（agent_context_prompt_assembly:145，prompt Block 1 注入）
- `context_budget_policy["memory_recall"]`（agent_context_helpers recall planner）
其余 vestigial（worker 行运行时不读）：`persona_summary`（MAIN-only）/ `bootstrap_template_ids`（UI-doc only）/ `policy_refs`（0 reader）/ `memory_access_policy`（worker 恒空）。
> **不变量**：authoring 直写的 worker 行必须携 instruction_overlays + context_budget_policy.memory_recall，否则 prompt/recall 退化。当前由 materialize-on-read（entity_ensure:951，每 dispatch 重写）注入——删它前必须把派生移进写路径。

## 现状（recon #2/#3/#6）
- 写：10 站 save_worker_profile（worker_profile_ops:786/854/865 / worker_service:861 / agent_service:378/628/685 / _coordinator:983），各配镜像 sync（_sync / Wave2bc draft-refresh / archive-sync / inline）。
- 读：8 authoring 站 get/list_worker_profile（worker_profile_ops:414/466/838 / worker_service:105/394/676/921 / agent_service:366）。
- **materialize-on-read（entity_ensure:951）是唯一运行时 get_worker_profile consumer**；read path（capability_pack/session/chat/dispatch name）已全切 get_agent_profile。
- revision：Wave 2a 已 ship agent_profile_revisions store 方法（无 blocker）。
- 2 个 inline 镜像 gap：_coordinator:986（minimal，不设 kind/9 字段）+ agent_service:378（resource_limits 不 sync）。
- AgentProfile ⊇ WorkerProfile（字段超集，authoring 字段读全可直接读 AgentProfile）。

## 子波分解（add-before-remove，每步 green）

### 2c-1 — 规范化完整 builder（加性，green 基础）
增强 `_build_agent_profile_from_worker_profile`（worker_profile_ops:109）使其产出**完整** worker 行：现有 worker 字段 + **instruction_overlays（2 worker 串）+ context_budget_policy.memory_recall（DEFAULT_WORKER_MEMORY_RECALL_PREFERENCES merge existing）+ bootstrap_template_ids**（merge entity_ensure:986 派生逻辑）。行为等价（materialize-on-read 本会设同值；现在 authoring 也设，冗余但一致）。导入 build_behavior_bootstrap_template_ids / _memory_recall_preferences / DEFAULT_WORKER_MEMORY_RECALL_PREFERENCES。

### 2c-2 — authoring 直写 AgentProfile + 删 worker_profiles 写（高风险核心）
- 写路径改：build AgentProfile(kind=worker) via 规范 builder → save_agent_profile；**删 save_worker_profile 调用** + Wave2bc draft-refresh / archive-sync（冗余）。
- 读路径改：get/list_worker_profile → get_agent_profile/list_agent_profiles(kind=worker)（is_worker_behavior_profile filter）。authoring 字段读用 AgentProfile（超集）。
- `_coordinator:986` + `agent_service:378` 2 gap：写完整 kind=worker 行。
- WorkerProfile 构造（~5）→ AgentProfile(kind=worker)（或经 builder）。

### 2c-3 — 删 materialize-on-read + 旧 builder + revision 切换
- 删 `entity_ensure._ensure_agent_profile_from_worker_profile`（行已完整，`_resolve_agent_profile` 直读 get_agent_profile）。
- 删 `_sync_worker_profile_agent_profile`（authoring 直写后冗余）。
- revision：ops:838/854 + worker_service:409 → agent_profile_revisions 方法（build AgentProfileRevision）。
- worker_profiles 表此后不读不写 → 待 W4 删 + migration。

## 不变量 / 验证
- 行为零变更：instruction_overlays + memory_recall 在 worker 行恒在（authoring 写）；read path 已验证。
- 每子波 0 regression（4137 baseline）+ e2e_smoke。
- 高风险（authoring 域 + 删 materialize-on-read）→ Codex+Opus 双评审 + deterministic 打底（W2bc Codex 幻觉教训）。
- WorkerProfile 类本波仍在（store get/save_worker_profile + 测试引用）；类删除 + 剩余改名留 W4。worker_profile.* wire 改名留 W3。

## ⚠ 2c-2 实施期序列修正（实读代码后推翻初版 plan 的 2c-2-before-2c-3 顺序）

> 实读 `_resolve_agent_profile` + `_ensure_agent_profile_from_worker_profile`（entity_ensure:839/950）后发现
> **materialize-on-read 是 always-overwrite-from-worker_profiles**，且 `_resolve_agent_profile` **优先**返回
> 重建镜像（行 850-855）。即每次 dispatch 都从 worker_profiles 重建并覆盖 agent_profiles(kind=worker)。
> **后果**：若先停 worker_profiles 写（plan 原 2c-2），materialize-on-read 会用陈旧 worker_profiles
> 每 dispatch 覆盖掉 authoring 刚写的 agent_profiles → 回归。**故 plan 的 2c-2-before-2c-3 顺序不安全**。

**修正后的安全序列**（read/write 耦合切换，承袭 W2bc 教训）：
1. **2c-2a 统一 builder（✅ 完成）**：authoring 持久化镜像（`_sync` + draft-refresh）改走 canonical
   `build_worker_agent_profile`，产出**完整** worker 行（旧 `_build_agent_profile_from_worker_profile`
   的 incomplete 镜像 instruction_overlays=[]/无 memory_recall 消除）。此后 authoring 镜像 ≡
   materialize-on-read 输出 → materialize-on-read 对**已持久化** worker 变冗余。
2. **2c-2b 翻转 materialize-on-read 为 create-if-absent**：`_resolve_agent_profile` 优先信任已存在的
   完整 agent_profile（authoring 保鲜），仅在镜像缺失时才从 worker_profiles 重建。此后 worker_profiles
   不再每 dispatch 覆盖镜像。
3. **2c-2c 停 worker_profiles 写**：authoring 写路径删 `save_worker_profile`（保留 in-memory WorkerProfile
   DTO 经 builder → save_agent_profile）；此时 worker_profiles 运行时无读者，停写安全。
4. **2c-3 删 materialize-on-read + 旧 builder + mirror-sync；authoring 读切 get_agent_profile；revision
   → agent_profile_revisions**。

### 2c-2a 实施记录（narrowed）
- **范围收窄**：只改两条**持久化 runtime 镜像**路径（worker_profile_ops `_sync_worker_profile_agent_profile`
  + `_save_worker_profile_draft` draft-refresh）。**故意不动** worker_service:152 的**瞬态文档镜像**
  （喂 `build_behavior_system_summary`，读 `bootstrap_template_ids`——canonical 会改文档输出，display-risk
  不并入地基步）→ 旧 `_build_agent_profile_from_worker_profile` 本波保留（仅 152 用）。
- **slug 保 baseline**：canonical 镜像不带 `behavior_agent_slug`，故 materialize 行为文件时直接
  `normalize_behavior_agent_slug(name or profile_id)`（= 旧 resolve 候选 #1，normalize 幂等）。
- **metadata key 收敛**：镜像溯源 key `worker_profile_id`→`source_worker_profile_id`（运行时唯一消费 key；
  旧 key 在 agent_profile.metadata 上**0 生产消费者**，dispatch_service:158 读的是 runtime_metadata）。
  唯一断言旧 key 的 test_control_plane_api:5197 已更新 + 加镜像完整性断言（instruction_overlays + memory_recall）。
- **deterministic 打底抓到 1 bug**：ruff F821——误删 bind-default 的 `revision` 赋值（它在 result data 用），
  已恢复。证 LLM judge 前先跑 ruff/类型的价值（W2bc 幻觉教训延续）。
- 回归：焦点集 255 passed；full 待跑（期望 4137）。
