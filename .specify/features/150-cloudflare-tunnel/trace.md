# F150 Workflow Trace

## 2026-07-23 Resume / Design

- F151 stable precondition：`687f20fc6246e7157957ab51ac474d46e91578b6`，F150 worktree 已 fast-forward 到同一 commit。
- F149 状态：Design/Tasks Gate 已完成，但 T000 正确等待 F150 stable；未执行 F149 production。
- 复核当前 front-door：现有 modes 为 `loopback|bearer|trusted_proxy`，F151 authority 保护 `FrontDoorConfig`、Guard、exposure 与 request dependencies，并明确要求 F150 更新 scope/hash/正负 tests。
- 复核 Cloudflare 官方 contract：origin header、derived JWKS、Access session 与 named tunnel 生命周期已进入 Research/Spec。
- Design 收敛：新增 manifest schema、origin contract、data model、clarifications 与 requirements checklist；未修改 production/tests。
- Gate：`GATE_DESIGN=PAUSE`，policy=always，reason=用户必须确认外部权限边界与最终安全/产品合同。

## 2026-07-24 用户产品边界纠正

- 用户明确：电脑保留 Web 入口，手机产品只走原生 iOS App；手机 Safari/响应式 Web 不算移动产品交付。
- “唯一远程方案”收敛为唯一 Cloudflare named tunnel 网络基础设施，不再错误解释为唯一客户端或唯一认证模型。
- F150 只拥有电脑 Web Access browser session + origin JWT；F153 拥有真实 iPhone transport spike、设备密钥、短期凭证与单设备撤销。
- 明确禁止把 Cloudflare service token 内置进 iOS App；F153 方案冻结前不创建 mobile route、Access Bypass 或伪设备表。
- Web 与 iOS 的 UI 均以 Claude Design 最初方案为视觉/交互基线；实现适配设计，现有 Web UI 不反向约束设计。iOS 保留视觉语言，但使用原生 SwiftUI/Apple 交互语义。
- 主审确认改写后的产品/安全合同闭合：`GATE_DESIGN=true`。该 Gate 不授权任何 Cloudflare 账户、DNS、tunnel、service 或 production 变更，只允许进入 Tasks Gate。

## 2026-07-24 Tasks Gate

- 新增 18 个唯一 tasks，其中 13 个行为任务均有真实 RED→GREEN→REFACTOR command/oracle；FR-1～FR-13 owner closure 完整。
- Python 命令统一使用 post-F151 core/provider/protocol/tooling/skills/policy/memory 七包 + Gateway，SDK path、`uv sync`、bare pytest 与 `-k` selector 均为 0。
- T001 固定为唯一首个行为任务：先扩展 F151 F150 authority，再允许任何 production 实现；sibling/iOS/mobile route/Bypass/service token/device/session 继续 fail closed。
- T003/T015 是需用户当次授权的 external live gate，不计行为 RED；未从 Tasks Gate 推导 Cloudflare 账户/DNS/tunnel/Access/service 权限。
- F150 只实现 backend contract、API projection 与最小 Settings entry；F149 继续拥有 Claude Design 初稿基线上的最终 Web composition。390px 只表示 Web 窄窗口，F153 独占原生 iOS。
- main 主审通过：`GATE_TASKS=true`。当前只放行 T000/T001，尚未执行行为测试或修改 production。
- T000 只读复核通过：HEAD 精确为 F151 stable `687f20fc6246e7157957ab51ac474d46e91578b6`；F149 Design export SHA 精确为 `a2db08ea0eb39278558e61e87a355b042c98ad24273b201940925f310271d98a`；F150 repo 内未发现 tunnel credential/service 副作用；18 tasks / 13 behavior / 39 commands / 39 oracles 机械闭合。T001 尚未开始。

## 2026-07-24 T001 exact authority R→G→R

- RED只修改`octoagent/tests/gate/test_runtime_architecture.py`，新增exact node `TestManifestIntegrity::test_f150_scope_allows_exact_web_access_contract_and_rejects_sibling_or_ios_drift`。唯一执行exit=1，唯一oracle=`F150_AUTHORITY_SCOPE_MISSING`；stdout SHA=`d947ac73b244c0142073526ee4e526c331f6bf43e79508eb9a7d81ef8e890914`，stderr为空；本地证据aggregate=`d1cec816df57b0e4d60071cb48913dc72d47c78d5f3e0cd7db7dda7d4ba6c989`。
- GREEN在唯一`repo-scripts/check-runtime-architecture.py`增加`validate_f150_implementation_scope(repo, base_ref)`，并同步F151 `f150-scope.md`。正向fixture覆盖config、manifest loader、classifier、JWT verifier、guard、doctor、API projection与最小Web Settings seam；11个独立负例覆盖同文件sibling、第二parser/guard/state、public bind、iOS route、Bypass、service token、device/session与F149页面状态机。exact selector通过；本地证据aggregate=`b82354a45e1077240d62521805829e169cb55223f1f07f4f8db04d250e8520fa`。
- REFACTOR复验新node通过，并额外复验既有F150/D03两个node为3/3通过；Ruff check/format、py_compile、checker C901≤10与`git diff --check`通过。本地证据aggregate=`d99cf4669d0b9b0f7b4b11317aee173debc9d5867964c0b28034c6a640be694a`；新增/触达checker函数≤50行；single parser/runner/module不变，无测试路径识别。
- 冻结SHA：runtime checker=`88b1d02d2210bd9f6fb90095a0d09dcfcb4210057330f9d91ac8d131a1812b34`，runtime gate test=`e2401ba4d3414fdc3e38503864ad95f8611074863a3d0523c199fb9c455807e9`，F150 scope inventory=`c418d1d92e92f128ae9b1b860fd1316258f7d12de55a06d9fb570927bc0a5da9`。
- T001只安装实施权限，不实现Cloudflare产品行为；电脑Web/原生iOS边界与Claude Design视觉基线未改变。

