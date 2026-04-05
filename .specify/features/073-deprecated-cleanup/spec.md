---
feature-id: "073"
title: Deprecated 残留全面清理
status: completed
milestone: M4
completed-at: 2026-04-05
---

# Feature 073: Deprecated 残留全面清理

## 概述
全面清除 ToolProfile 枚举 + Workspace 概念 + Butler 遗留命名。

## 交付物
- Phase A: ToolProfile 枚举从全代码库删除（760 处/75 文件）
- Phase B+C: Workspace 概念从模型/Store/Gateway 全层清除（787 处/82 文件）
- A6: Butler 遗留命名彻底替换为 Main Agent（38 文件）
- A7: WorkerExecutionStatus + SubagentOutcome 冗余枚举消除
- 数据库启动迁移函数 _migrate_butler_naming()

## 验证
- grep 确认零残留（仅迁移/兼容函数中保留旧值字符串）
- 83 单元测试通过
