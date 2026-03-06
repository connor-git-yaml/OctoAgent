# 需求质量检查表 — Feature 014: 统一模型配置管理

**生成日期**: 2026-03-04
**检查对象**: `.specify/features/014-unified-model-config/spec.md`
**检查版本**: Draft（2026-03-04）

---

## Content Quality（内容质量）

| # | 检查项 | 状态 | Notes |
|---|--------|------|-------|
| CQ-1 | 无实现细节（未提及具体语言、框架、API 实现方式） | [ ] | spec.md 第 248 行 NFR-003 提及"先写临时文件，再原子替换"——这是具体的技术实现策略（atomic write 模式），属于实现细节泄漏到需求层。需改为"写入操作须防止中断产生损坏文件"等面向业务结果的描述。 |
| CQ-2 | 聚焦用户价值和业务需求 | [x] | Problem Statement、User Stories 均以用户视角描述，成功标准以可观测结果表达，聚焦度良好。 |
| CQ-3 | 面向非技术利益相关者编写 | [ ] | FR-002 中出现 `api_key_env`（环境变量名）、FR-004 中出现 `master_key_env`、`litellm_proxy_url` 等字段名——这些是实现层字段命名，非技术利益相关者难以理解。需提升抽象层次，将字段名限制在 Key Entities 或 Appendix 中。此外 FR-005 直接提及 `model_list`、`litellm_params`，属于 LiteLLM 内部概念。 |
| CQ-4 | 所有必填章节已完成 | [x] | 包含：问题陈述、User Scenarios & Testing、Requirements（FR + NFR）、边界与排除、成功标准、Key Entities。结构完整。 |

**Content Quality 小计**: 2 / 4 通过

---

## Requirement Completeness（需求完整性）

| # | 检查项 | 状态 | Notes |
|---|--------|------|-------|
| RC-1 | 无 [NEEDS CLARIFICATION] 标记残留 | [ ] | 规范末尾 Clarifications 章节存在两处 **未解决** 的 `[NEEDS CLARIFICATION]` 标记：Q2（`.env.litellm` 处置策略）和 Q3（`runtime.llm_mode` 与 `.env` 中 `OCTOAGENT_LLM_MODE` 的语义边界）。这两个问题直接影响 FR-005（同步逻辑）和 FR-007（自动触发同步）的实现范围，必须在进入技术规划前解决。 |
| RC-2 | 需求可测试且无歧义 | [ ] | FR-007 使用 SHOULD 且未定义"自动触发同步"的触发时机精确性（命令执行前 / 执行后 / 成功时才触发？），存在实现歧义。FR-012（`octo config migrate`）使用 SHOULD，未说明迁移失败时的行为契约（是部分写入还是回滚）。 |
| RC-3 | 成功标准可测量 | [x] | SC-001 到 SC-007 均以可验证的输出状态或可执行的命令结果描述。SC-004 提及"5 分钟内完成迁移"为可量化时间指标。 |
| RC-4 | 成功标准是技术无关的 | [ ] | SC-004 中"通过 `octo doctor --live` 验证端到端 LLM 调用成功"依赖具体 CLI 命令，属于技术实现层的验收标准。成功标准应描述"用户能在 X 分钟内完成 Provider 切换并验证真实 LLM 服务可用"，而非绑定到具体命令名。 |
| RC-5 | 所有验收场景已定义 | [x] | 6 个 User Stories 均包含 3 个具体的 Given/When/Then 验收场景，覆盖正常路径和异常路径。Edge Cases 6 条均与对应 FR 关联。 |
| RC-6 | 边界条件已识别 | [x] | EC-1 到 EC-6 涵盖主要边界条件：语法错误、凭证缺失、文件覆盖冲突、不一致检测、disabled 状态别名、旧体系迁移。覆盖充分。 |
| RC-7 | 范围边界清晰 | [x] | Out of Scope 章节明确排除了 6 类内容（非模型配置、Web UI、热重载、多项目配置、废弃 octo init、Proxy 重启），描述具体。 |
| RC-8 | 依赖和假设已识别 | [ ] | 规范未显式声明对 Feature 003（`octo init` / `octo doctor` 已交付）的版本依赖。NFR-005 提及"与现有 `octo init` 命令共存"，但未列出前置条件表格。此外未说明 `packages/provider` 现有代码的兼容性假设（向后兼容边界由哪一层负责）。 |

**Requirement Completeness 小计**: 3 / 8 通过

---

## Feature Readiness（特性就绪度）