## 2026-07-24 T002 SSE probe R→G→R

- RED新增`octoagent/tests/gate/test_cloudflare_web_sse_probe.py`，精确5个node全部只因`F150_SSE_LIVE_PROBE_MISSING`失败；exit=1、stderr为空，本地证据aggregate=`a13266fbc3f658dd880fa5a261859a5f20c06c38b6f31be2cc304392ee4fd640`。
- GREEN新增唯一`repo-scripts/probe-cloudflare-web-sse.py`。注入transport/monotonic seam验证首事件、3事件分帧、`Last-Event-ID`断线重连与5-run汇总；timeout/non-SSE/slow-first-event fail closed。attestation只含hostname SHA-256与时序，不含URL/query token/event payload；GREEN 5/5，aggregate=`cd2a8971d7cabb67489aed35cc580a0beae80fbdf2e18b4766239cf057c42987`。
- REFACTOR补齐response iterator可靠关闭与原子写，保持显式URL/output/timeout且不读取HOME/cache/Cloudflare credential；Ruff/format/py_compile/C901≤10/diff-check通过，aggregate=`7614d4781c829e569af53fc927ab94d3c848e5173b0f19858398f281811b5521`。
- 冻结SHA：probe=`3069654bf1355469d7db7923ac83e0d637a889064eaa01325edcc7c724aa5082`（284行/16函数/max50），test=`cd8d22922d516f3be9d1649829efea2723965deb416a725048a31ba6565feb42`（221行/16函数/max24）。
- T003仍需用户对Cloudflare账户、DNS、named tunnel、Access application与service动作的当次明确授权；依赖图允许在此期间继续不写外部状态的T004～T012本地实现。

## 2026-07-24 T004 config / manifest R→G→R

- RED扩展既有`FrontDoorConfig`测试并新增`TestManifestContract`；42个选择项中26个旧合同保持通过，16个新合同全部只因`F150_WEB_MANIFEST_CONTRACT_MISSING`失败。失败覆盖`cloudflared`配置、exact manifest schema、格式/类型、项目根 containment、symlink、secret key/value、HOME/env fallback与单次读取；本地RED transcript aggregate=`8bd4d2b871a9a28a440274929cd0bde3971bb5e74d762764f688f675e3f4174b`。
- GREEN只扩展既有`FrontDoorConfig` owned fields/validators，并新增唯一`services/cloudflare_web_access.py`。loader只接受显式project root与相对路径，拒绝absolute/escape/symlink、重复JSON key、未知/缺失/错误类型与凭证字节；一次读取后返回frozen typed object，不读取HOME/env或创建缓存/registry/table。exact命令42/42通过。
- REFACTOR纠正T001 staged authority：新module当前public API必须为批准最终集合的非空子集，禁止额外public symbol，同时允许imports/private helpers；这避免为未来JWT/mutation阶段预建空壳函数。T001 compound authority复验通过，当前worktree的`validate_f150_implementation_scope(origin/master)`通过。
- Ruff check/format、py_compile、C901≤10与`git diff --check`通过。manifest module=`5a299a2fb15ea70d4a5162b67e91261eae3431f404029f85a447ed8f298ba642`（171行/12函数/max24）；config schema=`09a863a6c5669dc66028b6e819385f03efb1d964e6fc5adad3b9cd2e717b13f8`；manifest test=`cbe8b57648dc6ae1f81291a17342b7bd2c3505f3e8feb19f61df00128fb49224`。
- T004未创建Cloudflare账户/DNS/tunnel/Access外部状态，也未实现JWT、request guard、iOS transport或Web UI。电脑Web/原生iOS与Claude Design视觉基线边界保持。

## 2026-07-24 T005 request classifier / exposure R→G→R

