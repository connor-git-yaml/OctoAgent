# §5 需求（Requirements）

> 本文件是 [blueprint.md](../blueprint.md) §5 的完整内容。

---

### 5.1 功能需求（Functional Requirements）

> 以 "必须/应该/可选" 分级。v0.1 以"必须 + 少量应该"为主。
> 里程碑标注约定：`[Mx]` 表示该需求最早必须落地的里程碑；`[Mx-My]` 表示分阶段交付。

#### 5.1.1 多渠道接入（Channels）

- FR-CH-1（必须，[M0-M1]）：支持 WebChannel
  - [M0] 提供 Task 面板（task 列表、状态、事件、artifact）
  - [M0] 提供事件流可视化（EventStream）
  - [M1] 提供基础 Chat UI（SSE/WS 流式输出）
  - [M1] 提供 Approvals 面板（待审批动作）

- FR-CH-2（必须，[M2]）：支持 TelegramChannel
  - 支持 webhook 或 polling（默认 webhook）
  - 支持 pairing/allowlist（绑定用户/群）
  - thread_id 映射规则稳定（DM/群）

- FR-CH-3（应该，[M2]）：支持 Chat Import Core（导入通用内核）
  - 提供 `octo import chats` CLI 入口
  - 支持 `--dry-run` 预览与 `ImportReport`
  - 支持增量导入去重
  - 支持窗口化摘要（chatlogs 原文 + fragments 摘要）
  - 支持在 chat scope 内维护 SoR（例如群规/约定/持续项目状态）

- FR-CH-4（可选，[M3]）：微信导入插件（Adapter）
  - 解析微信导出格式 → NormalizedMessage 批量投递给 Chat Import Core

- FR-CH-5（应该，[M2]）：统一操作收件箱与移动端等价控制
  - Web 与 Telegram 必须共享 approvals / pairing / watchdog alerts / retry / cancel 的操作语义
  - 必须展示 pending 数量、过期时间、最近一次动作结果，避免用户只能读日志定位状态
  - 高风险动作在不同渠道的审批结果必须落同一事件链，禁止出现"Web 可做、Telegram 不可追溯"的分叉行为

#### 5.1.2 Task / Event / Artifact（任务系统）

- FR-TASK-1（必须，[M0+]）：Task 生命周期管理
  - 状态：`CREATED → QUEUED → RUNNING → (WAITING_INPUT|WAITING_APPROVAL|PAUSED) → (SUCCEEDED|FAILED|CANCELLED|REJECTED)`
  - 终态：SUCCEEDED / FAILED / CANCELLED / REJECTED
  - REJECTED：策略拒绝或 Worker 能力不匹配时使用，区别于运行时 FAILED
  - 支持 retry / resume / cancel

- FR-TASK-2（必须，[M0]）：事件流（Event Stream）
  - 对外提供 SSE：`/stream/task/{task_id}`
  - 每条事件有唯一 id、类型、时间、payload、trace_id

- FR-TASK-3（必须，[M0-M1+]）：Artifact 产物管理
  - 多 Part 结构：单个 Artifact 可包含多个 Part（text/file/json/image），对齐 A2A Artifact.parts
  - 支持 inline 内容与 URI 引用双模（小内容 inline，大文件 storage_ref）
  - artifact 版本化，任务事件中引用 artifact_id
  - [M1+] 流式追加：支持 append 模式逐步生成产物（如实时日志、增量报告）
  - 完整性：保留 hash + size 校验（A2A 没有但我们需要）

- FR-TASK-4（应该，[M1.5]）：Checkpoint（可恢复快照）
  - Graph 节点级 checkpoint（至少保存 node_id + state snapshot）
  - 支持"从最后成功 checkpoint 恢复"而不是全量重跑

#### 5.1.3 Orchestrator + Workers（多代理/分层）

