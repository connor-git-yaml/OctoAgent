# 技术调研报告: Feature 005 — Pydantic Skill Runner

**特性分支**: `codex/feat-005-pydantic-skill-runner`
**调研日期**: 2026-03-02
**调研模式**: 独立模式（tech-only）
**产品调研基础**: [独立模式] 本次技术调研基于 `docs/blueprint.md`、`docs/m1-feature-split.md` 与现有代码库上下文执行

## 1. 调研目标

**核心问题**:
- 问题 1: SkillRunner 应如何在当前代码基线上落地「结构化输出 -> tool_calls -> ToolBroker 执行 -> 结果回灌」闭环？
- 问题 2: 如何在 M1 阶段吸收 AgentZero/OpenClaw 优秀实践，同时不引入过度复杂度？
- 问题 3: SkillRunner 的错误分流、循环终止与上下文预算防护如何设计，才能满足 Constitution C2/C4/C8/C11？
- 问题 4: 与 Feature 004 ToolBrokerProtocol 的集成边界如何冻结，降低 Feature 007 集成风险？

**需求范围（Feature 005）**:
- Must-have 1: SkillManifest（输入/输出模型、tools_allowed、retry_policy、tool_profile）
- Must-have 2: SkillRunner（输入校验、模型调用、输出校验、tool_calls 执行、结果回灌、重试）
- Must-have 3: SkillRegistry（注册/发现/元数据查询）
- Must-have 4: 生命周期事件链（MODEL_CALL + TOOL_CALL + SKILL_*）
- Must-have 5: 至少两个可验证示例 Skill（echo / file_summary）

## 2. 架构方案对比

### 2.1 当前基线能力扫描

| 模块 | 当前状态 | 对 005 的意义 |
|------|---------|---------------|
| `packages/tooling` | 已有 `ToolBrokerProtocol`、`ToolMeta`、`ToolResult`、Hook 链、事件落盘 | 005 可直接复用工具执行与审计能力 |
| `packages/provider` | `LiteLLMClient.complete()` 仅返回 `content` 文本，不暴露结构化 `tool_calls` | 005 需新增结构化调用适配或以 Pydantic AI 直连 Proxy |
| `packages/core` | 事件模型与 EventStore 稳定，已有 MODEL_CALL / TOOL_CALL 事件类型 | 005 可扩展 Skill 级事件并复用现有落盘机制 |
| `apps/gateway` | 以任务与 SSE 为主，尚无 Skill 执行入口 | 005 暂不需要 Gateway 改造即可先做包级能力 |

### 2.2 方案对比表（>=2）

| 维度 | 方案 A: Pydantic AI Native SkillRunner（推荐） | 方案 B: Provider 文本输出 + 自研 JSON 解析 | 方案 C: 直接上 Graph-Orchestrated Runner |
|------|----------------------------------------------|-------------------------------------------|-------------------------------------|
| 核心思路 | 新增 `packages/skills`，SkillRunner 直接使用 Pydantic AI structured output + ToolBrokerProtocol | 复用 `LiteLLMClient.complete()` 文本输出，再手工解析 JSON | 直接把 Skill 运行建模成 pydantic-graph 节点状态机 |
| 结构化输出可靠性 | 高（Pydantic 模型原生校验） | 中低（受模型文本偏差影响） | 高（但实现复杂） |
| 与 Blueprint §8.4 对齐 | 高 | 中 | 中高 |
| 与 AgentZero/OpenClaw 借鉴融合 | 高（可实现循环检测、异常分流、hook） | 中（可实现但实现成本高） | 中（更偏 Pipeline，弱化 free loop） |
| M1 交付复杂度 | 中 | 中高（要补大量防错） | 高 |
| 推荐结论 | ✅ 推荐 | ❌ 不推荐 | ⏸ M2 再做 |

### 2.3 推荐方案（A）关键设计

1. 包结构（新增）:
   - `octoagent/packages/skills/src/octoagent/skills/models.py`
   - `octoagent/packages/skills/src/octoagent/skills/manifest.py`
   - `octoagent/packages/skills/src/octoagent/skills/runner.py`
   - `octoagent/packages/skills/src/octoagent/skills/registry.py`
   - `octoagent/packages/skills/src/octoagent/skills/exceptions.py`

2. 运行闭环:
   - `InputModel` 校验
   - 模型调用（alias 走 LiteLLM Proxy）
   - `OutputModel` 校验
   - 存在 `tool_calls` 时逐个经 `ToolBrokerProtocol.execute()`
   - ToolResult 结构化回灌（保留 `parts` + `artifact_ref`）
   - 达到终止条件（`complete=True`/`skip_remaining_tools=True`/循环检测触发）

3. 与 004 契约耦合点（冻结）:
   - 只依赖 `ToolBrokerProtocol.execute(...) -> ToolResult`
   - 只消费锁定字段：`output/is_error/error/duration/artifact_ref`
   - 不直接耦合 ToolBroker 内部 Hook 实现

## 3. AgentZero / OpenClaw 借鉴映射

### 3.1 AgentZero 借鉴