- RED只扩展既有`test_frontdoor_auth.py`与`test_frontdoor_exposure.py`。exact命令27项中10项既有回归通过，17项新合同全部只因`F150_REQUEST_CLASSIFIER_MISSING`失败；覆盖loopback peer、大小写/重复/空值marker、非loopback拒绝、`cloudflared` loopback-only与旧三mode矩阵。
- GREEN在同一`cloudflare_web_access.py`增加唯一纯classifier，并只在既有`validate_front_door_exposure`内增加`cloudflared`非loopback拒绝。marker只触发Access认证分类，绝不把header值当身份；没有增加TrustedHost/CORS、公网bind、mobile route、session/device或service token。
- REFACTOR exact命令27/27通过；Ruff check/format、py_compile、production/test/checker C901≤10与`git diff --check`通过。一次未授权的private sibling提炼被真实`validate_f150_implementation_scope(origin/master)`拒绝后已完整撤回；未扩大authority。T001 compound authority与当前工作树direct scope最终均通过。
- 冻结SHA：runtime checker=`558fa672db7077b451cecb54120f543fb0253e0af904c5626927bd091f25447a`（4955行/209函数/max48）；Web Access module=`c4af2702776664a9e9974423f788aad0a198f529f281d5f1cc2a16ec4a499dd9`（211行/15函数/max24）；exposure=`eabb56d09c47e3f1a4a9ea1d29e56948566992912297da63df31cb96f1b7066f`；classifier tests=`10816f6caef9f17bafd846ad7edc45914f69dbe8598e4646cf3de85eaab71e0e`；exposure tests=`03e2799e71438a10de078ed51090f1302a06eb977e6efdda8837a6fb5643c3a1`。
- T005未执行Cloudflare外部动作，也未实现JWT/JWKS、Guard、Web UI或iOS transport。T003继续等待用户对Cloudflare账户/DNS/tunnel/Access/service动作的当次明确授权。

## 2026-07-24 T006 Access JWT / JWKS R→G→R

- RED在`TestAccessJwtVerifier`建立20个确定性case；全部只因`F150_ACCESS_JWT_VERIFIER_MISSING`失败。覆盖single header、RS256、签名、required claims、issuer/audience/owner、60秒skew、10分钟TTL、32-key上限、3秒timeout、rotation refresh、single-flight、network fail-closed与secret-negative。
- Gate真值先纠正一处命名冲突：F150 Data Model/Tasks冻结的是`CloudflarePrincipal`，F151 allowlist误写`CloudflareAccessIdentity`。scope、checker与compound fixture三处统一后authority复验通过；没有扩展第二API或身份实体。
- GREEN在同一`cloudflare_web_access.py`增加公开`CloudflarePrincipal`与唯一`verify_cloudflare_access_jwt`工厂。工厂返回一个私有async callable，持有私有bounded cache与single-flight锁；JWKS URL只由team domain派生。首次GREEN为19/20，真实发现冷缓存unknown-kid未执行rotation refresh；修复该单分支后20/20通过。
- REFACTOR将JWT/base64/JWK/claims/cache职责拆成窄private helper，所有生产/测试/checker函数McCabe≤10；模块32函数最大33行。T006 20项及T004 manifest回归合计33/33，Ruff/format/py_compile/C901与`git diff --check`通过，T001 compound authority与当前工作树direct scope均通过。
- 冻结SHA：runtime checker=`de0abce847f2e4ce690cc1e9fef6614c255fd6897f2e21fca3fc613b746e8d82`（4955行/209函数/max48）；runtime gate test=`65d858d39adde542f2e10a8335c154561a684896b295525fd2bc0e6f3290e204`；Web Access module=`6506e1818854f65e0a88d022e0ea7db2671ad5ef1612135ea4496522ded28500`（513行/32函数/max33）；test=`6b8fd78be98b601643ee09bd613cab71924aafa48a80292b7ef12659d2917f67`（636行/42函数/max32）；scope=`a1388839d9b8cdfc508f193873fea3b4d242cc465738112cf910c6d63c73da3b`。
- verifier不读取Cookie、裸email header、service token、HOME、环境cache或宿主网络；principal只在请求作用域返回，不持久化、不记录claims。T007之前HTTP/SSE尚未接入Guard。

## 2026-07-24 T007 Web Guard / mutation / HTTP+SSE R→G→R

- RED在既有`test_frontdoor_auth.py`与`test_cloudflare_web_access.py`增加40个exact cases，全部只因`F150_WEB_ORIGIN_GUARD_MISSING`失败。合同覆盖本机direct、Cloudflare marker必须验证JWT、HTTP/SSE初连与重连共享同一verifier、Access失效、非loopback、GET/HEAD及POST/PUT/PATCH/DELETE exact Host/Origin/JSON矩阵，以及request-scoped principal且无session/device/CSRF状态。
- GREEN只扩展既有`FrontDoorGuard.__init__/authenticate/authenticate_cloudflare_access`、`get_front_door_guard`、`require_front_door_access`和同一Web Access module的无状态mutation validator。首次GREEN为36/40，暴露测试路由局部`Request`注解被FastAPI当query参数的合同夹具问题；修正为模块级注解后40/40通过。随后把verifier类型精确收紧为`Callable[..., Awaitable[object]]`并用ASGI scope观测request state，最终40/40通过。
- REFACTOR复验两份完整测试文件129/129通过；唯一warning为既有`ToolEntry.schema` Pydantic告警。F150 direct authority通过，database migration/session/device/pairing新增路径为0，新增/触达函数保守复杂度分别为2/9/5。新增/触达文件Ruff（排除基线SIM110）、py_compile与`git diff --check`通过。
- `frontdoor_auth.py`全文件仍有两个受F151保护的历史静态债务：`_is_trusted_proxy_client`的SIM110与既有`authorize` McCabe=11；origin/master逐字节复算同样存在。T007未通过修改protected sibling或机械格式化伪装清零，新增owned方法均≤10，authority保持fail-closed。
- 冻结SHA：Web Access module=`7d8e7cfcab53081b50955bab4b2b07c9dc79c3d9d77fe2b288acc3b36dd41c59`；FrontDoor auth=`0f73af4dffd142c13263b8bd4f4f4166e0ccbefeeab34ff25658fd092812c9c8`；deps=`dc5231d2f9c99d2521310e86826bbe8929f793fbf085be1472e4d9ab4e4a437a`；auth test=`dd08835598eedbf2850aa524f2fe60193901370a1191f23caac13da410fe19b3`；Web Access test=`7758ebbb06a0c3346b24fb99d1f72b59632a17cf7650369ab68f45e700e8c5e5`。
- T007没有创建Cookie/session/device/pairing表、第二middleware或Web bearer，也没有触碰Cloudflare账户/DNS/tunnel/Access/service外部状态。手机产品仍只走F153原生iOS；F150继续只实现电脑Web Access合同。

