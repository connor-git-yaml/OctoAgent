# OctoAgent 项目宪法（Constitution）

> 项目名称：**OctoAgent**
> 内部代号：**ATM（Advanced Token Monster）**
> 定位：**个人智能操作系统（Personal AI OS）**
> 版本：v0.2
> 来源：docs/blueprint.md §2、docs/blueprint/architecture-audit.md §14.14

---

## 项目概述

OctoAgent 是一个个人智能操作系统（Personal AI OS），而非聊天机器人。其核心特征：

- **入口**：多渠道（Web/Telegram 起步）
- **运行宿主**：单 Gateway application host 内以任务化（Task）与事件化（Event）驱动，可观测、可恢复、可中断、可审批
- **执行**：当前已实现的 Inline/Graph 路径必须如实暴露；未实现的隔离 backend 必须拒绝而非降级到宿主高权限执行
- **记忆**：有治理（SoR/Fragments 双线 + 版本化 + 冲突仲裁 + Vault 分区）
- **模型**：统一出口（ProviderRouter 直连），alias + 策略路由
- **工具**：契约化（schema 反射）+ 动态注入（Tool RAG）+ 风险门禁（policy allow/ask/deny）

---

## I. 系统级宪章（System Constitution）

> 系统级宪章是"不可谈判的硬规则"，用于防止系统在实现过程中走偏。

### 原则 1：Durability First（耐久优先）

- **MUST**：任何长任务/后台任务必须落盘 -- Task、Event、Artifact、Checkpoint 至少具备本地持久化
- **MUST**：进程重启后，任务状态不能"消失"，要么可恢复，要么可终止到终态（FAILED/CANCELLED/REJECTED）
- **MUST NOT**：不得存在仅存在于内存中、重启即丢失的关键状态

### 原则 2：Everything is an Event（事件一等公民）

- **MUST**：模型调用、工具调用、状态迁移、审批、错误、回放，都必须生成事件记录
- **MUST**：UI/CLI 不应直接读内存状态，应以事件流/任务视图为事实来源
- **MUST NOT**：不得绕过事件系统直接修改任务状态

### 原则 3：Tools are Contracts（工具即契约）

- **MUST**：工具对模型暴露的 schema 必须与代码签名一致（单一事实源）
- **MUST**：工具必须声明副作用等级：`none | reversible | irreversible`，并进入权限系统
- **MUST NOT**：不得存在未声明副作用等级的工具

### 原则 4：Side-effect Must be Two-Phase（副作用必须二段式）

- **MUST**：不可逆操作必须拆成 `Plan`（无副作用） -> Gate（规则/人审/双模一致性） -> `Execute`
- **MUST**：任何绕过 Gate 的实现都视为严重缺陷
- **MUST NOT**：不得将不可逆操作合并为单一步骤直接执行

### 原则 5：Least Privilege by Default（默认最小权限）

- **MUST**：Gateway application runtime / Orchestrator 默认不持有高权限 secrets（设备、支付、生产配置）
- **MUST**：secrets 必须按 project/scope 分区；工具运行时按需注入
- **MUST NOT**：secrets 不得进入 LLM 上下文

### 原则 6：Degrade Gracefully（可降级）

- **MUST**：任一插件/外部依赖不可用时，系统不得整体不可用；必须支持 disable/降级路径
- **SHOULD**：降级路径应有明确的文档说明和事件记录
- **示例**：memU 插件失效 -> 记忆能力降级为本地 SQLite/FTS，不影响任务系统

### 原则 7：User-in-Control（用户可控 + 策略可配）

- **MUST**：系统必须提供审批、取消、删除等控制能力（capability always available）
- **MUST**：所有门禁（审批/取消/风险拦截）默认启用（safe by default）
- **MUST**：用户可通过策略配置（Policy Profile）调整门禁行为——包括降级、自动批准、静默执行等
- **SHOULD**：对用户已明确授权的场景（如定时任务、低风险工具链），系统应减少打扰、体现智能化
- **MUST NOT**：在无任何策略授权的情况下，不得静默执行不可逆操作

