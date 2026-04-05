---
feature-id: 070b
title: 工具系统简化重构
status: completed
milestone: M4
completed-at: 2026-04-05
---

# Feature 070b: 工具系统简化重构

## 概述
用 `check_permission()` 单函数替代 PolicyCheckHook + PresetBeforeHook + ApprovalOverrideHook 三套 Hook 体系。

## 交付物
- 新增 `permission.py`（check_permission + effective_side_effect）
- 新增 `path_policy.py`（PathAccessPolicy 白/黑/灰名单路径拦截）
- 删除 PolicyEngine/Pipeline/Evaluators/三个 Hook 文件
- SkillRunner 删除 _handle_ask_bridge 桥接逻辑
- Gateway/CapabilityPack 不再注册权限 Hook

## 验证
- 83 单元测试通过
- 权限决策从 Hook Chain 简化为单函数调用
- 源码目录/API keys/配置文件被黑名单拦截