## 2026-07-24 T008 RemoteAccessStatus / doctor R→G→R

- RED只扩展既有`test_cloudflare_web_access.py`与`test_doctor_frontdoor.py`。5个既有doctor回归先保持通过，14个新case全部只因`F150_REMOTE_ACCESS_STATUS_MISSING`失败；覆盖unconfigured/pending_verification/ready/fault、hostname与owner脱敏、typed reason/recovery、无持久化及secret/deployment absence。
- GREEN在同一`cloudflare_web_access.py`增加私有frozen status projection与纯派生seam，并只在既有`DoctorRunner`增加批准的`check_cloudflare_web_access`方法。status与doctor消费同一typed manifest和显式瞬时facts，不重新解析manifest、不读取HOME/env、不创建cache/registry/table，也不改变Guard、host或认证模式。
- REFACTOR提炼未配置状态与fault选择，最终精确合同19/19、两份相关完整回归85/85通过；Ruff check/format、py_compile、`git diff --check`、F150 direct authority及session/device/pairing migration absence扫描通过。新增/触达核心函数最大49行，保守复杂度最大8。
- 冻结SHA：Web Access module=`333970d976d547b25ff1842f1d5e379f42f69e891b221b1ca931c7647e0ce35a`（671行）；doctor=`494bd5cf32adfe29a059fb631e673d9d36e7c0a20cde7f229b658eb68b1db19e`（851行）；Web Access test=`67e899da783e63910a5beedcd771d3909d04b5769ed567ece53791a09d9d0429`（953行）；doctor test=`4f173cfe9706236d2b6efe6765b23950be7aec614c99a740cfb96516a6ebd606`（198行）；runtime checker保持`de0abce847f2e4ce690cc1e9fef6614c255fd6897f2e21fca3fc613b746e8d82`。
- T008没有执行Cloudflare账户、DNS、tunnel、Access或service外部动作，也没有增加手机浏览器、WebView、iOS transport、service token、session/device/pairing状态；下一本地任务为T009唯一remote-access API projection。

## 2026-07-24 T009 remote-access API projection R→G→R

- RED只扩展既有`test_control_plane_api.py`，新增`TestRemoteAccessProjection`的7个确定性case；7/7均只因`F150_REMOTE_ACCESS_API_MISSING: status=404`见红。合同覆盖unconfigured/pending/ready/fault、typed recovery、exact schema、电脑Web与Access logout URL、共享Guard保护、业务层与HTTP投影相等及secret-negative。
- GREEN只在既有`routes/control_plane.py`增加获批的`remote_access_status` route。handler复用canonical config、同一typed manifest与`_derive_remote_access_status`，只映射瞬时facts和电脑Web动作URL；router既有共享`require_front_door_access`依赖继续保护该端点，没有第二Guard、transport、parser、registry或前端手写状态。
- REFACTOR复验exact合同7/7及相邻既有snapshot/config路由2/2通过；Ruff check/format、py_compile、`git diff --check`和当前工作树F150 direct authority均通过。handler为28行、保守复杂度3；现有route sibling的AST保持不变。
- 冻结SHA：control-plane route=`12e556dcdf5ac02bf958d5119c2bd80f9f49cdbf86a882a6ffa61239ef75666a`（300行）；control-plane test=`6f11e4c2f1cd00c1666eb1643db063d95597b36a71e30f186f2c707379860d61`（5870行）；runtime checker保持`de0abce847f2e4ce690cc1e9fef6614c255fd6897f2e21fca3fc613b746e8d82`。
- T009没有增加iOS/mobile route、service token、device/session/pairing或Cloudflare外部状态；F150继续只发布电脑Web API seam，原生iOS仍由F153独立实现。

## 2026-07-24 T010 desktop Web Settings seam R→G→R

