# 代码质量审查报告

**Feature**: 065-improve-behavior-templates
**审查日期**: 2026-03-19
**审查范围**: `_default_content_for_file()` 函数及相关测试

---

## 四维度评估

| 维度 | 评级 | 关键发现 |
|------|------|---------|
| 设计模式合理性 | EXCELLENT | 变更严格遵循 plan.md 设计，单函数纯字符串替换，函数签名不变，无过度工程 |
| 安全性 | EXCELLENT | 模板中明确声明 secrets 安全红线，无敏感信息泄露，无 prompt injection 风险 |
| 性能 | N/A | 纯字符串返回函数，一次性调用，无运行时性能影响 |
| 可维护性 | GOOD | 模板结构化清晰，Markdown 格式规范，但部分模板预算利用率未达 SHOULD 目标 |

---

## 检查维度详细分析

### 1. 代码质量：模板字符串格式

**结论: PASS**

- 全部 11 个模板文本（9 个 file_id + 2 个 Worker 差异化分支）的 Markdown 格式验证通过
- 无未闭合的 code fence（PROJECT.md 中单个 code fence 正确闭合）
- 标题层级一致：所有模板使用 `##` 作为顶级标题，`###` 作为二级区域，符合 plan.md "结构化 Markdown" 设计原则
- 无拼写错误或乱码
- 中文散文 + 英文技术术语的双语规范与 CLAUDE.md 语言规范一致（FR-034 PASS）

### 2. 变量使用：f-string 插值

**结论: PASS**

已验证的插值点：

| 文件 | 变量 | 插值位置 | 状态 |
|------|------|---------|------|
| BOOTSTRAP.md | `{agent_name}` | 自我介绍话术 | 正确插值 |
| IDENTITY.md (Butler) | `{agent_name}` | 身份名称字段 | 正确插值 |
| IDENTITY.md (Worker) | `{agent_name}` | 身份名称字段 | 正确插值 |
| PROJECT.md | `{project_label}` | 项目标题 | 正确插值 |

其余 7 个模板不使用动态插值，已确认无残留的 `{agent_name}` 或 `{project_label}` 原始占位符。函数签名保持不变（FR-036 PASS）。

### 3. 预算合规

**结论: PASS（MUST），部分 SHOULD 未达标**

全量预算合规数据：

| 文件 | 分支 | 字符数 | 预算 | 利用率 | MUST<=100% | SHOULD>=40% |
|------|------|--------|------|--------|-----------|-------------|
| AGENTS.md | Butler | 1135 | 3200 | 35.5% | PASS | **未达标** |
| AGENTS.md | Worker | 1073 | 3200 | 33.5% | PASS | **未达标** |
| USER.md | Default | 637 | 1800 | 35.4% | PASS | **未达标** |
| PROJECT.md | Default | 737 | 2400 | 30.7% | PASS | **未达标** |
| KNOWLEDGE.md | Default | 696 | 2200 | 31.6% | PASS | **未达标** |
| TOOLS.md | Default | 1307 | 3200 | 40.8% | PASS | PASS |
| BOOTSTRAP.md | Default | 861 | 2200 | 39.1% | PASS | **未达标** (差 19 字符) |
| SOUL.md | Default | 576 | 1600 | 36.0% | PASS | **未达标** |
| IDENTITY.md | Butler | 481 | 1600 | 30.1% | PASS | **未达标** |
| IDENTITY.md | Worker | 491 | 1600 | 30.7% | PASS | **未达标** |
| HEARTBEAT.md | Default | 636 | 1600 | 39.8% | PASS | **未达标** (差 4 字符) |

- **MUST 约束（不超预算）**: 全部 11 个模板 PASS，最高利用率 40.8%（TOOLS.md）
- **SHOULD 目标（>=40%）**: 仅 TOOLS.md 达标；其余 10 个模板在 30.1%-39.8% 区间
- **测试下限（>=30%）**: 全部通过，与 `test_default_template_within_budget` 中的 `budget * 0.3` 断言一致

说明：FR-033 明确 40% 为 SHOULD 而非 MUST，且"内容域覆盖率为硬约束，字符利用率作为辅助参考"。当前模板已从原始 1.7%-14.5% 提升到 30%-41%，提升幅度 2-20 倍。内容域覆盖率全部达标（见测试结果），因此预算利用率不构成阻断问题。

### 4. 测试质量

**结论: GOOD**

#### 新增测试覆盖

| 测试类 | 用例数 | 覆盖范围 | 评价 |
|--------|--------|---------|------|
| TestDefaultTemplateBudgetCompliance | 11（参数化） | 全部 11 个模板的上限+下限预算检查 | 覆盖全面，断言具体 |
| TestDefaultTemplateContentDomains | 11 | 每个 FR 的内容域关键词匹配 | 关键词选择合理，非脆弱断言 |

断言质量评估：

