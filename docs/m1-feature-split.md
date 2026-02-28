# M1 Feature 拆分建议

> **文档类型**: 里程碑拆分建议（Implementation Planning）
> **依据**: Blueprint §14（M1 定义）+ Feature 001（M0 交付经验）
> **状态**: Draft — 待 Owner 确认后冻结
> **日期**: 2026-02-28

---

## 1. 背景与动机

### 1.1 M0（Feature 001）交付度量

| 指标 | 数值 |
|------|------|
| 总任务数 | 68 |
| FR 覆盖 | 31/31 (100%) |
| User Story | 12 (P1×8 + P2×4) |
| Phase 数 | 14 |
| 测试数 | 105 (22 个测试文件) |
| 技术域 | 1 个（Task/Event/Artifact 持久化 + REST API + 最小 UI） |
| 新增 package | 2（`packages/core` + `apps/gateway`） |
| 前端页面 | 2（TaskList + TaskDetail） |
| 质量 | FR 100%、SC 8/8、Constitution 8/8、pytest 105/105、tsc 0 errors |

**关键经验**：M0 scope 清晰（单一技术域）、依赖链单一，因此质量极高。spec 31 条 FR 的规模对 review 和验证都在可控范围内。

### 1.2 M1 原始定义（Blueprint §14）

Blueprint 对 M1 的定义为"最小智能闭环"，预估 2 周：

- [ ] 接入 LiteLLM Proxy + 双模型 alias 配置（cheap/main 分离）
- [ ] 实现 Pydantic Skill Runner（结构化输出）
- [ ] 工具 schema 反射 + ToolBroker 执行
- [ ] Policy Engine（allow/ask/deny）+ Approvals UI
- [ ] 工具输出压缩（summarizer）

验收标准：

- LLM 调用 → 结构化输出 → 工具执行 端到端通过
- irreversible 工具触发审批流，approve 后继续执行
- 工具 schema 自动反射与代码签名一致（contract test 通过）
- 每次模型调用生成 cost/tokens 事件
- cheap/main alias 路由正确（summarizer 走 cheap，planner 走 main）

### 1.3 问题分析

M1 跨越 **4 个独立技术域**，如果作为单个 feature 实现：

| 风险 | 说明 |
|------|------|
| spec 过大 | 预估 80-100 条 FR，review 成本高，遗漏风险大 |
| 依赖交叉 | LiteLLM → Skill → Tool → Policy 形成长链，任一环节阻塞则全部停滞 |
| 无法增量验证 | 必须全部完成才能端到端测试，反馈周期过长 |
| 上下文溢出 | 单次 spec-driven 流程的上下文窗口压力过大 |

**结论**：M1 应拆分为多个 feature，每个 feature 独立 spec、独立实现、独立验证。

---

## 2. 拆分方案：4 个 Feature

### 2.1 依赖关系

```
Feature 002 ──→ Feature 003 ──→ Feature 004 ──→ Feature 005
  LiteLLM         Tool Contract    Skill Runner     Policy Engine
  + 成本治理       + ToolBroker     + 结构化输出      + Approvals
                  + 输出压缩                        + Chat UI
```

每个 feature 在前一个 feature 交付后开始，确保增量可验证。

### 2.2 与 Blueprint FR/需求 的映射

| Feature | Blueprint FR | Blueprint 设计章节 | M1 验收标准覆盖 |
|---------|-------------|-------------------|----------------|
| 002 | FR-LLM-1, FR-LLM-2 | §8.9 Provider Plane | ④⑤ cost/tokens + alias 路由 |
| 003 | FR-TOOL-1, FR-TOOL-2 | §8.5 Tooling | ③ schema 反射 contract test |
| 004 | FR-SKILL-1 | §8.4 Skill | ① LLM→结构化输出→工具执行 |
| 005 | FR-TOOL-3, FR-CH-1[M1] | §8.6 Policy Engine | ② irreversible 审批流 |

---

## 3. Feature 详细定义

### Feature 002: LiteLLM Proxy 集成 + 成本治理

