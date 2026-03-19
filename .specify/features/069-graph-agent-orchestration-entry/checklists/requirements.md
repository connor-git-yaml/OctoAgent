# Feature 065 — 需求质量检查表

> 自动生成，基于 `spec.md`（draft）与 `.specify/memory/constitution.md` 交叉审查。

---

## 1. Constitution 合规性

逐条检查 spec 中的 FR / NFR / 约束是否覆盖宪法八大系统原则 + 六条代理行为原则 + 非目标。

| # | Constitution 原则 | 覆盖情况 | 对应 spec 条目 | 状态 |
|---|-------------------|----------|---------------|------|
| 1 | **Durability First** — 长任务落盘、重启可恢复 | FR-065-05 AC-05（Checkpoint 落盘）、AC-08（重启恢复）明确覆盖 | FR-065-05 | PASS |
| 2 | **Everything is an Event** — 状态迁移生成事件 | FR-065-08 全面覆盖（启动/节点/状态变更均发射事件） | FR-065-08 | PASS |
| 3 | **Tools are Contracts** — schema 与签名一致 | FR-065-03 AC-08 明确引用此原则 | FR-065-03 | PASS |
| 4 | **Side-effect Must be Two-Phase** — 不可逆操作需门禁 | NFR-065-04 要求 gate 节点经 Policy Engine 门禁 | NFR-065-04 | PASS |
| 5 | **Least Privilege by Default** — secrets 不进 LLM 上下文 | NFR-065-04 要求 PIPELINE.md 不含 secrets | NFR-065-04 | PASS |
| 6 | **Degrade Gracefully** — 依赖不可用时降级 | NFR-065-03 覆盖 Registry 扫描失败、Engine 不可用、单文件解析失败三种降级 | NFR-065-03 | PASS |
| 7 | **User-in-Control** — 审批 / 取消 / 策略可配 | FR-065-06（HITL 审批 + 取消）、FR-065-03 AC-04/AC-05（resume/cancel）覆盖 | FR-065-06, FR-065-03 | PASS |
| 8 | **Observability is a Feature** — 可追踪、可查询 | FR-065-08（事件流）+ FR-065-09（管理 API）覆盖 | FR-065-08, FR-065-09 | PASS |
| 9 | **不猜关键配置** | 不直接相关（Pipeline 定义为静态文件） | 约束 §2 | N/A |
| 10 | **Bias to Action** | 不直接相关（本 Feature 提供工具，不改变 Agent loop 行为） | — | N/A |
| 11 | **Context Hygiene** | FR-065-07 AC-03 限制注入格式简洁、单条不超过 3 行 | FR-065-07 | PASS |
| 12 | **记忆写入治理** | 不直接相关（Pipeline 不涉及 Memory SoR 写入） | — | N/A |
| 13 | **失败必须可解释** | FR-065-01 AC-05（结构化错误）、FR-065-03 AC-06/AC-07（验证失败/未找到均返回结构化错误） | FR-065-01, FR-065-03 | PASS |
| 13A | **优先上下文而非堆硬策略** | FR-065-07 通过 system prompt 注入上下文引导决策 + trigger_hint 语义引导 | FR-065-07 | PASS |
| 14 | **A2A 协议兼容** | FR-065-05 AC-02 使用 `DelegationTargetKind.GRAPH_AGENT` + Work 对象，与 A2A Task 模型对齐 | FR-065-05 | PASS |
| — | **NG4: 不把所有工作流都图化** | 约束 §2 明确"Pipeline 定义是静态的，LLM 不能动态创建图结构" | 约束 | PASS |

### Constitution 合规性缺口