| # | 检查项 | 状态 | Notes |
|---|--------|------|-------|
| FR-A | 所有功能需求有明确的验收标准 | [ ] | FR-006（同步校验失败时拒绝写入）未在 User Stories 的验收场景中有 1:1 对应的 Given/When/Then 覆盖——EC-3 处理的是"文件已被手动修改"场景，而非 schema 校验失败场景。FR-013（`octo doctor` 新增检查项）在 Story 6 中有覆盖，但 FR-007（自动触发同步打印摘要）无专属验收场景。 |
| FR-B | 用户场景覆盖主要流程 | [x] | 6 个 User Stories 覆盖：查看配置（Story 1）、单一文件管理（Story 2）、增量添加 Provider（Story 3）、别名管理（Story 4）、配置同步（Story 5）、端到端验证（Story 6）。主要用户流程覆盖完整。 |
| FR-C | 功能满足 Success Criteria 中定义的可测量成果 | [x] | FR-001 到 FR-014 可追溯到 SC-001 到 SC-007。FR 与 SC 之间的 Traces to 标注提供了追踪链。Constitution Compliance Notes 补充了合规追踪。 |
| FR-D | 规范中无实现细节泄漏 | [ ] | 以下位置存在实现细节泄漏：(1) NFR-003 的原子写入策略（临时文件 + 原子替换）；(2) FR-001 字段名（`config_version`、`updated_at`、`providers`、`model_aliases`）；(3) FR-002/FR-004 中的具体字段名（`api_key_env`、`master_key_env`、`litellm_proxy_url`）；(4) FR-005 中的 LiteLLM 内部字段（`model_list`、`litellm_params`）。这些应移至 Technical Design（plan.md）层。 |

**Feature Readiness 小计**: 2 / 4 通过

---

## 专项检查（Contextual Checks）

### 需求完整性：FR 是否覆盖所有 User Stories

| User Story | 对应 FR | 覆盖评估 |
|------------|---------|---------|
| Story 1 - 非破坏性查看与更新配置 | FR-008, FR-009, FR-010, FR-011 | [x] 充分覆盖 |
| Story 2 - 单一配置文件统一管理 | FR-001, FR-002, FR-003, FR-004, FR-007, FR-014 | [x] 充分覆盖 |
| Story 3 - 增量添加新 Provider | FR-007, FR-008, FR-010 | [x] 充分覆盖 |
| Story 4 - 透明的模型别名管理 | FR-003, FR-008 | [x] 充分覆盖（alias set/list 在 FR-008 中定义） |
| Story 5 - 配置驱动的 LiteLLM 配置生成 | FR-005, FR-006, FR-007 | [ ] **部分覆盖**：Story 5 的 AC-1 提及 `general_settings.master_key`，该字段未在任何 FR 中定义为规范性要求，存在覆盖缺口。 |
| Story 6 - 配置完成后验证全链路 LLM 调用 | FR-013 | [ ] **部分覆盖**：Story 6 AC-2 要求检测两文件不一致并展示"具体的差异信息"，FR-013 仅说明"报告 WARN 并建议 sync"，未要求差异详情，存在验收标准与 FR 的细节差异。 |

**覆盖评估小计**: 4 / 6 充分覆盖

---

### 验收标准是否可测（是否可以写测试用例验证）

| 场景 | 可测性评估 | 问题描述 |
|------|-----------|---------|
| Story 1 AC-1：显示配置摘要不修改文件 | [x] 可测 | 可通过 CLI 输出断言 + 文件 mtime 比对验证 |
| Story 1 AC-2：仅更新 API Key | [x] 可测 | 可通过文件内容 diff 验证 |
| Story 1 AC-3：无 yaml 时引导 | [x] 可测 | 可通过 CLI 退出码和输出内容验证 |
| Story 3 AC-3：禁用 Provider | [x] 可测 | 可通过 yaml 字段断言 + litellm-config.yaml diff 验证 |
| Story 5 AC-1：sync 生成 litellm-config.yaml | [x] 可测 | 可通过文件内容结构断言验证 |
| Story 5 AC-3：语法错误不覆盖现有文件 | [x] 可测 | 可通过注入损坏 yaml 后验证目标文件 mtime 未变化 |
| Story 6 AC-1：`--live` 验证端到端调用成功 | [ ] **依赖外部**：需要真实 LLM Provider 连接，不可在 CI 单元测试中独立验证，需分离为集成测试场景并标注外部依赖。 |
| Story 6 AC-3：区分 Proxy 层失败与 Provider 层失败 | [ ] **歧义**：规范未定义"区分"的可观测形式（退出码差异、输出格式、结构化错误类型），导致测试用例无法用唯一标准验证"区分"成功。 |