**一句话目标**：替换 M0 的 Echo 模式，接入真实 LLM，建立统一模型出口和成本可见性。

#### 范围

| 维度 | 内容 |
|------|------|
| Blueprint FR | FR-LLM-1（统一模型出口）、FR-LLM-2（双模型体系） |
| 新增 package | `packages/provider`（LiteLLM client wrapper + cost model） |
| 改造模块 | `apps/gateway/services/llm_service.py`（EchoProvider → LiteLLM） |
| 数据模型 | ModelCallResult 扩展 cost/tokens 真实数据 |
| API 变更 | `/ready` 增加 `litellm_proxy` profile 检查 |
| 配置 | LiteLLM Proxy 连接配置、alias 映射表、fallback 策略 |

#### 关键交付

1. **LiteLLM Proxy 部署配置**：docker-compose 或独立进程，至少 1 个 provider 可用
2. **packages/provider 包**：
   - `LiteLLMClient`：封装 litellm SDK 调用，使用 model alias
   - `AliasRegistry`：cheap/main alias 映射（router/extractor/planner/executor/summarizer/fallback）
   - `CostTracker`：解析 litellm 返回的 usage → cost 计算
   - `FallbackManager`：provider 失败自动切换
3. **改造现有 LLM 服务**：
   - `LLMService` 从 `EchoProvider` 切换到 `LiteLLMProvider`
   - 保留 `EchoProvider` 作为测试用 fallback
   - MODEL_CALL 事件写入真实 cost/tokens/model_alias/provider
4. **Readiness 扩展**：`/ready` 增加 `llm` profile（检测 LiteLLM Proxy 可达性）

#### 验收标准

- [ ] cheap alias 和 main alias 分别路由到不同模型
- [ ] 每次模型调用事件包含真实 cost/tokens/latency/provider
- [ ] LiteLLM Proxy 不可达时降级到 fallback provider（或 Echo 模式）
- [ ] `/ready?profile=llm` 正确检测 LiteLLM Proxy 状态

#### 预估规模

- 任务数：~20-25
- FR 条数：~8-12
- 工期：3-4 天

#### 前置条件（Blueprint §16.2）

- [ ] LiteLLM Proxy 就绪：至少 1 个 provider 可用 + cheap/main 两个 alias 配通

---

### Feature 003: 工具契约 + ToolBroker + 输出压缩

**一句话目标**：建立工具治理基础设施——工具可声明、可反射、可执行、输出可压缩。

#### 范围

| 维度 | 内容 |
|------|------|
| Blueprint FR | FR-TOOL-1（工具契约化）、FR-TOOL-2（工具调用结构化） |
| Blueprint 设计 | §8.5（Tooling）全面实现 |
| 新增 package | `packages/tooling`（schema 反射 + ToolBroker + ToolResult） |
| 新增事件类型 | TOOL_CALL_STARTED, TOOL_CALL_COMPLETED, TOOL_CALL_FAILED |
| 数据模型 | ToolMeta, ToolCall, ToolResult |
| 依赖 | Feature 002（cheap alias 用于输出压缩 summarizer） |

#### 关键交付

1. **ToolMeta 数据模型**（对齐 §8.5.2）：
   ```yaml
   ToolMeta:
     tool_id: "namespace.name"
     version: "hash or semver"
     side_effect: none | reversible | irreversible
     risk_level: low | medium | high
     timeout_s: 30
     idempotency: supported | required | not_supported
     outputs:
       max_inline_chars: 4000
       store_full_as_artifact: true
   ```
2. **Schema 反射引擎**：
   - 从函数签名 + 类型注解 + docstring 自动生成 JSON Schema
   - Contract Test：反射出的 schema 与代码签名一致性验证
   - 工具注册表（ToolRegistry）
3. **ToolBroker**：
   - 工具执行编排（sequential / parallel / mixed）
   - 超时控制 + 取消
   - 结构化 ToolResult 回灌
   - TOOL_CALL_STARTED / COMPLETED / FAILED 事件生成
4. **输出压缩**（对齐 §8.5.4）：
   - 工具输出 > N 字符 → 全量存 artifact + summary 回灌上下文
   - summary 通过 cheap alias（summarizer）生成
