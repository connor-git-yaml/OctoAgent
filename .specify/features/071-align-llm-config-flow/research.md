---
feature_id: "071"
title: "Align LLM Config Flow"
status: "Completed"
created: "2026-03-20"
updated: "2026-03-20"
research_mode: "codebase-scan"
---

# Research

## 结论摘要

这次 feature 不需要新增一套配置系统，当前主线已经有可复用的正确骨架：

- `octoagent.yaml` 已经是配置单一事实源
- `ProviderEntry.base_url` 已存在于 schema，并已接入 LiteLLM 生成器
- Web/CLI 的高层可用入口已经是 `setup.quick_connect` / `octo setup`
- Memory alias 的运行时 fallback 已存在，但保存前校验缺位

因此本次最合理的推进方式不是推翻重做，而是把已有能力的接缝补齐，并统一对外叙事。

## 代码现状

### 1. Provider `base_url` 已有底层支持，但 Web 丢字段

- `ProviderEntry` 已定义 `base_url`
- `generate_litellm_config()` 会把 `base_url` 生成成 `api_base`
- Settings 页的 `ProviderDraftItem`、`parseProviderDrafts()`、`stringifyProviderDrafts()` 没有这个字段
- `SettingsProviderSection` 也没有对应输入框

结论：这是明显的 UI / draft round-trip 漏洞，不是后端能力缺失。

### 2. Memory alias 仅在运行时 fallback，保存前没有完整把关

- `MemoryConfig` 已定义四个 alias 引用位
- `build_memory_retrieval_profile()` 能在运行时检测 alias 不存在或 provider 不可用，然后回退
- `OctoAgentConfig` 当前只校验 `model_aliases -> providers`，不校验 `memory -> model_aliases`
- `setup.review` 只检查 provider / main / cheap 等主配置，不检查 memory 绑定关系

结论：这会让用户和 Agent 以为已经配置成功，实则运行时静默降级。

### 3. 高层入口已存在，但文案和 skill 仍在传播历史路径

- Web action `setup.quick_connect` 会保存配置、启动 LiteLLM Proxy，并处理 runtime activation
- CLI `octo setup` 走的也是 `quick_connect` 主路径
- `octo config sync` 与 Agent 工具 `config.sync` 实际只重新生成 `litellm-config.yaml`
- `skills/llm-config/SKILL.md` 仍写着手改 `litellm-config.yaml`、`memory.backend_mode`、Docker restart 等历史流程
- README / blueprint 仍残留 MemU 三模式与“直接修改 litellm-config.yaml”描述

结论：产品主路径已经存在，但知识层和表述层明显漂移。

## 本次 Feature 范围

### 纳入

- Web Provider `base_url` 输入与无损 round-trip
- Memory alias 的 schema 校验与 setup.review 风险收口
- Agent / CLI / skill / 文档统一当前配置入口与生效语义

### 暂不纳入

- secret capture / 脱敏 / secret audit 架构重做
- setup.review 基于 draft secret state 的完整重构
- 新增全新的配置协议或替换 LiteLLM 运行模型

## 设计判断

### 为什么把 `octoagent.yaml` 作为唯一入口继续强化

这符合 blueprint 已经收敛的方向，也能避免 Web、CLI、Agent 再各自维护一套“半内部 LiteLLM 配置”心智模型。`litellm-config.yaml` 应继续是衍生物，而不是用户主配置面。

### 为什么这次把 `config.sync` 文案改诚实，而不是继续堆自动重载

当前人类用户已经有 `setup.quick_connect` / `octo setup` 作为高层一键入口。对低层 `config.sync` 最合理的做法，是先把职责说清楚，避免 Agent 错用；是否补 live reload 再单独做 feature。

### 为什么 Memory alias 错误要在 schema 阶段阻断

只对“非空且引用不存在”的情况阻断，不会影响默认 fallback 路径；但能显著降低 typo / 配置幻觉带来的调试成本。
