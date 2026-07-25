# F149 Web 其余页面 v2：设计侦察

> 状态：Design/Tasks Gate已通过；2026-07-25 T000 rebase/recon完成并放行Implement。
> §1～§13保留2026-07-20的历史侦察；当前基线与漂移结论以§14为准。
> 当前上游基线：`origin/master=112a54aed0aca2971d8dabb34b6390efe9d779aa`。

## 1. 上游约束与范围

- `docs/blueprint.md` 是架构权威索引；`docs/blueprint/milestones.md` 规定 F149 分 A、B 两波。
- F148 已交付 shell、深色 `--cp-*` token、工作台组件和 3 条 L1 基线。F149 必须复用这些制品，不得建立第二套主题，也不得继续向 `src/index.css` 堆叠页面样式。
- F151 是生产代码硬前置，F150 的认证与公开访问契约稳定后，F149 才能进入生产实现；本阶段不得修改 F150/F151 生产代码。
- F149 不新增后端领域语义。页面只能呈现当前已有能力，缺少契约或设计时应显式阻断，不能用前端分支猜测。
- 普通用户界面不得出现 debug、JWT、AUD、JWKS、LiteLLM 等内部术语；必要的技术字段仅能进入 Advanced、管理或诊断区域。

## 2. 事实来源与证据

本次逐项核验了以下源文件和制品：

- 根目录 `AGENTS.md`、`docs/blueprint.md`、`docs/blueprint/milestones.md` 的 M11/F148/F149 段落；
- `.specify/features/148-web-workbench-v2/{recon.md,spec.md,completion-report.md}`；
- `docs/codebase-architecture/modules/06-frontend-workbench.md` 与 `docs/blueprint/architecture-audit.md` §14.14；
- `octoagent/frontend/src/App.tsx`、平台 snapshot/action/resource 合约、API client、主题 token 和各目标页面；
- 对应后端 routes、Pydantic response model、前端 Vitest 与 Playwright 文件；
- 仓库内 Claude Design、DesignSync、`.dc.html`、截图和 mockup 线索；
- `octoagent/tests/AGENTS.md` 的 L1/L2/L3/L4、worktree 与时序测试纪律。

当前可量化基线：

| 项目 | 当前事实 | F149 含义 |
|---|---:|---|
| `src/index.css` | 4,476 行 | F149 变更后必须保持零增量 |
| `src/types/index.ts` | 2,021 行、约 186 个导出类型 | 禁止继续放入网络 DTO；只保留待迁移兼容面或 UI view model |
| 根 `types` 导入者 | 约 70 个文件 | 不做 big-bang；为 F149 涉及路径建立 ratchet |
| F149 目标路由 | 9 个一级路由 + 任务详情 | 设计必须覆盖 desktop、390px 与通用状态 |
| 当前 L1 | approval center、chat scripted loop、front-door token | F149 只补浏览器独有语义和两波关键旅程 |
| 当前 changed-lines 脚本 | 只统计 backend Python，明确排除 frontend | F149 必须补前端等价门，不能用现有脚本声称 UI ≥90% |

## 3. Claude Design 覆盖

仓库中没有可直接复核的 F149 Claude Design 页面、状态稿、截图或 `.dc.html` 文件。唯一可追踪线索来自 F148 制品：Claude Design project `851e3fb2-2b5b-4251-a095-8a678b1b7fec`、文件 `OctoAgent Web.dc.html`，但当时 DesignSync 不可访问，F148 以书面风格约束完成了 shell。项目识别必须使用完整 UUID，不得把短前缀当作 active project ID。

因此当前设计覆盖判定如下：

| 内容 | 覆盖状态 | 可复用事实 |
|---|---|---|
| 全局 shell、导航、token、工作台层级 | 有仓库实现与 F148 书面规范 | 复用 `--cp-*`、现有 theme-v2/workbench-v2 |
| F149 A 波各页 desktop/mobile | 无可复核设计稿 | 不能自行臆造，需 Claude Design 补稿 |
| F149 B 波各页 desktop/mobile | 无可复核设计稿 | 不能自行臆造，需 Claude Design 补稿 |
| loading/empty/error/permission 状态 | 无统一状态稿 | 需一张共享 state sheet，并以组件测试验证 |
| Advanced 信息分层 | 只有仓库原则，无页面标注 | 需设计明确哪些字段折叠或移出普通界面 |

