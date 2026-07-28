# F158 Trace

- 2026-07-28：用户建立跨 Milestone Goal，要求重新核对交付、启动 Web/iOS、逐场景
  验收、跑通功能与视觉 E2E，并将 Claude Design 后期不佳方案改回早期方向。
- 2026-07-28：个人部署真实 LLM smoke 暴露 OpenAI OAuth
  `refresh_token_reused`；同一次探针中 Echo fallback 被激活且任务在 180 秒内仍为
  `RUNNING`。随后只读复核确认 `octo doctor --live` 实际只追加 Telegram readiness，
  没有模型调用。两项均作为 FR-009/T039-T045 真缺口进入 F158，`/ready` 与 doctor
  exit 0 不再计作模型可用证据。
- 2026-07-28：FR-009 先取得 6 个确定性 RED：两个 preflight credential 异常错误
  激活 Echo、两个 doctor live seam 不存在、Task 的 `error_category` 为空、Worker
  仍返回 `retryable=true`。GREEN 复用生产 `ProviderRouter`/config/credential store
  做无 fallback 的受控模型探针，并以唯一 `is_provider_auth_error` 统一
  `CredentialError`、`AuthenticationError` 与 HTTP 401/403。相同 6 节点随后
  `6 passed`，相关四文件回归 `66 passed`；普通瞬态错误的既有 fallback/retry
  回归保持。个人部署 T045 仍等待用户重新授权，不把 deterministic seam 当成真实
  provider 成功。
- 2026-07-28：确认当前活动 Goal 已建立。
- 2026-07-28：从干净 `origin/master=db3214ff` 开始审计，未在含未跟踪截图的旧 detached
  worktree 上修改。
- 2026-07-28：建立分支 `codex/f158-milestone-product-closure` 和独立 worktree。
- 2026-07-28：发现 Spec Driver wrapper 可读，但仓库声明的
  `plugins/spec-driver/scripts/*` 不存在；按相同 Gate 手工执行并记录风险。
- 2026-07-28：初始审计确认 F150 Settings consumer/style、Web visual E2E、iOS 工程与
  F150 verification report 缺失；M11 视觉完成声明与真实截图矛盾。
- 2026-07-28：创建 F158 Spec、Plan、Tasks 和交付审计；生产 Implement 继续关闭。
- 2026-07-28：直接渲染不可变设计导出 `#1a`，确认它就是用户指定的早期紧凑三栏
  工作台；保存浏览器证据 SHA
  `7262ef63a1fea81caf97371071527ba22f843f5f01efeb8b1821179aefb01cbf`。
- 2026-07-28：在 F158 工作树完成 `npm ci` 与 production build，并以真实 hermetic
  Gateway 启动当前 Web；保存主工作台截图 SHA
  `f9f77e9d8ff75cbcc38688b49c0561baa65c4b7266192f5d4e21c0b5b72a149a`。
  直接对照确认当前 Web 仅保留深色/绿色/三栏表面特征，没有保持早期稿的信息密度、
  对话层级、蓝黑标题层、任务/工件卡与右侧运行信息架构。
- 2026-07-28：冻结 `contracts/visual-product-contract.md` 与
  `inventories/e2e-visual-matrix.md`；明确 Web 视觉像素门、iOS 原生/真机边界和
  后期设计 lineage 处置。F158 Research/Design/Tasks Gate 通过，Implement 放行。
- 2026-07-28：为 F150 Settings composition 新增真实 RED 合同，复用既有
  `RemoteAccessSettings`、adapter 与 view-model 接入生产页面；真实浏览器确认
  “从电脑安全访问 Octo”、状态、恢复动作和 Advanced 诊断可达。
- 2026-07-28：修正 F149 A-wave 固定事件前缀与 Chat/F150 E2E 的 durable
  observable assertions；全 Playwright 最终 `19 passed / 1 conditional skip /
  0 failed / retries=0`，全前端单元 `70 files / 598 tests passed`。
