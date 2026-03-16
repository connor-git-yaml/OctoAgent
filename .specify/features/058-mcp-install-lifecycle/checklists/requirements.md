# Requirements Quality Checklist

**Feature**: 058-mcp-install-lifecycle (MCP 安装与生命周期管理)
**Spec Version**: Draft (2026-03-16)
**Checked By**: Quality Checklist Sub-Agent
**Check Date**: 2026-03-16

---

## Content Quality

- [x] 无实现细节（未提及具体语言、框架、API 实现方式）
  - **Notes**: **未通过 — 存在大量实现细节泄漏**。规范中多处涉及具体实现层面的内容：
    1. 背景/关键约束中引用了具体类名和内部链路：`McpRegistryService`、`SkillRunner -> LiteLLMSkillClient -> ToolBroker -> handler`、`mcp.{server}.{tool}` 命名空间格式（第 13、26-29 行）
    2. FR-001 提及 `独立 node_modules 目录`、`command/args/cwd`（第 178 行）
    3. FR-002 提及 `创建独立虚拟环境`、`command 指向 venv 内的可执行文件`（第 180 行）
    4. FR-003 提及 `npm 包独立 node_modules，pip 包独立 venv`（第 182 行）
    5. FR-005 提及 `mcp-servers.json` 具体文件名（第 189 行）
    6. FR-018 提及 `stdio 或 HTTP 连接`（第 230 行）
    7. FR-020 引用 `ToolBroker`、`Profile/Policy/Event/Hook`（第 237 行）
    8. FR-021 引用 `McpInstallerService`、`McpRegistryService` 具体服务名（第 239 行）
    9. FR-022 引用 `Event Store` 具体组件名（第 241 行）
    10. Key Entities 定义了具体数据模型名 `McpInstallRecord`、`McpServerConfig`、`InstallWizardState`（第 246-249 行）
    11. EC-02 引用 `ToolBroker 的 mcp.{server}.{tool} 命名空间机制`（第 163 行）
    12. User Story 6 Scenario 1 引用 `从 ToolBroker 注销其工具`（第 125 行）
  - **Verdict**: FAIL

- [ ] 聚焦用户价值和业务需求
  - **Notes**: User Stories 部分较好地聚焦了用户价值（每个 Story 以用户视角描述 Why），但 Requirements 部分（尤其 FR-020 到 FR-022 的"系统集成"类）更像是技术设计约束而非用户需求。Key Entities 更是纯技术设计产物。
  - **Verdict**: PARTIAL PASS — User Stories 部分合格，但 Requirements 和 Key Entities 偏向技术设计

- [x] 面向非技术利益相关者编写
  - **Notes**: User Stories 和 Acceptance Scenarios 使用了用户视角的语言，非技术读者可以理解核心需求。但关键约束（第 24-29 行）、Key Entities（第 246-249 行）和部分 FR 中的技术术语会让非技术读者困惑。
  - **Verdict**: PARTIAL PASS — 主体部分可读，但穿插了多处技术细节

- [x] 所有必填章节已完成
  - **Notes**: 规范包含：背景与动机、User Scenarios & Testing（含 8 个 User Stories + Edge Cases）、Requirements（含 Functional Requirements + Key Entities）、Success Criteria。所有必填章节均已完成。
  - **Verdict**: PASS

**Content Quality 综合判定**: FAIL
- 理由: 第一项"无实现细节"严重不通过。规范中存在大量具体类名（McpInstallerService, McpRegistryService, ToolBroker）、内部链路描述（SkillRunner -> LiteLLMSkillClient -> ToolBroker -> handler）、具体文件名（mcp-servers.json）、具体数据模型定义（McpInstallRecord, McpServerConfig）。这些内容属于技术设计/plan 阶段产物，不应出现在需求规范中。

---

## Requirement Completeness

- [x] 无 [NEEDS CLARIFICATION] 标记残留
  - **Notes**: 全文搜索确认无 `[NEEDS CLARIFICATION]` 标记。存在两处 `[AUTO-RESOLVED]` 标记（第 94 行、第 165 行），这些是已决策的标记，属正常。
  - **Verdict**: PASS

- [x] 需求可测试且无歧义
  - **Notes**: 大部分需求具有清晰的 Given/When/Then 场景，可测试性良好。但部分需求表述不够精确：
    - FR-008 "自动检测并重建连接" — 未明确检测机制和重建时机（是调用时检测还是周期检测？）
    - FR-012 "不允许出现长时间无反馈的等待" — "长时间"在 SC-005 中定义为 5 秒，但 FR-012 本身未交叉引用
  - **Verdict**: PASS（小瑕疵不影响整体可测试性）

- [x] 成功标准可测量
  - **Notes**: SC-001 到 SC-006 均提供了可测量指标：
    - SC-001: 3 分钟时间上限
    - SC-002: 延迟对比（与现有手动配置对比）
    - SC-003: 自动恢复，无需手动干预
    - SC-004: 重启后自动恢复
    - SC-005: 5 秒反馈时限
    - SC-006: 30 天内 80% 采用率
  - **Verdict**: PASS