Claude Design Prompt 见 `claude-design-prompt.md`，当前已标记 **READY TO SEND**：Worktree 已按 canonical `actual-input-manifest.md` 回存 10 页 Desktop/390 与 F148 shell Desktop/390 共 22 张现状图，main 已视觉复核并完成 `pending-product-decisions.md` 8/8 决策。发送前必须直接上传解压后的目录，并在 Claude 项目文件列表确认 `references/current/actual-input-manifest.md`、metadata 与 22 张图共 24 个 Reference 文件可见；ZIP 只作备份。Prompt 请求 20 个唯一 frame、Shared-States、Advanced-Pattern、References、逐页 theme/token provenance 与普通用户术语 absence。**Claude 输出缺失时 GATE_DESIGN 仍不通过**。

## 4. A 波页面与协议盘点

### 4.1 审批 `/approvals`

- 当前页面是 F145 候选审批中心，聚合新记忆、记忆整合、行为压缩三类候选；它不是通用工具调用 `/api/approvals` 的管理页。
- API：memory candidates 的查询/提升/丢弃，consolidation candidates 的接受/拒绝，behavior compact candidates 的接受/拒绝，以及 summary。
- `api/approval-center.ts` 与 `api/memory-candidates-types.ts` 均自行调用 fetch 并拼 token/header/error；单一 transport 问题不只存在于 page。
- 已有：并行加载、分来源错误与重试、全局 loading/empty、冲突与待处理状态映射；已有 API/model/page 测试和 L1。
- 缺口：无专门 permission 状态；设计必须避免把两种“审批”概念合并，也不能新增后端不支持的批量能力。
- 可复用：`PageHeader`、现有 card/list/modal、notification、F145 proposal view model。

### 4.2 任务 `/work`、`/tasks/:taskId`

- 列表通过 `GET /api/tasks` 每 5 秒轮询，具备 loading/error/empty，但直接显示原始状态值，错误态缺少明确重试。
- 详情通过 detail API 与 `/api/stream/task/{id}` SSE 更新。源码只把 `STATE_TRANSITION.payload.to_status` 映射为状态，并用 `ARTIFACT_CREATED` 触发刷新；其余 payload 当前直接进入 Raw 时间线，缺少 runtime decoder 与 secret scrub。
- TaskList 在所有 ready/loading/error/empty 分支真实渲染 OperatorInboxPanel 与 RecoveryPanel；main 已决定目标归位：Tasks 只保留用户语言“待处理事项”（非零显著可进入、0 项折叠/隐藏，不与 F145 候选合并），Recovery summary、backup/export/update dry-run/apply/restart/verify 全部移入 Settings → Advanced → 维护与恢复。
- 当前页面不提供 cancel/resume；F149 不得凭设计加入这些动作。
- 已有 TaskDetail 测试；TaskList 没有页面级测试。
- 可复用：F148 `useTaskLiveState` 的单一任务直播模型、badge/timeline/artifact 组件；避免建立第二份任务状态机。

### 4.3 自动化 `/automation`

- 读取 `automation` snapshot resource；页面只暴露 pause/resume。后端虽存在 create/run/delete action，当前产品页没有对应旅程，F149 不扩域。
- 已有 loading/error retry/empty/status 映射与页面测试。
- `job_id`、`action_id`、cron 原值已进入 Advanced，方向正确。
- 缺口：permission 状态与 mobile 操作布局没有可验证设计/L1。

### 4.4 设置 `/settings`

- 读取 config、project selector、memory、retrieval、setup snapshot；执行 setup review/apply/oauth-and-apply/quick-connect 与资源限制 action。
- 页面约 997 行，包含 review、save、pending changes 和错误 modal，且与 F151 将稳定的配置/认证契约高度耦合。
- 源码仍操作 `litellm_proxy_url`、`LITELLM_MASTER_KEY` 等内部字段；现有测试只保证部分标签不在主界面出现，不能证明内部概念不会从错误、hint 或默认值泄漏。
- F149 生产实现仍等待 F151/F150 稳定，但 Settings/MCP write-only secret 的窄 HTTP/application contract slice由 F149 自己承担，复用现有 config/secret store/application boundary；F151 只负责底层 runtime/package/secret reference，不需要独立 security Fix。
- 可复用：现有 provider section、pending changes、资源限制 UI 与 `api/client` recovery/update API。需要将高级字段明确收进 Advanced，并把维护与恢复拆成窄 section 由 Settings 组合，禁止继续增加 Settings God component、独立页面、management service/registry 或第二状态源。

## 5. B 波页面与协议盘点

