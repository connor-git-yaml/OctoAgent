---
feature_id: "045"
title: "Memory Panel Clarity Refresh"
milestone: "M4"
status: "Implemented"
created: "2026-03-13"
updated: "2026-03-14"
research_mode: "story"
blueprint_ref: "docs/blueprint.md §8.7 Memory；Feature 027（Memory Console + Vault Authorized Retrieval）；Feature 035（Guided User Workbench）；Feature 036（Guided Setup Governance）；Feature 044（Settings Center Refresh）"
predecessor: "Feature 027（Memory Console 产品化）、Feature 035（Workbench 页面骨架）、Feature 036（setup-governance 主链）、Feature 044（Settings 中 Memory 配置入口）"
---

# Feature Specification: Memory Panel Clarity Refresh

**Feature Branch**: `codex/045-memory-panel-clarity`  
**Created**: 2026-03-13  
**Updated**: 2026-03-14
**Status**: Implemented
**Input**: 重构 `/memory` 页面。去掉用户无法理解的开发/调试术语与 raw ID；清楚告诉用户 Memory 现在有没有工作、为什么这样判断、以及要让各项功能跑起来应该配置或执行什么；配置说明必须尽量精简，并明确指向 Settings 中的对应入口，同时支持 `local_only / memu + command / memu + http` 三条配置路径。

## Problem Statement

当前 `/memory` 页面已接入 canonical memory resource，但产品表达还有三个直接影响可用性的缺口：

1. **开发内部语义直接暴露给用户**  
   页面出现 `Active Scope`、raw scope_id、`Index Health`、backend id、`flush` 等内部术语。用户看得到很多状态，但并不知道这些词是什么意思，也不知道自己该做什么。

2. **页面没有先回答“Memory 到底有没有工作”**  
   现在页面更像一块调试面板：显示筛选项、layer、partition、索引信息与记录列表，但没有先区分“已正常工作”“已配置但还没内容”“配置缺失”“当前降级”这几种对用户最关键的状态。

3. **启用路径和配置路径没有最短闭环**  
   用户不知道本地 Memory 已经可以直接工作，也不知道什么时候该用本地 MemU command、什么时候才需要远端 HTTP bridge；即使需要配置，也不清楚最少只需要去 `Settings > Memory` 补哪几项字段。

因此，这次重构的目标不是改 Memory 后端能力，而是把 `/memory` 从“内部观察面板”改成“用户能判断状态、知道下一步”的工作台页面。

## Product Goal

让 `/memory` 先回答三个问题：

1. Memory 现在有没有在工作？
2. 如果没看到结果，是还没内容、配置不完整，还是系统正在降级？
3. 如果我想让它更完整地工作，最少要去哪里改什么？

## User Scenarios & Testing

### User Story 1 - 我能一眼判断 Memory 是否在工作 (Priority: P1)

作为普通用户，我希望进入 `/memory` 后，页面先用直白语言告诉我当前状态，例如“已在工作”“已经就绪但还没有内容”“增强模式未配完”“当前在降级运行”，而不是先给我一堆内部术语。

**Why this priority**: 这是 Memory 页面最基本的产品职责。如果用户连“有没有在工作”都看不出来，后面的记录列表、历史和授权信息都没有解释基础。

**Independent Test**: 打开 `/memory`，分别验证有数据、无数据、MemU 配置缺失、后端降级四类状态都能显示清晰的人类语言标题和下一步建议。

**Acceptance Scenarios**:

1. **Given** Memory 有可读记录且 backend 健康，**When** 用户打开页面，**Then** 页面明确告诉用户 Memory 正在工作，并展示当前结论/片段等用户可理解指标。
2. **Given** Memory backend 健康但还没有任何数据，**When** 用户打开页面，**Then** 页面明确告诉用户系统已经就绪，只是还没有对话或导入内容。
3. **Given** Memory 选择了增强模式但关键配置未补齐，**When** 用户打开页面，**Then** 页面明确列出缺少的最小配置项，而不是只显示原始 warning。
4. **Given** backend 当前 degraded / unavailable，**When** 用户打开页面，**Then** 页面明确说明现在是降级运行或连接失败，并给出下一步处理入口。

---

### User Story 2 - 我能知道最少要配置什么，而不是被实现细节淹没 (Priority: P1)

作为想启用或排查 Memory 的用户，我希望页面直接告诉我“基础使用不需要额外服务”“同机增强检索优先走本地 command”“远端增强检索才需要 HTTP bridge”，而不必理解 memu 独立部署、索引细节或 backend 实现。

**Why this priority**: 这正是用户请求里的核心诉求。产品应该暴露最少配置面，而不是把部署细节推给普通用户。

**Independent Test**: 在 `local_only`、`memu + command 但 bridge_command 缺失`、`memu + http 但 bridge_url 缺失`、`memu + http 但 bridge_api_key_env 缺失` 四种配置下打开页面，验证页面都能给出精简的清单式指引，并带到 Settings 的 Memory 分区。

**Acceptance Scenarios**:

