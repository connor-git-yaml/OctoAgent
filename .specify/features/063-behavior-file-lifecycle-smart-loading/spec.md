---
feature_id: "063"
title: "Behavior File Lifecycle & Smart Loading"
milestone: "M2"
status: "partially-implemented"
created: "2026-03-18"
updated: "2026-03-18"
research_mode: "cross-product-benchmark"
blueprint_ref: "docs/blueprint.md §14 Constitution #6, #8"
predecessor: "Feature 059 (unified-agent-behavior-system)"
research_ref: "063-behavior-file-lifecycle-smart-loading/research.md"
---

# Feature 063: Behavior File Lifecycle & Smart Loading

- **Feature Branch**: `feat/063-behavior-lifecycle`
- **Created**: 2026-03-18
- **Status**: Draft
- **Input**: 跨产品调研（OpenClaw / Agent Zero / Claude Code）+ Feature 059 行为文件体系上线后的实际使用反馈

---

## Problem Statement

### 直接触发

Feature 059 建立了 9 个行为文件 + 9 级 overlay 的完整行为体系。上线运行后发现三个结构性问题：

1. **BOOTSTRAP.md 永久注入**：Agent 完成 onboarding 后，BOOTSTRAP.md 仍然每次 session 都注入 system prompt，浪费约 2200 字符 token 预算且内容已无意义。OpenClaw 通过"删除即完成"检测机制解决了这个问题。

2. **全量注入无差异化**：Butler、Worker、Subagent 三种角色收到完全相同的 9 个行为文件。Worker 不需要 USER.md（用户偏好）和 BOOTSTRAP.md（onboarding 引导），Subagent 更只需要最小子集。每次 LLM 调用浪费约 5000-8000 字符的无关上下文。

3. **行为文件只增不减**：现有体系没有任何自动压缩或清理机制。长期运行后行为文件内容膨胀，逐步侵蚀有效 token 预算。OpenClaw 的实际使用显示其 workspace 已膨胀到 4574 文件/580MB（虽然我们有字符预算限制，但文件总量仍会增长）。

### 结构性问题

行为文件体系目前是 **"写入后即永久、加载即全量"** 的静态模型，缺乏生命周期管理和智能加载能力。这与三层 Agent 架构（Butler/Worker/Subagent）的差异化需求不匹配，也无法适应未来多 Project、多 Worker 场景下的 token 效率要求。

---

## User Stories

- **US-1**：作为 Butler Agent，完成 onboarding 对话后，BOOTSTRAP.md 不再注入我的 system prompt，让我的上下文更干净。
- **US-2**：作为 Worker Agent，我只收到与自己角色相关的行为文件（AGENTS + TOOLS + IDENTITY + PROJECT），不被 USER.md / SOUL.md / BOOTSTRAP.md 等无关内容干扰。
- **US-3**：作为 Subagent，我只收到最小行为子集（AGENTS + TOOLS + IDENTITY + USER），保证上下文精简、任务聚焦，同时保留用户基本偏好（语言、称呼）。
- **US-4**：作为系统运维者，行为文件总大小有自动监控和压缩机制，不会因为长期运行而无限膨胀。
- **US-5**：作为用户，关键行为 section 可以标记为"不压缩"，确保我精心编写的规则不被自动改写。
- **US-6**：作为 Agent，当行为文件被截断时，文件的开头（角色定义）和结尾（关键规则）都应保留，而不是从中间硬切。

---

## Functional Requirements

| FR ID | 描述 | 来源 | 验收标准 |
|-------|------|------|---------|
| FR-1 | BOOTSTRAP.md 支持双触发完成检测：`<!-- COMPLETED -->` 标记 OR 文件删除，均标记 onboarding 完成 | US-1 | 标记或删除后 → 后续 session 不含 BOOTSTRAP.md 内容 |
| FR-2 | onboarding 完成状态持久化，重启后不丢失 | US-1 | 重启 Gateway → BOOTSTRAP.md 仍不注入 |
| FR-3 | 定义 BehaviorLoadProfile 枚举（FULL / WORKER / MINIMAL） | US-2, US-3 | 三种 profile 各有明确的 file_id 白名单 |
| FR-4 | resolve_behavior_workspace() 接受 load_profile 参数，按白名单过滤 | US-2, US-3 | Worker 模式不含 USER/SOUL/HEARTBEAT/BOOTSTRAP |
| FR-5 | build_behavior_slice_envelope() 使用 WORKER profile 而非当前的 ad-hoc 子集 | US-2 | Worker 行为注入与 profile 定义一致 |
| FR-6 | Subagent 使用 MINIMAL profile | US-3 | Subagent 只收到 AGENTS + TOOLS + IDENTITY + USER |
| FR-7 | 行为文件总大小监控，超过阈值时发出警告事件 | US-4 | 总大小 > 阈值 → Event Store 记录警告 |
| FR-8 | 内置 Behavior Compactor，采用 LLM 智能合并模式（非简单压缩） | US-4 | 合并后总大小下降，合并前自动备份 |
| FR-9 | 支持 `<!-- 🔒 PROTECTED -->` 标记保护不被压缩的 section | US-5 | 被标记的 section 在压缩后内容不变 |
| FR-10 | 行为文件超出字符预算时采用 head/tail 截断策略（70% 头 + 20% 尾 + 中间截断标记） | US-6 | 截断后保留文件开头和结尾内容 |
| FR-11 | resolve_behavior_workspace() 增加 session 级缓存，文件修改时主动 invalidate | NFR-2 | 同一 session 内重复 resolve 不产生额外 IO |

---

## Non-Functional Requirements

| NFR ID | 描述 | 指标 |
|--------|------|------|
| NFR-1 | Bootstrap 状态检查不增加 session 启动延迟 | < 1ms（文件/DB 读取） |
| NFR-2 | BehaviorLoadProfile 过滤不增加 resolve 开销 | 与当前持平或更快（加载更少文件） |
| NFR-3 | Compactor 压缩过程不阻塞正常请求 | 后台异步执行 |
| NFR-4 | 向后兼容：无 load_profile 参数时等同 FULL | 现有调用方零改动 |

---

## Product Goal

让行为文件体系从"静态全量注入"演进为"有生命周期、按角色差异化加载、可自动压缩"的智能系统，在保持 9 级 overlay 灵活性的同时，大幅提升 token 利用效率。

---

## Scope

### In Scope

1. BOOTSTRAP.md 完成状态管理（双触发：标记 OR 删除 + 持久化 + 跳过注入）
2. BehaviorLoadProfile 定义与实现（FULL / WORKER / MINIMAL）
3. resolve_behavior_workspace() 差异化加载
4. Head/tail 截断策略（70% 头 + 20% 尾 + 中间截断标记）
5. Session 级行为文件缓存（invalidate on write）
6. Behavior Compactor 基础实现（LLM 智能合并 + 手动触发 + 阈值警告）
7. `<!-- 🔒 PROTECTED -->` 压缩保护标记

### Out of Scope

1. 行为文件的条件加载（按任务类型/工具类型动态裁剪）— 留待后续 Feature（参见 research.md §八）
2. 行为文件两阶段加载（摘要注入→按需全文）— 留待后续 Feature
3. SKILL.md 与 Behavior 统一发现机制 — 留待后续 Feature
4. Compactor 自动定时执行（cron）— MVP 先支持手动触发和阈值警告
5. 行为文件内条件段模板引擎 — 与 9 级 overlay 机制冲突，不采纳（参见 research.md §八）
6. `between_output_timeout_seconds` 死配置清理 — 独立小 PR