### 5.1 智能体 `/agents`

- snapshot 来源：agent profiles、worker profiles、MCP provider catalog、skill governance、config；已有 `agentManagementData.ts` 负责部分 UI 映射。
- 能力：主/自定义智能体卡片、创建/编辑、行为文件读写与归档、behavior version restore、profile review/apply、worker + project 创建。
- approval override 仍直接 `fetch`，没有走统一 bearer transport，加载失败被静默降级为空；这在 token 模式下存在真实风险。
- 主界面出现文件 ID/路径、Project-Agent Override、model alias、runtime ID 等技术概念，需要由设计标入 Advanced 或改成人类可理解的摘要。
- 已有 AgentCenter 测试；主内容缺少显式 permission/失败状态。

### 5.2 记忆 `/memory`

- snapshot 来源：memory、retrieval；真实可达 action 包括 query、consolidate、索引 start/cancel/cutover/rollback，以及子组件 edit/archive/restore。
- `MemoryActionsSection` 没有生产 importer；其中 diagnostics/backup/export 及 shared operator/channel 管理动作不属于 F149 Memory surface，不能因源码文件存在而扩入契约。
- 已有结果 empty/error/warning 和页面测试。
- 资源缺失错误会引导到不存在的 `/advanced`，并在普通界面暴露 “后端服务”“Advanced”；这是 F149 必须修的产品与可达性问题。
- retrieval lifecycle、embedding 等技术状态需在普通页面转译，原值留在 Advanced。

### 5.3 文件 `/files`

- 两种模式：artifact 文件与 workspace git。
- API 已集中在 client：任务、逻辑文件、diff、versions，以及 project/history/commit/diff/blame/rollback。
- 已有分层 loading/error/empty、竞态序号保护、FilesCenter 与 WorkspaceGit 测试。
- 技术 version/hash/storage 已放 Advanced，方向正确；缺口是错误态缺少一致的 retry、permission 状态和 mobile L1。

### 5.4 技能 `/skills`

- list/detail/install/uninstall 全部直接 `fetch`；网络 DTO 手写在根 `types/index.ts`。
- 有 loading/error/empty、详情、安装与卸载 modal，但没有 SkillCenter 组件测试。
- 普通界面暴露 `SKILL.md`、trigger patterns、tools/raw content；应由设计区分用户摘要和 Advanced。
- modal 使用硬编码白色/浅灰和旧 `--wb-*` 值，已偏离 F148 深色 token；不得继续扩展这套颜色。
- 安装表单复制了部分 kebab-case 校验。UI 可做即时反馈，但最终规则必须来自服务契约，不能形成第二个业务规则源。

### 5.5 MCP `/mcp`

- snapshot 来源：provider catalog；action 包括 save/delete/install/install_status。
- 页面包含 provider list、手工 command/args/cwd/env 编辑器和 npm/pip 安装向导。
- 安装向导使用 2 秒轮询，最长 5 分钟；轮询错误被吞掉，组件内部还手写 `InstallResult` 网络 DTO。
- 没有 McpProviderCenter/McpInstallWizard 页面测试；catalog 没有专门失败/permission 状态。
- Server ID、raw tool names、command/env 是高级信息；普通界面应呈现连接状态、名称和用户可执行动作。
- 后端 catalog 当前把 `config.env` 实值原样放入 response，编辑页再把值回填 textarea；这是直接出站漏洞。F149 必须采用 read summary + ephemeral masked input + keep/replace/remove，且成功/失败/关闭后清空。

## 6. API/OpenAPI 契约现状