| # | 缺口描述 | 建议 | 严重度 |
|---|----------|------|--------|
| C-GAP-1 | 原则 8 要求"敏感原文不得直接写入 Event payload"，spec 未明确 Pipeline 事件的 payload 脱敏策略（如节点执行参数可能包含敏感数据） | 在 FR-065-08 或 NFR-065-04 中增加 AC：Pipeline 事件 payload 中的工具参数必须经过脱敏处理，敏感字段使用 artifact 引用 | 中 |
| C-GAP-2 | 原则 13 要求失败分类（模型/解析/工具/业务），spec 中 Pipeline FAILED 状态缺少失败分类字段 | 在 FR-065-05 或 FR-065-08 中增加 AC：Pipeline run 进入 FAILED 时，metadata 必须包含 failure_category（tool / gate / timeout / handler_missing 等）和 recovery_hint | 中 |
| C-GAP-3 | 原则 7 要求"用户可通过 Policy Profile 调整门禁行为"，spec 未说明 Pipeline gate 节点的审批策略是否可通过 Policy Profile 配置为自动批准 | 在 FR-065-06 中增加说明：gate 节点审批行为遵循 Policy Engine Profile 配置（支持 auto-approve / ask / deny） | 低 |

---

## 2. FR 完整性

检查每个 FR 的验收标准是否充分、无歧义、可验证。

| FR | AC 数量 | 完整性评价 | 缺口 | 状态 |
|----|---------|-----------|------|------|
| FR-065-01 (PIPELINE.md) | 6 | 格式定义清晰，解析验证规则明确 | 未定义 `nodes.type` 枚举中 `tool` 与 `skill` 的区别逻辑；示例中使用 `tool` 类型但 AC-02 列出了 `skill / tool / transform / gate / delegation` 五种，缺少各类型的语义说明 | WARN |
| FR-065-02 (Registry) | 5 | 覆盖发现、缓存、刷新、错误处理 | 缺少文件系统 watch / 热加载机制说明（当前仅手动 refresh）；是否足够？ | INFO |
| FR-065-03 (LLM 工具) | 8 | 五个 action 的 schema + 错误处理覆盖完整 | (1) 缺少 `action="list"` 时的分页/截断策略（Pipeline 数量极多时）；(2) `start` 返回的 `run_id` 格式未定义 | WARN |
| FR-065-04 (Butler 路由) | 6 | 覆盖枚举扩展、决策字段、prompt 注入、fallback | fallback 条件中"按 Pipeline tags 就近匹配 Worker 类型"的匹配算法未定义 | WARN |
| FR-065-05 (执行集成) | 8 | 覆盖 Task/Work 创建、Engine 调用、Checkpoint、事件、终态、恢复 | (1) AC-03 "SkillPipelineEngine 从 PipelineRegistry 获取" — 需明确 Engine 如何引用 Registry（依赖注入？全局单例？）；(2) 恢复策略仅提到"最后一个成功 Checkpoint"，未说明恢复时的 Task/Work 状态重建 | WARN |
| FR-065-06 (HITL) | 7 | WAITING_APPROVAL / WAITING_INPUT 双路径覆盖完整 | (1) 未定义 WAITING_INPUT 的超时机制（用户长时间不提供输入时 Pipeline 如何处理）；(2) AC-05 "复用现有渠道审批基础设施"——需确认 Telegram 侧是否已有 Pipeline run 粒度的审批 UI | WARN |
| FR-065-07 (Prompt 注入) | 5 | 覆盖 Worker/Subagent/Butler 三层 + 空列表处理 | AC-01 仅提及 Worker/Subagent，Butler 在 AC-05 单独提及；建议统一为"所有 Agent 层" | INFO |
| FR-065-08 (事件) | 6 | 事件类型、payload 结构、SSE 推送、聚合查询均覆盖 | 缺少 Pipeline 错误事件的独立定义（节点执行失败是否有专用事件类型？还是复用 PIPELINE_RUN_UPDATED + error payload？） | WARN |
| FR-065-09 (管理 API) | 6 | CRUD + 刷新覆盖完整 | (1) 缺少分页参数定义（pipeline-runs 可能量大）；(2) 缺少 `DELETE /api/pipeline-runs/{run_id}` 或清理机制 | WARN |

