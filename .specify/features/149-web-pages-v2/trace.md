# F149 Spec Driver Trace

## Phase 0 — Constitution / Runtime

- 2026-07-20：读取根 `AGENTS.md`、`spec-driver.config.yaml` 与 Spec Driver 4.3.0 完整 skill。
- 项目 constitution/config 已存在；初始化脚本因本机缺少 zod 使用内置 fallback 校验，配置解析成功。
- 初始化脚本临时写入的 `.gitignore` 行已精确回退，未保留无关 tracked 变更。

## Phase 0.5 — Research

- 研究方式：本地 codebase-scan，不联网、不引入依赖。
- 产物：`recon.md`、`research/tech-research.md`。
- 结论：推荐稳定 OpenAPI 后采用 generated DTO → adapter → UI view model；拒绝保留 direct fetch 的换皮方案和全站 big-bang。

## Phase 1b — Specify / Clarify / Checklist

- 产物：`spec.md`、`checklists/requirements.md`、`claude-design-prompt.md`。
- 无 `[NEEDS CLARIFICATION]`；未解决外部输入以 blocker/待用户动作表示。
- TDD、测试分层、架构分层、坏味道、测试矩阵均已进入正式 Spec，不只记录在 completion report。

## Phase 2 — Design Gate

- 2026-07-20 第一轮 main 判定：退回返修，禁止进入 Plan/Tasks。
- 使用 `spec-driver-resume` 从 Design Gate 恢复；重新运行 project init/context resolver。zod 仍不可用，fallback 解析成功；init 临时产生的 `.gitignore` 行已精确回退。
- 第一轮自评闭环被 main 第二轮审查否定；`design-gate-review-round1.md` 已明确撤销 Gate 证据效力。
- 2026-07-20 第二轮返修：重建 actual call graph、raw JSON boundary、snapshot consumed projection、action registry drift、Gateway SSE 与 write-only secret contract；新增 `actual-input-manifest.md`、`pending-product-decisions.md`、`gate-prerequisites.md`、`code-smell-baseline.md`、`design-gate-review-round2.md`。
- 使用 Browser skill 与只读 current frontend + `reference-capture-vite.config.mjs` deterministic fixture 生成 actual Reference pack 22/22；metadata 明确 `not_real_backend=true`，抽查 Desktop/390 Web 窄窗口，全部 capture 无 Vite overlay/RouteError。
- 2026-07-20 main 已视觉复核 Reference pack，并完成 Tasks/Settings 8/8 产品决定：Tasks 只保留用户语言“待处理事项”，其余七项归位 Settings → Advanced → 维护与恢复；Settings/MCP write-only secret 窄 slice由 F149 承担，不另开 security Fix。
- 使用 `spec-driver-resume` 再次从 Design Gate 恢复时，`.specify/.spec-driver-path` 缺失且 fallback `plugins/spec-driver` 不存在，init/context resolver 无法运行；未产生写入。按制品回退规则，以现有 `spec.md` 与 Gate 报告确定恢复点，诊断不改变限定范围。
- 2026-07-20 Design Input Fix：canonical `actual-input-manifest.md` 继续是唯一事实源；生成 Claude 可见副本 `references/current/actual-input-manifest.md` 与解压上传目录副本，三者经 `cmp -s` 一致，manifest SHA-256 均为 `557ab716eed26134c3983e35d3d9ff1d08278b133591820a207225a0cf1e6b96`。
- Design Input Fix 将 Prompt/发送说明统一到完整 UUID `851e3fb2-2b5b-4251-a095-8a678b1b7fec`，加入 Spotify Design System 最高优先级禁止项、旧 2-page 反例边界、20 frame 逐页 F148/`--cp-*` provenance 与内部术语 absence 验收。
- 新增唯一推荐直接上传目录 `claude-design-upload/`：28 个文件（4 根级 Markdown + nested manifest/metadata/22 PNG）；备份 ZIP `f149-claude-design-input-2026-07-20.zip` 通过 `unzip -t`，SHA-256=`0ecb1b8e17c50ca1988a4998b43e3794df4aaa98fe2f441b5a48b52723c363a3`。ZIP 不作为 Claude 可自动解压输入。
- `claude-design-upload-checklist.md` 记录 24 个 Reference 文件名/size/hash；发送 Prompt 前、连接中断或应用重启后必须在 Claude 项目文件列表重新确认全部可见。本轮未操作 Claude Design UI 或外部上传。
- 2026-07-20 Claude Design anchor 4 首轮输出只读复核：20 个 frame 名称、Shared-States、Advanced-Pattern 与 References 22/22 已出现，但缺 20 行逐 frame provenance/Spotify/术语/state/390 Web 窄窗口证据与 10 surface 状态矩阵；另发现 unknown task event 默认时间线、MCP `5s`、restart `约 1 分钟/检查点续跑`、403 owner 重新登录、390 Web 窄窗口长按-only Advanced 与普通区实现词漂移。远端输出未导出回存，因此判定 REJECT / BLOCKED。
- 新增 `claude-design-revision-round1.md` 作为可直接粘贴的 anchor 4 窄返修 Prompt；只保留并修正现有 20 frame/References，不修改旧 page 1–3，不扩大 Spec 范围。本轮 artifact-only，未操作 Claude Design UI、上传或发送。
- 2026-07-21 用户解锁后提交 `claude-design-revision-round1.md`；连接中断后 Claude 自动恢复并写入。main 独立复核确认 20 行逐 frame 验收表、10×7 状态矩阵、普通用户术语、403 ownership、CONTRACT GAP 与 390 Web 窄窗口可达触发器均无新的视觉阻断。
- 用户与 main 随后冻结视觉方向：Claude 原稿的层级、留白、卡片节奏、信息密度与视觉张力为主设计基准；F148 仅约束 `--cp-*`、既有主题/组件边界和功能合同，不要求外观回退到旧 Web。`claude-design-visual-polish-round2.md` 仅做 visual-only polish，未新增功能/状态/协议或改动旧 page 1–3。
- active Spotify selector 已清为 `0`；真实云端 HTML 审计仍发现 5 个 baked Spotify/Figtree import，遂以 `claude-design-import-cleanup.md` 做纯机械删除。清理后云端导出 `_ds/spotify-design-system`、Spotify asset UUID 外链与 `Figtree` 均为 `0`。
- 云端 `OctoAgent Web.dc.html` 已原样回存到 `design-output/2026-07-21/OctoAgent Web.dc.html`，bytes=`280442`，SHA-256=`a2db08ea0eb39278558e61e87a355b042c98ad24273b201940925f310271d98a`；项目 UUID、来源与机械检查见同目录 `export-manifest.md`。
- 2026-07-21 main 独立最终复审通过：bytes/hash、20 unique frames、Shared-States、Advanced-Pattern、References、20 行表、10×7 矩阵、旧 page 1–3 markers 与 Spotify/Figtree 零引用均复核；结合真实云端画布视觉抽查，`GATE_DESIGN=true`。
- main 单次放行一次性生成 Plan/Tasks Gate 制品；不得进入 Implement、测试行为执行、stage/commit/push。视觉方向永久冻结为 Claude Design 原稿优先，F148 只约束 `--cp-*`、主题/组件边界与功能合同，不得借“对齐 F148”回退为旧 Web 密集平铺风格。
- 2026-07-21 Plan/Tasks 恢复诊断：`.specify/.spec-driver-path` 缺失且 fallback `plugins/spec-driver` 不存在，init/project-context/orchestration 脚本不可运行；按 `spec-driver-resume` 制品回退规则从已通过 Design Gate 恢复。此编排器缺口不作为 TDD RED 或生产 blocker。
- 2026-07-21 Plan 完成：冻结 finite REST/action/SSE/secret contract slice、types-only generation、唯一 transport/application seam、Claude visual SoT、A/B 波顺序与 L4/L3/L1 分工；F150/F151 + main Implement 放行保持 Phase 0 硬阻断。
- 2026-07-21 Tasks 完成：40 tasks，其中 32 个行为 task 均含完整 RED/GREEN/REFACTOR command（共 96 fields）；FR-001～030、SC-001～013、checker prerequisite、coverage、坏味道与 Design fidelity 均有映射。
- 机械 command-policy 检查：32/32 behavior tasks 各 3 commands；Python prefix、根目录独立性与禁用命令检查均 0 违规；未执行行为测试或修改 production/tests。
- 2026-07-21 main Plan/Tasks code review 指出 F149 不能把 pre-F151 SDK 路径冻结为未来 RED。窄返修后所有未来 Python/Playwright 环境改为 post-F151 canonical 七个保留 workspace packages + Gateway，retired SDK absent；T050 改用独立 seeded config-contract negatives，T000 增加 F151 stable commit 后 rebase/recon 与 canonical profile 不一致即回 Gate。任务/行为/command 数保持 40/32/96。
- 2026-07-21 main 最终独立复核通过：`GATE_TASKS=true`。Implement 未放行；F150/F151 stable commit、rebase/recon 与 main 再次明确授权未齐前，T000 保持 CLOSED，禁止 production/tests、behavior RED、stage/commit/push。
- 2026-07-24 用户冻结产品/设计边界：桌面端保留 Web；手机产品只走原生 iOS App，不把 390px Web/手机浏览器作为移动产品入口，F149 的 390px 仅为 Web 窄窗口响应式健壮性，不是手机产品、移动认证或 iOS 验收。Claude Design 最开始方案是 Web/iOS 共同视觉/交互基线；现有 Web 仅作 current-state evidence。只允许因明确功能合同、可用性、无障碍或 iOS 原生平台规范调整，必须记录原因和影响，禁止以实现方便为理由；iOS 保留视觉语言但使用 SwiftUI/Apple 原生导航、手势、控件与无障碍语义，不复制 Web 组件结构。F149 仍只实施 Web，该决定已同步至 Spec/Plan/Tasks/Checklist；未进入 Implement。
- Design/Tasks Gate 已通过；该时点生产实现仍需 F150/F151 稳定、rebase/recon 与 main
  另行明确放行。

