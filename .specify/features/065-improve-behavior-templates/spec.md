---
feature_id: "065"
feature_slug: improve-behavior-templates
research_mode: codebase-scan
status: draft
created: 2026-03-19
---

# Feature Specification: 全面改进 Behavior 默认模板内容

**Feature Branch**: `claude/bold-aryabhata`
**Created**: 2026-03-19
**Status**: Draft
**Input**: 改进 9 个行为文件（Behavior MD）的默认模板，参考 OpenClaw/Agent Zero 颗粒度，结合 OctoAgent 三层 Agent 架构实际能力，将过于简单的模板扩展为包含决策框架、安全红线、内存协议、委派规范、引导仪式、项目结构化模板等可操作内容。

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 新 Agent 首次启动时获得充分的角色与协作指令 (Priority: P1)

当用户创建新的 Agent profile（Butler 或 Worker），Agent 在首次加载行为文件时，AGENTS.md 应提供足够详细的角色定义、委派决策框架和协作规范，使 Agent 无需额外提示即可正确定位自身角色、做出合理的委派/自行处理判断。

**Why this priority**: AGENTS.md 是 Agent 行为的核心锚点，直接决定了 Agent 是否理解三层架构（Butler/Worker/Subagent）的分工关系、何时委派、何时自行处理。当前模板过于简略（Butler 版约 340 字符 vs 3200 预算），导致 Agent 缺乏关于委派时机、A2A 状态机交互、安全红线等关键指令。

**Independent Test**: 以默认模板创建一个新 Butler profile，检查 AGENTS.md 内容是否包含角色定位段落、委派决策框架段落、安全红线段落、内存协议段落，且总长度在 3200 字符预算内。

**Acceptance Scenarios**:

1. **Given** 系统首次为 Butler 创建行为文件, **When** `_default_content_for_file(file_id="AGENTS.md", is_worker_profile=False)` 被调用, **Then** 返回内容包含以下内容域：角色定位、委派决策框架（何时委派 vs 自行处理）、安全红线（不可做的事）、内存协议（事实存哪里）、A2A 状态机感知，且字符数不超过 3200。
2. **Given** 系统首次为 Worker 创建行为文件, **When** `_default_content_for_file(file_id="AGENTS.md", is_worker_profile=True)` 被调用, **Then** 返回内容包含 Worker 视角的角色定位、任务执行纪律、与 Butler 的协作协议、自主范围边界，且字符数不超过 3200。
3. **Given** 已有用户自定义过 AGENTS.md 的 Agent, **When** 系统加载行为文件, **Then** 用户自定义内容优先于默认模板，默认模板仅在文件不存在时使用。

---

### User Story 2 - 引导仪式帮助用户完成初始化设置 (Priority: P1)

首次进入项目时，BOOTSTRAP.md 提供结构化的引导仪式脚本，引导用户逐步完成必要的初始化配置（称呼、偏好、Agent 性格等），每一步明确告知下游存储位置（Memory/Behavior/Secrets），引导完成后自标记关闭。

**Why this priority**: BOOTSTRAP.md 是用户首次体验的入口点，决定了 onboarding 质量。当前模板已有引导框架但步骤不够结构化，缺少分步提问序列和数据流向提示。

**Independent Test**: 创建全新项目，触发 Bootstrap 流程，验证 BOOTSTRAP.md 包含编号步骤的引导脚本，每步标注数据去向，且包含完成标记机制。

**Acceptance Scenarios**:

1. **Given** 用户首次进入新项目, **When** BOOTSTRAP.md 被加载, **Then** 内容包含编号的引导步骤序列（不少于 4 步），每步包含提问内容和数据存储去向说明。
2. **Given** 引导仪式设计, **When** 检查 BOOTSTRAP.md 模板, **Then** 最后包含"完成引导"标记机制的说明（behavior.write_file 替换为 `<!-- COMPLETED -->`）。
3. **Given** BOOTSTRAP.md 内容, **When** 计算字符长度, **Then** 不超过 2200 字符预算。

---

### User Story 3 - Agent 工具使用遵循清晰的优先级和安全规范 (Priority: P1)

TOOLS.md 提供工具使用的优先级决策树和安全边界规范，使 Agent 在面对多种可用工具时能按正确顺序选择（受治理工具 > terminal > 外部调用），并遵守 secrets 隔离、path manifest 优先等原则。

