# F095 Codex Adversarial Review #1（spec + plan）

**时间**：2026-05-09
**Reviewer**：Codex CLI（gpt-5 high reasoning，via ChatGPT account）
**输入**：`spec.md` + `plan.md`（baseline 284f74d，spec 阶段 GATE_DESIGN 通过后）
**模式**：foreground

## Findings 总览

| 严重度 | 数量 | 处理状态 |
|--------|------|----------|
| HIGH | 5 | 全接受（其中 2 条触发 §6.1 / §6.2 决策翻转）|
| MEDIUM | 7 | 全接受 |
| LOW | 3 | 全接受 |

## 处理表

### HIGH

| # | Section | Concern 摘要 | 处理 |
|---|---------|-------------|------|
| H1 | spec §6.1 | USER.md 完全排除论证不严，Worker 实际需要语言/格式/确认偏好 | **接受**：实测 USER.md 内容（语言中文 / 信息组织 / 工作习惯）对 Worker 高价值且无 user-facing 指令 → **修订决策为 USER.md 扩入** |
| H2 | spec §6.2 / plan Phase 顺序 | Phase A 先扩 SOUL/HEARTBEAT 白名单但 Phase B 才创建 worker variant → 中间状态 Worker 看通用 SOUL/HEARTBEAT 含主 Agent 语气 | **接受**：Phase 拆分为 A（envelope+IDENTITY 修复）→ B（创建 worker variant）→ C（扩 SOUL/HEARTBEAT/USER 白名单）→ D（事件）→ E（Final）|
| H3 | spec §6.2 / plan Phase B | BOOTSTRAP.md 沿用通用模板假设不足，可能含 user-facing onboarding 语义 | **接受**：实测 BOOTSTRAP.md 内容是"你好！我是 __AGENT_NAME__... 你希望我怎么称呼你"——主 Agent 用户首次引导脚本 → **修订决策为 BOOTSTRAP.md 不扩入** Worker 白名单 |
| H4 | spec §6.3 | share_with_workers 字段语义降级未审计 envelope 全部消费者（shared_file_ids 字段名/语义）| **接受**：plan 增 contract audit 步骤；保留 `shared_file_ids` 字段名但 docstring 显式说明语义变更（"现在 = profile 白名单内文件 ID 列表"，不再 = "share_with_workers=True 文件 ID 列表"）|
| H5 | spec AC-5 / §6.5 | BEHAVIOR_LOADED cache miss only 让 F096 审计降级为"进程缓存生命周期可追溯" | **部分接受**：F095 范围保持 minimal（仅 LOADED 事件）；但在 BehaviorPack 加 `pack_id` 字段，让 F096 USED 事件可引用——避免 F096 返工 |

### MEDIUM

| # | Section | Concern 摘要 | 处理 |
|---|---------|-------------|------|
| M6 | plan Phase C | metadata raw_pack 路径 emit 缺 pack_source 字段 | 接受：payload 增 `pack_source: "filesystem" | "default" | "metadata_raw_pack"` |
| M7 | spec AC-4 / plan Phase B | Worker 创建入口未逐一审计 | 接受：plan 增 Worker profile 创建入口列表 + 集成测试 |
| M8 | spec AC-2 / plan Phase A+B | IDENTITY 修复缺 regression audit | 接受：plan 加 prompt 拼接顺序 / delegation additional_instructions 与 IDENTITY.worker 优先级测试 |
| M9 | spec §7.1 / plan §5.1 | F094 / F095 静态隔离假设过强 | 接受：plan 改"文件级低冲突 + 集成验证"；rebase 后必跑 Worker dispatch 双 agent_id（memory + behavior event）集成测 |
| M10 | plan Phase C sink 选择 | sink 推迟到 implement 阶段会导致 F096 返工 | 接受：plan 阶段定为 EventStore.record_event（与 F091/F092/F093 sink convention 一致）；structlog 仅作附加观测 |
| M11 | spec AC-6 | 行为零变更只覆盖 FULL 与 MINIMAL | 接受：AC-6 扩展为"所有非-WORKER load_profile"，测试覆盖 FULL / MINIMAL（BOOTSTRAP_ONLY 等若代码内不存在则跳过）|
| M12 | spec AC-1 / plan §0.2 | AC-1 4 层 vs plan §0.2 5 层不一致 | 接受：统一表述"Worker 加载 8 文件，覆盖 ROLE / COMMUNICATION / SOLVING / TOOL_BOUNDARY 四层 H2 核心 + BOOTSTRAP lifecycle layer（注意：BOOTSTRAP layer 来自 HEARTBEAT.md，因 spec §6.2 修订决策 BOOTSTRAP.md 不进 Worker）" |

### LOW

| # | Section | Concern | 处理 |
|---|---------|---------|------|
| L13 | plan Phase B 验收 | "_BEHAVIOR_TEMPLATE_VARIANTS 含 4 个 worker variant 条目（IDENTITY 旧 1 + 新 2 = 3）" 数字矛盾 | 接受：改为"含 3 个 worker variant 条目（IDENTITY + SOUL + HEARTBEAT）" |
| L14 | spec §10 checklist | 多个 [x] 实际是未来项 | 接受：spec §10 拆分"spec 阶段已决策"和"implement 阶段待完成" |
| L15 | plan §3 | blueprint.md 同步缺审计 | 接受：先 grep blueprint.md 是否有 Worker behavior / event audit / share_with_workers 章节，有则同步，无则在 plan §3 显式说明"经 grep 确认无相关章节" |

## 触发用户决策翻转的项

> 这些是**之前 GATE_DESIGN 已通过但现在改变**的决策，需要用户重新拍板。

| 项 | GATE_DESIGN 决策 | review 后修订 | 触发 finding |
|----|-------------------|----------------|--------------|
| USER.md 是否扩入 Worker | 不扩入（H1 哲学）| **扩入**（USER 内容是用户长期偏好，无 user-facing 指令；Worker 写 commit message 也需要语言中文偏好）| H1 |
| BOOTSTRAP.md 是否扩入 Worker | 扩入（沿用通用模板）| **不扩入**（BOOTSTRAP.md 实测内容是主 Agent 用户首次见面脚本，含 "你好！我是 __AGENT_NAME__"... "你希望我怎么称呼你" 等 user-facing 指令）| H3 |

修订后最终白名单（仍是 8 文件但集合不同）：
```python
BehaviorLoadProfile.WORKER: frozenset({
    "AGENTS.md",     # ROLE
    "TOOLS.md",      # TOOL_BOUNDARY
    "IDENTITY.md",   # ROLE（来自 IDENTITY.worker.md）
    "PROJECT.md",    # SOLVING
    "KNOWLEDGE.md",  # SOLVING
    "USER.md",       # COMMUNICATION（用户长期偏好，新决策接纳 H1）
    "SOUL.md",       # COMMUNICATION（来自 SOUL.worker.md）
    "HEARTBEAT.md",  # BOOTSTRAP（来自 HEARTBEAT.worker.md）
})
```
（去掉 BOOTSTRAP，加入 USER；FULL 9 文件 - BOOTSTRAP = 8 文件）