---

## 3. NFR 可测量性

每个 NFR 是否包含可量化的验收指标。

| NFR | 指标定义 | 可测量性 | 缺口 | 状态 |
|-----|----------|----------|------|------|
| NFR-065-01 并发安全 | "上限可配置（默认 10）" | 上限值可测量；但缺少并发隔离的具体测试标准（如两个 run 的 checkpoint 互不干扰） | 建议增加并发隔离验证用例 | WARN |
| NFR-065-02 性能 | list < 100ms, 启动延迟 < 500ms, checkpoint 异步但 at-least-once | 三项指标明确可量化 | "at-least-once 落盘"缺少衡量方式（如何验证不丢 checkpoint？需 crash test） | PASS |
| NFR-065-03 降级 | 三种降级场景均有具体行为描述 | 可通过故障注入测试验证 | 无 | PASS |
| NFR-065-04 安全 | gate 需 Policy Engine、handler_id 需权限检查、无 secrets | 可通过安全审计测试验证 | 缺少"PIPELINE.md 不得包含 secrets"的检测机制说明（静态扫描？加载时检查？） | WARN |

---

## 4. 风险覆盖度

对照 spec 中列出的风险表，检查遗漏。

| # | spec 已识别风险 | 缓解措施充分性 | 状态 |
|---|----------------|---------------|------|
| R1 | handler_id 引用不存在 | 静态验证 + 运行时 FAILED + 错误事件 — 充分 | PASS |
| R2 | 并发资源竞争 | 上限配置 + Loop Guard — 充分 | PASS |
| R3 | Butler DELEGATE_GRAPH 误判 | trigger_hint + fallback — 充分 | PASS |
| R4 | 格式演进不兼容 | version 字段 + 分支解析 — 充分 | PASS |
| R5 | 节点长时间阻塞 | timeout_seconds + Loop Guard — 充分 | PASS |
| R6 | Engine 外部调用暴露内部不变量 | 参数校验层 — 充分 | PASS |

### 未识别风险

| # | 风险描述 | 等级 | 建议缓解措施 |
|---|----------|------|-------------|
| R-NEW-1 | **Pipeline 定义热更新冲突**：正在运行的 Pipeline 对应的 PIPELINE.md 被修改或删除，run 使用的 definition 与文件系统不一致 | 中 | Pipeline run 启动时快照 definition（或绑定 version），运行期间不受文件系统变更影响 |
| R-NEW-2 | **循环引用**：PIPELINE.md 中 `next` 字段构成环路，导致 Pipeline 无限循环 | 中 | FR-065-01 解析器增加 DAG 环检测（AC-03 已有部分验证但未明确提及环检测） |
| R-NEW-3 | **Pipeline Token 预算失控**：Pipeline 中多个节点依次调用 LLM（如 `skill` 类型节点），总 token 消耗可能超出预期 | 低 | 复用 Feature 062 Loop Guard 的 token budget；在 FR-065-05 中增加 AC：Pipeline run 的 token 消耗纳入 Loop Guard 预算 |
| R-NEW-4 | **Checkpoint 恢复后上下文丢失**：恢复时仅有 checkpoint 状态，但节点可能依赖运行时上下文（如临时文件、网络连接） | 中 | 在 spec 约束中明确：Pipeline 节点 handler 必须设计为幂等/可重入，恢复时重新执行当前节点而非从中间状态续行 |

---

## 5. 接口契约完整性

检查 spec 中定义的所有对外接口（工具 schema、REST API、数据模型、事件 payload）是否契约完整。

### 5.1 `graph_pipeline` 工具 Schema