- 2026-07-28：新增主工作台视觉 E2E。第一次因 pixel snapshots 不存在真实失败；
  人工检查生成图后，同一 1440×900 selector 通过四个 `toHaveScreenshot` 基线及
  三栏 geometry/computed-style 合同。
- 2026-07-28：实际恢复紧凑左栏、蓝黑中央主舞台、深色用户消息、透明 agent
  回复、胶囊 composer 和连续右侧运行 rail；保存 1440×900 最终截图 SHA
  `9363b49495fa52bfd57fe52b4f2d8707c2c1e3b45a889ac25c44232df3acd9e8`。
- 2026-07-28：补建 F150
  `verification/verification-report.md`，如实区分“本地产品闭环通过”与“提交、CI、
  个人部署复验尚未完成”。
- 2026-07-28：创建 F152 Privacy/Identity/Ingestion Feature，冻结原生 iOS 唯一手机
  入口、device capability、逐次 consent、数据分层、TTL、删除链、非敏感 audit、
  HealthKit/EventKit 权限诚实边界与 SwiftUI 视觉原则；Research/Design/Tasks Gate
  通过，但 production Implement 在 T001 architecture authority 前保持关闭。
- 2026-07-28：F152 T001 在现有单一 runtime architecture checker 中建立
  `validate_feature_authority`，以 canonical scope hash 冻结 10 个 production path、
  7 个 test path、10 个 artifact path与禁止前缀；同一 test 先因
  `F152_ARCHITECTURE_AUTHORITY_MISSING` 真实 RED，再 GREEN/REFACTOR 通过，完整
  `octoagent/tests/gate/` 回归通过。F152 Implement 放行，F153 仍关闭。
- 2026-07-28：F152 T002 新增六个独立 ingestion stage、最小 provenance、
  raw/normalized/review/approved TTL 上界与无歧义 canonical JSON/SHA-256。4 个 L4
  节点先以 `F152_PRIVACY_INGESTION_MODELS_MISSING` 真实 RED，GREEN 后又以
  retention-stage 错配单缺陷再次见红并修复；最终 focused `4 passed`，core 与 F152
  gate 回归 `581 passed / 1 既有 warning`。
- 2026-07-28：F152 T003/T004 继续同一模型 owner：9 项 initial device capability、
  唯一 Gateway audience、设备 thumbprint、15 分钟 token 与 method/path/body
  hash/timestamp/nonce request proof 均先取得稳定缺失能力 RED；unknown/wildcard/
  future health/calendar capability、跨 audience、非 canonical path/timestamp 和把
  signature 混入 payload 均 fail closed，focused 最终 `31 passed`，未实现 F153 crypto。
- 2026-07-28：F152 T005/T006 完成 consent 与单向 ingestion 状态机：consent 精确绑定
  bundle/packet/purpose/owner/device/15 分钟窗口且一次消费；review bundle 只能转为
  同 provenance 的 approved packet，analysis result 不会自动写 Memory，只有用户显式
  选择且 `pending` 的 candidate 才能进入既有 review。两组分别先以
  `F152_CONSENT_GRANT_CONTRACT_MISSING` 和 `F152_INGESTION_STATE_MACHINE_MISSING`
  见红，最终 focused `16 passed`，core + authority gate 回归
  `624 passed / 1 既有 warning`。
- 2026-07-28：F152 T007 在唯一 `sqlite_init`/`StoreGroup` 下新增 append-only
  privacy audit：schema 只含 event/type/version、owner/device/object hash、count、
  data type/capability、decision/result/reason/UTC，无 payload/body/token/signature/
  nonce/email/system identifier 列。3 个节点先以
  `F152_PRIVACY_AUDIT_CONTRACT_MISSING` 见红，随后验证重启 durable、重复 event
  fail-closed 与敏感字段拒绝；core + authority gate 回归
  `627 passed / 1 既有 warning`。