**可测性评估小计**: 6 / 8 可独立测试

---

### 边界是否清晰（Out of Scope 是否充分）

| 边界项 | 评估 |
|--------|------|
| 非模型配置排除（Telegram 等） | [x] 明确 |
| Web UI 排除 | [x] 明确 |
| 热重载排除 | [x] 明确 |
| 多用户/多项目配置排除 | [x] 明确 |
| 废弃 `octo init` 排除 | [x] 明确 |
| 自动触发 Proxy 重启排除 | [x] 明确 |
| `.env` / `.env.litellm` 的最终处置策略 | [ ] **未澄清**：Out of Scope 中未说明旧三文件在 F014 交付后的生命周期（是否废弃、是否保留、是否仍被 LiteLLM Proxy 读取）。Q2 为 [NEEDS CLARIFICATION] 状态，该问题直接影响 FR-005 的实现边界，且未纳入 Out of Scope 说明中。 |

**边界清晰度评估小计**: 6 / 7 明确

---

### 非功能需求（NFR）是否可量化

| NFR | 量化评估 | 问题描述 |
|-----|---------|---------|
| NFR-001：sync < 1 秒 | [x] 可量化 | 明确时间上限，可用性能测试验证 |
| NFR-002：schema 校验错误须人类可读（含字段路径和期望类型） | [x] 可量化 | 可通过注入错误 yaml 断言输出内容包含字段路径 |
| NFR-003：原子性写入 | [ ] **实现细节混入** | "先写临时文件，再原子替换"是实现方案，不是 NFR 的验收标准。应改为可观测的失败防护要求，如"写入中断后原有文件不得损坏"，并移除实现细节。 |
| NFR-004：禁止明文凭证写入配置文件 | [x] 可量化 | 可通过尝试写入包含 `sk-` 前缀值断言系统拒绝 |
| NFR-005：与 `octo init` / `octo doctor` 共存 | [x] 可量化 | 可通过回归测试验证 Feature 003 功能不受影响 |
| NFR-006：向前兼容读取旧版本 config_version | [ ] **测试困难** | 仅用 SHOULD，且未定义"向前兼容"的最低兼容 config_version 版本范围，导致无法写出完整的向前兼容测试用例。 |

**NFR 量化评估小计**: 3 / 6 完全可量化

---

### 与 Feature 013 的冲突和依赖分析

| 维度 | 评估 | 说明 |
|------|------|------|
| 功能冲突 | [x] 无冲突 | Feature 013 是 M1.5 集成验收特性，聚焦于消息路由、检查点恢复、Watchdog、链路追踪四个验收域，与 F014 的配置管理域无重叠。 |
| 接口冲突 | [x] 无冲突 | Feature 013 不涉及 CLI 命令、octoagent.yaml、litellm-config.yaml 等 F014 核心产物。 |
| 环境依赖冲突 | [ ] **潜在风险** | Feature 013 的 LiteLLM Proxy 调用依赖现有的三文件体系（`.env` + `.env.litellm` + `litellm-config.yaml`），若 F014 的交付导致旧三文件被废弃或位置变更，Feature 013 的验收测试环境将受影响。两个特性的交付顺序和环境兼容策略需在 plan.md 中明确。 |
| 共享基础设施 | [ ] **潜在风险** | Feature 013 Story 1 要求系统以"真实组件"启动，若 F014 修改了 LiteLLM Proxy 的配置格式，Feature 013 的集成测试可能无法使用未同步的旧配置文件通过验收。需确认 F014 交付时是否对现有集成测试环境提供迁移支持。 |

---

### 向后兼容性风险

| 风险项 | 风险等级 | 说明 |
|--------|---------|------|
| `octo init` 行为变更 | 低 | NFR-005 和 Out of Scope 明确保留 `octo init`，两者并存。风险可控。 |
| `octo doctor` 检查项新增 | 低 | FR-013 为新增检查项（SHOULD 级别），不修改现有检查项逻辑，向后兼容。 |
| `litellm-config.yaml` 生成格式 | 中 | F014 引入基于 `octoagent.yaml` 推导的 `litellm-config.yaml` 自动生成。若生成格式与 Feature 013 集成测试期望的格式不一致，将造成兼容性问题。规范未说明生成的 `litellm-config.yaml` 须与现有手写格式保持 schema 兼容。 |
| `.env.litellm` 处置策略 | 高 | **最高风险项**：Q2 未解决。若 F014 交付后 `.env.litellm` 仍被 LiteLLM Proxy 依赖但规范未明确其生命周期，将导致新旧体系并存的配置冲突，影响所有依赖 LiteLLM Proxy 的现有特性（F008、F009、F010、F011、F012、F013）。 |
| `packages/provider` 兼容性 | 中 | 规范未说明 F014 引入的 `UnifiedConfig` 数据模型与现有 `packages/provider` 代码的边界。现有代码是否直接读取旧配置文件？F014 交付后是否需要修改 `packages/provider`？ |