## Phase 3 — Implement readiness

- 2026-07-25：F150产品实现提交`bf29d6be7d7a86c298cd45699488a8640065a566`、
  stable tip`5e6f4846703b7126cd104c8b9678e0c2f5300cc8`与
  `origin/master=112a54aed0aca2971d8dabb34b6390efe9d779aa`已就绪。
- F149 snapshot接入正式分支并无冲突rebase；merge-base精确等于`origin/master`。
- 重新完整读取tests契约、Playwright config与F151 stage profile；canonical post-SDK
  profile仍为七包+Gateway、SDK absent。Playwright CI retry漂移留给T050按真实TDD修复。
- Design export SHA仍为`a2db08ea0eb39278558e61e87a355b042c98ad24273b201940925f310271d98a`。
- T000关闭；Implement放行。
- T001以同一Vitest selector完成真实RED→GREEN→REFACTOR：RED稳定命中
  `F149_BOUNDARY_RULES_MISSING`并漏报九类seeded violation；GREEN后九类均拒绝、
  合法路径通过；REFACTOR收紧三类误报后同selector继续2/2通过。当前仓库实扫只剩
  `approval-center.ts`、`memory-candidates-types.ts`、`AgentCenter.tsx`、
  `SkillCenter.tsx`四个由后续任务拥有的真实direct fetch。