- 2026-07-28：F152 T008 在同一 store 以 review/approved/result/memory-candidate
  四张阶段表取代通用 blob；raw/normalized 仍无服务端表。删除使用
  `BEGIN IMMEDIATE`、secure-delete 与 WAL truncate，先收集 lineage 和 retained
  audit hash，再原子删除正文并写 receipt；完成后同 request 幂等返回。SQLite trigger
  注入中途失败时所有正文保持、receipt 标记 failed，移除故障后同 request 可恢复完成，
  candidate marker 在 DB/WAL 均不存在。3 个 deletion 节点先以
  `F152_PRIVACY_DELETION_CASCADE_MISSING` 见红，最终 store `6 passed`，core +
  authority gate 回归 `630 passed / 1 既有 warning`。
- 2026-07-28：F152 T009 新增不含私钥的 `DeviceIdentity` 与单一 Policy 请求授权
  seam；active device、短期 capability、request proof 和 replay key 必须精确绑定。
  17 个对抗节点先统一以 `F152_PRIVACY_POLICY_MISSING` 见红，随后验证 revoke 优先于
  expiry、cross-owner/device/key、过期/未来 token、nonce replay、method/path/body/
  token 漂移、超时 timestamp、缺 capability 及 unknown stage/capability 全部稳定
  fail-closed；Policy + F152 focused 回归 `104 passed`。
- 2026-07-28：F152 T010 将 Memory 候选确认冻结为独立 exact model，必须绑定
  candidate canonical hash、owner、device、decision 与 UTC；只有 `pending` 候选可
  在第二次明确 approve/reject 后生成新状态，原对象保持不变。9 个节点先以
  `F152_MEMORY_SECOND_CONFIRMATION_MISSING` 见红，随后验证 analysis result 不能冒充
  candidate、确认 hash/身份漂移、mixed provenance 和重复确认均 fail-closed；
  T009/T010 focused `26 passed`。
- 2026-07-28：F152 T011 在既有 Protocol 包新增唯一跨端 projection：F153 只消费
  device identity/capability/request proof，F154/F155 只消费 review→deletion 的七项
  ingestion contract；raw/normalized local stage 不发布。JSON Schema 每次从同一
  Pydantic authority 生成并绑定 canonical SHA，consumer 不能跨 Feature fallback。
  7 个节点先以 `F152_PROTOCOL_CONTRACT_MISSING` 见红，最终三 consumer fixtures、
  schema deterministic/mutation isolation 与 health/calendar absence 均通过；
  Core/Policy/Protocol/F152 focused `137 passed`。
- 2026-07-28：F152 T012 新增 architecture ratchet，机械验证 Core→Policy→Protocol
  单向依赖、唯一 privacy store/audit/policy/schema owner、无 Memory store 旁路、无
  broad exception、六张 SQLite 表仅由 `sqlite_init` 声明，且 F152 production
  functions 均不超过 50 行；删除 transaction 的 54 行职责被提炼后 focused
  architecture/store `8 passed`，McCabe≤10。
- 2026-07-28：F152 T013 已同步 Blueprint 索引、总体架构数据流与 M12 roadmap：
  raw/normalized local-only、单次 consent、approved packet、独立 Memory 二次确认、
  non-sensitive audit、provenance 删除级联、revoke/replay fail-closed 及三 consumer
  exact schema 成为上游真值；M12 状态改为 In Progress，但 F153/iOS production 仍由
  T014 Verify 阻断。
- 2026-07-28：F152 T014 完成 Verify。F152 blast radius 最终
  `714 passed / 1 既有 warning`，全仓库 Gate `208 passed`，Ruff/format/C901/
  py_compile、secret scan 与 `git diff --check` 均通过。全 Gate 首次在既有 F151
  clean-wheel relocation 被误判挂起；诊断确认是 import owner 对每条 occurrence
  重扫 site-packages 的算法退化。既有 selector 先以“repeated import owner was
  rescanned”见红，再用 transaction-local cache 修复，relocation 从 `138.68s`
  降至 `21.34s`，clean-wheel `9 passed`。F152 verification report 已落盘，F153
  原生 iOS production 解锁；F158 总 Goal、提交、CI、部署和 iOS 交付仍未完成。