- 第一次尝试在依赖未安装的工作树运行 RED，命令只得到 `vitest: command not found`，明确判定为无效环境失败，不计行为 RED。按仓库锁文件执行本地 `npm ci --ignore-scripts` 后，正式 RED 的 17 个精确合同全部只因 `F150_DESKTOP_WEB_SETTINGS_ENTRY_MISSING` 失败。
- GREEN新增唯一 `frontend/src/api/remote-access.ts::readRemoteAccessStatus`，复用既有 `frontDoorRequest` 并对四态、字段集合、类型与 HTTPS action URL fail closed；新增 `RemoteAccessSettings` 语义组件，覆盖普通语言、四态、电脑 Web/Access logout、恢复动作、重试与 Advanced 诊断。首次 GREEN 为16/17，唯一失败是测试对拆分文本节点的观察方式；修正测试观察后最终17/17通过。
- 组件冻结 `data-visual-baseline="claude-design-original"` 与 `data-composition="status-card-actions-advanced"`，不使用旧 `wb-*` 样式、不修改旧 `SettingsPage`。F150交付可由F149挂载的完整状态/动作组件，F149继续负责按Claude Design初稿完成最终页面composition；禁止为了现有Web实现方便让设计退回旧界面。
- REFACTOR复验17/17，新旧client回归8/8，`npm run check:complexity`与`npm run build`通过。API为86行，组件为186行/复杂度计数187；production build转换169个modules。冻结SHA：API=`522f3b5beb1c38efc0599817157b210a5d7f4fb9bd18946fbfeef1b4b7d50a23`，API test=`75ae36b6eb384755ee8184f64196982d7192c4cb2836ffa612564d174715be1b`，component=`5a8e5b7205ef52991cbd61b2ae7726993adfaf8c4b750112dc95395f1ca19adc`，component test=`f08b9827d2b8c1bb6b7bd89c0eb7a83e68829a4cce816ac0f11f8f9bedc900b8`。
- T010暴露F151 authority把`frontend/src/**/*.test.ts(x)`误判为production的Gate缺陷。先扩展既有exact compound node取得唯一`F150_AUTHORITY_SCOPE_MISSING` RED，再只让唯一checker忽略四种前端test/spec后缀；authority node与当前工作树direct scope最终通过，production allowlist没有扩大。checker=`661a158a92f77010ce53de7f187cc1007d9ac800e75cf6c9a7d8f1eeb2636ef2`，runtime gate test=`e998089833782cab36cd75ac471d0c3d4052283f6663e5167e1308879be6b4ab`。
- Ruff check/format、py_compile与`git diff --check`通过。F151 gate test全文件C901 ratchet仍精确为4个历史项（54/37/17/11），本次没有新增第5项，也没有为追求表面全绿机械压缩无关历史夹具。
- T010没有增加手机浏览器、WebView、iOS task、service token、pairing/device/session或第二fetch/state registry，也没有执行Cloudflare账户、DNS、tunnel、Access或service外部动作。

## 2026-07-24 T011 real Gateway composition R→G→R

- T011新增4个确定性L3节点。有效RED为3 fail/1 pass：REST、SSE、reconnect、claim/signature/expiry、JWKS rotation/timeout与mutation均因真实Gateway尚未在lifespan组合manifest/verifier/guard而返回503；invalid config与public bind已由既有module-entry在Uvicorn前exit78，因此第四节点保持正向PASS。
- RED同时暴露F151尚未授权唯一runtime composition root。先扩展既有F150 authority compound node，让`harness/octo_harness.py::OctoHarness._bootstrap_paths`输入真实见红为`F150_AUTHORITY_SCOPE_MISSING`；随后只授权该method，并用独立harness sibling mutator证明其他bootstrap仍不可变。没有放宽`main.create_app`、route、第二parser/guard或request-time lazy composition。
- GREEN只在唯一`_bootstrap_paths`读取canonical typed config/manifest、构造一个共享verifier与`FrontDoorGuard`并写入`app.state`，同时在既有Cloudflare module增加私有、`trust_env=False`、不跟随redirect的JWKS HTTP seam。最终4/4通过；REST、SSE和Last-Event-ID reconnect只触发一次JWKS fetch，错误owner/audience/signature/expired、key rotation、timeout、mutation及non-loopback全部fail closed。
- REFACTOR为诚实no-op；Feature相关回归223 passed/1 skipped，扩大后的最终回归314 passed/1 skipped。Ruff/format/py_compile、frontend complexity/build、`git diff --check`与当前工作树authority/security direct validation通过。Harness全文件历史6个E501+1个F841及3个C901高复杂函数与stable baseline逐项相同；本次唯一获批method没有新增Ruff/C901项，未越权机械改动其他bootstrap。
- 冻结SHA：Cloudflare module=`4adc546f786373fab02609afa42a00c2be5eecce688ad375d7c9717a8ec5b8c1`；Harness=`580c3c757bb0841598cba8e5053d884ffd231266f213cd3df4134eac135ac864`；L3 test=`9165e4db43a1e68733e0eb5e39e6a8677f24e92fee94f69285b3bc5453aedfc5`。

## 2026-07-24 T012 adversarial security ratchet R→G→R

