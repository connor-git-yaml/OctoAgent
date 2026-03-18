---
feature_id: "063"
title: "Behavior File Lifecycle & Smart Loading — 任务清单"
created: "2026-03-18"
updated: "2026-03-18"
---

# 任务清单

## Phase 1: Bootstrap 生命周期管理 [P0]

- [ ] **T1.1** 新增 `OnboardingState` 模型 + 原子读写函数 (`behavior_workspace.py`)
- [ ] **T1.2** `ensure_filesystem_skeleton()` 创建 BOOTSTRAP.md 时写入 `bootstrap_seeded_at`
- [ ] **T1.3** `_resolve_behavior_source()` 对 BOOTSTRAP file_id 增加 onboarding 完成检查
- [ ] **T1.4** 路径 A（标记触发）：`behavior.write_file` 检测 BOOTSTRAP.md 的 `<!-- COMPLETED -->` 标记 → 写入 completed (`capability_pack.py`)
- [ ] **T1.5** 路径 B（删除触发）：`load_onboarding_state()` 检测 seeded 但文件已不存在 → 写入 completed (`behavior_workspace.py`)
- [ ] **T1.6** BOOTSTRAP.md 默认模板追加"完成引导"指令段 (`behavior_workspace.py`)
- [ ] **T1.7** Legacy 兼容检测：无 state 文件 + (IDENTITY.md 已修改 OR 有历史 session) → 自动标记完成
- [ ] **T1.8** 单元测试：双触发、状态持久化、跳过注入、legacy 兼容 (`test_behavior_workspace.py`)

## Phase 2: BehaviorLoadProfile 差异化加载 [P0]

- [ ] **T2.1** 新增 `BehaviorLoadProfile` 枚举 + `_PROFILE_ALLOWLIST` 常量 (`behavior_workspace.py`)
- [ ] **T2.2** `resolve_behavior_workspace()` 新增 `load_profile` 参数，按白名单过滤
- [ ] **T2.3** 新增 `truncate_behavior_content()` head/tail 截断函数 (70% 头 + 20% 尾 + 中间标记) (`behavior_workspace.py`)
- [ ] **T2.4** `resolve_behavior_pack()` 增加 session 级缓存 + write invalidate (`butler_behavior.py`)
- [ ] **T2.5** `resolve_behavior_pack()` 透传 `load_profile` (`butler_behavior.py`)
- [ ] **T2.6** `build_behavior_slice_envelope()` 改用 `BehaviorLoadProfile.WORKER` (`butler_behavior.py`)
- [ ] **T2.7** `_build_system_blocks()` 根据 Agent 角色选择 load_profile (`agent_context.py`)
- [ ] **T2.8** 单元测试：三种 profile 白名单、head/tail 截断、缓存命中/invalidate、向后兼容、与 Phase 1 联动 (`test_behavior_workspace.py`)

## Phase 3: Behavior Compactor [P1]

- [ ] **T3.1** 新增 `measure_behavior_total_size()` + 阈值常量 (`behavior_workspace.py`)
- [ ] **T3.2** `resolve_behavior_pack()` 完成后检查总大小，超阈值写入警告事件
- [ ] **T3.3** 新增 `extract_protected_sections()` / `merge_after_compaction()` (`behavior_workspace.py`)
- [ ] **T3.4** 新增 `behavior_compactor.py`：LLM 智能合并模式的 `compact_behavior_file()` + `compact_all_behavior_files()`
- [ ] **T3.5** 扩展 behavior 工具集新增 `action=compact` 子命令 (`capability_pack.py`)
- [ ] **T3.6** CLI `octo behavior compact` 命令 (`behavior_commands.py`)
- [ ] **T3.7** 单元测试：protected section、合并后变大跳过、备份创建、阈值警告 (`test_behavior_compactor.py`)
