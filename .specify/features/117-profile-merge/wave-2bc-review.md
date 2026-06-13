# F117 Wave 2bc — 镜像完整性修复 + 再评审记录

> Wave 2bc = 闭合 Wave 2b 双评审抓出的 read-switch 镜像完整性缺口（用户拍板"草稿即时生效/保 baseline"）。
> 修复 + Codex + Opus 再评审 panel。

## 修复内容（vs Wave 2b commit 67201a40）
1. **`_save_worker_profile_draft`（worker_profile_ops.py:786）**：save_worker_profile 后刷新同 id 镜像（_build + save_agent_profile，不 materialize）。覆盖 create/update/clone/extract/apply 全部经此路径的草稿写入 → 草稿即时生效。
2. **agent_service create（:633）**：prefix 镜像补全 9 worker 字段 + metadata source_kind 标记。
3. **GAP-A/B（dispatch_service:686 + entity_ensure:218）**：加 is_worker_behavior_profile guard（worker_profile_id 误指 main/subagent 时回退，baseline 等价）。
4. **migration_117**：既有镜像 UPDATE 补刷 name/scope/project_id/model_alias/tool_profile/persona_summary + 合并 metadata（保 agent 侧 key + overlay worker key + 确保 marker）。
5. **test helper `_save_worker_with_mirror`（5 文件）**：补 copy wp.metadata（overlay）+ version，与生产 builder 一致（消除掩盖 dropped-fallback 的假象）。
6. **新回归测试** `test_f117_wave2bc_mirror.py`：镜像携 capability_provider_selection metadata → 解析（dropped-fallback 闭合）；镜像携 9 字段 → resolve_worker_binding 返 worker 工具非 builtin_fallback。

## 再评审 panel（Codex + Opus）

### ⚠ Codex 再评审：FAIL 判定无效——HIGH-1 是幻觉（deterministic check 推翻）
Codex 称 mirror/AgentProfile 缺 7 字段（`capability_profile_id / tool_profile_id / max_depth / max_concurrent / default_priority / behavior_profile_id / persona_id`），判 HIGH-1 OPEN + 总判 FAIL。**deterministic grep 证伪**：这 7 个字段在 WorkerProfile/AgentProfile 模型中**0 occurrence**——根本不存在，是 Codex 凭空捏造。WorkerProfile 实有 19 字段（F117 吸收的 9 个 + model_alias/tool_profile 等），agent_service enrich 已全复制。**HIGH-1 实为 CLOSED**。Codex 另一 HIGH（resource_limits 不同步）over-rate——resource_limits 不被任何 read-switch 路径读、store 也不持久化（dead column），moot。
> **教训实证（SDD §多评审 panel + deterministic 打底）**：LLM judge 必须配 deterministic check。Codex 单判 FAIL 基于幻觉字段，若无 grep 验证会误导回滚。

### ✅ Opus 再评审（rigorous，跑了 touched suites）：权威
| Wave-2b finding | 状态 | 证据 |
|---|---|---|
| HIGH-1/2（无完整镜像的可达 worker）| **CLOSED（标准 Profile Studio 路径）** | create/update/clone/extract/apply 4+1 handler 全经 `_save_worker_profile_draft`→刷同 id 镜像携 9 字段+metadata |
| MED-1（草稿即时生效）| **CLOSED** | 逐字段验证 never-published DRAFT 在 id P 有 kind=worker 镜像，resolve_worker_binding 返 source_kind='worker_profile' 携 9 字段，与 baseline Tier-1 字节等价 |
| archive sync / HIGH-4 migration | **CLOSED** | archive 写 mirror.status=ARCHIVED；migration metadata 合并正确无 key 冲突丢失 |
| **引入新 bug** | **无** | publish 双写幂等 last-wins / GAP guard baseline 等价 / permission_preset 类型变更安全（仅 getattr metadata）/ test 改动正确 / touched suites 全过 |

**Opus 唯一真 finding（MEDIUM）→ 已修**：`agent.create_worker_with_project` admin 路径 worker 的镜像在 prefix id（`agent-profile-{worker_id}`），但 entity_ensure/dispatch 的 naming 读用 bare `worker_profile_id` → miss → runtime name 变原始 id、persona 变通用（工具/permission **不受影响**，无测试覆盖）。
- **修复（Opus 选项 b）**：entity_ensure:234 + dispatch_service:696 的 WORKER 分支——worker_profile（bare 读）miss 时回退到已解析的 agent_profile 镜像（携 worker name/summary），name/persona 与 baseline 等价。标准路径 worker_profile 非 None 不触发回退（行为不变）。

### Opus LOW（归档，不阻塞）
- resource_limits 不同步：confirmed moot（不读/不持久化），leave。
- migration 用 setdefault vs builder 用 force 设 source_kind：cosmetic 不一致，无 worker 设冲突 source_kind，便时对齐。
- migration run_apply metadata-merge 无单测（逻辑 review 确认正确）。
- create_worker_with_project 路径无测试覆盖（naming 回退已修，建议后续补 admin-path 测试）。

## 状态
- Codex FAIL = 幻觉，deterministic 推翻；Opus 权威：标准路径全 CLOSED + 0 新 bug + 1 MEDIUM 已修。
- read-switch（Wave 2b）+ 镜像完整性（Wave 2bc）= **read-switch 现安全可解 BLOCK**。
- 终验：full 4135+ + e2e_smoke + 新回归测试。
