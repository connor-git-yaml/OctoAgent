---
feature_id: "049"
title: "Butler Persona & Clarification Behavior System"
milestone: "M4"
status: "Draft"
created: "2026-03-14"
updated: "2026-03-14"
research_mode: "full"
blueprint_ref: "docs/blueprint.md §2 Constitution；docs/blueprint.md 2026-03-13 架构复核纠偏（每个 Agent 都必须拥有完整上下文栈）；Feature 039（message-native A2A 主链）；Feature 041（Butler-owned freshness runtime）；Feature 042（Profile-first Agent Console）；参考 OpenClaw AGENTS/BOOTSTRAP 模板与 Agent Zero 的 role/communication/solving prompt 分层"
predecessor: "Feature 039、041、042"
parallel_dependency: "Feature 048 负责页面表达；049 负责默认 Butler 行为、persona 载体与缺信息补问策略。"
---

# Feature Specification: Butler Persona & Clarification Behavior System

**Feature Branch**: `codex/049-butler-persona-behavior-system`  
**Created**: 2026-03-14  
**Updated**: 2026-03-14  
**Status**: Draft  
**Input**: 将“缺信息时的 Butler 补问策略”和“默认 persona 调整”收敛成一个正式 Feature，而不是继续为天气、待办、推荐类问题逐个加 case-by-case 特判。参考 OpenClaw 的 workspace 行为文件体系与 Agent Zero 的多段 prompt 分层，建立一套可持续调优、可由 Agent 自己提案修改的 Butler 行为系统。

## Problem Statement

当前 Butler runtime 已经具备 durable session、A2A delegation、memory/recall 等运行时底座，但默认行为层仍存在四个结构性问题：

1. **缺信息时经常“先给框架，再补边界”**  
   面对“帮我排今天下午工作优先级”这类问题，系统可能直接给出泛化答案，而不是先清楚说明“我还没有你的真实待办/日历/工作列表”。这会让用户误以为系统已经掌握了关键上下文。

2. **行为模式散落在实现细节与临时文案中**  
   当前补问、保守声明、fallback、人格口吻等规则分散在 system blocks、feature 特判和页面文案里，缺乏统一、可审计、可演化的默认行为体系。

3. **没有正式的用户可理解行为载体**  
   用户想调整 Butler 的性格、回答风格、补问偏好、工具边界表达时，当前没有类似 OpenClaw `AGENTS.md / SOUL.md / USER.md / TOOLS.md / BOOTSTRAP.md` 这样的正式载体。

4. **补问策略尚未抽象成通用能力**  
   目前像天气缺城市这种问题，已经有专门处理；但“任务排期缺待办”“推荐缺预算/地点”“选择题缺偏好”“比较缺评判标准”等更广泛的 under-specified 请求，还没有统一的 Clarification-first 行为框架。

因此，049 的目标不是再为天气、日历、推荐分别补逻辑，而是：

> 把 Butler 的默认行为从“问题来了先答”升级成“先判断是否缺关键上下文，再决定补问、best-effort fallback 或委派”，并把这套行为做成可持续演进的 persona/behavior 系统。

## Product Goal

交付一套正式的 Butler 行为系统：

- Butler 回答前能先区分：已知事实、合理推断、缺失输入
- 对 under-specified 请求优先采用 clarification-first，而不是直接输出看似完整的泛化答案
- persona、沟通风格、补问原则、工具边界表达进入正式的 markdown behavior pack
- 这套 behavior pack 可以按 project/agent profile 绑定，并支持 agent 自己提出更新
- Worker 不直接继承完整 Butler 私有行为文件，只接收经过筛选的上下文胶囊或行为切片

## Design Direction

### 049-A 行为原则

Butler 默认遵循：

- 先判断是否缺关键输入
- 缺关键信息时先补问，必要时明确说明边界
- 如果用户不想补充，则提供清楚标记为“通用框架 / best-effort”的 fallback
- 回答时区分“我知道 / 我在推断 / 我还需要你补充”

### 049-B 行为载体

参考 OpenClaw 的 workspace 文件与 Agent Zero 的 prompt slicing，拟引入一套正式行为文件：

1. `AGENTS.md`：总体运行约束与行为入口
2. `SOUL.md`：身份、语气、边界、长期人格
3. `USER.md`：用户偏好、默认城市/时区/沟通习惯等 owner basics
4. `PROJECT.md`：当前 project 的目标、术语、成功标准
5. `TOOLS.md`：工具使用边界、何时补问、何时委派、何时解释限制
6. `MEMORY.md`：沉淀后的长期行为偏好与经验
7. `BOOTSTRAP.md`：首次建立或重塑 persona 时的引导 ritual