**Why this priority**: TOOLS.md 控制 Agent 的工具选择行为，直接影响安全性（secrets 泄露风险）和效率（是否选择最优工具）。当前模板虽然涵盖了关键原则，但缺少结构化的决策优先级和分场景指引。

**Independent Test**: 审查 TOOLS.md 模板内容是否包含工具选择优先级列表、secrets 安全边界规范、delegate 信息整理规范、读写场景指引。

**Acceptance Scenarios**:

1. **Given** TOOLS.md 默认模板, **When** 检查内容, **Then** 包含工具选择优先级（至少 3 级）、secrets 安全红线、path manifest 使用规范、delegate 信息整理规范。
2. **Given** TOOLS.md 内容, **When** 计算字符长度, **Then** 不超过 3200 字符预算。

---

### User Story 4 - Agent 具有稳定的人格和沟通风格 (Priority: P1)

SOUL.md 和 IDENTITY.md 共同定义 Agent 的核心价值观、沟通风格、边界感和自我认知，使 Agent 在不同会话中保持一致的"人格"。

**Why this priority**: SOUL.md（当前仅 27 字符 vs 1600 预算）和 IDENTITY.md（当前约 80 字符 vs 1600 预算）是利用率最低的两个文件。它们共同决定 Agent 的"性格"一致性，对用户体验的连续性有直接影响。

**Independent Test**: 检查 SOUL.md 是否包含核心价值观列表、沟通风格描述、边界声明；IDENTITY.md 是否包含结构化字段（名称、角色定位、表达风格、自我认知）。

**Acceptance Scenarios**:

1. **Given** SOUL.md 默认模板, **When** 检查内容, **Then** 包含核心价值观（不少于 3 条）、沟通原则、认知边界声明，字符数不超过 1600。
2. **Given** IDENTITY.md 默认模板（Butler 版）, **When** 检查内容, **Then** 包含结构化的身份字段（名称、角色定位、表达风格）和自我修改权限说明，字符数不超过 1600。
3. **Given** IDENTITY.md 默认模板（Worker 版）, **When** 检查内容, **Then** 角色定位字段反映 specialist worker 身份。

---

### User Story 5 - 用户画像和项目上下文有渐进式结构化模板 (Priority: P2)

USER.md 和 PROJECT.md 提供结构化的渐进式填充模板，帮助 Agent（或用户手动编辑时）按一致的结构维护用户偏好和项目上下文。

**Why this priority**: USER.md（当前约 90 字符 vs 1800 预算）和 PROJECT.md（当前约 60 字符 vs 2400 预算）内容极少，缺乏引导用户或 Agent 渐进补充信息的结构化框架。提供模板可以避免信息散落和重复，但不如角色定义和安全规范紧迫。

**Independent Test**: 检查 USER.md 是否包含分区的用户画像框架（基本信息、沟通偏好、工作习惯等占位区域）；PROJECT.md 是否包含项目元信息框架（目标、关键术语、验收标准、目录结构等占位区域）。

**Acceptance Scenarios**:

1. **Given** USER.md 默认模板, **When** 检查内容, **Then** 包含分区的用户画像占位框架（至少 3 个信息区域），且明确标注"稳定事实应进入 Memory"的存储边界提示，字符数不超过 1800。
2. **Given** PROJECT.md 默认模板, **When** 检查内容, **Then** 包含项目元信息占位框架（至少 4 个信息区域：目标、术语表、关键目录、验收标准），字符数不超过 2400。

---

### User Story 6 - 知识管理有入口地图而非内容堆砌 (Priority: P2)

KNOWLEDGE.md 提供知识入口的结构化模板，引导 Agent 维护"阅读地图"而非复制正文，包含文档类别、引用方式和更新时机的指引。

**Why this priority**: KNOWLEDGE.md（当前约 50 字符 vs 2200 预算）过于简略。作为知识管理入口，需要指引 Agent 如何组织知识引用而非堆砌全文，但其影响范围相对局限于信息组织维度。

**Independent Test**: 检查 KNOWLEDGE.md 是否包含文档分类框架、引用规范、更新策略提示。

**Acceptance Scenarios**:

1. **Given** KNOWLEDGE.md 默认模板, **When** 检查内容, **Then** 包含知识入口的分类框架（至少 3 个类别占位）、canonical 引用规范、更新触发条件提示，字符数不超过 2200。

---

### User Story 7 - 长任务有结构化的自检和进度报告规范 (Priority: P2)