- T012先新增12个单缺陷negative case；首次行为RED为12/12只因`F150_SECURITY_RATCHET_MISSING`失败。每例先执行clean accept control，并验证accepted/rejected bytes存在observable delta。
- GREEN在唯一runtime architecture checker增加`validate_f150_security_surface`纯scanner，并复用到F150当前工作树authority：拒绝raw header/claim/principal/JWKS/token日志、第二manifest parser/JWT verifier/Cloudflare guard/remote state registry、Bypass、service token、device/session/pairing/mobile route、public bind、TrustedHost/CORS及`current-web|wb-*`旧Web视觉基线。现有exact authority compound node继续保护path+symbol sibling，没有新增第二checker或allowlist旁路。
- 精确T012命令13/13通过；最终Feature回归314 passed/1 skipped，Ruff/format/py_compile、frontend complexity/build与`git diff --check`通过。当前工作树authority+security direct validation为PASS，must-fix=0。
- 冻结SHA：security test=`bd72e7b97d21a38b2c6dfa217ff5b1ba9634a754da967e1f7ac30064a2fa3127`；runtime checker=`209759c62b05416a16cd1eecc855951df6186161c0c2174ee38d27acc9129d66`；runtime gate test=`e9cd330978001941902cd6504b02b5e5808d1285d7f7accf6cc76910afcaa248`。
- T011/T012仍只服务电脑Web与共享Gateway后端。手机产品继续只走原生iOS App；没有新增手机浏览器/WebView、第二pairing/session/device或App内service token，也没有执行任何Cloudflare账户、DNS、tunnel、Access application或service外部动作。

## 2026-07-25 T003 named tunnel SSE live gate

- 经用户当次明确授权，在个人部署中创建独立 named tunnel、DNS route、owner-only Access application 与受管 `cloudflared` LaunchAgent；既有 tunnel 保持未修改。独立最小 FastAPI origin 只绑定 `127.0.0.1:18880`，未替换或修改 production Gateway；非浏览器请求得到 Access `302`，service 状态为 `running`。
- 验证复用用户已登录的 Chrome Access 会话，但没有读取、导出或保存 Cookie、JWT、身份、密码、OTP、tunnel credential 或 service token。第一次页面预检的前三轮均满足时序，第四轮在第三事件已收到且服务端已结束后因临时页面多余执行 `reader.cancel()` 被误报为 `Failed to fetch`；该预检不构成 5-run 证据。只移除临时探针的多余收尾后执行完整 transaction。
- 正式 transaction 为 5/5 PASS。首事件分别为 `639/652/681/654/702ms`；相邻事件间隔分别为 `[544,1221]`、`[504,1310]`、`[500,1241]`、`[501,1166]`、`[500,1194]ms`；强制断线后的恢复分别为 `1221/1310/1241/1166/1194ms`，全部满足首事件≤5秒、相邻间隔250ms～2秒、恢复≤10秒。
- 脱敏证据为`evidence/live/T003-named-tunnel-sse/attestation.v1.json`，SHA-256=`5685d1d976bf522b54f78c25548355ce0b9dbbb4225920feccb868e9319893a9`，1313 bytes。只含UTC、`cloudflared 2026.7.3`、非敏感config SHA、hostname SHA、时序与PASS结果；hostname明文、owner identity、Cookie/JWT/token/credential等secret扫描命中为0。
- T003只证明真实 named tunnel + Access 的早期 SSE 基础假设成立，不是行为 RED，也不替代T013桌面Web旅程、T014部署合同或需再次单次授权的T015最终production live gate。手机产品仍只走F153+原生iOS App。

## 2026-07-25 T013 desktop Web Access L1 characterization

- 新增`frontend/e2e/remote-access-web.spec.ts`，其中test-only HTTP edge只模拟Cloudflare Access的HttpOnly session、登录callback、logout和一次性上游故障；认证后的所有SPA/API/SSE字节仍代理到既有hermetic L1 Gateway。该fixture没有实现Octo JWT/Guard/API、没有第二Octo session/device，也没有挂载或修改F149-owned Settings composition。
- 旧命令的第一次调用在浏览器启动前因Playwright配置没有命名`chromium` project而退出，明确判定为无效前置错误，不是RED。后续authority复核发现该命名配置本身不在F150范围且并非产品能力，最终命令改为直接使用既有Playwright默认Chromium，并撤回该一行配置；最终字节下exact collect仍为2/2。
- 首次有效执行即2/2 PASS：电脑Web完成Access登录回跳、刷新、真实Gateway SSE聊天、Access logout、session expiry后的重新认证和一次性503后的用户触发恢复；浏览器session/local storage均没有Octo front-door bearer。390px只验证Web横向overflow和键盘焦点边界，并明确排除手机/iOS交付表述。
- 因现有production在首个有效合同上已经通过，T013如实从`BEHAVIOR`改列`CHARACTERIZATION`；没有保存或伪造RED，也没有为了凑R→G→R引入产品改动。冻结诊断oracle`F150_DESKTOP_WEB_ACCESS_FLOW_MISSING`只用于后续回归失败定性。
- L1运行同时暴露launcher仍读取宿主HOME候选路径；只在test launcher内把HOME/XDG config/cache/data重定向到`.l1-runtime/<mode>/.home`，并调整为实例wipe后创建隔离目录。复验日志不再出现宿主HOME多实例扫描，exact场景仍2/2 PASS。
- 静态与构建门通过：launcher Ruff check/format、frontend complexity、production build（169 modules）及`git diff --check`均PASS。冻结SHA：T013 spec=`ef99f25e92b4bcdc584454d6935bc953439a8ae1b13ea1f10f76837d676bdd88`；Playwright配置恢复baseline SHA=`35daa578de893483d965c391002d64b7c68873aec84cccbb3bb948cb50aa8edc`；L1 launcher=`5accadb8d80aa163a156e142af18e8966433be66c47ade122f8699b9c0e088c5`。
- T013没有修改Cloudflare账户、DNS、tunnel、Access application、service或production认证/UI；手机产品仍只走F153+原生iOS App。下一任务为T014本地部署合同，T015外部live仍需新的单次用户授权。