`HEARTBEAT.md` 等主动性文件保留为后续扩展，不作为本 Feature 的核心边界。

### 049-C 运行时装配

运行时不再把 persona 当成一段大 prompt，而是切成类似 Agent Zero 的 role / communication / solving / tool / memory 结构，再由 Butler runtime 选择性装配。

## Scope Alignment

### In Scope

- Butler 默认 clarification-first 行为规范
- 通用 missing-context 判断框架
- markdown behavior pack 结构设计与 runtime 装配规则
- project / agent profile 绑定行为文件的方式
- agent 可提出 behavior pack 修改的机制
- Worker 继承哪些行为切片、哪些不能继承
- acceptance 场景：排期、推荐、实时查询缺位置、比较缺标准等

### Out of Scope

- 为天气、餐厅、旅游、购物等场景分别新增专用 case cache
- 直接重做 Worker persona 全家桶
- 重新设计 memory 数据模型
- 新增完整的 prompt IDE 或 persona marketplace
- 绕过现有 governance 直接让 agent 悄悄改动所有行为文件

## User Scenarios & Testing

### User Story 1 - 缺信息时，Butler 会先补问而不是假装已经理解 (Priority: P1)

作为用户，我希望当我的问题缺关键条件时，Butler 先问我缺的是什么，而不是直接给出看似完整、其实泛化的答案。

**Why this priority**: 这是当前最影响信任感的行为问题，也是 case-by-case 特判最不该继续扩张的地方。

**Independent Test**: 提出几个典型 under-specified 请求，验证 Butler 能识别缺失上下文并先补问或声明边界。

**Acceptance Scenarios**:

1. **Given** 用户说“帮我把今天下午的工作拆成 3 个优先级”，**When** 系统尚未拿到真实待办/日历/work 列表，**Then** Butler 先说明缺失项，并引导用户补充或选择 best-effort fallback。
2. **Given** 用户说“帮我推荐一家餐厅”，**When** 缺少城市、预算或场景，**Then** Butler 不直接给具体名单，而是优先补最关键的 1-2 个条件。
3. **Given** 用户不想补充更多信息，**When** Butler 进入 fallback，**Then** 它明确标记“这是通用建议，不是基于你的真实上下文”。

---

### User Story 2 - Butler 有稳定人格，但人格不是硬编码在一个大 prompt 里 (Priority: P1)

作为用户，我希望 Butler 的语气、边界和沟通习惯是稳定的，同时又能按 project 或个人偏好逐步调整，而不是每次修改都去改代码或一段神秘的大 system prompt。

**Why this priority**: 这是让 OctoAgent 真正成为 Personal AI OS 的关键，不然行为调优永远会退化成临时 patch。

**Independent Test**: 修改行为文件中的语气、补问偏好或委派表达后，新会话中的 Butler 行为随之变化，并且可追溯这次变化来自哪一层文件。

**Acceptance Scenarios**:

1. **Given** project 绑定了新的 `SOUL.md / USER.md` 内容，**When** Butler 启动新会话，**Then** 它的语气和默认沟通方式按新配置表现。
2. **Given** 行为 pack 的某一层缺失，**When** 系统装配 Butler runtime，**Then** 会回退到默认模板，并记录可解释的来源链。

---

### User Story 3 - Agent 可以帮助我演化行为文件，但不能悄悄改掉它们 (Priority: P2)

作为用户，我希望 Butler 自己能发现“当前行为不够好”并提出修改建议，甚至帮我草拟文件变更，但系统不能在没有治理的情况下悄悄重写我的行为规范。

**Why this priority**: 你明确提出想借鉴 OpenClaw 的文件式行为系统，但这必须符合 OctoAgent 的 user-in-control 约束。

**Independent Test**: Butler 可以生成 behavior pack 变更提案，并通过 review/apply 链路落地；未审批时不会直接生效。

**Acceptance Scenarios**:

1. **Given** Butler 识别出某类请求经常缺补问，**When** 它生成 behavior pack 提案，**Then** 提案以可审查差异形式呈现，而不是直接覆写文件。
2. **Given** 用户批准提案，**When** 系统应用变更，**Then** 后续会话行为按新规则执行，并留下可审计记录。

---