### 原则 8：Observability is a Feature（可观测性是产品功能）

- **MUST**：每个任务必须可看到：当前状态、已执行步骤、消耗、产物、失败原因与下一步建议
- **MUST**：没有可观测性的功能不可上线
- **SHOULD**：可观测数据应结构化，便于检索和分析
- **MUST**：事件与日志默认遵循最小化原则，仅记录排障和审计所需字段
- **MUST NOT**：敏感原文（secrets、凭证、隐私数据）不得直接写入 Event payload/日志，必须采用脱敏、摘要或 artifact 引用
- **SHOULD**：定义数据保留与清理策略（日志保留期、可清理副本、审计副本边界）

---

## II. 代理行为宪章（Agent Behavior Constitution）

> 代理行为宪章用于约束 Orchestrator/Worker 的行为策略（prompt + policy 的组合），避免"动作密度低""猜配置""乱写记忆"等典型事故模式。

### 原则 9：不猜关键配置与事实

- **MUST**：改配置/发命令前必须通过工具查询确认（read -> propose -> execute）
- **MUST NOT**：不得基于假设或推测执行涉及外部系统的操作

### 原则 10：默认动作密度（Bias to Action）

- **MUST**：对可执行任务，必须输出下一步"具体动作"
- **MUST NOT**：禁止无意义的"汇报-等待"循环
- **SHOULD**：动作必须满足安全门禁与可审计要求

### 原则 11：上下文卫生（Context Hygiene）

- **MUST NOT**：禁止把长日志/大文件原文直接塞进主上下文
- **MUST**：必须走"工具输出压缩/摘要 + artifact 引用"模式
- **SHOULD**：上下文中的内容应保持精简、结构化

### 原则 12：记忆写入必须治理

- **MUST NOT**：禁止模型直接写入 SoR（Source of Record）
- **MUST**：只能提出 WriteProposal，由仲裁器验证后提交
- **MUST**：写入提案必须包含证据引用和置信度

### 原则 13：失败必须可解释

- **MUST**：失败要分类（模型/解析/工具/业务）
- **MUST**：失败必须给出可恢复路径（重试、降级、等待输入、人工介入）
- **MUST NOT**：不得出现无分类、无恢复路径的失败状态

### 原则 13A：优先提供上下文，而不是堆积硬策略

- **MUST**：除权限、审批、审计、loop guard、memory 写入治理等硬边界外，系统应优先通过显式上下文、行为文件、runtime hints、工具能力与已确认事实来引导模型决策
- **SHOULD**：当模型可以基于充分上下文稳定判断时，优先减少 case-by-case 的代码特判、字符串 heuristic 和过度防御式 prompt 限制
- **MUST**：设计默认行为时，应充分信任模型在完整上下文下的理解、规划与表达能力，而不是默认把行为写死在代码分支里
- **MUST NOT**：不得因为担心模型失控，就把常见场景全部降级成僵硬的硬编码流程，导致 Agent 无法正常发挥

### 原则 14：A2A 协议兼容（A2A Protocol Compatibility）

- **MUST**：内部 Task 状态机是 A2A TaskState 的超集，保留 WAITING_APPROVAL、PAUSED、CREATED 等内部治理状态
- **MUST**：对外暴露 A2A 接口时，通过 A2AStateMapper 将内部状态映射为标准 A2A TaskState（submitted/working/input-required/completed/canceled/failed/rejected）
- **MUST**：终态包含 REJECTED（策略拒绝/能力不匹配），区别于运行时 FAILED
- **MUST NOT**：不得在主运行态 ↔ 委托运行态的内部通信中丢失内部状态精度（不降级为 A2A 状态）
- **SHOULD**：委托运行态 ↔ 外部 SubAgent 通信使用标准 A2A TaskState，确保互操作性
- **MUST**：Artifact 采用 A2A 兼容的 parts 多部分结构（text/file/json/image），同时保留 artifact_id、version、hash、size 等内部治理字段
- **MUST**：Artifact 支持 append 流式追加模式（对齐 A2A append + lastChunk）
- **SHOULD**：对外暴露 A2A Artifact 时，通过映射层转换（内部独有字段降级到 metadata）