- **预算测试**: 使用 `len(content) <= budget` 和 `len(content) >= int(budget * 0.3)` 双向约束，错误消息包含实际值和预算值，便于定位问题
- **内容域测试**: 使用 `or` 连接的多关键词匹配（如 `"委派" in content or "delegate" in content.lower()`），对中英文双语模板友好，不因措辞微调而脆弱
- **已有测试兼容**: `TestBootstrapTemplate.test_default_template_contains_completion_instructions` 仍然通过（"完成引导" 和 "<!-- COMPLETED -->" 关键词保留）

#### test_butler_behavior.py 中的硬编码断言

以下 4 个断言直接匹配模板中的具体字符串片段：

| 行号 | 断言字符串 | 匹配状态 | 风险评估 |
|------|-----------|---------|---------|
| 215 | `"specialist Worker"` | PASS | 低风险——角色定位核心术语 |
| 216 | `"Butler 负责默认会话总控"` | PASS | 低风险——角色定位核心句 |
| 420 | `"web / filesystem / terminal"` | PASS | 中风险——具体工具列表可能随版本变化 |
| 421 | `"委派决策框架"` | PASS | 低风险——章节标题 |
| 422 | `"specialist worker lane"` | PASS | 低风险——架构术语 |
| 423 | `"不要把用户原话原封不动转发过去"` | PASS | 中风险——具体指令措辞可能调整 |

### 5. 向后兼容

**结论: PASS**

- 函数签名完全不变：`_default_content_for_file(*, file_id, is_worker_profile, agent_name, project_label) -> str`
- 返回类型不变：`str`
- 已有全部 74 个测试通过（54 + 20），无回归
- 调用链不变：`bootstrap_files -> _default_content_for_file -> 写入磁盘`
- `BEHAVIOR_FILE_BUDGETS` 字典未修改
- `ALL_BEHAVIOR_FILE_IDS` 元组未修改

### 6. 安全性

**结论: PASS**

- **Secrets 防护**: TOOLS.md 模板明确列出 4 个禁写位置（behavior files、secret-bindings.json 值字段、LLM 上下文、日志），BOOTSTRAP.md 提示敏感信息走 secret bindings workflow，AGENTS.md（Butler/Worker）均有安全红线章节
- **Prompt injection 风险**: 模板内容为系统预设的中文 Markdown 指令文本，不包含用户输入的动态内容（`agent_name` 和 `project_label` 由系统内部提供，非用户直接输入到模板生成函数）。模板中无 `eval`、无动态代码执行、无外部 URL 引用
- **敏感信息泄露**: 模板中无硬编码 key/token/密码/路径等敏感值。唯一的动态值是 `agent_name` 和 `project_label`，均为非敏感的标识符
- **Constitution 合规**: 模板内容与 Constitution 13A（优先上下文非硬策略）高度对齐，安全红线章节呼应 Constitution 4（Two-Phase）和 5（Least Privilege）

---

## 问题清单

| 严重程度 | 维度 | 位置 | 描述 | 修复建议 |
|---------|------|------|------|---------|
| INFO | 可维护性 | behavior_workspace.py:1598 | Butler AGENTS.md 模板利用率 35.5%，未达 FR-033 的 40% SHOULD 目标 | FR-033 为 SHOULD 级别，内容域已全覆盖。如后续希望提升利用率，可在委派框架和 A2A 感知章节补充更多场景指引 |
| INFO | 可维护性 | behavior_workspace.py:1553 | Worker AGENTS.md 模板利用率 33.5%，是所有模板中利用率最低的之一 | 同上，可在协作协议和执行纪律章节补充更多边界场景 |
| INFO | 可维护性 | behavior_workspace.py:1846-1866 | IDENTITY.md（Butler/Worker）利用率约 30%，距离 40% SHOULD 目标差距最大 | 可考虑在自我认知章节补充更多关于上下文重建、会话连续性策略的指引 |
| INFO | 测试质量 | test_butler_behavior.py:420 | `"web / filesystem / terminal"` 硬编码断言匹配特定工具列表文本，若模板中工具列表措辞微调则需同步更新 | 低优先级。当前断言有效且通过，但未来如调整模板措辞需注意此处同步 |
| INFO | 测试质量 | test_butler_behavior.py:423 | `"不要把用户原话原封不动转发过去"` 硬编码断言匹配特定指令句子 | 同上。可考虑改为关键词匹配（如 `"原话" in content and "转发" in content`），但当前实现已满足需求 |

---

## 总体质量评级

**EXCELLENT**

评级依据:
- CRITICAL: 0 个
- WARNING: 0 个
- INFO: 5 个（均为 SHOULD 级建议，非阻断问题）

全部 74 个测试通过（54 + 20），函数签名向后兼容，Markdown 格式规范，f-string 插值正确，安全红线全面覆盖，无敏感信息泄露风险。5 个 INFO 级问题均为"可进一步优化"的建议，不影响当前实现的正确性和质量。

---

## 问题分级汇总

- CRITICAL: 0 个
- WARNING: 0 个
- INFO: 5 个