| 来源模式 | 借鉴点 | 在 005 中的落地 |
|---------|--------|-----------------|
| `Response.break_loop` | 工具可终止当前 loop | `SkillOutput.complete: bool` + `skip_remaining_tools: bool` |
| 双层循环（monologue/message_loop） | 外层任务循环 + 内层工具循环 | SkillRunner 采用「step loop」迭代，限制 max_steps |
| 三层异常分流 | 可恢复/可修复/不可恢复分层 | `SkillRepeatError`、`SkillValidationError`、`ToolExecutionError` |
| Extension hooks | 生命周期可插拔 | `skill_start/end`、`before/after_llm_call`、`before/after_tool_execute` |
| SKILL.md 注入 | 长描述入上下文 | `SkillManifest.description_md`（可选文件加载） |

### 3.2 OpenClaw 借鉴

| 来源模式 | 借鉴点 | 在 005 中的落地 |
|---------|--------|-----------------|
| tool-loop-detection | 重复调用/振荡检测 | 首版实现「签名重复 N 次」检测（M1: N=3） |
| tool-result-context-guard | 上下文预算防护 | ToolResult 回灌前执行 budget check，超限走 `artifact_ref` 摘要 |
| ToolInputError 即时反馈 | 结构化错误返喂模型重试 | OutputModel 校验失败写入 `validation_feedback` 后重试 |
| AgentToolResult 多 Part | 输出不只 text | SkillRunner 的 tool feedback 支持 `parts`（text/file/json） |

## 4. 依赖评估

| 依赖 | 作用 | 状态 |
|------|------|------|
| `pydantic>=2.10,<3.0` | Input/Output/Manifest 强类型 | 已有 |
| `pydantic-ai-slim>=0.0.40` | structured output + tool calling 语义 | 已在 tooling 包中引入，可复用 |
| `octoagent-tooling` | ToolBrokerProtocol/ToolResult/ToolMeta | 已有（Feature 004 交付） |
| `octoagent-core` | EventStore / Event model / Task trace | 已有 |
| `structlog` | 生命周期结构化日志 | 已有 |

**结论**: 005 不需要新增第三方重依赖；可在现有依赖集合内完成。

## 5. 设计模式建议

1. **Template Method（SkillRunner）**:
   - 固定主流程：validate_input -> llm_step -> validate_output -> run_tools -> feedback -> finalize
   - 各 Skill 仅提供 InputModel/OutputModel 与 prompt/manifest

2. **Strategy（重试与终止策略）**:
   - `RetryPolicy`（max_attempts/backoff/upgrade_model_on_fail）
   - `LoopGuardPolicy`（max_steps/repeat_signature_threshold）

3. **Observer/Hook（可观测）**:
   - 生命周期 hook 统一发事件 + 日志
   - 对齐 AgentZero extension 思路，但以 typed hook interface 约束

## 6. 技术风险矩阵

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| Pydantic AI 与 LiteLLM Proxy 的 tool_calls 行为在不同 provider 不一致 | 中 | 高 | Phase 0 先做最小 PoC（echo + 1 tool_call）；失败则 fallback 到单 provider 验证 |
| OutputModel 过严导致频繁失败重试 | 中 | 中 | 引入结构化 validation feedback，限制 max_attempts=3，超限转 FAILED |
| 工具调用循环导致 token 爆炸 | 中 | 高 | 引入重复签名检测 + max_steps（默认 8） + budget guard |
| 005 与 004 接口漂移 | 低 | 高 | 严格绑定 `contracts/tooling-api.md` 锁定签名；契约测试先行 |
| 事件过载导致排障困难 | 中 | 中 | 事件 payload 结构化 + 摘要化，敏感字段脱敏，长文本走 artifact |

## 7. 需求-技术对齐度（独立模式）

| 需求点 | 覆盖结论 | 说明 |
|--------|----------|------|
| LLM->结构化输出->工具执行闭环 | 完全覆盖 | 方案 A 主链路直接覆盖 |
| 自动重试与错误可解释 | 完全覆盖 | 异常分流 + retry policy |
| 循环控制与完成信号 | 完全覆盖 | `complete/skip_remaining_tools` + repeat signature guard |
| 与 ToolBroker 集成 | 完全覆盖 | 基于 Protocol 契约集成 |
| 可观测与审计 | 完全覆盖 | Skill 级事件 + 现有 EventStore |

## 8. 推荐结论

**推荐采用方案 A（Pydantic AI Native SkillRunner）**，并按以下顺序落地：

1. 先交付 `SkillManifest + SkillRunner + SkillRegistry` 的最小闭环（echo_skill）。
2. 再接入 `ToolBrokerProtocol` 完成 `file_summary_skill`（mock broker）。
3. 最后补齐循环检测、上下文预算防护、生命周期 hooks 与事件链。

**对后续阶段的直接建议**:
- Spec 阶段强制写入以下非功能要求：`max_steps`、`repeat_signature_threshold`、`context_budget_limit`。
- Plan 阶段将 Feature 005 的代码范围控制在 `packages/skills`，避免跨 app 层改动。
- 实现阶段先做契约测试，再做集成测试，确保 007 集成可替换性。
