# F117 Wave 2c-2c 实施分解（authoring 停写 worker_profiles）

> 上游：authoring 层读写地图（delegated map agent，35 tool_uses 取证）+ 主节点 id-divergence 裁定。
> 目标：authoring 彻底停写 worker_profiles，读+写切 agent_profiles(kind=worker)。保留 materialize-on-read + worker_profiles 表（W4 删）。Option B（持久化 AgentProfile，in-memory 可用 WorkerProfile DTO）。

## 关键发现（map agent）
- **读切可独立于写切先做**：authoring 生命周期写路径（draft/publish/archive）已每写 worker_profiles 同步 canonical **bare-wpid** 镜像（2c-2a/2bc）→ bare-wpid 镜像始终 current → 读切 agent_profiles 是 fresh、逐字段等价。
- **类型 ripple 小**：authoring 内部以 `model_dump`/duck-type 为货币（`_review/_save_worker_profile_draft`/`_publish` 全读同名字段）；`_get_worker_profile_in_scope` 翻 AgentProfile 出口，8 调用方无需改逻辑（AgentProfile ⊇ WorkerProfile 字段，W0 fold-in）。snapshot_payload 形状逐字段不变。
- **id-divergence**：authoring 生命周期路径=**bare wpid 同 id 镜像**（干净）；程序化创建（agent_service create / _coordinator 主 Agent）=**`agent-profile-{wpid}` 前缀镜像**。_coordinator 主 Agent 实为 **kind=main**（未传 kind→默认 main），带个历史 worker_profiles 行。
- **实例/测试现状**：真实例 0 前缀 worker（仅 bare-wpid 同 id default octo）；**0 测试用 create_worker_with_project**。→ 前缀路径的 dedup/by-id-miss 是 latent 正确性问题、非 test/实例 blocker。
- **revision 表切有存量风险**：sqlite_init 建 agent_profile_revisions 但**不 backfill**；仅 migration_117 apply（不可逆）复制。未迁移老实例切 revision 读 → 历史 revision UI 消失。**→ revision 表切推迟 W4**（migration 保证 backfill）。

## 主节点裁定
1. **revision 表切（WR1 写 + R-pub + R5 读）推迟 W4**——与 worker_profiles DROP 同批，天然有 migration backfill。2c-2c 不碰 revision 表。
2. **id-divergence**：2c-2c **不做 id 收口**（保持前缀 id + project.default 不变，避免可观察 id 变更）。读切对前缀加 **bare-then-prefix fallback**（小过渡 shim，W4 id 收口后删）+ 列表 dedup（防前缀+bare 重复条目）。彻底 id 收口（agent_service/_coordinator → canonical bare/main）留 W4 与 migration 同批。
3. **agent_service/_coordinator 写切推迟到 D 子步 + W4**：worker_profile_ops 生命周期路径（bare-wpid 干净）先切；程序化创建（前缀）写切风险高（涉 project.default + self-heal），单独 e2e 或并 W4。

## 子步分解（每步 0 regression 可独立 commit + review）

| 子步 | 范围 | 风险 | 评审 |
|---|---|---|---|
| **A1 by-id 读切** | R6(worker_service:676 create dup)/R7(:921 apply mode)/R8(worker_profile_ops:417 id 探测)/R9(`_get_worker_profile_in_scope`:461 + builtin source:422 出口翻 AgentProfile)/R4(worker_service:394 revisions scope-check get)：`get_worker_profile`→`get_agent_profile` + **bare-then-prefix fallback**；`_get_worker_profile_in_scope`/`_resolve_builtin_worker_source` 返回 AgentProfile | 低（镜像 current；duck-type 兼容） | Codex + 自查 grep |
| **A2 listing 读切** | R3(worker_service:105 `get_worker_profiles_document`)：`list_worker_profiles`→`list_agent_profiles` + `kind=="worker"` filter + **dedup**（按 source_worker_profile_id/bare-id，前缀+bare 去重） | 中（dedup 正确性） | Codex + 自查 |
| **B draft 停写** | `_save_worker_profile_draft`:789 删 `save_worker_profile`（保 in-memory WorkerProfile DTO→`build_worker_agent_profile`:827 已存在 + save_agent_profile）；返回值改镜像/DTO | 低（镜像写早已在；前提 A 落） | Codex+Opus |
| **C publish/archive/rl 停写** | W2(publish:866 改只更 DTO+revision，镜像统一由 handler `_sync` 写)/W7(archive:861 已镜像-only，删 worker save 半)/W3(resource_limits:378 改 agent_profile) | 中（publish 双写顺序） | Codex+Opus |
| **D 程序化创建停写** | agent_service create(628/685)/_coordinator 主 Agent(983)：删 worker_profiles save；agent_service 写 canonical bare 镜像或保前缀；_coordinator 只写 main profile（去 worker_profiles 行） | 中高（project.default + self-heal，单独 e2e） | Codex+Opus（或并 W4） |
| ~~E revision 表切~~ | **推迟 W4**（存量 backfill） | — | — |