- main独立复审T001时发现原AST规则仍可被`window.fetch`、`Headers.set`、
  `getFrontDoorToken`直读和手写`access_token` query四种写法绕过；corrective RED
  以`F149_BOUNDARY_RULES_MISSING`同时证明四项漏报，修复后同selector 2/2通过。
  全部T001–T005 targeted tests为12/12；仓库实扫现在也报告上述四个owner文件内的
  header/token违规，同时明确允许F150全局`FrontDoorGate`读取token，未引入第二认证入口。
- T002以同一Vitest selector完成真实RED→GREEN→REFACTOR：五类seeded
  index/style/theme/token regression从全漏报变为精确拒绝，合法`--cp-*`控制通过；
  当前仓库CLI以`index.css=4476`、palette=336、Spotify=6、non-cp token=59、
  second-theme=37作为诚实不恶化基线，未把遗留值伪报为已清零。
- T003以同一Vitest selector完成真实RED→GREEN→REFACTOR：完整相对路径/bytes
  clean-diff、`any`与未命名`unknown/JsonValue`均由TypeScript AST稳定拒绝，
  命名raw/metadata/schema-as-data开放边界通过；真实generator alias/artifact仍由
  T005与后续Gateway contract任务创建，未伪造现有OpenAPI产物。
- T004以同一Vitest selector完成真实RED→GREEN→REFACTOR：TypeScript AST只计
  authored executable changed lines，tests/generated/`.d.ts`/type-only排除；
  89.99%拒绝、90%接受，新source无LCOV按0%，且CLI同时纳入
  committed/staged/unstaged diff与untracked source。真实coverage provider/alias仍由T005拥有。