## 2026-07-25 T014 named tunnel/service deployment contract R→G→R

- RED在既有`test_doctor_frontdoor.py`新增`TestCloudflaredServiceContract`的9个确定性case；9/9全部只因`F150_CLOUDFLARED_SERVICE_CONTRACT_MISSING`失败。正向控制要求named tunnel、manifest一致的hostname/origin/Access audience、catch-all 404以及service installed/running；负例逐项覆盖quick tunnel、public bind、hostname/audience漂移、inline token、缺catch-all与service未安装/未运行。
- GREEN在既有Cloudflare cohesive module增加私有YAML/service合同校验，并只扩展F151批准的`DoctorRunner.check_cloudflare_web_access`显式注入参数。旧调用不传部署facts时保持原四态投影；新部署facts不从`HOME`、env、Cloudflare账户或系统service自行读取，也没有创建第二parser、CLI、provider、registry或状态表。
- 模板同步到`docs/codebase-architecture/remote-access.md`与Blueprint部署章节：只允许`tunnel`、`credentials-file`路径引用与两条ingress；origin固定`127.0.0.1`，末项固定404。`credentials-file`不等于credential内容，Octo不读取/复制/上传JSON；inline token/credentials与`url` quick tunnel明确禁止。真实域名属于部署者事实，`maojiwang.work`只可用于用户个人部署，不是项目默认值。
- REFACTOR复验exact合同9/9及相关Cloudflare+doctor回归106/106通过；Ruff check/format、py_compile与`git diff --check`通过。冻结SHA：Cloudflare module=`0d063a05bb7fe975cc8d191abbcfdf9a21f5c18d8df59536887cbee0c3d936cc`（773行）；doctor=`e459dae08294be48f0318c013b9a2b6570ffc166098c39a426fb93fc8aee358d`（884行）；doctor test=`e827b6ee54d7de87935eef1002fee99b7d042aa3feeaed5633f8d04b2fb99979`（325行）。
- T014没有执行Cloudflare登录、DNS/tunnel/Access application或系统service变更，也没有触碰F149页面composition或创建手机浏览器/iOS实现。下一步T015是production live外部门，必须由用户重新给予单次授权。

## 2026-07-25 T015 production peer integrity corrective R→G→R

- 首次production live transaction复用既有named tunnel与已认证电脑Chrome会话；SPA可达，但API统一在JWT前返回403。隔离Gateway日志与本机对照证明Uvicorn默认信任loopback代理并用`X-Forwarded-For`公网访客地址改写ASGI `client`，导致`FrontDoorGuard`把真实`cloudflared → 127.0.0.1`回源误判为非loopback。该失败不生成attestation。
- RED在既有`apps/gateway/tests/test_main.py`新增唯一production-entry合同，当前字节仅因`F150_UVICORN_PEER_INTEGRITY_MISSING`失败；它要求Uvicorn收到同一app、host/port和`proxy_headers=False`。
- GREEN只修改唯一`gateway.__main__`的`uvicorn.run`参数；F151真实subprocess探针同步要求同一安全事实。精确GREEN通过，F150/F151相关回归106/106、F150 authority/security门13/13及Ruff/format/diff-check通过。
- 该修复不信任或解析`X-Forwarded-For`，也不增加第二代理、Guard、session/device或mobile route；它恢复既有“真实TCP peer是origin authority、转发头只是不可信marker”合同。

## 2026-07-25 T015 production Web live PASS

- 修复后的production Gateway复用既有named tunnel、owner-only Access policy与受管`cloudflared` service完成最终live transaction。桌面SPA、受保护REST、真实SSE、Access logout、One-time PIN重新认证、刷新及错误恢复全部PASS；T003的5/5首事件、事件间隔与重连时序证据继续逐字节引用，没有用单次人工观察替代数值门。
- One-time PIN identity provider由用户当次明确授权新增；没有修改DNS、tunnel、owner-only policy、session duration、origin绑定或catch-all规则，也没有增加Bypass、mobile route、浏览器bearer、Octo session/device/pairing。验证只使用已登录Chrome页面，不读取或保存OTP、Cookie、JWT、owner identity、hostname、Access audience、tunnel credential或service token。
- 脱敏证据为`evidence/live/T015-production-web/attestation.v1.json`，SHA-256=`e219136a51dd9f5855d7a77c8c96e33bc82817e270da8e49532f7312ffa9148d`，1814 bytes。证据只保存散列、PASS结果与T003引用；owner email、hostname、team domain、Access audience、tunnel id及credential路径扫描命中为0。
- 临时production runtime在验收后已停止并删除，`127.0.0.1:18880`无残留监听；受管`cloudflared`仍保持单实例运行，个人部署config SHA前后不变。该个人域名事实未写入项目默认配置或attestation。

