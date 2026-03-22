# OctoAgent 当前技术文档地图

本文回答的问题不是“代码怎么跑”，而是“仓库里这些文档分别该在什么时候看、谁更权威、谁只是规划或历史拆解”。

## 1. 使用原则

阅读当前仓库文档时，建议先按下面的优先级理解：

1. **目标态 / 设计依据**：看 [`docs/blueprint.md`](../blueprint.md)
2. **当前产品使用说明**：看 [`octoagent/README.md`](../../octoagent/README.md)
3. **当前代码实现导览**：看 [本组 codebase architecture 文档](./README.md)
4. **专题深挖**：看 [`docs/design/`](../design/README.md) 下的单一主题文档
5. **重构方案**：看 [`docs/refactor-plan/`](../refactor-plan/README.md)
6. **里程碑拆解 / 历史规划**：看 [`docs/milestone/`](../milestone/README.md)
7. **Feature 研发制品**：看 `.specify/features/*`
8. **Agent/Codex 配置同步说明**：看 [`.agent-config/agent-config-sync.md`](../../.agent-config/agent-config-sync.md)

## 2. 现有文档按角色分类

### 2.1 仓库入口文档

| 文档 | 角色 | 适合什么时候看 | 注意事项 |
| --- | --- | --- | --- |
| [`README.md`](../../README.md) | 仓库入口、对外简介、文档入口 | 第一次进入仓库时 | 它不是实现级架构说明 |
| [`octoagent/README.md`](../../octoagent/README.md) | 当前产品安装、配置、运行与使用说明 | 想跑起来或理解当前产品路径时 | 它偏产品和运维入口，不会逐个解释类和函数 |

### 2.2 设计蓝图与 canonical 架构文档

| 文档 | 角色 | 适合什么时候看 | 注意事项 |
| --- | --- | --- | --- |
| [`docs/blueprint.md`](../blueprint.md) | 工程蓝图、设计目标、模块职责、路线图 | 做 Feature 设计、评审或判断方向时 | 它包含目标态结构，不能直接等同于当前物理目录 |
| [`docs/codebase-architecture/README.md`](./README.md) | 当前代码架构总览 | 想理解当前真实实现骨架时 | 以 codebase scan 为准，不替代 blueprint |

### 2.3 当前实现专题深挖

| 文档 | 角色 | 适合什么时候看 | 注意事项 |
| --- | --- | --- | --- |
| [`docs/design/README.md`](../design/README.md) | 设计专题目录入口 | 想先看有哪些深挖主题时 | 作为设计文档索引，不替代 blueprint 或 codebase map |
| [`docs/design/llm-provider-config-architecture.md`](../design/llm-provider-config-architecture.md) | LLM Provider / alias / setup / 协议与竞品对标专题 | 想深挖模型配置到调用这条链时 | 它是专题文档，不是总代码地图 |

### 2.4 里程碑和规划拆解

| 文档 | 角色 | 适合什么时候看 | 注意事项 |
| --- | --- | --- | --- |
| [`docs/milestone/README.md`](../milestone/README.md) | 里程碑文档目录入口 | 想先看有哪些历史拆解时 | 作为里程碑索引入口 |
| [`docs/milestone/m1-feature-split.md`](../milestone/m1-feature-split.md) | M1 拆解 | 回溯 M1 规划时 | 偏历史规划 |
| [`docs/milestone/m1.5-feature-split.md`](../milestone/m1.5-feature-split.md) | M1.5 拆解 | 回溯 runtime 闭环演进时 | 偏历史规划 |
| [`docs/milestone/m2-feature-split.md`](../milestone/m2-feature-split.md) | M2 拆解 | 回溯多渠道与治理演进时 | 偏历史规划 |
| [`docs/milestone/m3-feature-split.md`](../milestone/m3-feature-split.md) | M3 拆解 | 回溯工作台/配置/记忆产品化演进时 | 偏历史规划 |
| [`docs/milestone/m4-feature-split.md`](../milestone/m4-feature-split.md) | M4 拆解 | 回溯 setup governance / runtime safety 演进时 | 偏规划和任务拆解 |

### 2.5 Refactor Plans