- [ ] 成功标准是技术无关的
  - **Notes**: SC-001 到 SC-005 均以用户体验和系统行为描述，技术无关。SC-006 以用户行为指标描述，同样技术无关。
  - **Verdict**: PASS

- [x] 所有验收场景已定义
  - **Notes**: 8 个 User Stories 共 20 个 Acceptance Scenarios，覆盖了主要流程。7 个 Edge Cases 覆盖了异常情况。但缺少以下场景：
    - 安全相关：安装包含恶意代码的 MCP server 时系统如何保护？
    - 安全相关：MCP server 尝试访问受限文件路径时的行为？
    - 并发场景：同时安装多个 MCP server 时的行为？
    - 磁盘空间不足时的安装行为？
  - **Verdict**: PASS（主要流程已覆盖，但安全场景有缺口）

- [x] 边界条件已识别
  - **Notes**: Edge Cases EC-01 到 EC-07 覆盖了多数边界：安装中断、同名工具冲突、无效包、空闲连接、注册表损坏、版本冲突、卸载使用中 server。但缺少：
    - 磁盘空间不足
    - 并发安装冲突
    - 安装路径权限不足
    - npm/pip 命令本身不可用
  - **Verdict**: PASS（核心边界已覆盖）

- [x] 范围边界清晰
  - **Notes**: P1/P2 优先级分层清晰，MUST/SHOULD 级别明确。MVP 范围通过 P1 标注划定。但规范缺少一个明确的"Out of Scope"章节来声明哪些内容不在本次范围内（例如：MCP server 版本更新、MCP server 市场/搜索功能、多实例部署支持等）。
  - **Verdict**: PASS（隐含的范围边界可从 P1/P2 推断，但显式 Out of Scope 更佳）

- [x] 依赖和假设已识别
  - **Notes**: 关键约束部分（第 24-29 行）隐含了依赖假设（ToolBroker 存在、McpRegistryService 存在等），但规范缺少独立的"依赖与假设"章节。隐含假设包括：
    - 系统上已安装 Node.js/npm（FR-001）
    - 系统上已安装 Python/pip（FR-002）
    - Docker 可选但需预装（FR-018/FR-019 已处理）
    - 网络连通性（未明确处理离线场景）
  - **Verdict**: PASS（核心依赖可从上下文推断，但缺少明确章节）

**Requirement Completeness 综合判定**: PASS（有小缺口但不影响主体完整性）

---

## Feature Readiness

- [x] 所有功能需求有明确的验收标准
  - **Notes**: FR-001 到 FR-022 中，所有 MUST 级需求（FR-001 到 FR-013, FR-015, FR-020 到 FR-022）都能在 User Stories 的 Acceptance Scenarios 中找到对应的验证方式。SHOULD 级需求（FR-014, FR-016 到 FR-019）同样有对应的 User Story 场景。每个 FR 都有追溯标注（Traceability）指向对应的 User Story。
  - **Verdict**: PASS

- [x] 用户场景覆盖主要流程
  - **Notes**: 8 个 User Stories 覆盖：
    - 核心安装流程（npm: Story 1, pip: Story 2）
    - 安装记录持久化（Story 3）
    - 持久连接管理（Story 4）
    - 安装向导 UX（Story 5）
    - 卸载流程（Story 6）
    - 健康检查（Story 7）
    - Docker 安装（Story 8）
    覆盖面完整，从安装到运行到卸载的完整生命周期均有覆盖。
  - **Verdict**: PASS

- [x] 功能满足 Success Criteria 中定义的可测量成果
  - **Notes**: 逐项对照：
    - SC-001（安装流程 3 分钟）→ FR-001/FR-002/FR-010~FR-013 覆盖
    - SC-002（首次调用延迟相当）→ FR-007 覆盖
    - SC-003（异常自动恢复）→ FR-008 覆盖
    - SC-004（重启后恢复）→ FR-006/FR-007 覆盖
    - SC-005（5 秒反馈）→ FR-012 覆盖
    - SC-006（80% 采用率）→ FR-010~FR-013 覆盖（向导体验决定采用率）
  - **Verdict**: PASS

- [ ] 规范中无实现细节泄漏
  - **Notes**: 与 Content Quality 第一项相同，存在严重的实现细节泄漏。具体包括：
    1. **具体类名/服务名**: McpInstallerService, McpRegistryService, ToolBroker, SkillRunner, LiteLLMSkillClient, LiteLLMClient（第 13, 26-29, 125, 163, 237, 239 行）
    2. **内部链路描述**: `SkillRunner -> LiteLLMSkillClient -> ToolBroker -> handler`（第 26 行）
    3. **具体文件名**: `mcp-servers.json`（第 189 行）
    4. **具体协议/连接方式**: `stdio 或 HTTP 连接`（第 230 行）
    5. **具体数据模型设计**: Key Entities 章节定义了具体类名、字段名、关系（第 246-249 行）
    6. **"零改动"约束**: `ToolBroker / SkillRunner / LiteLLMClient 零改动`（第 29 行）— 这是技术架构决策，非需求
    7. **具体命名空间格式**: `mcp.{server}.{tool}`（第 27, 163, 237 行）— 这是内部实现约定
  - **Verdict**: FAIL

