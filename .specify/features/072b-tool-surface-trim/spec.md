---
feature-id: 072b
title: Core/Deferred 工具分层接通
status: completed
milestone: M4
completed-at: 2026-04-05
---

# Feature 072b: Core/Deferred 工具分层接通

## 概述
接通 Feature 061 已实现但未启用的 Core/Deferred 分层机制，LLM 首轮从 56 个 schema 降到 9 个。

## 交付物
- resolve_profile_first_tools 按 CoreToolSet 分流
- Deferred 工具列表注入 system prompt
- tool_search 返回后自动提升到 Active
- CoreToolSet.default() 调优为 9 个高频工具

## 验证
- LLM 首轮 tools schema 数从 56 降到 9
- tool_search 提升链路连通