依赖：A1 → A2 → (B ∥ C ∥ D 写切，A 落后) → W3(FE) → W4(migration + 删 materialize + 删类表 + 塌缩 + id 收口 + revision 切 + completion)。

## ⚠ A1 实施期发现（推翻 map "读切独立低风险"，主节点深析）

A1 forward 实现（reads 返回 AgentProfile）实测 **74 authoring 测试 PASS**（runtime 正确），但深析暴露**两条耦合**，证 A1 **非干净独立步**：

1. **类型 cascade**：`_get_worker_profile_in_scope` 返回 AgentProfile → 下游 `_publish_worker_profile_revision(profile)`/`_save_worker_profile_draft(existing)`/`_review_worker_profile_draft(existing/source)`/`_worker_profile_snapshot_payload(profile)`/`_sync_worker_profile_agent_profile(profile)` 全标注 `WorkerProfile`，duck-type 运行 OK 但**类型谎言**铺满 authoring 层。widen 全签名 = 6-8 处级联（_publish 返回 WorkerProfile→AgentProfile→_sync param→build_worker_agent_profile param…）。**reverse-converter（读 mirror→WorkerProfile DTO）可避开 cascade**（下游不变），是更优 A1 实现。
2. **metadata gap**：canonical mirror **不携 `worker_profile.metadata`**（Codex[1] 实证只 source_* keys）。authoring `_review_worker_profile_draft:594`（payload metadata 空时 fallback existing.metadata）+ `_worker_profile_snapshot_payload:768`（clone source.metadata）读 existing/source metadata → 读切后拿 source_* keys 非 user keys。real 实例（空 metadata）+ 测试不暴露（74 PASS），但**非字节级 metadata 等价**。**根因**：read-switch 与 write-switch 耦合——B/C 直写 agent_profile 时**须把 user metadata 并入**（或 canonical builder merge worker_profile.metadata，但改 runtime mirror shape 风险）。

**裁定（推翻 A1-独立）**：**A1（读切）与 B/C（写切）是 metadata + 类型耦合的原子单元，不可 A1 单独 commit**。正确实现：(i) reverse-converter 避类型 cascade，(ii) B/C 写路径携 user metadata 保 metadata 等价，(iii) A1+B/C 一起跑 cluster 双评审。forward A1 探针已 revert（runtime 正确性已验证，但 mis-decomposed）。下一 focused session 做 2c-2c 原子单元（reverse-converter + metadata-carrying writes）。

## watch points（review 必查）
1. A2 dedup：`list_agent_profiles(kind=worker)` 在前缀 worker 已 dispatch 实例上返回 bare+前缀两行 → 去重（实例/测试当前无，但生产 create_worker_with_project 路径在）。
2. A1 fallback：by-id 读 bare miss → 试 `agent-profile-{id}`（前缀 worker 未 dispatch 时只有前缀镜像）。
3. `_worker_profile_snapshot_payload` 类型注解放宽接受 AgentProfile（运行时 duck-type OK）。
4. D 的 project.default_agent_profile_id 保持不变（id 收口隔离到 W4）。
