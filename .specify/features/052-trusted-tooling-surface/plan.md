---
feature_id: "052"
title: "Trusted Tooling Surface & Permission Relaxation"
created: "2026-03-14"
updated: "2026-03-14"
status: "Implemented"
---

# Plan - Feature 052

## 1. 目标

把当前 Tooling / MCP / Skills 默认权限面从“默认不给工具”推进到：

- trusted local baseline
- MCP auto-readonly mount
- skill inherit-by-default
- recommended tools 替代 selected-tools-only 语义
- 保持 approval / policy / audit 主链不变

## 2. 对标结论

### 2.1 对 Agent Zero

- 优点：主循环默认拥有更完整的 code/terminal/tool surface
- 我们借鉴：默认 ambient tool surface，不再把可逆工具过早裁掉
- 我们保留：比其更严格的 policy / audit / side-effect gate

### 2.2 对 OpenClaw

- 优点：session 装配时直接给出较完整的 built-in tools + context files
- 我们借鉴：MCP / built-in tools 更接近 ambient visible surface，而不是多重开关后才可见
- 我们保留：ToolBroker / control-plane / approval 的可观测主链

## 3. 设计原则

### 3.1 默认给足可逆工具，危险动作单独 gate

不是放弃最小权限原则，而是把“默认限制”从粗粒度 profile 前移，迁到不可逆动作的精细审批与审计。

### 3.2 Ambient Tool Surface 是主链，不是旁路

所有默认挂载与推荐逻辑都必须继续经由 capability pack、ToolIndex、ToolBroker、LLM metadata。

### 3.3 Provider 配置默认应该帮助 Agent，而不是默认熄火

MCP / Skills 的 provider 配置默认应该让 agent 更自然地获得能力，而不是要求用户做二次启用才能真正生效。

### 3.4 兼容层必须平滑

`selected_tools_json`、旧 skill provider 配置、旧 MCP catalog、现有 UI 断言都要保留兼容路径，避免一次性打断历史 runtime。

## 4. 实施切片

### Slice A - 核心模型与默认权限基线

- 新增 `mount_policy` / `permission_mode` / `recommended_tools`
- trusted local baseline 接入 capability pack / orchestration

### Slice B - MCP auto-readonly 挂载

- capability pack / control plane / MCP catalog 增加挂载策略
- runtime tool universe 吃到 auto-readonly

### Slice C - Skills inherit mode

- skill provider CRUD 支持 `permission_mode`
- `LLMService + LiteLLMSkillClient + SkillRunner` 吃到 ambient tools

### Slice D - recommended tools / prompt contract

- ToolIndex / DynamicToolSelection / EffectiveToolUniverse 升级
- `selected_tools_json` 兼容为 recommended mirror

### Slice E - 文档与验证

- 回写 blueprint / README / feature docs
- 跑后端、前端、集成回归

## 5. 风险

- 如果 trusted baseline 直接覆盖 policy，上层安全语义会回归
- 如果 MCP auto-readonly 判定过宽，会把误标注的工具自动暴露
- 如果 skill inherit 没有保留 restrict 模式，会破坏已有显式白名单 skill
- 如果 recommended/mounted 语义没对齐，可能导致 UI 和 LLM runtime 各说各话

## 6. 验证方式

- capability pack / ToolIndex / control-plane 单测
- skill inherit/restrict 合同测试
- MCP auto-readonly runtime filtering 测试
- LLM metadata / prompt contract 测试
- 前端 capability/settings 资源展示测试

## 7. 本轮实施顺序

1. 先做 Slice A，定义稳定模型与兼容字段
2. 再做 Slice B，接通 MCP auto-readonly
3. 再做 Slice C，接通 skill inherit-mode
4. 最后做 Slice D/E，完成推荐工具语义和文档回写

## 8. 实施结果

- Slice A 已完成：`recommended_tools / mount_policy / permission_mode` 已成为正式模型字段。
- Slice B 已完成：MCP registry、capability pack、control-plane catalog 已接通 `auto_readonly / auto_all / explicit`。
- Slice C 已完成：Skill provider 默认 `permission_mode=inherit`、`tool_profile=standard`，runtime 已继承 ambient mounted tools。
- Slice D 已完成：`recommended_tools` 已作为推荐子集进入 runtime metadata，并兼容映射到 `selected_tools_json`。
- Slice E 已完成：052 feature docs、blueprint 与 M4 split 已回写；后端定向回归与 lint 校验通过。