HEARTBEAT.md 提供长任务执行期间的自检清单和进度报告规范，帮助 Agent 在长时间运行时定期评估任务状态、识别停滞、适时报告进度和主动收口。

**Why this priority**: HEARTBEAT.md（当前约 55 字符 vs 1600 预算）极为简略。长任务自检直接影响任务可观测性和用户对 Agent 的信任度，但仅在长任务场景下生效，日常短交互影响有限。

**Independent Test**: 检查 HEARTBEAT.md 是否包含自检频率指引、检查项清单、进度报告格式指引、收口时机判断标准。

**Acceptance Scenarios**:

1. **Given** HEARTBEAT.md 默认模板, **When** 检查内容, **Then** 包含自检触发条件、检查项清单（至少 4 项）、进度报告要素、收口判断标准，字符数不超过 1600。

---

### Edge Cases

- **字符预算溢出**: 模板改进后某个文件默认内容超过 `BEHAVIOR_FILE_BUDGETS` 中定义的上限时，系统应如何处理？当前 truncation 机制应能兜底，但需确保新模板在设计阶段就控制在预算内。
- **Worker vs Butler 差异化不足**: AGENTS.md 和 IDENTITY.md 有 `is_worker_profile` 分支，其他文件（如 TOOLS.md、HEARTBEAT.md）在 Worker 场景下内容是否也需要差异化？[AUTO-RESOLVED: 当前架构中 Worker 与 Butler 共享大部分行为文件（TOOLS/HEARTBEAT/SOUL 等），仅 AGENTS.md 和 IDENTITY.md 有 Worker-specific 分支。本次保持现有分支策略，不新增差异化分支，避免复杂度膨胀]
- **用户已自定义内容的迁移**: 改进默认模板后，已有用户自定义过行为文件的 Agent 不受影响（自定义内容优先于默认模板）。但用户可能希望参考新模板重新编辑自定义文件 -- 此场景不在 Feature 065 范围内。
- **多语言适配**: 默认模板使用中文。如未来需要英文或其他语言版本，需要额外的 i18n 机制。[AUTO-RESOLVED: OctoAgent 当前面向中文用户，所有模板保持中文，与 CLAUDE.md 语言规范一致]
- **BOOTSTRAP.md 已完成标记的幂等性**: 如果引导已完成（文件已被替换为 `<!-- COMPLETED -->`），后续模板改进不会影响已完成的引导状态。
- **模板内容与 system prompt 其他部分的冲突**: 行为文件内容会被注入 system prompt。如果模板中的指令与 provider 层的 system prompt 指令矛盾，应以哪个为准？当前架构中 Behavior 最高优先级（参考 Agent Zero 的 insert 到 system_prompt[0]），模板设计需避免与上游 system prompt 冲突。
- **project_label 和 agent_name 占位符**: `_default_content_for_file` 接收 `project_label` 和 `agent_name` 参数用于模板插值。改进后的模板如需引用更多动态变量，需评估是否扩展函数签名。

---

## Requirements *(mandatory)*

### Functional Requirements

**AGENTS.md 内容域**

- **FR-001**: AGENTS.md（Butler 版）默认模板 MUST 包含以下内容域：(a) 角色定位与三层架构感知, (b) 委派决策框架（自行处理 vs 委派 Worker 的判断准则）, (c) 安全红线（不可执行的动作类型）, (d) 内存与存储协议（事实/偏好/秘密分别存放的位置指引）, (e) A2A 状态机交互感知。
- **FR-002**: AGENTS.md（Worker 版）默认模板 MUST 包含以下内容域：(a) Worker 角色定位与自主范围, (b) 与 Butler 的协作协议（如何接收委派、如何报告完成/失败）, (c) 任务执行纪律（围绕 delegate objective 执行，不偏离）, (d) Subagent 创建的判断准则。
- **FR-003**: AGENTS.md 两个版本的默认内容 MUST 各自不超过 3200 字符。

**USER.md 内容域**

- **FR-004**: USER.md 默认模板 MUST 包含渐进式用户画像框架，至少覆盖基本信息区、沟通偏好区、工作习惯区，每个区域提供占位提示。
- **FR-005**: USER.md 默认模板 MUST 在显著位置标注存储边界：稳定事实应通过 Memory 服务存储，此文件仅维护高频引用的偏好摘要。
- **FR-006**: USER.md 默认内容 MUST 不超过 1800 字符。