---

## III. 技术能力约束与默认基线

> 本节区分"能力约束"与"默认实现"：能力约束是硬规则，具体库/产品是当前阶段的推荐基线，可替换但不得降低能力。

### 语言与运行时

- **MUST**：主工程使用 Python 3.12+
- **MUST**：依赖管理使用 uv
- **MUST**：生产配置、运行时选择与文档只能暴露已真实实现并通过验收的 execution backend
- **MUST**：请求未实现或不可用的隔离 backend 时必须 fail closed，不得静默回退到宿主 Inline 执行
- **MUST NOT**：没有真实隔离实现、生命周期与安全测试前，不得宣称支持 Docker/SSH/远程 sandbox
- **MUST**：delegation selector、profile capability、Worker transient backend 与历史 Event/Console projection 必须使用各自值域和类型；不得用一个 backend enum 混用四种语义
- **MUST**：用户 runtime selector 必须在业务 Task/Work/Event/cancel 前严格、大小写敏感地验证；批量输入必须先整批预检，不得按 worker type 自动重选非法值
- **MUST**：`python -m octoagent.gateway` 是唯一 production/service 启动入口；直接导入 ASGI `app` 只用于 import/test contract，不构成第二个生产启动授权
- **MUST**：module entry 必须先解析唯一受支持的 help/host/port 参数，再在 typed exception boundary 内只导入一次 canonical `main.app`；`main.app=create_app()` 是唯一 app 构造与 preflight owner，配置与 exposure validation 各执行恰一次，Uvicorn 必须接收该 app instance 和同一组 resolved host/port，不得使用 module string 形成第二入口
- **MUST**：canonical static preflight 必须在应用 front-door env > YAML mode precedence 前完整解析配置恰一次；env override 不得绕过 malformed、retired 或 unknown runtime YAML。同步可解析的 static security/runtime config invalid 必须在 Uvicorn 前 typed exit 78；真正 runtime service composition/assembly 失败只由既有 FastAPI lifespan/composition root fail closed，以 startup nonzero、readiness/request/workload副作用为0验收，不得为映射 exit 78 新增第二 preflight 或重复构造 runtime
- **MUST**：普通 service descriptor load/start/restart 不得顺手重写或迁移旧 argv；canonical、legacy、invalid schema、invalid JSON 的读取都不得产生外部写或 `.corrupted` 副本。遇 legacy direct host command 必须 typed reject 且零写入，只有显式 install/update/bootstrap 操作可以 validated atomic migration；显式 repair 必须校验 replacement 与 expected digest

### Web / API

- **MUST**：API 层使用 FastAPI + Uvicorn
- **MUST**：任务流式事件优先使用 SSE

### 数据持久化

- **MUST**：结构化数据（Task/Event/Artifact 元信息）使用 SQLite（WAL 模式）
- **MUST**：事件表 append-only
- **MUST**：SQLite 是记忆事实的 Source of Record；SQLite FTS 与 LanceDB 分别是可由 SQLite 事实重建的词法索引和向量索引
- **MUST NOT**：不得把 SQLite FTS 规定为 LanceDB 的必经中间态，也不得把任一检索索引提升为 Source of Record

### 模型访问边界