- 仓库未发现可执行的 OpenAPI client generator 配置或依赖；当前 `apiFetch<T>` 依赖调用方手写泛型，运行时不校验。
- approvals、files、skills、plugins、workspace git 等独立 routes 多数已有 `response_model`。
- canonical `routes/control_plane.py` 的 snapshot/resource/action 端点目前以 `.model_dump()` 返回，缺少足以生成核心 Web DTO 的明确 response schema；task detail也缺 response model。snapshot 本身是包含 16 个 resources 与 degraded envelope 的开放聚合，F149 选择 raw envelope → consumed projection，而不是伪造完整闭合 DTO。
- `/api/stream/task/{id}` 是 `text/event-stream`，普通 JSON OpenAPI 无法自然描述每帧 data；当前 frontend `SSEEventData.final?` 与 backend 每帧必传 `final` 已有漂移。SSE contract 属 Gateway/web adapter；只闭合 TaskDetail 实际消费 payload，未知/历史事件保留 raw diagnostic boundary。
- control-plane `ActionRequestEnvelope.params`、`ActionResultEnvelope.data` 与 registry 的 params/result schema 当前都是动态字典；handler routes 与 `build_action_registry` 又是两份声明。资源限制、behavior read/write/restore、memory consolidate、MCP install/status 已有 handler 但 registry 缺项，不能只补前端类型。
- 因而“立即生成全部 F149 DTO”在当前基线不可行。正确顺序是：F150/F151 稳定公开契约 → 补齐 F149 所需 endpoint schema → 固定 OpenAPI artifact → 生成 client/types → adapter 映射为 UI view model。
- 生成类型是唯一网络 DTO 来源；页面与 domain 不得导入手写 wire type。优先 types-only artifact；generated client 只有在注入现有统一认证 transport 时才允许，禁止第二 fetch client。
- F149 新增/触达的 contract/projection 中 `any` 一律失败；不授权清扫非 F149 历史代码。`unknown`/递归 `JsonValue` 只允许在明确命名的 raw snapshot/event、metadata 或 schema-as-data transport boundary；经 decoder/type guard 才能进入 domain/UI。合法 JSON Schema object 不能充当 action-specific 强类型证据，也不要求 F149 闭合全 control-plane。
- 完整逐端点、SSE、action、RED test 与手写 command/view model 边界见 `contract-manifest.md`。

## 7. 目标分层与禁止依赖

- `api/client` 是唯一底层认证 transport。
- `platform/queries/actions` 与 `useWorkbenchData` 是现有 application orchestration，编排 load/mutate/retry/resource permission。
- pure projection/state 禁止 React、Gateway、transport/generated wire import，只含确定性 reducer/mapper/view model。
- page 与 WorkbenchContext 是 UI/composition；页面允许消费现有 context 与 pure projection，但禁止 direct fetch、手写 wire DTO 或重建 snapshot/action 主路径。
- 禁止 optional fallback、class-level/global mutable injection、重复状态源、兼容层叠加和第二条网络主路径。
- 默认复用现有 `api/client → platform/contracts/actions/queries → domain projection → page`；Application Port 只是概念 seam，不要求每页/每 endpoint 新建 interface/service/registry。只有真实多实现或隔离测试价值时才新增 TypeScript structural port。

### 分层 non-goal

- 不为“看起来分层”而新增实体、service、registry 或状态容器；
- 不生成第二个 transport/client；
- 不把相同 snapshot/action 数据复制到页面局部 store；
- 不用大规模目录搬迁替代职责修复。

## 8. F150/F149 auth ownership

| 情况 | 唯一 owner | F149 页面行为 |
|---|---|---|
| 未登录、401、session 过期、登出 | F150 全局 Access Gate / transport | 不渲染页面级登录态，不解释 token，不重试鉴权 |
| 已登录但 origin 403 / 资源级 permission | F149 adapter + domain page state | 保持 shell，显示资源权限不足及真实恢复动作 |
| 404 / 409 | 对应 F149 domain error mapper | 分别呈现 not-found / conflict，不误判为 auth |

验证边界：L4 穷举各页面 origin 403 projection；L1 只保留一条 F150/F149 交界旅程，证明 401 进入全局 Access、403 留在页面资源态。当前限制只允许 Feature 制品，因此待同步 Blueprint 的准确文案放在 `blueprint-sync.md`；设计通过且允许生产实施时再应用到权威文档。

## 9. 响应式与状态覆盖

- 设计宽度至少包含 desktop 与 390px；沿用现有 shell 在 960px 的 drawer、720px 的堆叠和 600px 的 modal 约束。
- 每个一级页必须有 loading、empty、recoverable error、permission denied；不适用的 empty 需要在设计矩阵中说明原因。
- 任务详情还需 not-found/disconnected，长列表需滚动与键盘焦点，modal/drawer 需 focus trap、Escape、焦点归还和可感知名称。
- F148 `workbench-v2.css` 在 1080px 直接隐藏右栏，不能作为 F149 内容“响应式完成”的证据。
- 所有状态分支优先用各 domain co-located Vitest/component test 验证；不得把十页塞进 A/B 两个巨型 test 文件。
- Playwright 包含一条轻量全路由 390px geometry/a11y sweep、A/B 各一条关键旅程和一条 F150/F149 auth 边界；SSE、file chooser、modal/focus 只在确属浏览器语义时进入 L1。