**Feature Readiness 综合判定**: FAIL
- 理由: "规范中无实现细节泄漏"未通过。大量实现层面的类名、链路、文件名和数据模型设计出现在需求规范中。

---

## Summary

| Dimension | Result | Details |
|-----------|--------|---------|
| Content Quality | FAIL | 1/4 items failed: 存在大量实现细节泄漏 |
| Requirement Completeness | PASS | 8/8 items passed |
| Feature Readiness | FAIL | 1/4 items failed: 实现细节泄漏（同 Content Quality 根因） |

**Total**: 16 items checked, **14 passed**, **2 failed**

## Detailed Failure Analysis

### FAIL-01: 实现细节泄漏（Content Quality + Feature Readiness）

**根因**: 规范混合了需求规范（What）和技术设计（How）两个层次的内容。

**具体问题清单**:

| 位置 | 泄漏内容 | 建议修复方式 |
|------|----------|-------------|
| 第 13 行 | `McpRegistryService` 类名 | 改为"当前的 MCP 管理模块" |
| 第 26-29 行 | 整段关键约束引用内部类名和链路 | 移至 plan.md 的技术约束章节；spec 中仅保留"不替换现有功能路径，只做扩展"这一需求级约束 |
| 第 125 行 | `从 ToolBroker 注销其工具` | 改为"从系统注销其工具" |
| 第 163 行 | `ToolBroker 的 mcp.{server}.{tool} 命名空间机制` | 改为"现有工具命名空间隔离机制" |
| 第 178 行 | `独立 node_modules 目录`、`command/args/cwd` | 改为"安装依赖到独立目录、自动生成运行时配置" |
| 第 180 行 | `创建独立虚拟环境`、`command 指向 venv 内的可执行文件` | 改为"创建独立的依赖环境、自动生成运行时配置" |
| 第 182 行 | `npm 包独立 node_modules，pip 包独立 venv` | 改为"每个 server 拥有独立的依赖隔离环境" |
| 第 189 行 | `mcp-servers.json` 文件名 | 改为"安装注册表与运行时配置分离存储" |
| 第 230 行 | `stdio 或 HTTP 连接` | 改为"建立通信连接" |
| 第 237 行 | `ToolBroker`、`Profile/Policy/Event/Hook` | 改为"注册到系统工具管理体系，保持与现有治理流程的完整兼容" |
| 第 239 行 | `McpInstallerService`、`McpRegistryService` | 改为"安装完成后系统自动写入运行时配置并触发工具发现" |
| 第 241 行 | `Event Store` | 改为"系统事件记录" |
| 第 246-249 行 | Key Entities 整节（具体类名、字段定义） | 保留实体概念描述但去除具体类名和字段列表，将详细数据模型移至 plan.md |

### 修复优先级

以上所有问题的根因相同（需求与设计混合），修复方式统一：
1. 将实现层面的约束和设计迁移到 plan.md 的技术约束章节
2. spec.md 中用需求级别的语言替代具体技术术语
3. Key Entities 保留概念级描述（描述"是什么"和"关系"），去除具体类名和字段定义

### Constitution 覆盖分析（补充检查项，非打分项）

| Constitution 条款 | 规范覆盖情况 |
|-------------------|-------------|
| #1 Durability First | FR-006 (注册表持久化) + SC-004 (重启恢复) 覆盖 |
| #2 Everything is an Event | FR-022 明确覆盖 |
| #3 Tools are Contracts | FR-020 通过 ToolBroker 注册覆盖（隐含 schema 一致性） |
| #4 Two-Phase (Side-effect) | **覆盖不足** — 安装操作是不可逆的副作用（写入文件系统），但规范未明确要求 Plan -> Gate -> Execute 流程。EC-01 处理了安装中断的清理，但未覆盖安装前的确认门禁。User Story 5 Scenario 1 的第四步"确认安装"部分覆盖，但未上升到 FR 级别的明确要求 |
| #5 Least Privilege | **覆盖不足** — 规范未提及安装路径限制、env 变量隔离（env 中可能包含 secrets）、安装源校验（npm/pip 包的完整性/安全性验证）。仅 FR-003 的依赖隔离部分覆盖 |
| #6 Degrade Gracefully | FR-008 (自动重连) + EC-03 (无效包处理) + FR-019 (Docker 不可用降级) 覆盖 |
| #7 User-in-Control | FR-010~FR-013 (安装向导) + EC-07 (卸载等待) 覆盖，但缺少安装过程中的取消能力 |
| #8 Observability | FR-022 (事件记录) + FR-016/FR-017 (健康检查) 覆盖。但安装过程和工具调用链路的可观测性描述较薄 |

**注意**: Constitution 覆盖分析属于编排器特别要求的补充检查，不计入上述三维度评分，但其中的缺口（#4 Two-Phase、#5 Least Privilege）建议在修复实现细节泄漏问题时一并补充为需求级别的约束。