5. **示例工具**：至少实现 2-3 个内置工具（如 `system.echo`、`system.datetime`、`system.file_read`）用于端到端验证

#### 验收标准

- [ ] 工具 schema 自动反射与代码签名一致（contract test 通过）
- [ ] ToolBroker 执行工具并生成完整事件链（STARTED → COMPLETED/FAILED）
- [ ] 工具输出超阈值时自动压缩，summary 存入上下文、全量存 artifact
- [ ] 工具超时时正确生成 TOOL_CALL_FAILED 事件

#### 预估规模

- 任务数：~30-35
- FR 条数：~12-16
- 工期：4-5 天

---

### Feature 004: Pydantic Skill Runner（结构化输出框架）

**一句话目标**：建立 Skill 运行时——LLM 产生结构化输出，可调用工具，可重试，可验证。

#### 范围

| 维度 | 内容 |
|------|------|
| Blueprint FR | FR-SKILL-1（Skill 框架） |
| Blueprint 设计 | §8.4（Skill 运行语义）全面实现 |
| 新增模块 | Skill manifest + SkillRunner + SkillRegistry |
| 集成 | Pydantic AI（structured output + tool_calls 解析） |
| 依赖 | Feature 002（LLM 调用）、Feature 003（ToolBroker 执行工具） |

#### 关键交付

1. **Skill Manifest**（对齐 §8.4.1）：
   ```yaml
   SkillManifest:
     skill_id: "namespace.name"
     version: "0.1"
     input_model: InputModel (Pydantic)
     output_model: OutputModel (Pydantic)
     model_alias: "main"
     tools_allowed: ["system.echo", "system.file_read"]
     retry_policy:
       max_attempts: 3
       upgrade_model_on_fail: true
   ```
2. **SkillRunner**（对齐 §8.4.2 运行语义）：
   - 校验输入（InputModel）
   - 调用模型（通过 LiteLLM alias）
   - 解析并校验输出（OutputModel）— Pydantic AI structured output
   - 若输出包含 tool_calls → ToolBroker 执行 → 结果回灌模型
   - OutputModel 校验失败 → 自动重试（含 model upgrade 策略）
   - 输出最终结果（校验 + 产物）
3. **SkillRegistry**：Skill 注册、发现、元数据查询
4. **示例 Skill**：至少实现 1-2 个端到端可验证的 Skill（如 `echo_skill`、`summarize_skill`）

#### 验收标准

- [ ] LLM 调用 → 结构化输出（OutputModel 校验通过）→ 工具执行 端到端通过
- [ ] OutputModel 校验失败时自动重试，重试次数不超过 max_attempts
- [ ] Skill 内 tool_calls 通过 ToolBroker 执行并结果回灌
- [ ] Skill 执行全过程事件可追溯（MODEL_CALL + TOOL_CALL 事件链完整）

#### 预估规模

- 任务数：~25-30
- FR 条数：~10-14
- 工期：4-5 天

#### 技术风险

- Pydantic AI structured output 与 LiteLLM Proxy 的兼容性需提前验证
- tool_calls 解析在不同 LLM provider 间的一致性（依赖 LiteLLM 的 function calling 统一层）

---

### Feature 005: Policy Engine + Approvals + Chat UI

**一句话目标**：建立安全治理层——工具调用可门禁、可审批；用户可通过 Chat UI 交互和审批。

#### 范围

| 维度 | 内容 |
|------|------|
| Blueprint FR | FR-TOOL-3（工具权限门禁）、FR-CH-1[M1]（Chat UI + Approvals 面板） |
| Blueprint 设计 | §8.6（Policy Engine）全面实现 |
| 新增模块 | PolicyEngine + PolicyProfile + ApprovalService |
| 状态机扩展 | 激活 WAITING_APPROVAL（M0 已在 TaskStatus 枚举中预留） |
| 新增事件类型 | APPROVAL_REQUESTED, APPROVED, REJECTED |
| 新增 API | `POST /api/approve/{approval_id}`（审批决策） |
| 前端 | Approvals 面板 + 基础 Chat UI（SSE 流式输出） |
| 依赖 | Feature 003（ToolMeta 的 side_effect/risk_level）、Feature 004（Skill 运行时集成） |

