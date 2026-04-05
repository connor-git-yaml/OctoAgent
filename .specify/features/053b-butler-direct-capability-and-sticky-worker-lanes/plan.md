---
feature_id: "053"
title: "Butler Direct Capability & Sticky Worker Lanes"
created: "2026-03-14"
updated: "2026-03-14"
status: "Implemented"
---

# Plan - Feature 053

## 1. 目标

把 OctoAgent 的主 Agent 收口成：

- 默认 direct-capable 的 Butler
- profile-first 与 single-loop 统一入口
- retained delegation 仍有高质量 handoff contract
- 同题材 follow-up 默认优先沿用 specialist worker lane

## 2. 实施切片

### Slice A - Canonical worker lens

- 在 `orchestrator` 最前面规范化 `requested_worker_profile_id`
- 让 profile-first 请求稳定进入 single-loop main path

### Slice B - Butler handoff composer

- 扩 ButlerDecision contract
- 为 retained delegation 生成 objective/context/tool/return contract

### Slice C - Direct capability surface

- 新增 `filesystem.list_dir / filesystem.read_text / terminal.exec`
- 把 `filesystem / terminal` 纳入 Butler 默认工具组
- 保持 ToolBroker / Policy / approval / audit 不变

### Slice D - Sticky worker lanes

- 为运行时 hints 增加最近同题材 worker continuity 提示
- 在 ButlerDecision 和 delegation metadata 中保留 continuity topic / preferred lane

### Slice E - Behavior files + tests

- 更新默认 behavior templates
- 增加 `behavior/system/*.md`
- 回归测试与 feature 文档收口

## 3. 风险

- 如果 terminal 工具边界控制不严，会放大主 Agent 的副作用面
- 如果 sticky lane 判定过宽，会把本应直接回答的问题过早委派
- 如果 handoff composer 过重，可能把 prompt 重新做得太厚

## 4. 验证方式

- `test_orchestrator.py`：single-loop canonicalization / handoff payload / sticky routing
- `test_capability_pack_tools.py`：filesystem/terminal builtin tools 与 audit 事件
- `test_butler_behavior.py`：默认行为文件与 decision contract