- T005以同一Vitest selector完成真实RED→GREEN→REFACTOR：锁定
  `openapi-typescript@7.13.0`与`@vitest/coverage-v8@2.1.9`，三条alias、
  post-SDK exporter命令与LCOV排除合同通过；targeted `test:coverage`真实加载v8并生成
  LCOV。OpenAPI exporter/artifact仍等待T010–T012，未把未来命令冒充现成产物。
- T010以三个可收集Gateway L4节点完成真实RED→GREEN→REFACTOR：RED仅因
  `F149_REST_CONTRACT_MISSING`命中snapshot names、endpoint manifest与TaskDetail
  fields三项缺口；GREEN后67个实际REST operation均有有限2xx JSON schema，
  snapshot/TaskDetail开放JSON只停留在命名raw boundary。REFACTOR合并既有
  control-plane API回归后11/11通过，Ruff与diff-check通过；未新增第二schema源。
- T011以两个可收集Gateway L4节点完成真实RED→GREEN→REFACTOR：RED只因
  `F149_ACTION_CONTRACT_MISSING`命中缺失的同源action record；GREEN后七个F149
  实际action由同一`ActionContractDefinition`派生handler、runtime validation、
  registry与types artifact，其他action继续显式open schema-as-data。REFACTOR
  保留既有owner错误码与`behavior.write_file(file_path=...)`调用兼容，精确回归
  12/12、更宽control-plane回归115 passed/1 skipped；F151最窄T011 authority与
  runtime architecture总门通过，未新增第二dispatcher/registry。
- T012以四个可收集Gateway L4节点完成真实RED→GREEN→REFACTOR：首次输出因展开
  `ModuleNotFoundError`上下文被主动判为无效，改成能力断言后四项只命中
  `F149_TASK_SSE_CONTRACT_MISSING`。GREEN后`final`为必填，状态变更只输出
  `to_status`，artifact只输出刷新信号，其他已知/历史事件经限深、限项、限字符串、
  限总bytes及secret scrub后才进入diagnostic。REFACTOR连同既有SSE历史回放、
  实时、终态、dedup与队列测试13/13通过，并把复杂event generator抽为单一内部
  协程；Gateway仍只有原SSE路由，core无F149 frame模型。
- T013以四个可收集Gateway L4节点完成真实RED→GREEN→REFACTOR：首轮RED虽命中
  正确oracle，但PYTHONPATH多含已退休SDK，主动判为不合格；随后把当前test相同
  SHA复制到detached旧生产基线，以七包+Gateway规范环境取得4/4固定
  `F149_SECRET_EGRESS_CONTRACT_MISSING`且输出sentinel为0。GREEN后MCP读模型彻底
  移除`env`兼容字段，只公开`name/configured/redacted_summary`；首次MCP安装只
  接受一次性新值，后续Settings/MCP持久化编辑共用严格`keep|replace|remove`，
  placeholder、未声明字段与额外schema均fail closed。原始值从读模型与领域结果
  边界排除，MCP持久化异常固定化，SSE复用既有diagnostic sanitizer；action/
  snapshot/SSE/error/audit/log测试均未发现sentinel。REFACTOR精确回归23/23，
  完整control-plane与MCP registry回归96 passed/1 skipped；没有global secret
  值扫描器、第二scrub算法、第二transport或明文兼容路径。
- T014以一个deterministic L3节点完成真实RED→GREEN→REFACTOR：同一最终test在
  T013前旧生产基线上经真实FastAPI lifespan、SQLite与ASGITransport走到snapshot，
  以`F149_DETERMINISTIC_L3_CONTRACT_MISSING: snapshot leaked secret`单缺陷见红；
  当前实现串联OpenAPI、snapshot、`project.select`、非法secret mutation固定错误、
  control events与终态SSE历史回放，HTTP/SSE/log均无sentinel。GREEN为1/1，
  REFACTOR连同T010–T013 L4合同14/14；没有新增production composition seam、
  第二transport、固定sleep、网络或真LLM。