#### 关键交付

1. **PolicyEngine 核心**（对齐 §8.6.1-8.6.2）：
   - 输入：tool_call / action_plan / task_meta
   - 输出：Decision（allow / ask / deny）
   - 默认策略：irreversible → ask；reversible → allow；read-only → allow
   - 所有外部发送/支付/删除 → ask
2. **PolicyProfile 配置**（对齐 §8.6.2 策略可配）：
   - per-project / per-channel 策略覆盖
   - 策略变更生成事件，可审计可回滚
   - safe by default，用户可调整
3. **审批工作流**（对齐 §8.6.3）：
   - 触发 ask → APPROVAL_REQUESTED 事件 → Task 进入 WAITING_APPROVAL
   - 用户 approve → APPROVED 事件 → Task 回到 RUNNING，Skill 继续执行
   - 用户 reject → REJECTED 事件 → Task 进入终态
   - 审批超时策略（可配）
   - 审批载荷：action summary, risk explanation, idempotency_key
4. **Approvals REST API**：
   - `POST /api/approve/{approval_id}` — 审批决策
   - `GET /api/approvals` — 待审批列表
5. **前端扩展**：
   - **Approvals 面板**：待审批动作列表，支持 approve/reject 操作
   - **基础 Chat UI**：消息输入框 + SSE 流式输出展示（替代 M0 的纯展示 UI）
6. **Skill 运行时集成**：
   - SkillRunner 第 4 步（tool_calls）插入 PolicyEngine 判定
   - ask 决策时暂停 Skill 执行，等待审批结果

#### 验收标准

- [ ] irreversible 工具触发审批流，approve 后继续执行，reject 后终止
- [ ] read-only 和 reversible 工具默认 allow，无需审批
- [ ] PolicyProfile 配置变更生成事件并可审计
- [ ] Approvals 面板正确展示待审批动作，支持 approve/reject
- [ ] Chat UI 可发送消息并展示 SSE 流式响应
- [ ] 审批超时按配置策略执行（默认 deny 或 escalate）

#### 预估规模

- 任务数：~35-40
- FR 条数：~14-18
- 工期：5-7 天

#### Constitution 对齐

| 宪法原则 | Feature 005 中的体现 |
|----------|---------------------|
| C4: Side-effect Must be Two-Phase | Plan → Gate（Policy Engine）→ Execute |
| C7: User-in-Control + 策略可配 | 审批流 + PolicyProfile 可配 + 默认 safe |
| C8: Observability is a Feature | 审批事件可追溯 + Approvals 面板 |

---

## 4. 总量预估与 M0 对比

| 指标 | M0 (Feature 001) | M1 (Feature 002-005) |
|------|-------------------|----------------------|
| Feature 数 | 1 | 4 |
| 总任务数 | 68 | ~110-130 |
| 总 FR 数 | 31 | ~44-60 |
| 新增 package | 2 | 2-3 |
| 新增前端页面 | 2 | 2（Chat + Approvals） |
| 新增事件类型 | 8 | ~6（TOOL_CALL×3 + APPROVAL×3） |
| 预估总工期 | 10-13 天 | ~16-21 天（与 blueprint "2 周" 基本一致） |

---

## 5. 实施策略建议

### 5.1 推荐顺序

```
Week 1:  Feature 002（LiteLLM）→ Feature 003（Tool Contract）
Week 2:  Feature 004（Skill Runner）→ Feature 005 前半（Policy Engine 后端）
Week 3:  Feature 005 后半（Approvals UI + Chat UI）→ M1 集成验证
```

### 5.2 每个 Feature 的 Spec-Driven 流程

沿用 Feature 001 的成功模式：

1. **Research** — 产研调研 + 技术调研
2. **Spec** — 功能需求规范（FR + US + Edge Cases + Constraints）
3. **Plan** — 任务拆分 + 依赖关系 + 并行策略
4. **Implement** — 按 Phase 逐步实现
5. **Verify** — Layer 1（Spec-Code 对齐）+ Layer 2（原生工具链）+ Constitution 合规