1. **Given** 当前模式是 `local_only`，**When** 用户查看指引区，**Then** 页面说明基础记忆已经可用，不需要额外 Memory 服务，只需先在 Chat 或导入中产生内容。
2. **Given** 当前模式是 `memu` 且 transport=`command`、`bridge_command` 为空，**When** 用户查看指引区，**Then** 页面明确提示去 `Settings > Memory` 补齐本地命令。
3. **Given** 当前模式是 `memu` 且 transport=`http`、`bridge_url` 为空，**When** 用户查看指引区，**Then** 页面明确提示去 `Settings > Memory` 补齐 Bridge 地址。
4. **Given** 当前模式是 `memu` 且 transport=`http`、`bridge_api_key_env` 为空，**When** 用户查看指引区，**Then** 页面明确提示去 `Settings > Memory` 补齐 API Key 环境变量名。
5. **Given** 当前模式是 `memu` 且配置完整，**When** 用户查看指引区，**Then** 页面说明增强检索已经接通或当前正在降级回退，而不是继续显示“待配置”。

---

### User Story 3 - 我看到的是 Memory 产品信息，而不是调试面板 (Priority: P2)

作为日常使用工作台的用户，我希望 `/memory` 页面主要呈现“记住了什么、现在状态如何、我下一步该做什么”，而不是 raw scope_id、索引键值对或调试动作按钮。

**Why this priority**: 这关系到 M4 workbench 是否真正面向普通用户。高级诊断仍然可以存在，但应该退到 Advanced 或更次要的位置。

**Independent Test**: 打开 `/memory`，验证页面不再直接出现 `Active Scope`、`Index Health`、raw scope_id 列表、`flush` 按钮等开发语义；同类能力若保留，需改成用户语言并降低视觉权重。

**Acceptance Scenarios**:

1. **Given** 页面渲染完成，**When** 用户浏览状态区和记录列表，**Then** 不再看到 `Active Scope`、raw scope_id 列表或 `Index Health` 原始标题。
2. **Given** 用户需要进一步排查，**When** 页面给出高级入口，**Then** 指向 `Settings > Memory` 或 `Advanced`，而不是把调试键值直接堆在主视图。

## Edge Cases

- `available_scopes` 为空但 backend 健康时，页面应解释为“还没有内容”，而不是“scope 异常”。
- `memory.warnings` 非空但 records 仍可读时，页面应表达为“当前有提醒/降级”，而不是直接判定完全不可用。
- `memu` 已配置完成但 `retrieval_backend` 回退为本地时，页面应说明“增强模式已配置，但当前使用本地回退”。
- `memu + command` 已配置但本地命令当前不可执行时，页面应表达为“增强模式当前不可用/已回退”，而不是继续假装配置缺失。
- `partition` 或 `metadata` 中出现未知业务值时，页面应做宽松展示，但不把 raw 技术细节抬到主视图上。

## Functional Requirements

- **FR-001**: `/memory` 页面 MUST 先输出用户可理解的状态摘要，明确区分“正在工作 / 已就绪但暂无内容 / 配置未完成 / 当前降级”。
- **FR-002**: 页面 MUST 移除或翻译明显的开发/调试术语，包括 `Active Scope`、raw scope_id 列表、`Index Health`、`flush`、raw backend id 等直接暴露。
- **FR-003**: 页面 MUST 基于现有 canonical `memory`、`config`、`setup_governance` resource 组合推导状态，不新增平行后端接口。
- **FR-004**: 当当前模式为 `local_only` 时，页面 MUST 明确说明基础 Memory 不需要额外服务，并引导用户先通过 Chat 或导入产生内容。
- **FR-005**: 当当前模式为 `memu` 且最小配置不完整时，页面 MUST 明确列出缺失项，且只暴露用户需要补的最小字段；本地 command 模式优先提示 `bridge_command`，HTTP 模式再提示 `Bridge 地址`、`API Key 环境变量名`。
- **FR-006**: 页面 SHOULD 提供直接跳转到 `Settings > Memory` 的入口；如使用 hash deep-link，Settings 页面 MUST 能滚动到对应 section。
- **FR-007**: 页面 MAY 保留 `memory.flush` 等已有动作，但如果保留，MUST 改写为用户语言并解释目的，不得继续使用内部命名。
- **FR-008**: Memory 记录列表 MUST 继续展示用户有意义的信息（摘要、层级、是否需授权、更新时间、证据数），并 SHOULD 降低或移除 raw scope/backend/proposal/derived 技术信息。
- **FR-009**: 当前页面若需暴露高级排障信息，MUST 通过次级入口指向 `Advanced` 或 `Settings`，而不是把原始键值直接作为主信息块。
- **FR-010**: 前端测试 MUST 更新，覆盖状态摘要、最小配置指引、去术语化文案和 Settings deep-link 行为。

## Key Entities

- **MemoryPanelStatusSummary**: 基于 `memory` + `config` + `setup` 推导出的用户状态摘要，包含状态标题、说明、推荐动作和模式说明。
- **MemorySetupGuideItem**: 指向用户下一步的最小动作项，如“去 Settings > Memory 补本地命令”“去 Settings > Memory 补 Bridge 地址”“先去 Chat 产生第一批上下文”。
- **MemoryDisplayRecord**: 在 `MemoryRecordProjection` 之上做用户语言包装后的展示项，强调摘要、主题、授权和更新时间，而不是内部 ID。

## Success Criteria

### Measurable Outcomes

- **SC-001**: `/memory` 首屏不再出现 `Active Scope`、raw scope_id、`Index Health`、`flush` 等明显开发语义。
- **SC-002**: 用户能在 5 秒内从标题和引导区判断当前属于“在工作 / 没内容 / 没配完 / 降级”中的哪一种。
- **SC-003**: 当需要增强检索配置时，页面只要求用户关注当前 transport 对应的最少字段，并能直接跳到 `Settings > Memory`。
- **SC-004**: 相关前端回归测试通过，覆盖新的状态表达、设置入口和 Memory 页面去术语化结果。
