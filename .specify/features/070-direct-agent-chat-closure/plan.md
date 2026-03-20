---
feature_id: "070"
title: "Direct Agent Chat Closure"
status: "In Progress"
created: "2026-03-20"
updated: "2026-03-20"
---

# Plan

## Slice A - 后端 direct session 建模收口

- 明确区分用户 direct agent session 与 `BUTLER_MAIN` / `WORKER_INTERNAL`
- 修正 session.create / projected session id / thread seed 的建模
- 确保 direct agent session 有稳定的 runtime/session kind 语义

## Slice B - 模型别名与首条消息绑定

- 让 worker/agent profile 的 `model_alias` 真正穿透到 enqueue / process_task_with_llm
- 保证 direct session 首条消息把 `session_id / thread_id / agentProfileId` 一起带到后端
- 修复 `USER_MESSAGE.control_metadata` 的持久化

## Slice C - 用户会话投影与前端恢复

- 过滤内部 `worker_internal` session
- route session 恢复优先于默认 web session
- direct session 首次发送、刷新恢复、旧 direct session 兼容显示一致

## Validation

- gateway 定向单测
- frontend 定向单测
- 必要时读取 live 实例 `/api/control/resources/sessions` 做人工复核