| 文档 | 角色 | 适合什么时候看 | 注意事项 |
| --- | --- | --- | --- |
| [`docs/refactor-plan/README.md`](../refactor-plan/README.md) | 重构方案目录入口 | 发现结构性坏味道、想看成体系改法时 | 它是方案和迁移建议，不等于已实现事实 |
| [`docs/refactor-plan/capability-pack-simplification.md`](../refactor-plan/capability-pack-simplification.md) | Capability Pack / 默认工具面重构方案 | 想讨论工具面收窄、surface 分层、registry 拆分时 | 当前仍是提案，不是既成实现 |

### 2.6 Repo Meta Docs

| 文档 | 角色 | 适合什么时候看 | 注意事项 |
| --- | --- | --- | --- |
| [`.agent-config/agent-config-sync.md`](../../.agent-config/agent-config-sync.md) | Claude/Codex 共享配置同步说明 | 需要修改或同步 `CLAUDE.md` / `AGENTS.md` 时 | 它解释的是生成链，不是产品架构 |

### 2.7 Feature 制品

`.specify/features/<feature-id>-<slug>/` 下的文档是 Feature 级研发制品，适合回答：

- 这个 feature 当时解决什么问题
- 怎么规划的
- 做过哪些任务拆分

它们不适合用来替代总代码架构说明。  
例如，本次文档 feature 的制品就在：

- [`.specify/features/072-document-codebase-architecture/spec.md`](../../.specify/features/072-document-codebase-architecture/spec.md)
- [`.specify/features/072-document-codebase-architecture/research.md`](../../.specify/features/072-document-codebase-architecture/research.md)
- [`.specify/features/072-document-codebase-architecture/plan.md`](../../.specify/features/072-document-codebase-architecture/plan.md)
- [`.specify/features/072-document-codebase-architecture/tasks.md`](../../.specify/features/072-document-codebase-architecture/tasks.md)

## 3. 现在如果要理解当前系统，最短路径是什么

### 3.1 想快速建立全局认知

按这个顺序读：

1. [`README.md`](../../README.md)
2. [`docs/codebase-architecture/README.md`](./README.md)
3. [`docs/codebase-architecture/current-doc-map.md`](./current-doc-map.md)

### 3.2 想设计新 Feature 或判断架构方向

按这个顺序读：

1. [`docs/blueprint.md`](../blueprint.md)
2. 相关 `.specify/features/*`
3. [当前代码架构总览](./README.md)
4. 对应模块分册

### 3.3 想直接读实现

按问题选择：

- durable runtime：看 [Gateway Runtime / Control Plane](./modules/02-gateway-runtime-and-control-plane.md)
- 持久化和共享模型：看 [Core / Persistence](./modules/01-core-domain-and-persistence.md)
- 模型配置与调用：看 [Provider / LLM Stack](./modules/03-provider-and-llm-stack.md)
- tool / policy / skill：看 [Tooling / Policy / Skill Runtime](./modules/04-tooling-policy-skill-runtime.md)
- memory 与 A2A：看 [Memory / Protocol](./modules/05-memory-and-protocol.md)
- Web 工作台：看 [Frontend Workbench](./modules/06-frontend-workbench.md)

## 4. 新文档集与旧文档的边界

这组 `docs/codebase-architecture/*` 文档有三个明确边界：

1. **它们讲当前真实实现**
   不是目标态路线图，不替代 blueprint。

2. **它们讲模块、类和函数**
   不是用户安装教程，不替代 `octoagent/README.md`。

3. **它们优先做索引和实现级解释**
   对已经有专题深挖的领域，会链接过去，而不会简单复制一遍。

## 5. 需要特别小心的误读

### 5.1 “blueprint 写了，就说明当前已经这样实现了”

不成立。Blueprint 是方向、约束和目标态，当前代码里仍有不少职责集中在 `apps/gateway/services/*`。

### 5.2 “Feature 文档就是当前唯一事实源”

不成立。Feature 文档反映某次需求的制品，不等同于当前总代码说明。

### 5.3 “专题文档能替总代码地图”

不成立。专题文档只适合深入某一条链，例如 LLM Provider 配置流；它们不负责解释整个仓库的模块边界。