## 10. 敏感信息策略

| 级别 | 示例 | 下发 | 普通区 | Advanced / 复制 |
|---|---|---|---|---|
| secret | token、key、credential、env value、含密 command arg | 永不下发 | 永不显示 | 永不显示、永不复制；前端脱敏不是补救 |
| operator-sensitive | runtime ID、绝对/相对 path、净化后的 command、hash、raw status | 仅按权限和最小字段下发 | 不可见 | 默认中间截断；只有服务端已净化、非 secret、权限允许时可复制 |
| safe summary | 名称、用户状态、变量名、`configured`、服务端生成的脱敏摘要 | 可下发 | 可显示 | 可显示；不得反推出 secret |

env read 只允许显示变量名、是否已配置和服务端脱敏摘要；write 使用 ephemeral masked input 与 keep/replace/remove，redacted placeholder 不得回写，提交/失败/关闭后清空。secret 不得进入 response/SSE/error/log/DOM/clipboard/TDD evidence。绝对宿主路径默认不下发；优先 logical/workspace-relative path。command 只允许服务端净化后的 executable/argument summary，不能把可能含密的 raw command 交给前端再隐藏。

## 11. 测试分层与 TDD 约束

| 层 | F149 职责 | 禁止替代 |
|---|---|---|
| L4 | Vitest/component：view model、state、DTO mapping、a11y 分支；Python unit/service：必要的 schema/adapter 纯契约 | 不用 Playwright 穷举这些分支 |
| L3 | Echo、DI stub 或 `ScriptedModelClient` 下的 bootstrap/API/Event/存储/LLM 派发契约 | 不访问真 LLM、真外部系统或宿主状态 |
| L1 | 全路由 390px geometry/a11y sweep、A/B 各一条关键旅程、F150/F149 交界，以及真实 focus/modal/file chooser/SSE 浏览器语义 | 不重复下层业务排列组合 |
| L2 live | 仅真 LLM 判断力或真实外部系统事实；F149 当前预计无必要新增 | 不作为 L3/L4 正确性的替代 |

每个行为 task 必须按 RED → GREEN → REFACTOR 排列，记录精确命令与可稳定复现的失败 oracle。Tasks Gate 的文字与 grep 不算 TDD 证据；Implement/Review 必须回存实际 RED 输出、exit code、UTC 时间、HEAD SHA、工作树状态和观察到的 oracle，GREEN/REFACTOR 使用同一行为命令。纯机械搬迁只能声明为 atomic relocation，并以前后契约、旧路径 absence 与 import gate 证明，不能伪装成 TDD。

Python 测试必须按 `octoagent/tests/AGENTS.md` 使用固定 `PYTHONPATH` 和 `python -m pytest`；worktree 禁止 `uv sync`，禁止固定 sleep、blanket rerun、宿主 `~/.octoagent`、复制生产算法。时序敏感用例按契约使用 `xdist_group`。

仓库现有 `repo-scripts/check-changed-lines-coverage.py` 明确只统计 backend Python 并排除 frontend。F149 进入 Implement 前必须在 Plan/Tasks 中建立前端等价 changed-lines ≥90% gate（以 Vitest lcov 和 git diff 新增可执行行为输入），不得把“现有脚本 PASS/EXEMPT”误报为 F149 UI 覆盖达标。

## 12. 坏味道审计

逐项 source path+line、baseline/current metric、处置与 owner 见 `code-smell-baseline.md`。该清单来自实际 source/import/call scan；不能用对报告关键词的 `rg` 冒充审计。未来 AST checker 尚不存在，必须先有 checker 行为 RED task 才能成为 Gate；职责漂移与 mock-self 由独立 adversarial review 判定。

### F149 必须修

- AgentCenter、SkillCenter、`api/approval-center.ts` 与 `api/memory-candidates-types.ts` 绕过统一 transport 的直接 fetch/token/header/error 分叉；
- F149 页面/组件手写网络 DTO，包括 MCP `InstallResult`；
- TaskList、SkillCenter、MCP 页面缺少组件级行为测试；
- MemoryPage 的不可达 `/advanced` 链接；
- 普通页面的 raw status、内部协议名、路径、runtime ID、模型/认证实现术语泄漏；
- MCP poll error 与 agent override load error 被静默吞掉；
- MCP catalog 回显 env value、Settings 普通 apply 后 secret state 未清空；
- Skill modal 的硬编码浅色与旧 token；
- 缺少一致的 permission/empty/error/mobile 可验证状态。

