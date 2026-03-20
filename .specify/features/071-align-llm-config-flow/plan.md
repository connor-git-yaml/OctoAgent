---
feature_id: "071"
title: "Align LLM Config Flow"
status: "In Progress"
created: "2026-03-20"
updated: "2026-03-20"
---

# Plan

## Summary

收口 LLM 配置主路径，目标不是新增机制，而是把现有 `octoagent.yaml -> setup.quick_connect / octo setup -> litellm-config.yaml -> Gateway runtime` 这条链路补齐接缝并统一叙事。实现重点扩展为五块：Web 自定义 Provider 的 `base_url`、Memory alias 保存前校验、CLI 自定义 Provider / wizard / setup 主路径、Gateway 运行时 alias 架构修正，以及 Agent / CLI / 文档对当前配置入口和生效语义的一致说明。

## Technical Context

**Language/Version**: Python 3.12 + TypeScript / React  
**Primary Dependencies**: FastAPI, Pydantic v2, React, Vitest, pytest  
**Storage**: `octoagent.yaml` + `litellm-config.yaml`（衍生） + SQLite / 本地实例目录  
**Testing**: pytest, Vitest  
**Target Platform**: 本地托管实例 + Web UI + CLI + Agent runtime  
**Project Type**: monorepo（gateway + provider package + frontend）  
**Performance Goals**: 不引入额外 runtime 请求；仅增强配置前校验与 UI round-trip  
**Constraints**: 不在本 feature 中重做 secret 安全链路；必须保持 `setup.quick_connect` 与 `octo setup` 现有主路径可用；不能继续扩大 alias 双轨语义债务  
**Scale/Scope**: 涉及 frontend settings / agent center、gateway setup review / capability tools / runtime init、provider schema / CLI / wizard / alias registry / skill / docs

## Constitution Check

- **Durability First**: 通过 `octoagent.yaml` 持久化配置，不引入临时仅内存状态
- **Everything is an Event**: 不改变现有 control plane action 事件链
- **Tools are Contracts**: 只在既有 schema/skill/tool contract 上补字段与文案，不绕开 contract
- **Least Privilege by Default**: secret 架构问题本次显式排除，不弱化后续治理空间
- **Degrade Gracefully**: Memory alias 留空继续 fallback，只有明显错配时才在保存前阻断；legacy 语义 alias 仅作为兼容层，不能压过显式配置

结论：通过，无需新增架构例外。

## Project Structure

### Documentation

```text
.specify/features/071-align-llm-config-flow/
├── spec.md
├── research.md
├── plan.md
└── tasks.md
```

### Source Code

```text
octoagent/
├── apps/gateway/src/octoagent/gateway/
│   └── main.py
├── apps/gateway/src/octoagent/gateway/services/
│   ├── control_plane.py
│   ├── capability_pack.py
│   └── llm_service.py
├── apps/gateway/tests/
│   ├── test_control_plane_api.py
│   └── test_main.py
├── frontend/src/domains/agents/
│   ├── AgentEditorSection.tsx
│   └── agentManagementData.ts
├── frontend/src/domains/settings/
│   ├── SettingsPage.test.tsx
│   ├── SettingsProviderSection.tsx
│   └── shared.tsx
├── frontend/src/pages/
│   └── AgentCenter.test.tsx
├── packages/provider/src/octoagent/provider/
│   └── alias.py
├── packages/provider/src/octoagent/provider/dx/
│   ├── config_commands.py
│   ├── config_schema.py
│   ├── config_bootstrap.py
│   └── cli.py
├── packages/provider/tests/dx/
│   └── test_config_schema.py
├── packages/provider/tests/
│   └── test_alias.py
├── README.md
├── docs/blueprint.md
└── skills/llm-config/SKILL.md
```

**Structure Decision**: 这是一次跨 surface 的配置流收口，不新增模块，直接在现有 Settings / control_plane / provider dx / docs 上修正断点。

## Slice A - Web Provider base_url

- 扩展 `ProviderDraftItem`，让 `base_url` 进入 parse / stringify / preset / 页面编辑态
- Settings UI 增加 `API Base URL` 字段和简短说明
- 增加前端测试，验证 round-trip 与提交 draft 保留 `base_url`

## Slice B - Memory alias 前置校验

- 在 `OctoAgentConfig` 中新增 `memory.*_model_alias -> model_aliases` 校验
- 在 `setup.review` 中补充更明确的 Memory alias 风险提示
- 增加 pytest，验证错误 alias 阻断、正确 alias 通过

## Slice C - 统一入口与生效语义

- 修正文案：`config.sync` / Agent `config.sync` 只表述为“重新生成 LiteLLM 衍生配置”
- 更新 `skills/llm-config/SKILL.md`，明确 Web / CLI / Agent 的配置入口、Memory alias 配置位和生效方式
- 更新 README / blueprint，清理 `litellm-config.yaml` 直接编辑和 MemU 三模式的历史叙事

## Slice D - CLI / Agent 自定义 Provider 闭环

- CLI `octo config provider add` 支持 `base_url`，并在更新已有 Provider 时保留未显式修改的可选字段
- CLI wizard 收集 `providers.0.base_url` 与 `memory.*_model_alias`，让 CLI-first 用户能一次性生成完整 draft
- Agent capability pack 暴露高层 `setup.review` / `setup.quick_connect`，直接复用 canonical setup 流程

## Slice E - Runtime Alias Architecture

- 把 Gateway `LLMService` 的 alias 解析从“历史 MVP 语义 alias -> main/cheap/fallback”重构为“配置 alias 优先，legacy 语义 alias 兼容 fallback”
- Gateway 启动时从 `octoagent.yaml.model_aliases` 构造 runtime alias registry，避免 runtime 与 LiteLLM 生成配置出现双轨事实源
- `worker_profile.review`、`agent_profile.save` 与 AgentCenter alias 选项统一收敛到当前配置 alias 集，去掉硬编码 `reasoning`
- `octo setup` 扩展 custom provider + `base_url` 主路径，避免 CLI 主入口与低层命令分裂

## Validation

- `pytest octoagent/packages/provider/tests/dx/test_config_schema.py -q`
- `pytest octoagent/packages/provider/tests/test_alias.py -q`
- `pytest octoagent/apps/gateway/tests/test_control_plane_api.py -q -k "setup_review or setup_governance"`
- `pytest octoagent/apps/gateway/tests/test_main.py -q -k "stream_model_aliases or runtime_alias_registry"`
- `npm test -- --run src/pages/AgentCenter.test.tsx`
- `npm test -- --run octoagent/frontend/src/domains/settings/SettingsPage.test.tsx`