**PROJECT.md 内容域**

- **FR-007**: PROJECT.md 默认模板 MUST 包含项目元信息框架，至少覆盖：项目目标、关键术语表、核心目录结构、验收标准。
- **FR-008**: PROJECT.md 默认模板 SHOULD 保留 `project_label` 动态插值点。
- **FR-009**: PROJECT.md 默认内容 MUST 不超过 2400 字符。

**KNOWLEDGE.md 内容域**

- **FR-010**: KNOWLEDGE.md 默认模板 MUST 包含知识入口地图框架，至少覆盖：canonical 文档引用区、API/接口文档区、运维/部署知识区。
- **FR-011**: KNOWLEDGE.md 默认模板 MUST 明确指引"引用入口而非复制正文"的原则。
- **FR-012**: KNOWLEDGE.md 默认内容 MUST 不超过 2200 字符。

**TOOLS.md 内容域**

- **FR-013**: TOOLS.md 默认模板 MUST 包含工具选择优先级规范（受治理工具 > terminal > 外部调用），包含至少 3 级优先级。
- **FR-014**: TOOLS.md 默认模板 MUST 包含 secrets 安全边界规范（不写入 behavior files、不写入 project.secret-bindings.json 的值字段、不传入 LLM 上下文）。
- **FR-015**: TOOLS.md 默认模板 MUST 包含 delegate 信息整理规范（不裸转发用户原话，整理为 objective + 上下文 + 工具边界）。
- **FR-016**: TOOLS.md 默认模板 SHOULD 包含读写场景的快速指引（只读问题用 filesystem 工具、事实更新用 Memory 工具等）。
- **FR-017**: TOOLS.md 默认内容 MUST 不超过 3200 字符。

**BOOTSTRAP.md 内容域**

- **FR-018**: BOOTSTRAP.md 默认模板 MUST 包含编号的引导步骤序列（不少于 4 步），每步明确：提问内容、预期回答类型、数据存储去向。
- **FR-019**: BOOTSTRAP.md 默认模板 MUST 在最后包含完成标记机制说明（behavior.write_file 替换为 `<!-- COMPLETED -->`）。
- **FR-020**: BOOTSTRAP.md 默认模板 SHOULD 在引导开始前包含简短的自我介绍话术模板。
- **FR-021**: BOOTSTRAP.md 默认内容 MUST 不超过 2200 字符。

**SOUL.md 内容域**

- **FR-022**: SOUL.md 默认模板 MUST 包含核心价值观列表（不少于 3 条），覆盖"结论优先""不装懂""边界明确"等原则。
- **FR-023**: SOUL.md 默认模板 MUST 包含沟通风格原则（稳定、可解释、协作）。
- **FR-024**: SOUL.md 默认模板 SHOULD 包含认知边界声明（哪些事情 Agent 会承认不确定或不知道）。
- **FR-025**: SOUL.md 默认内容 MUST 不超过 1600 字符。

**IDENTITY.md 内容域**

- **FR-026**: IDENTITY.md 默认模板 MUST 包含结构化身份字段：名称（动态插值）、角色定位（Butler/Worker 差异化）、表达风格占位。
- **FR-027**: IDENTITY.md 默认模板 MUST 包含自我修改权限说明（可提出 behavior proposal，默认不静默改写）。
- **FR-028**: IDENTITY.md 默认内容 MUST 不超过 1600 字符。

**HEARTBEAT.md 内容域**

- **FR-029**: HEARTBEAT.md 默认模板 MUST 包含自检触发条件说明（多长时间或多少步骤后进行自检）。
- **FR-030**: HEARTBEAT.md 默认模板 MUST 包含自检清单（至少 4 项），覆盖：任务进度、是否偏离目标、工具使用是否合理、是否应收口。
- **FR-031**: HEARTBEAT.md 默认模板 SHOULD 包含进度报告的要素指引（完成了什么、遇到什么阻碍、下一步计划）。
- **FR-032**: HEARTBEAT.md 默认内容 MUST 不超过 1600 字符。

**跨文件一致性**