- 2026-07-28：F153 T001-T011 完成 Gateway/device trust 与原生 registration App
  的代码闭环：唯一 SQLite store、owner/mobile routes、P-256 proof、短 token、
  replay/revoke、Secure Enclave/ThisDeviceOnly Keychain、单 ephemeral URLSession
  与六个 SwiftUI registration states。iPhoneOS App/XCTest target 均成功编译；
  focused Python/Gateway/authority `42 passed`，source/bundle secret scan 与 T016
  architecture ratchet 通过。
- 2026-07-28：F153 T012 保持 partial。scheme generic destination 因
  CoreSimulator `1051.54.0 < 1051.55.0`、iOS runtime=0 以 exit 70 失败；runtime
  自动安装需要 macOS 管理员授权。因此 Swift XCTest、Simulator cold start/
  screenshot/visual/a11y、Cloudflare live 和真 iPhone 场景均未声称通过。F153
  T017 已同步 Blueprint/F158 真值与设计偏离；F154 继续关闭。
- 2026-07-28：完成 Web 全 route/surface 的 Claude 早期视觉恢复。新增独立
  `claude-surfaces.css`，把 Approvals、Tasks、Task Detail、Automation、Settings、
  Agents、Memory、Files、Skills、MCP 收敛为紧凑页头、低亮度连续卡片、细边框与
  单一绿色强调；没有恢复后期大 Hero、径向发光或大面积低信息密度卡片。
- 2026-07-28：新增 9 条真实 L1 surface 场景和 10 个业务 surface pixel
  baselines。完整当前字节回归为 build PASS、complexity PASS、Vitest
  `70 files / 598 passed`、Playwright `38 passed / 1 once-only conditional skip /
  0 failed`；重建 fresh L1 fixture 后审批 UI→REST→真实落盘另行 `1 passed`。
  视觉 snapshot generation 与 no-update rerun 均 `10 passed`，人工检查后清除了
  Playwright 洋红遮罩噪声。正式证据见
  `evidence/web/2026-07-28/verification-report.md`。
- 2026-07-28：在 Claude Design 云端项目
  `851e3fb2-2b5b-4251-a095-8a678b1b7fec` 完成最终谱系清理。必需 frame 没有重复
  中间副本可安全删除，因此删除数为 0，后期 radial glow、大 Hero、大圆角卡片墙
  在原 frame 上改回 `1a` 的近黑、紧凑、高密度工作台语言。第一次写回误删 `4n`
  与 `4o`，机械导出核验发现后立即窄修恢复；最终 `4a`–`4o` 各 1、`4n`
  为表头 6 列 + 20 行、`4o` 为表头 8 列 + 10 行，23 个
  `data-screen-label` 唯一，Spotify Design System/Figtree/radial-gradient 均为 0。
  新不可变导出 SHA
  `1d497d8cc4e8a06e9f2bff296784d4648e0bb0784a73c8fe4f8a7bd9812132f7`，
  Task Detail 视觉抽查 SHA
  `201ae887e8072f40afc982a603693f91a254db27b99d21195fc2f60853b903e4`。
  谱系、初次回归与窄修事实见
  `../149-web-pages-v2/design-output/2026-07-28/lineage-cleanup-status.md`。
- 2026-07-28：F153/F158 最终架构收口没有通过放宽门或 `noqa` 规避。七个新增的
  多参数函数改为 typed request/options/context，focused F152/F153/F158
  `77 passed / 1 既有 warning`，完整 repository architecture gate 返回 `0`。
- 2026-07-28：从 F158 worktree 使用显式九段 pre-SDK `PYTHONPATH`、禁用 user
  site，并排除需要真实 OpenAI OAuth 的 `apps/gateway/tests/e2e_live` 后，确定性后端
  完整回归为 `5709 passed / 9 skipped / 1 xfailed / 1 xpassed`。一次误把 live lane
  纳入的运行因宿主 OAuth refresh token 已复用失败，不计作回归证据，也没有通过补跑
  冒充 deterministic PASS。