| 检查项 | 状态 | 说明 |
|--------|------|------|
| action 枚举完整 | PASS | list / start / status / resume / cancel 五个 action |
| 必填/可选参数标注 | PASS | 每个 action 的必填/可选字段已标注 |
| 返回值结构定义 | WARN | 仅描述了语义（"返回摘要信息"、"返回 run_id"），未给出 JSON schema 或 TypedDict 定义 |
| 错误响应结构 | WARN | AC-06/AC-07 提到"结构化错误"，但未定义错误码 / 错误字段结构 |
| 工具 side-effect 等级声明 | FAIL | Constitution 原则 3 要求工具声明副作用等级（none/reversible/irreversible），spec 未为 graph_pipeline 各 action 声明副作用等级 |

### 5.2 REST API

| 检查项 | 状态 | 说明 |
|--------|------|------|
| 端点列表完整 | PASS | 5 个端点覆盖 CRUD + refresh |
| 请求参数定义 | WARN | 筛选参数（task_id / pipeline_id / status）已提及但缺少分页（page / page_size） |
| 响应 schema 定义 | WARN | AC 仅描述返回内容语义，未给出 JSON schema |
| 错误响应格式 | PASS | AC-06 要求与现有 `/api/skills` 风格一致 |
| 认证/鉴权 | INFO | 单用户系统，未提及认证——符合 v0.x 定位 |

### 5.3 数据模型

| 检查项 | 状态 | 说明 |
|--------|------|------|
| PipelineManifest 字段完整 | PASS | FR-065-02 AC-02 定义了完整字段列表 |
| ButlerDecision 扩展 | PASS | FR-065-04 AC-02 定义了 pipeline_id 可选字段 |
| ButlerDecisionMode 扩展 | PASS | FR-065-04 AC-01 定义了 DELEGATE_GRAPH 枚举值 |
| PipelineCheckpoint 与现有模型关系 | WARN | spec 复用现有 PipelineCheckpoint 但未说明是否需要扩展字段 |

### 5.4 事件 Payload

| 检查项 | 状态 | 说明 |
|--------|------|------|
| PIPELINE_RUN_UPDATED payload | PASS | AC-01/AC-03/AC-04 定义了必含字段 |
| PIPELINE_CHECKPOINT_SAVED payload | PASS | AC-02/AC-04 定义了必含字段 |
| 事件与 SSE 映射 | PASS | AC-05 明确复用现有 SSE 基础设施 |
| 事件 payload 大小控制 | WARN | 未定义 payload 大小上限（如节点输出过大时是否截断） |

---

## 汇总

| 维度 | PASS | WARN | FAIL | INFO |
|------|------|------|------|------|
| Constitution 合规性 | 13 | 0 | 0 | 2 |
| Constitution 缺口 | — | 2 (中) | 0 | 1 (低) |
| FR 完整性 | 0 | 7 | 0 | 2 |
| NFR 可测量性 | 2 | 2 | 0 | 0 |
| 风险覆盖度 | 6 | 0 | 0 | 0 |
| 新增风险 | — | 3 (中) | 0 | 1 (低) |
| 接口契约完整性 | 9 | 6 | 1 | 1 |

### 关键行动项（按优先级排序）

1. **[FAIL]** 为 `graph_pipeline` 工具各 action 声明副作用等级（Constitution 原则 3 硬性要求）
2. **[WARN/中]** 补充 Pipeline 事件 payload 脱敏策略（Constitution 原则 8）
3. **[WARN/中]** Pipeline FAILED 状态增加失败分类 + 恢复提示（Constitution 原则 13）
4. **[WARN/中]** FR-065-01 解析器增加 DAG 环检测（风险 R-NEW-2）
5. **[WARN/中]** 明确 Pipeline run 启动时快照 definition 的策略（风险 R-NEW-1）
6. **[WARN]** 为 `graph_pipeline` 工具和 REST API 补充返回值 / 错误码的结构化 schema
7. **[WARN]** REST API 端点补充分页参数定义
8. **[WARN]** FR-065-06 补充 WAITING_INPUT 超时机制