### User Story 4 - Worker 只继承需要的行为切片，而不是把 Butler 私有习惯全泄漏出去 (Priority: P2)

作为系统设计者，我希望 Worker 拿到的是与任务相关的行为切片，而不是 Butler 的完整行为 pack 或用户私有偏好全集。

**Why this priority**: 否则 behavior pack 会变成新的隐私与边界泄漏源。

**Independent Test**: 触发 Butler -> Worker delegation，验证 Worker runtime 只消费允许转交的行为切片，如任务风格、格式要求、引用标准，而不是完整 `USER.md / MEMORY.md`。

**Acceptance Scenarios**:

1. **Given** Butler 委派一个 research task，**When** WorkerSession 启动，**Then** Worker 仅获得必要的 communication/format/task rules，而不是完整用户偏好全集。
2. **Given** 某行为文件被标记为私有，**When** 发生委派，**Then** 该内容默认不随 delegation 自动传递。

## Edge Cases

- 补问不能无限循环；系统需要定义“最多追问几轮”与何时退回 best-effort fallback。
- 对简单常识问题不能过度补问，否则会把 Butler 变成低效表单。
- `USER.md` 中的默认值（如城市/预算习惯）只能作为偏好或已确认事实，不应静默冒充实时真相。
- 行为文件缺失或损坏时，系统必须回退到默认模板，并保留解释链。
- 多 project 并存时，behavior pack 必须 project-scoped；不能把一个 project 的风格污染到另一个 project。

## Functional Requirements

- **FR-001**: Butler runtime MUST 在回答前区分“已知事实 / 合理推断 / 缺失输入”，并将此判断纳入默认行为流程。
- **FR-002**: 对 under-specified 请求，Butler MUST 优先采用 clarification-first，而不是直接输出看似完整的泛化答案。
- **FR-003**: 当用户不愿补充信息时，Butler MAY 提供 best-effort fallback，但 MUST 明确标记其通用性与边界。
- **FR-004**: 系统 MUST 引入正式的 markdown behavior pack，至少包含 `AGENTS.md`、`SOUL.md`、`USER.md`、`PROJECT.md`、`TOOLS.md`、`MEMORY.md`、`BOOTSTRAP.md` 七类载体。
- **FR-005**: Butler runtime MUST 以分层方式装配行为系统，至少拆分为 role / communication / solving / tool-boundary / memory-policy 等逻辑段，而不是只依赖单一大 prompt。
- **FR-006**: behavior pack MUST 支持 project / agent profile 绑定，并记录每次运行使用的 effective behavior source。
- **FR-007**: 系统 MUST 支持 agent 生成 behavior pack 更新提案；默认情况下，核心行为文件的修改需要 review/apply 或等价治理动作后才生效。
- **FR-008**: Worker runtime MUST 只继承经过筛选的行为切片，不得默认读取 Butler 完整私有行为文件。
- **FR-009**: 本 Feature MUST 提供通用 acceptance matrix，覆盖任务排期、推荐、比较、实时查询缺位置等 under-specified 场景，而不是仅覆盖天气单案。
- **FR-010**: Butler 的默认行为改进 MUST 优先通过 persona/behavior system 达成，不得继续扩张为若干 isolated case patches。

## Key Entities

- **BehaviorPack**: Butler 默认行为文件集合，按 project/agent profile 绑定。
- **BehaviorLayer**: 运行时装配后的逻辑层，如 role、communication、solving、tool-boundary、memory-policy。
- **ClarificationDecision**: 对当前请求的行为判断结果，包含 `can_answer_directly / needs_clarification / best_effort_fallback / delegate_after_clarification`。
- **BehaviorPatchProposal**: 由 agent 生成的行为文件更新提案，经过治理后再生效。
- **BehaviorSliceEnvelope**: Delegation 时转交给 Worker 的行为切片胶囊。

## Success Criteria

- **SC-001**: Butler 面对 under-specified 请求时，不再默认直接给出伪完整答案，而是先补问或明确边界。
- **SC-002**: Butler 的默认人格、沟通方式和补问策略可以通过 behavior pack 调整，并在新会话中稳定生效。
- **SC-003**: 行为调整不再主要依赖代码硬改或单点 prompt patch，而是进入正式、可审计的 project-scoped 载体。
- **SC-004**: Worker 不会默认继承 Butler 全量私有行为文件，行为边界与 delegation 边界保持一致。
- **SC-005**: 验收矩阵能证明 049 解决的是一类“缺关键信息”问题，而不是只修某个天气 case。