- FR-A2A-1（必须，[M1.5]）：Butler（主 Agent / 主执行者 + 监督者）负责：
  - 当前阶段作为**唯一对用户负责的发言人**，同时是主要执行者
  - 拥有自己的 `ButlerSession`、`ButlerMemory` 与 Recall runtime
  - 目标理解与分类，直接处理用户请求
  - Worker 创建、选择与 A2A 派发（类似 Agent Zero 的 Agent0，但多了创建 Worker 的能力）
  - 全局停止条件与监督（看门狗策略）
  - 高风险动作 gate（审批/规则/双模校验）
  - 永远以 Free Loop 运行，不做模式选择
  - 绑定一个 Project，是该 Project 的所有者之一
  - 后续若开放"用户直连 Worker"表面，也必须创建独立 `DirectWorkerSession`，不得绕过 Butler 语义偷改主链

- FR-A2A-2（必须，[M1.5]）：Workers（持久化自治智能体）具备：
  - 独立 Free Loop（LLM 驱动，自主决策下一步），类似 Agent Zero 的 Agent0
  - 独立 `WorkerSession`、`WorkerMemory` 与 Recall runtime（避免主会话带宽瓶颈）
  - 独立 persona / tool set / capability set / permission set / auth context
  - 每个 Worker 工作时绑定一个 Project，是该 Project 的所有者之一
  - 当 Butler 派发任务且 Worker 无合适 Project 时，可动态创建新 Project（对应独立的上下文与行为文件）
  - 默认通过 Butler 下发的 A2A context capsule 获得任务上下文，而不是直接读取完整用户历史
  - 可调用 Skill Pipeline（Graph）执行确定性子流程
  - 可创建 Subagent 处理子任务
  - 可隔离执行环境（Docker/SSH）
  - 可回传事件与产物
  - 可被中断/取消，并推进终态

- FR-A2A-2b（必须，[M2]）：Subagent（临时智能体）具备：
  - 由 Worker 按需创建的临时 LLM 驱动代理体，以 Free Loop 运行
  - 不拥有 Project，共享所属 Worker 的 Project 上下文与行为文件
  - 被分配一个临时角色（persona），复用 Worker 的 tool set / capability set
  - 任务完成后结束生命周期，临时内容（session、conversation、中间产物）全部回收
  - 最终产物回传给 Worker 后，Subagent 的 A2AConversation 可归档或删除

- FR-A2A-3（应该，[M2]）：A2A-Lite 内部协议
  - Butler 与 Worker 之间使用统一、**message-native** 的消息 envelope
  - 支持 TASK/UPDATE/CANCEL/RESULT/ERROR/HEARTBEAT
  - `A2AConversation`、`A2AMessage`、`WorkerSession` 必须是一等可审计对象
  - 内部状态为 A2A TaskState 超集，通过 A2AStateMapper 双向映射
  - Worker ↔ 外部 SubAgent 通信时使用标准 A2A TaskState
  - 不接受"只做 envelope 适配、实际仍是进程内直调"的半实现作为最终验收

#### 5.1.4 Skills / Tools（能力沉淀与治理）

- FR-TOOL-1（必须）：工具契约化（schema 反射）
  - 从函数签名+类型注解+docstring 生成 JSON Schema
  - 工具必须声明 metadata：risk_level、side_effect、timeout、idempotency_support

- FR-TOOL-2（必须）：工具调用必须结构化
  - LLM 只能输出 tool_calls（JSON），由系统执行并回灌结构化结果
  - 工具输出超阈值必须压缩（summary + artifact）

- FR-TOOL-3（必须）：工具权限门禁（Policy Engine）
  - 默认 allow/ask/deny
  - irreversible 默认 ask（除非白名单策略）
  - 支持 per-project / per-channel / per-user 策略覆盖

- FR-SKILL-1（应该）：Skill 框架（Pydantic）
  - 每个 skill 明确 InputModel/OutputModel
  - 明确 `permission_mode=inherit|restrict`、retry_policy，以及作为可选收窄器的 `tools_allowed`
  - 可单元测试与回放

- FR-TOOLRAG-1（可选）：Tool Index + 动态注入（Tool RAG）
  - 使用向量数据库（LanceDB）做工具 embedding 检索与注入
  - 支持按 description + 参数 + tags + examples 索引
  - runtime 必须同时暴露 `mounted_tools / blocked_tools / recommended_tools`；`recommended_tools` 只是推荐子集，不得再充当唯一真实工具宇宙

