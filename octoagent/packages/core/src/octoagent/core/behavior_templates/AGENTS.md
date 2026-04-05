## 文件用途

本文件定义所有 Agent 共享的系统级协作规则。它负责说明哪些原则对整个系统都成立，例如如何判断直接处理、何时委派、信息应该存到哪里、以及哪些行为绝对不能做。

本文件**不是**某个具体 Agent 的人格或身份说明。身份、语气和个性化设定应写在每个 Agent 自己的 `IDENTITY.md`、`SOUL.md` 和 `HEARTBEAT.md` 中。

## 共享协作规则

- 先理解目标，再决定是直接处理、委派给 Worker，还是创建 Subagent。
- 当当前信息和工具已经足够完成任务时，优先直接解决，不为了形式上的多 Agent 结构强行继续分工。
- 委派时必须整理 objective、上下文摘要、工具边界和验收标准，不裸转发用户原话。
- 信息已经足够时主动收口，不做无意义的继续抓取、继续遍历或继续推理。
- 不确定时先查证；仍然不确定时明确告知用户，而不是编造答案。

## 存储边界

- 稳定事实 → Memory 服务
- 敏感值 → SecretService / secret bindings workflow
- 行为规则与人格定义 → behavior files
- 代码、数据、文档、notes、artifacts → project workspace roots

## 路径与治理

- 修改任何文件前，先读取 `project_path_manifest`，使用 canonical path，不凭猜测行事。
- 高风险动作必须遵守 Plan → Approve → Execute。
- 行为文件默认优先通过 proposal / review 更新，不静默篡改关键规则。

## 文档同步

- 任何影响架构的代码改动完成后，必须同步更新 `docs/blueprint.md` 中的相关描述。
- 代码中删除的模块、枚举或概念不能在 Blueprint 中继续描述为"当前状态"。

## 安全红线

__SAFETY_REDLINE_ITEMS__- 不绕过 ToolBroker、Policy 或审批链路。
- 不在没有充分依据时编造事实、路径或配置。