- **MUST**：统一通过 ProviderRouter 的 canonical turn/transport contract 访问模型能力
- **MUST NOT**：业务代码中不得硬编码厂商模型名，必须使用 alias
- **MUST NOT**：不得新增绕过 ProviderRouter 的 SDK、直连 HTTP 或第二套模型调用路径
- **MUST**：Provider 包只依赖更底层的模型 transport/auth 能力，不得反向 import Gateway application host
- **MUST**：Gateway → Provider 的 route DTO 只承载当前真实配置输入与env/profile凭据引用；不得为未存在的配置字段预留open headers/body或传递raw credential，Provider内置动态headers必须留在Provider auth/transport边界
- **MUST**：Gateway 与 Provider 的 distribution manifest 必须声明静态、TYPE_CHECKING 与常量动态 import 的全部直接依赖；clean-wheel 不得依赖传递安装掩盖缺项
- **MUST**：运行时 service injection 必须由 composition root 以实例级、显式且互斥的 runtime-bundle/storage-only 模式完成；不得使用 class-level mutable setter、global locator 或 `None` fallback。storage-only 构造不得隐式创建模型 runtime、加载 reranker/model、注册 background task 或访问网络
- **MUST**：已经确定的 deterministic application result 可以经窄持久化 seam 完成，但该 seam 必须复用唯一的 session/storage persistence primitive，不得接受通用 LLM override、复制 Task/Event/Artifact/checkpoint/session/turn 持久化算法、触发模型派生 compaction/extraction 或形成第二 runtime/provider path
- **MUST**：异步 runtime lifecycle 的公共关闭语义统一使用 `aclose`，共享 Router 只能由唯一 bundle/runtime owner 关闭；background drain、local model clients、Router、stores 的顺序与 exactly-once ownership 必须可测试

### Agent / Workflow

- **MUST**：数据模型使用强类型 schema（Pydantic 或等价方案）
- **SHOULD**：v0.x 默认基线的 Skill 层使用 Pydantic AI（结构化输出 + 工具调用）
- **SHOULD**：v0.x 默认基线的 Graph Engine 使用 pydantic-graph

### 可观测

- **MUST**：可观测能力需支持 OTel 语义（trace/span/context 贯通）
- **MUST**：结构化日志必须绑定 trace_id / task_id（及必要请求上下文）
- **MUST**：metrics 数据从 Event Store（SQLite）聚合查询，不引入独立 metrics 服务
- **SHOULD**：v0.x 默认基线使用 Logfire + structlog

---

## IV. 质量门控

> 确保交付质量的最低标准。

### 测试基线