## 2026-07-25 T016 原生 iOS handoff与设计基线

- `docs/blueprint.md`、Milestone、deployment与`docs/codebase-architecture/remote-access.md`在本任务开始前已完整冻结F153真机transport候选、设备密钥、challenge/proof-of-possession、短期凭证、轮换与单设备撤销；同时明确App内Cloudflare service token禁止、F153方案冻结前不得启用mobile route或Access Bypass。
- 视觉边界继续以Claude Design最初方案作为Web/iOS共同基线。iOS由后续Feature使用SwiftUI与Apple原生导航、手势、控件和无障碍语义，不复制Web组件结构；现有Web UI不是设计基线，F150也没有新增iOS production task。
- 因权威文档已满足合同，T016为诚实的artifact validation/no-op。机械核验changed Swift/iOS production path=0、F153正式owner行=1、当前Web UI正向基线声明=0；没有为制造变更重复改写Blueprint。

## 2026-07-25 T017 提交前全量验证与主审

- Python聚焦回归覆盖F150 authority、SSE probe、配置/manifest、JWT/Guard/exposure、
  doctor、API projection、真实Gateway全链、F151 startup与module entry，结果为
  `363 passed / 1 skipped / 0 failed`；跳过项是既有条件性用例。F150
  authority/security精确门另行复验`22/22`通过。
- Frontend adapter/Settings unit为`17/17`，complexity gate通过，production build
  转换169 modules；Playwright以真实Gateway和test-only Access edge验证电脑Web
  登录回跳、刷新、SSE、登出/重新认证、错误恢复及390px Web窄窗口，结果`2/2`。
- Ruff/format/py_compile与`git diff --check`通过。F150新增cohesive production文件
  为`cloudflare_web_access.py`、SSE probe、API adapter与Settings semantic component，
  共1345 physical LOC；Python最大函数50行，McCabe最高8，exact函数体重复组0。
  职责保持6簇：config/manifest、classifier/JWT/mutation、Guard/exposure/composition、
  status/doctor/API、Web adapter/view-model、probe/deployment evidence。F151迁移前
  harness三项历史复杂度和`FrontDoorGuard.authorize`历史规则未被F150修改，exact
  authority gate证明受保护AST不变。
- T003 attestation仍为
  `5685d1d976bf522b54f78c25548355ce0b9dbbb4225920feccb868e9319893a9`
  /1313 bytes；T015 attestation仍为
  `e219136a51dd9f5855d7a77c8c96e33bc82817e270da8e49532f7312ffa9148d`
  /1814 bytes。两份JSON状态均PASS，raw email/hostname/URL/tunnel UUID/JWT模式为0；
  T015 evidence内owner identity、Cookie、credential、OTP、service token扫描均为0。
  manifest schema仍为exact 7字段且`additionalProperties=false`，changed database
  migration=0。
- 全局`check-runtime-architecture.py all`是F151 feature-only changed-scope命令，
  会按设计以`CHANGED_PATH_OUTSIDE_F151`拒绝当前F150制品路径，不能冒充F150失败；
  当前适用的F150 exact authority compound gate已经通过。
- staged diff主审发现Cloudflare生产路径虽然只解析一次manifest，但Guard、dependency
  与status projection仍会各自重新读取`FrontDoorConfig`，违反FR-11同对象复用合同。
  新增`test_guard_and_projection_reuse_startup_front_door_object`后以
  `F150_REMOTE_ACCESS_STATUS_MISSING`取得真实RED；GREEN把启动期typed对象同时注入
  Guard与`app.state`，三处生产消费者复用同一对象，非Cloudflare旧模式保留既有运行期
  校验。REFACTOR后完整聚焦回归升为363 passed/1 skipped；修正文件SHA分别为
  FrontDoor Guard=`328a68174f32fd51d121850208339d784e66e4fb9f22f124630928ad3b5fef53`、
  Harness=`bed7fabf4b069323c25c91aa8323dae2e5ae17a202e858b5a4290e901dd113e1`、
  deps=`80d763e436542db820d7c468a8ef922fcc2cabff229ac3dab186917ed6ed2068`、
  route=`e540447c86cdb39edf118341c54950f13c6f78e31b1148c1b8a3a63782f59180`、
  test=`70d234f77b575f05e8aa870d77433af316a04b5a1340da74716b39bdda85848f`。
- 本地实现、live与提交前验证已经完成，must-fix=0。由于尚未stage/commit，F149
  T000所需的stable commit仍不存在；T017保持未勾选，不把工作树状态误报为稳定版。