### 5.3 风险缓解

| 风险 | 缓解措施 |
|------|---------|
| LiteLLM Proxy 部署/配置阻塞 | Feature 002 开始前完成 LiteLLM Proxy 部署验证（§16.2 前置条件） |
| Pydantic AI + LiteLLM 兼容性 | Feature 004 spec 阶段做 PoC spike，验证 structured output + tool_calls |
| Policy Engine 复杂度膨胀 | M1 仅实现最小策略模型（基于 ToolMeta.side_effect 的规则引擎），不引入 ML 策略 |
| 前端工作量低估 | Chat UI 和 Approvals 面板控制在最小可用范围，不追求交互体验 |

### 5.4 备选方案：3 Feature 合并版

如果觉得 4 个 feature 粒度过细，可将 003 和 004 合并：

| 方案 | Feature 数 | 优点 | 缺点 |
|------|-----------|------|------|
| **A: 4 Feature（推荐）** | 4 | 每个 scope 清晰、可独立验证、风险隔离 | feature 间切换成本 |
| **B: 3 Feature** | 3 | 减少 spec 流程次数 | 003+004 合并后 ~55-65 任务，接近 M0 全量 |

**推荐方案 A**，理由：Feature 001 的经验表明，scope 清晰是质量的关键保障。

---

## 6. 待确认事项

以下事项建议在 Feature 002 spec 阶段前冻结：

1. **LiteLLM Proxy 部署方式**：Docker 容器还是独立进程？是否需要 docker-compose 配置？
2. **首批 provider 选择**：cheap alias 和 main alias 分别使用哪些模型/provider？
3. **首批工具清单**：Feature 003 需要实现哪些内置工具作为验证？（建议：文件读写 + 系统命令 + 日期时间）
4. **审批超时策略**：默认超时时间？超时后 deny 还是 escalate？
5. **Chat UI 最小范围**：仅文本输入+流式输出？是否需要消息历史？是否需要 Markdown 渲染？

---

## 附录 A: Blueprint 需求到 Feature 的完整映射

| Blueprint FR | 级别 | Feature | 说明 |
|-------------|------|---------|------|
| FR-LLM-1 | 必须 | 002 | 统一模型出口（LiteLLM Proxy） |
| FR-LLM-2 | 应该 | 002 | 双模型体系（cheap/main） |
| FR-TOOL-1 | 必须 | 003 | 工具契约化（schema 反射） |
| FR-TOOL-2 | 必须 | 003 | 工具调用结构化 |
| FR-SKILL-1 | 应该 | 004 | Skill 框架（Pydantic） |
| FR-TOOL-3 | 必须 | 005 | 工具权限门禁（Policy Engine） |
| FR-CH-1[M1] Chat UI | 必须 | 005 | 基础 Chat UI（SSE 流式输出） |
| FR-CH-1[M1] Approvals | 必须 | 005 | Approvals 面板 |

## 附录 B: M1 新增事件类型预览

| 事件类型 | Feature | 说明 |
|---------|---------|------|
| TOOL_CALL_STARTED | 003 | 工具调用开始（含 tool_id, 参数摘要） |
| TOOL_CALL_COMPLETED | 003 | 工具调用完成（含结果摘要, 耗时, artifact_ref） |
| TOOL_CALL_FAILED | 003 | 工具调用失败（含错误分类, 可恢复性） |
| APPROVAL_REQUESTED | 005 | 审批请求（含 action summary, risk explanation） |
| APPROVED | 005 | 审批通过 |
| REJECTED | 005 | 审批拒绝（区别于 Task 级 REJECTED） |

## 附录 C: M1 新增/激活的 TaskStatus

| 状态 | Feature | 说明 |
|------|---------|------|
| WAITING_APPROVAL | 005 | M0 已预留，M1 Feature 005 激活 |

M0 已预留但 M1 仍不激活的状态：QUEUED、WAITING_INPUT、PAUSED（推迟到 M1.5）。