- T015先发现planned exporter缺失导致exit 2，明确判为无效预检；补齐只读三权威
  source的deterministic exporter后，`openapi:check`完成三次types-only生成并只以
  三个`F149_GENERATED_DRIFT`报告checked-in `.d.ts`缺失，形成有效RED。GREEN生成
  REST/action/Task SSE三份声明并通过字节比对；REFACTOR再次`openapi:check`后
  `tsc -b`通过，checker/package回归5/5。两次独立/tmp导出逐字节一致，生成物无
  `any`或fetch client；可重建JSON/比对树已gitignore，只提交唯一exporter与`.d.ts`。
  首次commit Gate又如实发现生成的REST类型复制了F150已批准的session/remote
  schema语义；最终只为三个exact `.d.ts` path增加types-only authority，仍拒绝
  可执行export与新增敏感模式，不删除真实F149页面合同，也不放宽其他frontend文件。
- T020先以可导入但固定invalid的raw decoder和空projection形成4条稳定RED；
  GREEN对16个冻结resource、缺section、degraded/resource_errors、action IDs做
  runtime fail-closed，并只输出实际消费字段。REFACTOR把resource/degraded/error/
  action校验拆为窄纯函数；platform projection不import generated、transport或React，
  sentinel raw metadata在adapter与projection两层均不可见。最终7/7、`openapi:check`
  与`tsc -b`通过。跨Feature Gate只为raw decoder exact path授权resource-name常量
  与decoder两个runtime exports，没有给其他frontend路径开放session语义；无网络、
  宿主状态或UI改动。
- T021以三份既有/新增API L4测试形成8条稳定RED：Approvals与Memory仍各自直读
  token、组装header并调用fetch，同时401/403/404/409归属未闭合；GREEN删除两套
  鉴权helper，统一经`frontDoorRequest`，并由共享F149 error ownership mapper把
  401交回F150 global auth、403/404/409留给surface。REFACTOR同一精确selector
  27/27通过，连同相关query/projection回归68/68、`openapi:check`、`tsc -b`与
  complexity均通过。runtime architecture只为`client.ts`新增的
  `apiErrorFromResponse`开放exact export authority，正向工作树通过，额外export与
  `DeviceSession`敏感语义均被拒绝。边界实扫只剩T022拥有的Agent/Skills旧直连点；
  本task没有UI/CSS、第二transport、第二auth owner或宿主状态改动。
- T022以inert Agent/Skills adapter形成3条稳定`F149_AGENT_SKILL_ADAPTER_MISSING`
  RED；GREEN后审批覆盖列表/撤销与Skills列表/详情/安装/删除六条请求统一经过
  `api/client`，wire type直接消费T015 generated REST declarations，并保留
  `ApiError` status/code。REFACTOR精确selector 11/11、Agent页面回归14/14、
  `openapi:check`、`tsc -b`与complexity通过，F149全仓boundary首次无遗留直接
  fetch而完整PASS。审查主动撤销Prettier对两个旧页面产生的大范围纯排版噪声，只
  保留9增/18删与7增/42删的transport迁移；DOM、inline style与`wb-*` class multiset
  不变。runtime architecture真实工作树通过，并分别拒绝adapter额外export、页面
  direct fetch与视觉class漂移；无第二transport、UI/CSS或宿主状态改动。
- T023以inert Task SSE decoder与旧TaskDetail形成6条稳定RED：state、artifact、
  diagnostic三类投影缺失，unknown/history仍进入默认时间线，页面状态合同不完整。
  GREEN后generated wire只在`api/f149/raw/`命名边界验证，TaskDetail只消费
  typed state/artifact与有界净化diagnostic；current-task与task_seq单调合同阻止
  子任务终态关闭当前流或旧回放覆盖badge，artifact只触发详情刷新。REFACTOR把
  REST历史也显式重建为最小投影，丢弃raw payload和额外顶层字段；Advanced默认
  收起，403/404/recoverable/disconnected互斥。最终精确selector 11/11、全前端
  490/490、生产build、OpenAPI字节比对、F149 boundary与complexity均通过。
  repository-scope runtime architecture也通过，额外runtime export、第二
  re-export与新视觉class三类对抗样本均被拒绝。`TaskDetail.tsx`未改CSS或既有
  视觉class；本task没有Gateway协议改动、第二EventSource/transport、raw DOM泄漏
  或Claude Design视觉回退。