---

## 汇总

| 维度 | 通过 | 总计 | 通过率 |
|------|------|------|--------|
| Content Quality | 2 | 4 | 50% |
| Requirement Completeness | 3 | 8 | 38% |
| Feature Readiness | 2 | 4 | 50% |
| **合计** | **7** | **16** | **44%** |

---

## 未通过项汇总（必须修复）

以下未通过项阻止进入技术规划阶段：

### 阻断级（必须修复后重新审查）

1. **[RC-1] 存在 2 处未解决的 [NEEDS CLARIFICATION] 标记**
   - Q2：`.env.litellm` 的处置策略（直接影响 FR-005 的同步范围定义）
   - Q3：`runtime.llm_mode` 与 `.env` 中运行时变量的单一信息源边界
   - **修复建议**：回到 clarify 阶段，与产品负责人确认后在 Clarifications 中标记 AUTO-RESOLVED 并补充决策理由。

2. **[RC-8] 依赖和假设未显式声明**
   - 缺少对 Feature 003 前置条件的正式声明（类似 Feature 013 的前置条件表格）
   - 缺少对 `packages/provider` 向后兼容假设的边界定义
   - **修复建议**：在 spec.md 中新增"前置条件（Pre-conditions）"章节，参照 Feature 013 格式列出 F003 和 `packages/provider` 的兼容要求。

3. **[F013 兼容风险] `.env.litellm` 处置策略与 Feature 013 集成测试环境冲突**
   - F013 集成验收依赖现有三文件体系；F014 的 `.env.litellm` 处置策略未定，存在高概率破坏 F013 验收环境
   - **修复建议**：在解决 Q2 时同步明确 F014 与 F013 的交付顺序约束，并在 Pre-conditions 或 Out of Scope 中注明"F014 不修改 F013 已验证的测试环境配置"。

### 重要级（建议修复，影响实现准确性）

4. **[CQ-1 / FR-D] 实现细节泄漏**
   - NFR-003 原子写入策略、FR-001/FR-002/FR-004/FR-005 的具体字段名和 LiteLLM 内部字段
   - **修复建议**：将字段命名移至 Key Entities 的属性描述中（保留概念级别），LiteLLM 内部字段（`model_list`、`litellm_params`）移至 plan.md。NFR-003 改为面向结果的描述。

5. **[RC-2] FR-007 和 FR-012 的行为契约不完整**
   - FR-007 自动触发的精确时机（成功时 / 任意时）未定义
   - FR-012 迁移失败的回滚行为未定义
   - **修复建议**：在对应 FR 中补充失败路径的行为定义。

6. **[CQ-3] 技术术语面向非技术利益相关者不友好**
   - FR 中大量出现环境变量名（`api_key_env`、`master_key_env`）和 LiteLLM 内部概念
   - **修复建议**：将技术字段名抽象为概念描述，或在 Key Entities 章节集中定义后在 FR 中引用概念名。

7. **[Story 6 AC-3] 测试歧义**
   - "区分 Proxy 层失败与 Provider 层失败"缺乏可观测的区分形式定义
   - **修复建议**：补充错误输出的结构描述或分类标准，使测试用例可以唯一判定"区分成功"。

---

## 执行摘要

**阶段**: 质量检查表
**状态**: 失败
**产出制品**: `.specify/features/014-unified-model-config/checklists/requirements.md`
**关键发现**: 16 项检查，7 项通过，9 项未通过（通过率 44%）
**后续建议**: 存在 3 个阻断级问题（2 处未解决的 NEEDS CLARIFICATION + 依赖声明缺失 + F013 兼容风险），必须回到 clarify 阶段修复后重新执行质量检查。同时建议修复 4 个重要级问题（实现细节泄漏、FR 行为契约不完整、术语面向用户友好性、测试歧义），以提高技术规划阶段的准确性。
