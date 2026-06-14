# F117 Wave 2c-2（2c-2a + 2c-2b）双评审 panel 裁定

> 范围：cluster commit `75e3013d`（2c-2a authoring 镜像统一 canonical）+ `4aca1d5e`（2c-2b 翻转
> materialize-on-read create-if-absent）。评审 = Codex（GPT-5.x，跨 provider）+ 独立 Opus（主线第二视角），
> 均对抗式。两者**显著分歧**（Codex 4 HIGH / Opus 0 HIGH）→ 主节点逐条 deterministic 裁定（方法论
> "多评审 panel 分歧必须人裁" + W2bc 教训"LLM judge 必配确定性检查"）。

## 裁定汇总

| Codex finding | 裁定 | deterministic 证据 | 处理 |
|---|---|---|---|
| **[1] HIGH** canonical builder 丢 `worker_profile.metadata` → `capability_provider_selection` 回归 | **驳回（幻觉）** | ① `git show 7199f468` master materialize-on-read builder（line 936+）metadata 只 merge `existing_profile.metadata` + source_* key，**从不 merge worker_profile.metadata**——与 canonical 逐字一致；② baseline 运行时镜像（=materialize 输出）本就无 worker_profile.metadata key；③ `capability_provider_selection` 全仓 **0 生产写入点**（仅 capability_pack:1224 读 + test 手设）；④ Codex 引用的 worker_profile_ops:571/807 实为通用 metadata 处理，**无该字符串**（幻觉行号，同 W2bc Codex 幻觉模式）| 无需改 |
| **[5] HIGH** create_worker_with_project 前缀镜像残缺被信任 | **驳回（等价）** | baseline `_ensure_agent_profile_from_worker_profile(agent-profile-{wpid})` → `get_worker_profile(前缀)` 返 None → 重建 **no-op** → baseline 本就 return 残缺前缀镜像。flip 等价。Opus 正确。| 无需改 |
| **[4] MED** rename 后 behavior_agent_slug 陈旧 | **驳回（pre-existing）** | baseline 已是"materialize 写 name-based slug / runtime 读 profile_id-based slug"分裂（canonical 镜像无 behavior_agent_slug → resolve 落 source_worker_profile_id=profile_id）。本 cluster 未改任一侧。F117 范围外既存条件。| 无需改 |
| **[3] HIGH** publish commit-then-sync 窗口 + self-heal 丢失 | **采纳（low-med）** | worker_profile_ops:877 `_publish` 内部 commit worker_profile **早于** 调用方 mirror sync（worker_service:963）+ 终 commit（983）→ 确有 commit-between。flip 下并发 dispatch 窗口内信任陈旧（完整）镜像；sync 失败则陈旧持久（baseline 会 self-heal）。**单用户 → 窗口极小、transient、一版陈旧**。| **2c-2c 消除**（停 worker_profiles 写 → 单写统一行，dual-write 不一致根除）。文档追踪。|
| **[2] HIGH** migration INSERT 写残缺镜像 → flip 后被信任 | **采纳，已在 flip 源头防御** | migration_117:468 "agent-only 字段取默认"（instruction_overlays=[] 等）。W4 migration 跑后 + flip → 残缺镜像被信任 → prompt 注入空 overlays（真回归，但 W4 才发生，当前无 live 残缺行）。| **flip 加完整性 guard**（见下）|

**净结论：Codex 4 HIGH 中 2 条幻觉驳回（[1][5]）、1 条 pre-existing 驳回（[4] MED）、1 条采纳归 2c-2c（[3]）、1 条采纳并已源头修复（[2]）。0 条阻塞当前 commit 的"运行时已坏"。**

panel 价值实证：Opus deterministic 穷举（7 个 save_worker_profile 调用点全覆盖 + master builder 对照）驳回了 Codex 2 条幻觉 HIGH；Codex 抓到 Opus 漏掉的 2 条前瞻性真问题（[2] W4 migration / [3] commit 窗口）。分歧经主节点 deterministic 裁定收敛。

## [2] 源头修复：flip 完整性 guard（不盲信"所有镜像都完整"）

`_resolve_agent_profile` 的 trust 分支加 `and existing.instruction_overlays`——只信任**完整** canonical
worker 镜像（canonical 恒置 WORKER_INSTRUCTION_OVERLAYS，残缺源[migration INSERT 默认 / 历史 inline]恒空）；
残缺 worker 镜像 fall through 重建，**保留 materialize-on-read 对残缺镜像的 self-heal**。

- **当前态行为等价**：当前无 bare-wpid 残缺镜像（authoring 全走 canonical / 前缀残缺镜像重建 no-op 后仍 return existing，同 baseline）→ 4139 passed 0 regression 实证。
- **W4 防御**：migration 即使写残缺行，运行时也自动重建成 canonical → **不依赖 migration 自身修正**（虽仍建议 W4 让 migration INSERT 写 canonical-complete，但 guard 使其非关键）。
- 测试锁：`test_resolve_agent_profile_trusts_existing_worker_mirror`（完整镜像→信任）+ `test_resolve_agent_profile_rebuilds_incomplete_worker_mirror`（残缺镜像→重建 self-heal）。

## 下游须知

- **2c-2c**：停 worker_profiles 写后，[3] 的 dual-write 不一致窗口**根除**（单写统一 agent_profiles 行）。
- **W4 migration**：建议让 migration INSERT 路径也写 canonical-complete 镜像（instruction_overlays + memory_recall），与 flip guard 双保险；真实例迁移前用户确认 + 备份。