- T024以固定recoverable helper与无语义primitive形成16条稳定RED，另有2条
  secret/未净化不可达控制通过。GREEN后loading/ready/empty/recoverable/403/404/
  disconnected/409由单一pure helper互斥决策，401继续交回F150 global auth；
  operator-sensitive path/command只有已净化、非secret、workspace-relative且获权时
  才可复制。REFACTOR复用既有`InlineCallout`提供status/alert语义，没有新CSS、
  视觉class或全局业务store。最终精确18/18、全前端508/508、生产build、OpenAPI、
  F149 boundary/style/complexity与repository runtime architecture均通过。
- T025以可导入但拒绝全部command的inert typed wrapper形成9条稳定RED，2条
  negative control通过。GREEN后七条F149 action由generated schema约束，并在
  application boundary做runtime fail-closed；`behavior.restore_version`保留，
  dead Memory与Automation action不进入本task。REFACTOR直接复用既有Workbench
  executor与`executeWorkbenchActionWithRefresh`，非法command、envelope/action/
  result漂移和executor exception均返回稳定typed error，异常不回显params/secret。
  最终精确15/15、全前端520/520、生产build、OpenAPI、F149 boundary/style/
  complexity与repository runtime architecture均通过。没有第二action pipeline、
  service、页面、CSS、视觉class或Claude Design排版改动。
- T030先逐页复核Claude Design的Approvals Desktop/390原稿，再以hero、三类数量
  胶囊、从容留白、卡片节奏与单列主次操作形成6条稳定RED，既有29条行为控制保持。
  GREEN后loading/empty及分来源403/404/409复用共享state/error owner，401不下沉，
  技术错误不进普通界面；三类候选与既有动作未扩域。REFACTOR响应跨Feature Gate，
  从本页新增/触达文件清零旧`wb-*`视觉类，样式只消费`--cp-*`，不是给旧Web套皮。
  真实Chromium桌面与390 Web窄窗口视觉验收通过：无横向溢出、主操作≥44px；
  390仅为Web响应式健壮性，不代表手机产品，手机仍只走原生iOS。最终精确35/35、
  全前端523/523、生产build、OpenAPI、F149 boundary/style/complexity与repository
  runtime architecture均通过。
- T032以可导入inert维护区形成8条稳定RED，既有Settings十四项控制保持；首次
  正式RED未持久化，随后只以同字节同命令做一次透明捕获复跑并在证据中标记。
  GREEN把summary、backup、export、dry-run、apply、restart、verify七项能力归入
  Settings唯一Advanced路径，移除Tasks旧Recovery入口；状态由单一reducer/hook
  拥有，origin403只提示资源权限，401继续交回global auth。backup/apply/restart
  强词确认、有效dry-run-before-apply、导出不含密钥与运行日志、restart不虚构
  固定时长均闭合。REFACTOR逐页对照Claude Design Settings Desktop/390，保留
  宽松hero、窄卡片、留白与视觉张力；真实Chromium 1440/390 Web窄窗口无横向
  溢出、操作控件≥44px。最终精确22/22、全前端532/532、生产build、OpenAPI、
  F149 boundary/style/complexity及changed-lines coverage 95.03%通过。
  repository runtime architecture因F151缺本次新路径证据slice而如实阻断，
  未伪造跨Feature证据或放宽checker。
- T031以旧TaskList形成5条稳定行为RED，既有Recovery/cancel/resume/知识审批
  absence控制通过。GREEN把raw status收敛为窄pure人话投影，loading/empty/
  recoverable-error/origin-403与筛选互斥，401继续交回F150 global auth；非零
  待处理事项复用既有operator inbox端点，零项只保留安静的一行状态，旧技术型
  `OperatorInboxPanel`删除。REFACTOR逐页对照Claude Design Tasks Desktop/390，
  保留警示横幅、宽松hero、筛选胶囊、大卡片、留白与视觉张力；真实Chromium
  1440/390 Web窄窗口无横向溢出，全部操作与Advanced触发器≥44px。最终精确6/6、
  全前端538/538、生产build、OpenAPI、F149 boundary/style/complexity及
  changed-lines coverage 94.90%通过。repository runtime architecture因F151
  尚无TaskList新路径证据slice而如实阻断，未伪造跨Feature证据；没有第二store、
  transport、action pipeline或维护入口。390仍只作Web响应式检查，手机产品只走
  原生iOS。