- **MUST**：核心 domain models 具备单元测试
- **MUST**：事件存储的事务一致性有测试覆盖
- **MUST**：工具 schema 反射一致性有 contract test
- **MUST**：任何 Event schema/projection 变更都必须通过历史事件回放兼容测试（replay compatibility）
- **MUST**：Gateway wheel 必须在干净环境中可安装、可导入并可启动；Gateway manifest 必须声明全部直接依赖
- **MUST**：Provider wheel 必须可独立导入，且 import-direction gate 必须阻止 Provider → Gateway 的静态、延迟和动态 import
- **MUST**：退役路径与失真实现术语必须有静态门禁，历史制品例外必须精确列举
- **MUST**：runtime 热点必须有复杂度 ratchet，任何改动不得让既有热点继续恶化
- **MUST**：行为改动必须留下稳定 RED -> 最小 GREEN -> REFACTOR 证据；纯机械迁移必须声明 atomic relocation，并以 manifest/hash/absence/import contract 验收，不得伪装成单元 TDD。迁移时点的机械 snapshot 必须可从冻结 base 重放；其后的目标文件行为修改必须由独立 machine scope 与 RGR evidence 授权，不得用最终文件 raw hash 冒充迁移时点证据
- **MUST**：RED 证据必须包含实际test runner命令、失败test id与目标行为缺失原因；ledger、字符串计数、随机失败或全仓已知违规不得冒充RED
- **MUST**：RGR 证据必须机器可验证地关联行为slice、production/test node、完整命令、exit、UTC、base/tree/worktree fingerprint；正式Python/Frontend runner只能在Feature canonical run root下生成同一精确的JUnit/stdout/stderr/exit/invocation/tree六件套，不得使用别名、调用者自选路径、缺件或多件；稳定runner artifact必须重算hash、相互交叉验证并与索引记录一致，不能只相信JSONL或文本自述
- **MUST**：evidence checker必须用负面fixtures拒绝missing/fake、一致伪造但结构化runner结果不符、RGR reordered、selector mismatch、collection error、skip、确定性node rerun与blanket rerun argv
- **MUST**：RGR scope 必须以 exact path/inventory/symbol 机器映射覆盖 committed、staged、unstaged 与 untracked 最终态；跨阶段共享文件必须按稳定 AST qualname/JSON key 分区，早期 Gate 不得追索未来阶段证据
- **MUST**：Feature planned-diff 必须以 machine-readable source→target/delete 与 exact path closure 覆盖 production、tests、config、docs、scripts、frontend、workflow 与独立 lane；tree/glob删除必须用可复算的tree-prefix或exact-path matcher在冻结base展开为带object identity的exact tracked paths，根级dotfile不得依赖含糊`**`语义。changed-path只能减去逐字节/hash不变的既有用户baseline，不能宽泛排除其他Feature、历史设计目录或evidence；planned path无owner、owner path不在planned closure或changed path无owner都必须失败
- **MUST**：`declared-new` 只可标注冻结 base tree 中确实不存在的 exact path，且不自动取得 changed-path ownership；既有路径误标或只命中宽泛 declared-new glob 都必须失败
- **MUST**：为 evidence checker 自身建立 Phase0 证据时，只能使用标准 test runner、JUnit/raw/exit/invocation/tree 的冻结 transaction；生成后必须硬停。主任务必须通过唯一machine-readable anchor manifest及其外部SHA提供不可变输入，聊天文本不能作为唯一机器通道。正式checker重算anchor与artifact字节hash；missing/malformed/replaced/mixed/second-anchor均失败，runner不得生成、替换或补跑。evidence必须有Design→Phase0-RED→Implement→Verify→Final exact lifecycle，早于first_state、错误first_writer或unknown ignored path均失败
- **MUST**：lifecycle中每个committed exact artifact必须是Final必需项，并与唯一first_state、first-writer task、producer command及final required set双向闭合；可选exact path、无可达producer或producer越界多写必须失败
- **MUST**：TaskService/AgentContextService 的 storage-only 与 runtime-bundle operation、构造点及能力必须由 machine-readable allowlist 完整覆盖；未知 operation/callsite 默认拒绝，storage-only 构造或可达调用不得创建或触发 model、reranker auto-load、background task、network 或 runtime capability
- **MUST**：Feature 的活动制品与历史复审必须由 machine-readable current/superseded 清单区分；当前authority/docs必须以active authority index导出的exact path集合完整扫描并做语义审查，显式历史/退役陈述与现役/必选/完成表/运行图陈述分开判定。实现后同步必须有独立documentation slice，不得借用更早的CI/workflow GREEN证据覆盖后续文档改动
- **MUST**：测试分层以 `octoagent/tests/AGENTS.md` 为唯一执行契约；纯逻辑/model/store/service/adapter 优先 L4，bootstrap/API/Event/存储/LLM 派发使用确定性 L3，L1 只验证浏览器独有语义，L2 live 只验证真判断力或真实外部系统事实
- **MUST**：worktree 测试使用 PYTHONPATH 锁、`uv run --no-sync python -m pytest`；不得用固定 sleep、blanket rerun、宿主状态、真网络或真 LLM 掩盖下层正确性
- **MUST**：Feature 自动验证不得读取宿主凭证、依赖默认 HOME、访问真实模型/外部网络或产生外部成本；凭证存在、环境可发现或测试 skip 都不构成授权。任何 release/manual live 检查都必须在执行前由主任务核对条件并取得用户当次明确授权，且不得冒充确定性自动证据
- **MUST**：本地changed-lines coverage必须覆盖committed、staged、unstaged与untracked production最终态，并使用fresh coverage artifact；EXEMPT必须独立报告，不得冒充达到coverage阈值。Frontend不属于Python changed-lines门，必须运行完整Vitest与typecheck
- **MUST**：最终确定性验证必须包含CI并行形态`-n auto --dist=loadgroup`；新增/修改确定性node的rerun必须为0，quarantine manifest相对base不得增长，既有登记rerun必须单列并受review date治理
- **MUST**：所有新增 seam 的依赖方向保持 domain/model/contracts 被 service/application 与 adapter/infrastructure 消费，并由 composition root/UI 向下组装；下层不得 import Gateway/UI，上层不得复制下层业务规则。存量 mixed cluster 若尚未达到 clean layering，必须以 exact cross-role edge baseline 如实登记、只减不增，不得用 role tag 冒充已经完成的物理分层
- **MUST**：每个架构 Feature 都要把坏味道分为本次 must-fix、不得恶化 ratchet、后续独立 Fix；复杂度与 changed-lines coverage 不能替代职责、依赖、状态唯一性和测试 oracle 审查
- **MUST**：被迁移的 backing module 必须有直接或声明式确定性测试 owner；store覆盖roundtrip/corruption/atomic/concurrency，application经DI fake验证业务持久化结果
- **MUST**：test owner 的 direct/indirect/declarative/scheduled 状态必须由 AST import 与 collect 事实验证；planned/scheduled 不得冒充 covered，Verify 时必须归零
- **SHOULD**：关键流程有集成测试覆盖