#### 5.1.5 记忆系统（Memory）

- FR-MEM-1（必须）：记忆双线
  - Fragments（事件线/可追溯）+ SoR（权威线/可覆盖）
  - SoR 必须版本化：`current/superseded`，同 subject_key 永远只有 1 条 current

- FR-MEM-2（必须）：记忆写入治理
  - 模型先生成 WriteProposal（ADD/UPDATE/DELETE/NONE）
  - 仲裁器验证合法性、冲突检测、证据引用 → commit

- FR-MEM-3（应该）：分区（Vault）
  - 支持敏感数据分区与授权检索（默认不检索）

- FR-MEM-4（可选）：文档知识库增量更新（doc_id@version）
  - doc_hash 检测变更，chunk 内容寻址，增量嵌入

#### 5.1.6 执行层（JobRunner & Sandboxing）

- FR-EXEC-1（必须）：JobRunner 抽象
  - backend：local_docker（默认），ssh（可选），remote_gpu（可选）
  - 统一语义：start/stream_logs/cancel/status/artifacts/attach_input

- FR-EXEC-2（必须）：默认隔离执行
  - 代码执行、脚本运行默认进 Docker
  - 默认禁网；按需开网（白名单）

- FR-EXEC-3（应该）：Watchdog
  - 检测无进展（基于事件/日志/心跳）
  - 自动提醒/自动降级/自动 cancel（策略可配）

- FR-EXEC-4（应该，[M2]）：长任务交互式控制
  - 用户可查看实时 stdout/stderr、最近产物与当前步骤，并在必要时发送确认输入或主动中断
  - 交互输入、重试、取消都必须事件化并可回放，不能只存在于临时终端会话

#### 5.1.7 模型与认证（Provider）

- FR-LLM-1（必须，[M1]）：统一模型出口（LiteLLM Proxy）
  - 业务侧只用 model alias，不写厂商型号
  - 支持 fallback、限流、成本统计

- FR-LLM-2（应该）：双模型体系
  - cheap/utility 模型用于摘要/抽取/压缩/路由
  - main 模型用于规划/高风险确认/复杂推理

#### 5.1.8 管理与运维

- FR-OPS-1（必须）：配置与版本
  - config 可分：system / user / project / plugin
  - 任何配置变更生成事件并可回滚

- FR-OPS-2（必须）：最小可用可观测
  - logs：结构化日志（task_id/trace_id）
  - metrics：任务数、失败率、模型消耗、工具耗时
  - traces：至少对模型调用与工具调用打点

- FR-OPS-3（应该，[M2]）：引导式上手与诊断修复
  - `octo config`、`octo doctor`、`octo onboard` 必须形成连续的首次使用路径，覆盖 provider、channel、runtime、首次发消息验证
  - 配置流程必须可恢复：中断后能从上次步骤继续，而不是要求用户重头再做
  - 诊断输出必须给出可执行修复动作，而非仅输出原始报错

- FR-OPS-4（应该，[M2]）：自助备份/恢复与会话导出
  - Web/CLI 都应支持触发 backup/export，覆盖 tasks / events / artifacts / chats / config 元数据
  - restore 必须支持 dry-run、冲突提示与最近一次恢复验证时间，避免"只有 shell 脚本可恢复"

### 5.2 非功能需求（Non-functional Requirements）

- NFR-1：可靠性
  - 单机断电/重启后不丢任务元信息
  - 插件崩溃不应拖死主进程（隔离/超时/熔断）

- NFR-2：安全与隐私
  - secrets 不进 prompt
  - Vault 分区默认不可检索
  - 所有外部发送类动作必须门禁

- NFR-3：可维护性
  - 明确模块边界与协议
  - 核心数据模型版本化
  - 具备测试基线（unit + integration）

- NFR-4：性能与成本
  - 普通交互响应：< 2s 起流（可用 cheap 模型）
  - 任务成本可视；支持预算阈值与自动降级策略

- NFR-5：可扩展性
  - 新增 channel / tool / skill / memory backend 不应修改核心内核逻辑（或改动极小）

---