- T033以可导入inert secret mutation形成4条稳定RED，既有Settings十五项控制
  继续通过。GREEN把前端密钥草稿收敛为`keep/replace/remove`三态typed command，
  与Gateway既有合同精确一致；遮罩占位与空替换fail closed，保存成功、失败或关闭
  都清空ephemeral value。普通Provider卡片沿用Claude Design原稿的“访问密钥”、
  “已保存的值不会显示”、“检查改动”和“保存并生效”，Provider ID、鉴权方式、
  环境变量名和API Base URL只在默认收起的Advanced；值不进入DOM、clipboard、
  error或证据。REFACTOR精确19/19、全前端543/543、生产build、OpenAPI、F149
  boundary/style/complexity及changed-lines coverage 92.24%通过。对实际页面完成
  桌面与390px Web窄窗口视觉抽查后，将本次触及的Settings旧`wb-*`视觉类迁到
  co-located `f149-settings-*`和`--cp-*`，保留Claude Design原稿层级、留白、
  卡片节奏与视觉张力；同时移除锁死三列的内联布局，使窄窗口单列且无横向挤压。
  未放宽checker、未改`index.css`，repository runtime architecture最终通过。
  390仍只作Web响应式检查，手机产品只走原生iOS。
- T034以旧Automation页面形成5条稳定RED，既有pause/resume、人读schedule与
  empty控制继续通过。GREEN复用共享resource page state与唯一Workbench action
  executor，闭合loading/empty/recoverable/origin-403及卡片内rejected冲突刷新；
  普通界面只保留pause/resume，job/action/cron原式只在按卡片打开的可访问
  Advanced sheet。REFACTOR逐页对照Claude Design Automation Desktop/390原稿，
  把触及页面的旧`wb-*`视觉类迁到co-located `f149-automation-*`与`--cp-*`，
  真实Chromium Desktop双列、390px Web窄窗口单列均无横向溢出，操作≥44px，
  Esc关闭sheet后焦点回原触发器。视觉审查额外发现真实`0 */4 * * *`泄漏普通区，
  随即转为“每4小时”，未知cron只显示“按自定义计划”；内置系统任务也用普通中文
  展示。最终精确13/13、全前端546/546、生产build、OpenAPI、F149 boundary/
  style/complexity、changed-lines coverage 92.46%与repository runtime
  architecture全部通过。未新增第二transport/store/service/registry或未授权动作；
  390仍只作Web响应式检查，手机产品只走原生iOS。
- T040以旧Agents页面与行为历史形成6条稳定RED，既有32条Agent编辑、投影与
  行为控制继续通过。GREEN复用typed F149 action与共享resource state，闭合
  loading/empty/recoverable/origin-403、覆盖加载失败、Behavior restore 409重新
  加载及Advanced原始字段边界；Agent卡片与dialog提炼到窄`AgentOverview`，没有
  第二transport、action pipeline、store、service或registry。REFACTOR逐页对照
  Claude Design Agents Desktop/390，保留宽松hero、桌面卡片/历史双栏、留白与
  视觉张力；真实Chromium 1440/390 Web窄窗口无横向溢出，全部操作≥44px，
  Advanced与历史面板关闭后焦点回原触发器。最终精确39/39、全前端553/553、
  生产build、OpenAPI、F149 boundary/style/complexity及changed-lines coverage
  90.06%通过。repository runtime architecture因F151仍永久冻结T022
  transport-only阶段的AgentCenter旧视觉class multiset而如实阻断；本task未修改、
  放宽或绕过F151 authority，也未为过门恢复旧`wb-*`样式。390仍只作Web响应式检查，
  手机产品只走原生iOS。
- 只读`npm audit --omit=dev`仍报告既有DOMPurify与React Router生产依赖风险；
  本task未越界自动升级，留待最终安全审查显式处置。