- 2026-07-28：个人部署只读审计确认 loopback Gateway 与 Cloudflare connector 已
  恢复，但 launchd 仍指向 `~/.octoagent/app` 的 2026-07-25 旧 managed checkout。
  当前域名页面因此不是本分支字节。后续必须先提交，再使用仓库正式 managed checkout
  安装/更新路径部署，随后完成登录态 SPA/API/SSE 与 Settings 复验。
- 2026-07-28：提交并推送 Web/iOS/F150/F158 第一批真实交付
  `7f72e23ab7168f07e602bbdd19a9776d39a16c28`。GitHub Actions 首轮仅在 Ubuntu
  Chromium 的 280×32 中文标题 glyph 边缘出现 419 像素差异；diff 没有布局、背景、
  边框或尺寸漂移。
- 2026-07-28：提交 `d440413c85a59cba9e868f51b002d135dbb45735` 收敛空 composer
  的状态顺序，并使视觉样式断言不依赖前序聊天内容；提交
  `e84ffd435f742ba2784b85c074346ab63ecedbc1` 同时设置该唯一标题断言的 pixel/ratio
  上限，避免 Playwright 取两阈值较小值继续误报。14 个 snapshot 均未重生成；
  authoritative CI 的 frontend、architecture、benchmark 与 L1 Playwright 已通过。
- 2026-07-28：通过仓库正式 `install-octo-user.sh` 更新个人 managed checkout 到
  `e84ffd43`，完成 `uv sync`、前端 build 并重启 Gateway。loopback ready/home=200，
  个人域名=Access 302，tunnel running，部署源码 SHA 与分支一致。
- 2026-07-28：Chrome 可枚举既有 Access 登录页，但接管页面持续超时；没有读取
  cookie/local storage 绕过认证，登录后个人 SPA/API/SSE 保持未验证。Gateway 启动
  日志另行暴露 OpenAI Codex refresh token reused/401，真实模型对话保持阻断。
- 2026-07-28：提交 `e84ffd43` 的权威 GitHub Actions run
  `30364899065` 最终 success：backend deterministic、frontend、architecture、
  benchmark、L1 Playwright 五个 job 全部通过。L1 在 Linux/Chromium 上证明功能和
  视觉基线可复现；Node 20 action deprecation 仅为上游 action annotation，不是失败。
- 2026-07-29：提交并推送 F153 registration 六态视觉、Simulator UI/Swift unit
  与真实模型终态收口 `35d7aa14`；iOS 26.5 / iPhone 17 Pro Simulator 完整 scheme
  为 12/12。随后在 detached clean worktree
  `/tmp/f158-ios-clean-worktree.YDqxgp/repo` 对同一提交再执行完整 scheme，仍为
  12/12，运行后 worktree clean。
- 2026-07-29：使用仓库正式 `install-octo-user.sh` 把个人 managed checkout 更新到
  `35d7aa14` 并重启 Gateway；loopback ready/home=200，`octo.maojiwang.work`
  返回 Access 302，11 个部署 CSS 与当前分支 build 的 path→SHA map 逐字节一致。
  内置浏览器和 Chrome 都能到达 Access 登录页，但 DOM/交互通道超时；没有读取
  cookie/local storage 绕过认证，登录后 SPA/API/SSE 继续保持未验证。
- 2026-07-29：提交 `35d7aa14` 的权威 run `30376168635` 中 frontend、
  architecture、benchmark、L1 Playwright 均通过；backend 的 pytest assertions
  通过，但 changed-lines coverage 为 36/42，未达 90%，所以该 run 正确失败。
  没有使用 `[cov-exempt]` 绕过；新增两个 doctor 真实模型探针失败边界测试后，
  本地 CI 等价回归为 `5715 passed / 10 skipped / 1 xfailed / 1 xpassed`，
  scripted gate `18 passed`，changed-lines coverage 提升为 38/42=90.5% PASS。
  修复提交 `ebe8cd29` 已推送，权威 run `30378276329` 的四个前端/架构 job 已通过，
  随后 backend deterministic 也通过；该 run 最终五个 job 全绿。