### 安全基线

- **MUST**：secrets 不进 prompt
- **MUST**：Vault 分区默认不可检索
- **MUST**：所有外部发送类动作必须经过门禁
- **MUST**：安全配置损坏、缺少必填安全字段或 backend 不可用时必须 fail closed，不得以默认值或高权限本机路径继续运行
- **MUST**：任何自动更新/同步在检测到tracked unstaged、staged或untracked本地改动时必须在fetch/checkout/reset/merge/dependency sync前fail closed；不得通过checkout/reset静默丢弃用户修改

### 可靠性基线

- **MUST**：单机断电/重启后不丢任务元信息
- **MUST**：插件崩溃不应拖死主进程（隔离/超时/熔断）

---

## V. 关键设计取舍

> 明确记录的战略性取舍，避免实现过程中反复讨论。

1. **不追求通用多智能体平台**：先把"单体 OS"打牢，不在早期追求可扩展的多代理生态
2. **不引入重量级编排器**：使用 pydantic-graph（嵌入式）+ SQLite Event Store + Watchdog，不引入 Temporal 等需要独立服务的编排器
3. **不绑死任何外部依赖**：所有外部依赖（Provider、Channel、Memory 实现）都必须可替换、可降级
4. **Free Loop 与 Graph 双模式共存**：自由任务用 Free Loop，关键流程用 Graph；安全边界始终在 Policy Engine
5. **本地优先**：Mac + 局域网设备为主，允许部分组件云端化但不以此为第一目标
6. **模块化单体优先**：单 Gateway application host 显式持有 runtime services；没有第二个真实部署单元前，不新增 management/kernel/worker package

---

## VI. 非目标（Anti-goals）

> 以下内容在 v0.x 阶段明确排除，引入这些方向视为违反宪法。

- **NG1**：不构建"插件市场/生态平台"
- **NG2**：不支持"企业级多租户/权限体系/复杂 RBAC"
- **NG3**：不追求"全自动无人值守做所有高风险动作"（高风险动作必须默认需要审批或强规则门禁）
- **NG4**：不在 v0.x 阶段把所有工作流都图化（允许 Free Loop 存在，关键流程逐步固化为 Graph）
- **NG5**：不建立第二套 runtime、Provider path、配置路径或 compatibility namespace
- **NG6**：不以 class-level mutable service injection、进程级可变 registry 或未实现 backend 占位配置替代真实运行边界