### 本 Feature 建立 ratchet

- `src/index.css` 4,476 行：零新增；新样式放目标 domain 的 v2 stylesheet 并只用 `--cp-*`。
- `src/types/index.ts` 2,021 行：F149 路径不得新增 wire DTO，触达的 DTO 迁向生成类型；不要求一次迁完整仓库。
- `AgentCenter.tsx`、`SettingsPage.tsx` 等 God component：只按明确 seam 拆出 view model/application adapter；维护能力必须成为窄的 `MaintenanceRecoverySection` 一类 UI 组件由 Settings 组合，禁止借机重写全站或复制状态。
- 页面自建轮询与错误状态：复用 snapshot/action 或显式 application port；不新增第三种加载主路径。
- 测试不得只验证 mock 自己；oracle 必须落在渲染结果、端口调用契约、生成 schema 或浏览器语义。
- 前端 changed-lines coverage 当前无仓库 gate：F149 必须新增等价 ratchet，且不得使用 coverage exempt 代替测试。
- frontend coverage 只统计 authored executable TS/TSX；tests、generated DTO、`.d.ts` 与纯 type-only 行必须排除。

### 后续独立 Fix / 上游前置

- control-plane OpenAPI response schema 补齐需要与 F150/F151 契约共同稳定；只补 F149 必需端点，不做全 API big-bang。
- 通用工具审批与 F145 候选审批是否统一属于产品语义决策，不在 F149 偷渡。
- 全仓根 `types` hub、全部旧 CSS、后端 God service 与历史兼容层另立 Feature/Fix。

## 13. Design Gate 阻断与已决范围

1. **Reference 已关闭**：Worktree 已生成并通过 main 视觉复核 `actual-input-manifest.md` 的 22/22 当前截图，明确使用 deterministic fixture/not real backend；仅作为 current UI/layout evidence。
2. **产品决定已关闭**：Tasks/Settings 8 项归位见 `pending-product-decisions.md`；Prompt 不再让 Claude 猜。
3. **唯一 Design Gate 阻断**：Claude 尚未回存 20 个唯一页面 frame、Shared-States、Advanced-Pattern 与 References；在此之前 GATE_DESIGN 不通过。
4. **生产契约前置**：实现等待 F150/F151 稳定，以及 raw snapshot decoder、Gateway SSE、action 同源 registry、F149-owned write-only secret slice 就绪。`any` 禁止，开放 JSON 只可留在命名 boundary。
5. **main 已定范围**：审批维持 F145 三类候选；自动化维持 pause/resume；任务不新增 cancel/resume；Tasks“待处理事项”与 Settings Advanced“维护与恢复”落点均已冻结。

## 14. 2026-07-25 T000 rebase/recon

- F149本地snapshot已接入`codex/f149-web-pages-v2`并无冲突rebase到
  `origin/master=112a54aed0aca2971d8dabb34b6390efe9d779aa`；当前分支提交为
  `aa664c4162f88d13105bdadc57870770e169d832`，merge-base精确等于上游。
- F151 stable commit为`687f20fc6246e7157957ab51ac474d46e91578b6`。F150产品实现提交为
  `bf29d6be7d7a86c298cd45699488a8640065a566`，仓库级门禁配套及stable tip为
  `5e6f4846703b7126cd104c8b9678e0c2f5300cc8`；F149不复制其Access、JWT、
  request classifier、SSE或remote status状态机。
- Claude Design导出仍为`280442` bytes，SHA-256仍为
  `a2db08ea0eb39278558e61e87a355b042c98ad24273b201940925f310271d98a`；
  它继续是视觉/交互基线，现有Web只作current-state evidence。
- 已重新完整读取`octoagent/tests/AGENTS.md`、
  `octoagent/frontend/playwright.config.ts`及F151 stage command/profile SoT。
  post-SDK `PYTHONPATH`仍精确为core/provider/protocol/tooling/skills/policy/memory七包
  加Gateway，retired SDK计数为0，与Tasks一致。
- Playwright现状仍是`workers=1`、local retry=0、CI retry=1。后者违反T050冻结的
  `retries=0`目标，作为T050 seeded-negative/actual-config合同的真实待修项保留；
  不在T000提前修改，也不增加fallback或blanket rerun。
- 结论：F150/F151前置、上游基线、Design SoT和canonical package profile均闭合；
  T000完成，T001可开始。§3与§13中“Design尚未回存/仍阻断”只记录历史状态，不再代表
  当前Gate。