- **FR-033**: 所有 9 个默认模板 SHOULD 在字符预算利用率上达到 40% 以上（当前多数文件不到 10%），MUST 不超过 95%。实际验收以各文件对应 FR 的内容域覆盖率为硬约束，字符利用率作为辅助参考指标。[AUTO-CLARIFIED: 内容域完整性比字符数更有意义；40% 作为 SHOULD 目标已是 3-4 倍提升]
- **FR-034**: 所有默认模板 MUST 使用中文散文、英文代码标识符的双语规范，与 CLAUDE.md 语言规范保持一致。
- **FR-035**: 所有默认模板的内容 MUST 反映 OctoAgent 实际架构能力（三层 Agent、A2A-Lite、Policy Engine、Memory 系统、Skill Pipeline、Event Sourcing），而非照搬 OpenClaw 或 Agent Zero 的概念。
- **FR-036**: 所有默认模板 MUST 保持现有的函数签名兼容性（`_default_content_for_file` 的参数列表不变：`file_id`, `is_worker_profile`, `agent_name`, `project_label`）。

### Key Entities

- **BehaviorWorkspaceFile**: 行为文件的运行时表示，包含 file_id、content、budget_chars、source_kind 等属性。默认模板改进直接影响 `source_kind="default_template"` 的文件内容。
- **BehaviorPackFile**: 行为文件打包后的传输表示，用于 system prompt 注入。模板内容最终通过此实体进入 LLM 上下文。
- **BEHAVIOR_FILE_BUDGETS**: 每个行为文件的字符预算上限映射表（dict）。所有模板改进 MUST 在此预算内。
- **_default_content_for_file**: 默认模板内容生成函数。Feature 065 的唯一代码变更点。

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 9 个行为文件的默认模板预算利用率 SHOULD 提升至 40% 以上（当前最高 14.5%），MUST 不超过 95%。核心验收以各 FR 内容域覆盖率为准。
- **SC-002**: 每个默认模板包含的内容域数量符合对应 FR 要求（通过内容域覆盖率检查验证）。
- **SC-003**: 无任何默认模板超过 `BEHAVIOR_FILE_BUDGETS` 中定义的字符预算上限。
- **SC-004**: 现有单元测试全部通过（`_default_content_for_file` 的已有测试不因内容变更而失败，如需调整断言则同步更新）。
- **SC-005**: 以默认模板创建的新 Agent，在首次交互中 SHOULD 能正确识别自身角色（Butler 或 Worker）、说明可用能力范围、遵守委派规范，无需额外人工提示。[AUTO-CLARIFIED: 此为定性集成验收标准，不纳入自动化测试；验收方式为手动创建 Agent 并发送测试消息观察回复]

---

## Clarifications

### Session 2026-03-19

以下歧义/缺失在需求澄清阶段被检测并自动解决。完整分析见 `checklists/clarify-report.md`。

| # | 问题 | 自动选择 | 理由 |
|---|------|---------|------|
| AMB-01 | FR-033 的 40%-95% 利用率区间应为 MUST 还是 SHOULD | 40% 为 SHOULD，上限 95% 为 MUST；内容域覆盖率为硬约束 | 内容域完整性比字符数更有意义 |
| AMB-02 | FR-022 价值观"结论优先""不装懂"是逐字要求还是示例 | 语义等价即可，不要求逐字匹配 | 模板内容是给 LLM 的指令文本，语义比字面更重要 |
| AMB-03 | FR-018 "预期回答类型"的含义 | 回答形式说明（如"自由文本""是/否"），非数据类型声明 | 引导脚本而非 API contract |
| MISS-01 | BOOTSTRAP.md 引导步骤的具体内容约束 | 至少覆盖现有 5 个维度（称呼/Agent 名/性格/时区/偏好），可增删但不少于 4 步 | 保持向前兼容 |
| MISS-02 | SC-005 不可自动化测试 | 作为 SHOULD 级定性集成验收标准，手动验证 | LLM 输出随机性使硬判定不可行 |
| MISS-03 | 模板 Markdown 格式使用规范 | 可用轻量 Markdown（标题/列表），避免过度装饰 | 结构化标记助于 LLM 解析指令层级 |
| MISS-04 | 模板间交叉引用策略 | 各文件独立完整，不使用交叉引用 | BehaviorLoadProfile 差异化加载不保证所有文件同时在上下文 |
| CONF-01 | FR-035 与 system prompt 冲突的处理 | 模板应避免与 provider 层指令矛盾，而非依赖优先级覆盖 | "避免冲突"优于"依赖覆盖" |
| TEST-02 | FR 中数量下限约束的验证方式 | 采用子字符串/关键词匹配，非精确结构解析 | 模板是指令文本，过度结构化限制内容质量 |
