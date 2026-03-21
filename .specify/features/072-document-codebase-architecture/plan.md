---
feature_id: "072"
title: "Document Current Codebase Architecture"
status: "Completed"
created: "2026-03-21"
updated: "2026-03-21"
---

# Plan

## Summary

基于最新 `origin/master` 对当前仓库执行 codebase scan，把“蓝图目标态”和“当前真实落地架构”分开表达，输出一套面向维护者的代码架构文档。交付物包括：feature 制品、代码架构总览、现有技术文档地图、6 份模块实现文档，以及 README 中的新文档入口。

## Technical Context

**Language/Version**: Markdown 文档；扫描对象为 Python 3.12 + TypeScript/React 代码  
**Primary Dependencies**: 当前实现涉及 FastAPI、Pydantic、SQLite、LiteLLM、React、Vitest、pytest，但本 feature 本身不新增运行时依赖  
**Storage**: 文档落在 `docs/` 和 `.specify/features/072-document-codebase-architecture/`  
**Testing**: 以源码对照和文档一致性检查为主，不新增自动化测试  
**Target Platform**: 仓库维护者、Feature owner、后续接手此代码库的 Agent 或工程师  
**Project Type**: monorepo 文档化 feature  
**Constraints**:
- 不改动现有运行时行为
- 不能把 blueprint 目标结构误写成当前物理目录
- 文档必须引用当前真实实现，而不是只复述旧蓝图
- 不与现有 `README` / `blueprint` / 专题文档制造新冲突

## Constitution Check

- **Durability First**: 本 feature 只新增持久化文档，不引入新运行时状态
- **Everything is an Event**: 不影响事件链和控制面动作
- **Tools are Contracts**: 文档会强调当前 tool / protocol / schema 的单一事实源边界
- **Least Privilege by Default**: 不新增 secret 暴露面
- **Observability is a Feature**: 会在模块文档中说明当前可观测与控制面聚合点

结论：通过，无需架构例外。

## Project Structure

### Feature Artifacts

```text
.specify/features/072-document-codebase-architecture/
├── spec.md
├── research.md
├── plan.md
└── tasks.md
```

### Documentation Deliverables

```text
docs/codebase-architecture/
├── README.md
├── current-doc-map.md
└── modules/
    ├── 01-core-domain-and-persistence.md
    ├── 02-gateway-runtime-and-control-plane.md
    ├── 03-provider-and-llm-stack.md
    ├── 04-tooling-policy-skill-runtime.md
    ├── 05-memory-and-protocol.md
    └── 06-frontend-workbench.md
```

### Touched Existing Docs

```text
README.md
```

**Structure Decision**: 不去改写 `docs/blueprint.md` 或 `octoagent/README.md` 的主体内容，而是在 `docs/` 下新增一个“当前代码架构”文档簇，并通过根 README 提供入口。

## Slice A - Feature 制品补齐

- 用 codebase-scan 结果重写 `spec.md`
- 新增 `research.md` 记录扫描边界、当前模块清单、已有文档盘点与文档结构决策
- 生成 `plan.md` 与 `tasks.md`

## Slice B - 架构总览与文档地图

- 新增 `docs/codebase-architecture/README.md`
- 新增 `docs/codebase-architecture/current-doc-map.md`
- 在总览中明确：
  - 当前真实模块分类
  - blueprint 目标态与当前物理目录的区别
  - 主控制流和数据流
  - 后续阅读路径

## Slice C - 模块实现文档

- Core / Persistence
- Gateway Runtime / Control Plane
- Provider / LLM Stack
- Tooling / Policy / Skill Runtime
- Memory / Protocol
- Frontend Workbench

每篇文档必须覆盖：

1. 模块职责边界
2. 当前目录与关键文件
3. 核心类
4. 核心函数实现逻辑
5. 模块与其他模块的连接点

## Slice D - 入口与一致性

- 更新根 README 的 Documentation Map
- 逐篇对照 `docs/blueprint.md`、`README.md`、`octoagent/README.md`、`docs/llm-provider-config-architecture.md`
- 确保新文档不制造第二套事实源

## Validation

- 人工对照当前源码路径，确认文档引用的模块、类、函数真实存在
- 运行 `git diff --check`，确保没有明显格式错误
- 抽查 README 中的新链接与文档目录结构是否一致
