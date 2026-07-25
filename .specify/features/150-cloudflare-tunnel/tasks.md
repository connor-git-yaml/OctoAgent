# F150 Cloudflare Tunnel 远程访问 — Tasks

> 状态：`GATE_DESIGN=true`，`GATE_TASKS=true`。T000～T017已完成；T003真实 named tunnel SSE live gate 与T015 production live gate均已通过。产品实现提交为`bf29d6be7d7a86c298cd45699488a8640065a566`，仓库级门禁配套提交及当前稳定点为`5e6f4846703b7126cd104c8b9678e0c2f5300cc8`；F149 T000已获得可消费基线，F150 exact authority继续生效。
>
> 产品边界：电脑保留 Web；手机产品只走原生 iOS App。F150 只交付桌面 Web 的 Cloudflare Access contract，不创建手机浏览器、WebView、mobile route、Access Bypass、iOS service token 或设备身份。Web/iOS 均以 Claude Design 最初方案为视觉语言基线；现有 Web UI 不是基线。
>
> 实施边界：F151 stable commit 为 `687f20fc6246e7157957ab51ac474d46e91578b6`。未完成 T001 authority R→G→R 前，禁止修改 F151 protected production symbol。Cloudflare 账户、DNS、named tunnel、Access application 和系统 service 的任何变更都需要用户当次明确授权。

## 统一命令与证据纪律

Python 行为命令统一从仓库根执行：

```bash
cd octoagent && env PYTHONNOUSERSITE=1 PYTHONPATH="$(pwd)/packages/core/src:$(pwd)/packages/provider/src:$(pwd)/packages/protocol/src:$(pwd)/packages/tooling/src:$(pwd)/packages/skills/src:$(pwd)/packages/policy/src:$(pwd)/packages/memory/src:$(pwd)/apps/gateway/src" LITELLM_LOCAL_MODEL_COST_MAP=True uv run --project . --no-sync python -m pytest <exact-nodeids>
```

前端行为命令统一为：

```bash
cd octoagent/frontend && npm test -- <exact-test-file>
```

Playwright 命令统一为：

```bash
cd octoagent/frontend && env PYTHONNOUSERSITE=1 LITELLM_LOCAL_MODEL_COST_MAP=True npm run test:e2e -- <exact-spec>
```

每个标为“行为任务”的条目必须保存真实 RED / GREEN / REFACTOR 的 command、exit code、UTC、Git HEAD/tree、worktree fingerprint、stdout/stderr SHA-256 和稳定 oracle。禁止把 collect-only、旧失败、测试路径错误、外部 live 结果或 blanket rerun 冒充 RED。任何失败只允许修复后执行下一阶段，不覆盖或补写既有证据。若新 L1 合同首次有效执行即在现有 production 上通过，必须如实改列 `CHARACTERIZATION`，不得为了凑 RED 人为制造实现缺口。

## Phase 0 — 实施入口与 authority

- [x] **T000 [PROCESS] 重新核对实施基线与外部权限**
  - **输入**：F151 stable commit、当前 F150 Spec/Plan/Tasks、F149 Design export SHA `a2db08ea0eb39278558e61e87a355b042c98ad24273b201940925f310271d98a`。
  - **动作**：
    1. 确认当前 HEAD 包含 F151 stable commit，且 F151 `f150-scope.md`、runtime architecture checker/test 与 canonical evidence 未被旧基线覆盖。
    2. 确认 F149 仍以 Claude Design 初稿为视觉基线，390px 只表示 Web 窄窗口。
    3. 确认本机没有由 F150 隐式创建的 Cloudflare tunnel/DNS/Access/service 变更。
  - **Gate**：任何 baseline 漂移先回 Design/Tasks Gate；本任务不产生行为 RED。

- [x] **T001 [L4][BEHAVIOR] 先扩展 F151 的 F150 exact authority，继续拒绝 sibling/iOS 越界**
  - **FR**：FR-12、FR-13。
  - **RED_SETUP**：只在 `octoagent/tests/gate/test_runtime_architecture.py` 扩展现有 F150 scope compound node；正向 fixture 精确包含本 Tasks 后续批准的 config、manifest loader、classifier、JWT verifier、guard、doctor、API projection 与最小 Settings seam，负向 fixture 分别注入同文件 sibling、第二 parser/guard/state registry、public bind、mobile route、Access Bypass、service token、device/session 表和 F149 页面状态机。
  - **RED_COMMAND**：`cd octoagent && env PYTHONNOUSERSITE=1 PYTHONPATH="$(pwd)/packages/core/src:$(pwd)/packages/provider/src:$(pwd)/packages/protocol/src:$(pwd)/packages/tooling/src:$(pwd)/packages/skills/src:$(pwd)/packages/policy/src:$(pwd)/packages/memory/src:$(pwd)/apps/gateway/src" LITELLM_LOCAL_MODEL_COST_MAP=True uv run --project . --no-sync python -m pytest -q -rA tests/gate/test_runtime_architecture.py::TestManifestIntegrity::test_f150_scope_allows_exact_web_access_contract_and_rejects_sibling_or_ios_drift`
  - **RED_ORACLE**：`F150_AUTHORITY_SCOPE_MISSING`；当前 checker 不认识新 exact allowlist，正向 fixture 被拒；所有负例必须有真实 observable delta。
  - **GREEN_CHANGE**：更新 `.specify/features/151-runtime-boundary-architecture-truth/inventories/f150-scope.md` 与唯一 `repo-scripts/check-runtime-architecture.py`，按 path + symbol/selector 精确放行；更新现有 F151 architecture-quality/no-growth 真值。禁止新建第二 checker、parser、runner 或 F150 专用绕过开关。
  - **GREEN_COMMAND**：与 RED_COMMAND 完全相同。
  - **GREEN_ORACLE**：正向 accept，所有单缺陷负例 fail closed；F151 既有 F150/D03 nodes 仍通过。
  - **REFACTOR_COMMAND**：GREEN_COMMAND，加 `uv run --project octoagent --no-sync ruff check repo-scripts/check-runtime-architecture.py octoagent/tests/gate/test_runtime_architecture.py`、`ruff format --check`、C901≤10 与 `git diff --check`。
  - **REFACTOR_ORACLE**：单入口/单解析器/单 runner 不变；新增/触达函数≤50 行、McCabe≤10，无 broad exception、mutable global、测试路径识别或 sibling 放宽。

- [x] **T002 [L4][BEHAVIOR] 建立独立、可脱敏的 Web SSE live probe 工具**
  - **FR**：FR-6、FR-9。
  - **RED_SETUP**：新增 `octoagent/tests/gate/test_cloudflare_web_sse_probe.py`，覆盖首事件、3 个 500ms 分帧事件、断线后重连、5-run 汇总、域名 hash、secret-negative、timeout 和非 SSE 响应；clock/transport 注入，不访问网络。
  - **RED_COMMAND**：`cd octoagent && env PYTHONNOUSERSITE=1 PYTHONPATH="$(pwd)/packages/core/src:$(pwd)/packages/provider/src:$(pwd)/packages/protocol/src:$(pwd)/packages/tooling/src:$(pwd)/packages/skills/src:$(pwd)/packages/policy/src:$(pwd)/packages/memory/src:$(pwd)/apps/gateway/src" LITELLM_LOCAL_MODEL_COST_MAP=True uv run --project . --no-sync python -m pytest -q -rA tests/gate/test_cloudflare_web_sse_probe.py`
  - **RED_ORACLE**：`F150_SSE_LIVE_PROBE_MISSING`。
  - **GREEN_CHANGE**：新增唯一 `repo-scripts/probe-cloudflare-web-sse.py`；只接受显式 URL/输出路径/timeout，不读 HOME/cache/Cloudflare credential，不写 token/cookie/identity/hostname 明文，不进入 Gateway production。
  - **GREEN_COMMAND**：与 RED_COMMAND 完全相同。
  - **GREEN_ORACLE**：5-run 阈值与 attestation schema 全部通过；任何一次失败总体失败。
  - **REFACTOR_COMMAND**：GREEN_COMMAND，加 Ruff、format、py_compile、C901≤10 与 `git diff --check`。
  - **REFACTOR_ORACLE**：单 probe runner；解析、计时、脱敏、汇总职责清楚，无 sleep 型测试或第二 SSE protocol。

- [x] **T003 [LIVE][EXTERNAL] 经单次授权执行真实 named tunnel 早期 SSE spike**
  - **前置**：T002 完成；用户明确授权本次 Cloudflare/DNS/tunnel/Access/service 动作并提供或确认账户、域名和 credential 的使用范围。
  - **动作**：以独立最小 FastAPI SSE probe、named tunnel 和 Access application 执行 5 次首事件/分帧/断线重连验证；不修改 production Gateway。
  - **PASS**：每次首事件≤5 秒；3 个事件相邻到达 250ms～2 秒；断线后≤10 秒恢复；attestation 只含 UTC、cloudflared version、非敏感 fingerprint、hostname hash、时序与结果。
  - **FAIL**：任一次不满足即暂停协议实现并回 Design Gate；本任务不是行为 RED，也不得通过跳过 Access/使用 quick tunnel 假绿。

## Phase 1 — canonical config 与 origin security

- [x] **T004 [L4][BEHAVIOR] 实现 canonical FrontDoorConfig 与单 manifest parser**
  - **FR**：FR-1、FR-2、FR-7、FR-11。
  - **RED_SETUP**：扩展 `apps/gateway/tests/services/config/test_config_schema.py`，新增 `apps/gateway/tests/test_cloudflare_web_access.py`；覆盖 `cloudflared` mode、required fields、email normalization、manifest exact schema、unknown/missing/type/format、project-root containment、symlink、secret key/value、无 HOME/env fallback、同 bytes 单对象复用。
  - **RED_COMMAND**：`cd octoagent && env PYTHONNOUSERSITE=1 PYTHONPATH="$(pwd)/packages/core/src:$(pwd)/packages/provider/src:$(pwd)/packages/protocol/src:$(pwd)/packages/tooling/src:$(pwd)/packages/skills/src:$(pwd)/packages/policy/src:$(pwd)/packages/memory/src:$(pwd)/apps/gateway/src" LITELLM_LOCAL_MODEL_COST_MAP=True uv run --project . --no-sync python -m pytest -q -rA apps/gateway/tests/services/config/test_config_schema.py apps/gateway/tests/test_cloudflare_web_access.py::TestManifestContract`
  - **RED_ORACLE**：`F150_WEB_MANIFEST_CONTRACT_MISSING`。
  - **GREEN_CHANGE**：只在既有 `FrontDoorConfig` 增加冻结字段；新增 cohesive `services/cloudflare_web_access.py` typed loader/model；schema 只来自 F150 contract，不创建第二 root、registry、database table 或 secret field。
  - **GREEN_COMMAND**：与 RED_COMMAND 完全相同。
  - **GREEN_ORACLE**：有效配置/manifest accept；每个单缺陷稳定拒绝；非 cloudflared loopback 兼容回归通过。
  - **REFACTOR_COMMAND**：GREEN_COMMAND，加 Ruff/format/C901≤10、public type annotations 与 `git diff --check`。
  - **REFACTOR_ORACLE**：manifest 只解析一次；config/doctor/guard/projection 后续共享 typed object，无 ambient fallback。

- [x] **T005 [L4][BEHAVIOR] 实现 peer+marker request classifier 与 loopback 暴露矩阵**
  - **FR**：FR-2、FR-4、FR-7。
  - **RED_SETUP**：扩展 `test_frontdoor_auth.py` 与 `test_frontdoor_exposure.py`；覆盖无 marker loopback=`direct_local`、任一 `Cf-Access-*|CF-*|Forwarded|X-Forwarded-*` marker=`cloudflare_access`、非 loopback reject、header 大小写/重复、`cloudflared` 仅 loopback bind、旧 mode 矩阵不漂移。
  - **RED_COMMAND**：`cd octoagent && env PYTHONNOUSERSITE=1 PYTHONPATH="$(pwd)/packages/core/src:$(pwd)/packages/provider/src:$(pwd)/packages/protocol/src:$(pwd)/packages/tooling/src:$(pwd)/packages/skills/src:$(pwd)/packages/policy/src:$(pwd)/packages/memory/src:$(pwd)/apps/gateway/src" LITELLM_LOCAL_MODEL_COST_MAP=True uv run --project . --no-sync python -m pytest -q -rA apps/gateway/tests/test_frontdoor_auth.py::TestCloudflareRequestClassifier apps/gateway/tests/test_frontdoor_exposure.py::TestCloudflaredExposure`
  - **RED_ORACLE**：`F150_REQUEST_CLASSIFIER_MISSING`。
  - **GREEN_CHANGE**：在 `cloudflare_web_access.py` 增加纯 classifier，在现有 `validate_front_door_exposure` 精确增加 `cloudflared` branch；不信任 marker 内容，不增加 TrustedHost/CORS 或公网 bind。
  - **GREEN_COMMAND**：与 RED_COMMAND 完全相同。
  - **GREEN_ORACLE**：exact matrix 全绿；旧 loopback/bearer/trusted_proxy 回归全绿。
  - **REFACTOR_COMMAND**：GREEN_COMMAND，加 Ruff/format/C901≤10 与 authority gate。
  - **REFACTOR_ORACLE**：classifier 单一、纯函数、无第二 peer parser；authority sibling 负例仍拒绝。

- [x] **T006 [L4][BEHAVIOR] 实现 Access JWT/JWKS/owner verifier**
  - **FR**：FR-4、FR-6、FR-7。
  - **RED_SETUP**：在 `test_cloudflare_web_access.py` 建 verifier matrix：single JWT header、RS256 only、derived JWKS、iss/aud/exp/iat/sub/email required、optional nbf、60 秒 skew、casefold owner、wrong owner、unknown kid refresh、10 分钟 TTL、32-key bound、3 秒 timeout、single-flight、rotation、network failure、secret-safe reason/log。
  - **RED_COMMAND**：`cd octoagent && env PYTHONNOUSERSITE=1 PYTHONPATH="$(pwd)/packages/core/src:$(pwd)/packages/provider/src:$(pwd)/packages/protocol/src:$(pwd)/packages/tooling/src:$(pwd)/packages/skills/src:$(pwd)/packages/policy/src:$(pwd)/packages/memory/src:$(pwd)/apps/gateway/src" LITELLM_LOCAL_MODEL_COST_MAP=True uv run --project . --no-sync python -m pytest -q -rA apps/gateway/tests/test_cloudflare_web_access.py::TestAccessJwtVerifier`
  - **RED_ORACLE**：`F150_ACCESS_JWT_VERIFIER_MISSING`。
  - **GREEN_CHANGE**：在同一 module 实现 injected async JWKS fetcher/clock、bounded cache 与 `CloudflarePrincipal`；不持久化 principal，不读取浏览器 Cookie/裸 email header，不接受 service token。
  - **GREEN_COMMAND**：与 RED_COMMAND 完全相同。
  - **GREEN_ORACLE**：正向 JWT accept；所有单缺陷 reject；secret-negative scan 通过。
  - **REFACTOR_COMMAND**：GREEN_COMMAND，加 Ruff/format/C901≤10、并发 single-flight deterministic test 与 authority gate。
  - **REFACTOR_ORACLE**：一个 verifier/cache authority；无永久 stale、ambient network、broad exception 或 claim dump。

- [x] **T007 [L4][BEHAVIOR] 将 Web identity、mutation gate 与 HTTP/SSE 接入唯一 FrontDoorGuard**
  - **FR**：FR-3、FR-5、FR-10、FR-11。
  - **RED_SETUP**：扩展 `test_frontdoor_auth.py` 和 `test_cloudflare_web_access.py`；覆盖 HTTP/SSE 初连/reconnect 共享 verifier、GET/HEAD、POST/PUT/PATCH/DELETE exact Host/Origin/JSON、默认 `:443`、跨站/缺失/表单 media type、Access expiry/revoke、伪 marker、本机 direct、既有限流；断言数据库/response 无 session/device/CSRF token。
  - **RED_COMMAND**：`cd octoagent && env PYTHONNOUSERSITE=1 PYTHONPATH="$(pwd)/packages/core/src:$(pwd)/packages/provider/src:$(pwd)/packages/protocol/src:$(pwd)/packages/tooling/src:$(pwd)/packages/skills/src:$(pwd)/packages/policy/src:$(pwd)/packages/memory/src:$(pwd)/apps/gateway/src" LITELLM_LOCAL_MODEL_COST_MAP=True uv run --project . --no-sync python -m pytest -q -rA apps/gateway/tests/test_frontdoor_auth.py::TestCloudflareWebGuard apps/gateway/tests/test_cloudflare_web_access.py::TestMutationGate`
  - **RED_ORACLE**：`F150_WEB_ORIGIN_GUARD_MISSING`。
  - **GREEN_CHANGE**：扩展现有 `FrontDoorGuard`、`get_front_door_guard`、`require_front_door_access`；HTTP/SSE 共用相同 dependency/verifier，mutation 只增加无状态 exact gate。禁止第二 middleware、Octo Cookie/session、pairing/device 表或 Web bearer。
  - **GREEN_COMMAND**：与 RED_COMMAND 完全相同。
  - **GREEN_ORACLE**：正负矩阵全绿；旧 bearer/trusted_proxy/local tests 全绿。
  - **REFACTOR_COMMAND**：GREEN_COMMAND，加 Ruff/format/C901≤10、authority gate 和 database migration absence scan。
  - **REFACTOR_ORACLE**：单 Guard/依赖链；HTTP/SSE 无分叉；无兼容 session 或隐藏 downgrade。

## Phase 2 — status、API 与桌面 Web

- [x] **T008 [L4][BEHAVIOR] 实现纯派生 RemoteAccessStatus 与 doctor 检查**
  - **FR**：FR-1、FR-2、FR-6、FR-11。
  - **RED_SETUP**：扩展 `services/operations/test_doctor_frontdoor.py` 并新增 status tests；覆盖 unconfigured/pending_verification/ready/fault、hostname/owner 脱敏、typed reason/recovery、无持久化、无 raw JWT/cookie/team/tunnel credential、同 typed manifest object。
  - **RED_COMMAND**：`cd octoagent && env PYTHONNOUSERSITE=1 PYTHONPATH="$(pwd)/packages/core/src:$(pwd)/packages/provider/src:$(pwd)/packages/protocol/src:$(pwd)/packages/tooling/src:$(pwd)/packages/skills/src:$(pwd)/packages/policy/src:$(pwd)/packages/memory/src:$(pwd)/apps/gateway/src" LITELLM_LOCAL_MODEL_COST_MAP=True uv run --project . --no-sync python -m pytest -q -rA apps/gateway/tests/services/operations/test_doctor_frontdoor.py apps/gateway/tests/test_cloudflare_web_access.py::TestRemoteAccessStatus`
  - **RED_ORACLE**：`F150_REMOTE_ACCESS_STATUS_MISSING`。
  - **GREEN_CHANGE**：在现有 application/operations 层增加 typed projection；doctor 与 status 复用 canonical loader/validator，不重新解析 manifest，不写数据库。
  - **GREEN_COMMAND**：与 RED_COMMAND 完全相同。
  - **GREEN_ORACLE**：四态与恢复动作 exact；故障不降级认证或改 host。
  - **REFACTOR_COMMAND**：GREEN_COMMAND，加 Ruff/format/C901≤10、secret scan 与 authority gate。
  - **REFACTOR_ORACLE**：状态只由 facts 派生；无第二 registry/cache/parser。

- [x] **T009 [L4/L3][BEHAVIOR] 发布唯一 remote-access API projection**
  - **FR**：FR-6、FR-11。
  - **RED_SETUP**：扩展 `apps/gateway/tests/test_control_plane_api.py`；覆盖 GET status、typed recovery action、打开 desktop Web URL/Access logout 指引、unconfigured/fault、schema exact、secret-negative、同 Guard 保护；mutation 不允许前端手写 state。
  - **RED_COMMAND**：`cd octoagent && env PYTHONNOUSERSITE=1 PYTHONPATH="$(pwd)/packages/core/src:$(pwd)/packages/provider/src:$(pwd)/packages/protocol/src:$(pwd)/packages/tooling/src:$(pwd)/packages/skills/src:$(pwd)/packages/policy/src:$(pwd)/packages/memory/src:$(pwd)/apps/gateway/src" LITELLM_LOCAL_MODEL_COST_MAP=True uv run --project . --no-sync python -m pytest -q -rA apps/gateway/tests/test_control_plane_api.py::TestRemoteAccessProjection`
  - **RED_ORACLE**：`F150_REMOTE_ACCESS_API_MISSING`。
  - **GREEN_CHANGE**：在既有 control-plane/router/application boundary 增加单一 projection endpoint；不创建第二 transport/state registry，不暴露 iOS route/device/service token。
  - **GREEN_COMMAND**：与 RED_COMMAND 完全相同。
  - **GREEN_ORACLE**：schema/guard/error/secret matrix 全绿；直接业务层与 HTTP projection 相等。
  - **REFACTOR_COMMAND**：GREEN_COMMAND，加 Ruff/format/C901≤10 与 architecture import gate。
  - **REFACTOR_ORACLE**：router 只做 transport mapping；状态推导留在 application/service；无 God handler。

- [x] **T010 [L4][BEHAVIOR] 增加 F149 可消费的前端 API adapter、view-model 与最小 Settings 入口**
  - **FR**：FR-8、FR-10、FR-13。
  - **RED_SETUP**：新增/扩展 `frontend/src/api/remote-access.test.ts` 与 `frontend/src/domains/settings/RemoteAccessSettings.test.tsx`；覆盖四态、普通语言、Advanced 诊断、打开 desktop Web、Access logout/recovery、无 JWT/service token/provider selector/pairing/device/mobile browser 文案；以 F149 Claude Design semantic markers 验证层级，不冻结当前 Web 的 CSS/layout。
  - **RED_COMMAND**：`cd octoagent/frontend && npm test -- src/api/remote-access.test.ts src/domains/settings/RemoteAccessSettings.test.tsx`
  - **RED_ORACLE**：`F150_DESKTOP_WEB_SETTINGS_ENTRY_MISSING`。
  - **GREEN_CHANGE**：增加唯一 API adapter/pure view-model 与可由 F149 挂载的完整 Settings 语义组件；F150 不修改旧 `SettingsPage`，也不把新入口塞进当前旧 Web composition。视觉结构以 Claude Design 初稿为基线，禁止为复用当前旧 Web 样式而改设计；F149 stable 后只负责最终页面挂载与视觉 composition，不复制状态机。
  - **GREEN_COMMAND**：与 RED_COMMAND 完全相同。
  - **GREEN_ORACLE**：正向状态/动作可用；absence matrix 全绿；390px 断言只命名为 Web narrow viewport。
  - **REFACTOR_COMMAND**：GREEN_COMMAND，加 `npm run check:complexity`、`npm run build` 与 `git diff --check`。
  - **REFACTOR_ORACLE**：单 adapter/view-model；无 raw DTO 泄漏、第二 fetch client、iOS task 或现有 Web 样式反向约束。

- [x] **T011 [L3][BEHAVIOR] 验证真实 Gateway 进程的 HTTP/SSE 同链与 fail-closed**
  - **FR**：FR-2～FR-7、FR-11。
  - **RED_SETUP**：新增 `octoagent/tests/integration/test_f150_cloudflare_web_access.py`；真实 Gateway 使用临时 config/manifest、注入本地 JWKS HTTP fixture，覆盖 SPA/REST/SSE、reconnect、wrong owner/aud/signature/expired、JWKS timeout/rotation、mutation、non-loopback/public bind、invalid config exit78、无 Uvicorn/backend side effect。
  - **RED_COMMAND**：`cd octoagent && env PYTHONNOUSERSITE=1 PYTHONPATH="$(pwd)/packages/core/src:$(pwd)/packages/provider/src:$(pwd)/packages/protocol/src:$(pwd)/packages/tooling/src:$(pwd)/packages/skills/src:$(pwd)/packages/policy/src:$(pwd)/packages/memory/src:$(pwd)/apps/gateway/src" LITELLM_LOCAL_MODEL_COST_MAP=True uv run --project . --no-sync python -m pytest -q -rA tests/integration/test_f150_cloudflare_web_access.py`
  - **RED_ORACLE**：`F150_GATEWAY_WEB_ACCESS_CHAIN_MISSING`。
  - **GREEN_CHANGE**：只补齐 production composition/wiring；不新增新认证中间件、second app、second config load 或真实外网访问。
  - **GREEN_COMMAND**：与 RED_COMMAND 完全相同。
  - **GREEN_ORACLE**：真实进程正负矩阵全绿；HTTP/SSE verifier identity 同源；故障无 downgrade。
  - **REFACTOR_COMMAND**：GREEN_COMMAND，加 Ruff/format、runtime architecture gate 与 `git diff --check`。
  - **REFACTOR_ORACLE**：L3 不替代 L4；没有 sleep/rerun/HOME/credential/network fallback。

- [x] **T012 [L4][BEHAVIOR] 执行 adversarial security、secret 与架构坏味道门**
  - **FR**：FR-6、FR-10～FR-13。
  - **RED_SETUP**：扩展 F150 architecture/security gate：扫描新增数据库 migration/state、session/device/pairing/mobile route/Access Bypass/service token、第二 manifest parser/JWT verifier/Guard/remote registry、raw header/claim logging、current-Web CSS 作为设计基线；每个负例有 clean accept control 与 observable delta。
  - **RED_COMMAND**：`cd octoagent && env PYTHONNOUSERSITE=1 PYTHONPATH="$(pwd)/packages/core/src:$(pwd)/packages/provider/src:$(pwd)/packages/protocol/src:$(pwd)/packages/tooling/src:$(pwd)/packages/skills/src:$(pwd)/packages/policy/src:$(pwd)/packages/memory/src:$(pwd)/apps/gateway/src" LITELLM_LOCAL_MODEL_COST_MAP=True uv run --project . --no-sync python -m pytest -q -rA tests/gate/test_runtime_architecture.py::TestManifestIntegrity::test_f150_scope_allows_exact_web_access_contract_and_rejects_sibling_or_ios_drift apps/gateway/tests/test_cloudflare_web_access.py::TestSecretAndArchitectureBoundary`
  - **RED_ORACLE**：`F150_SECURITY_RATCHET_MISSING`。
  - **GREEN_CHANGE**：只修真实发现的 duplicate authority/secret exposure/职责漂移；不得通过 allowlist 扩大或删除断言假绿。
  - **GREEN_COMMAND**：与 RED_COMMAND 完全相同。
  - **GREEN_ORACLE**：所有 must-fix=0；合法单路径正向通过。
  - **REFACTOR_COMMAND**：GREEN_COMMAND，加全量 Ruff/format/C901≤10、frontend `npm run check:complexity` / `npm run build` 与 `git diff --check`。
  - **REFACTOR_ORACLE**：无第二 transport/auth/session/state/registry/compat path，无 God function/test 或吞错。

- [x] **T013 [L1][CHARACTERIZATION] 验证桌面 Web Access 旅程与窄窗口 Web 健壮性**
  - **FR**：FR-5、FR-8、FR-10、FR-13。
  - **SETUP**：新增 `frontend/e2e/remote-access-web.spec.ts`，用 test-only deterministic local Access edge fixture 驱动登录回跳、刷新恢复、真实 Gateway SSE、登出、过期后重新认证与错误恢复；edge fixture 不实现 Octo JWT/Guard/API/session。桌面 viewport 为产品验收，390px 只检查 Web overflow/focus，不出现“手机/iOS 已交付”断言。
  - **COMMAND**：`cd octoagent/frontend && env PYTHONNOUSERSITE=1 PYTHONPATH="$(cd .. && pwd)/packages/core/src:$(cd .. && pwd)/packages/provider/src:$(cd .. && pwd)/packages/protocol/src:$(cd .. && pwd)/packages/tooling/src:$(cd .. && pwd)/packages/skills/src:$(cd .. && pwd)/packages/policy/src:$(cd .. && pwd)/packages/memory/src:$(cd .. && pwd)/apps/gateway/src" LITELLM_LOCAL_MODEL_COST_MAP=True npm run test:e2e -- e2e/remote-access-web.spec.ts`
  - **DISCOVERY**：旧命令第一次在pytest/浏览器前因未声明命名`chromium` project退出，判为无效前置错误；最终命令直接使用既有Playwright默认Chromium，不修改F150 authority外的Playwright配置。首次有效执行及最终字节复验均为2/2 PASS，因此不存在诚实behavior RED。
  - **PASS**：登录回跳、刷新、真实 SSE、Access logout、session expiry 后重新认证、一次性上游错误恢复全部通过；浏览器未保存 Octo bearer，未出现二次登录/配对/session/device；390px 仅验证 Web overflow 与键盘焦点。
  - **REVIEW**：test-only Access edge 不复制生产认证；L1 launcher 的 HOME/XDG 全部重定向到 instance root；无固定 sleep/blanket retry；未挂载或修改 F149-owned 最终 Web composition，Claude Design 基线不退化。

## Phase 3 — 部署、live 与交接

- [x] **T014 [L3][BEHAVIOR] 冻结 named tunnel/service 配置模板与只读诊断**
  - **FR**：FR-1、FR-2、FR-6、FR-7。
  - **RED_SETUP**：新增 service/template/doctor deterministic tests，覆盖 named tunnel、loopback origin、Access audience/hostname、quick tunnel/public bind/credential leakage/system service missing；所有 filesystem/service facts 注入。
  - **RED_COMMAND**：`cd octoagent && env PYTHONNOUSERSITE=1 PYTHONPATH="$(pwd)/packages/core/src:$(pwd)/packages/provider/src:$(pwd)/packages/protocol/src:$(pwd)/packages/tooling/src:$(pwd)/packages/skills/src:$(pwd)/packages/policy/src:$(pwd)/packages/memory/src:$(pwd)/apps/gateway/src" LITELLM_LOCAL_MODEL_COST_MAP=True uv run --project . --no-sync python -m pytest -q -rA apps/gateway/tests/services/operations/test_doctor_frontdoor.py::TestCloudflaredServiceContract`
  - **RED_ORACLE**：`F150_CLOUDFLARED_SERVICE_CONTRACT_MISSING`。
  - **GREEN_CHANGE**：补充只读 doctor/template 与 `docs/codebase-architecture/remote-access.md` / deployment docs；不自动登录 Cloudflare、不创建 DNS/tunnel/service、不保存 credential。
  - **GREEN_COMMAND**：与 RED_COMMAND 完全相同。
  - **GREEN_ORACLE**：named/service 正向与 quick/public/secret 负例全绿。
  - **REFACTOR_COMMAND**：GREEN_COMMAND，加 Ruff/format、Markdown links/fences/trailing 检查。
  - **REFACTOR_ORACLE**：单诊断事实源；无通用 remote CLI/provider abstraction 或外部副作用。
  - **RESULT**：有效 RED 为9/9仅命中`F150_CLOUDFLARED_SERVICE_CONTRACT_MISSING`；GREEN/REFACTOR为9/9，相关Cloudflare+doctor回归106/106。只读合同要求named tunnel、loopback origin、hostname/audience一致、catch-all 404与托管service installed/running；quick tunnel、public bind、inline credential和service缺失均fail closed。模板不含个人域名默认值，不读取credential内容，不执行Cloudflare或系统service外部写入。

- [x] **T015 [LIVE][EXTERNAL] 经单次授权执行最终 named tunnel 发布验证**
  - **前置**：T004～T014 全部完成；用户重新明确授权本次账户/DNS/tunnel/Access/service 操作。
  - **动作**：对 production Gateway 执行 desktop SPA、REST、SSE、logout/expiry/re-auth、5-run 时序、断线恢复与 secret scan；同时证明 origin 仍只绑定 loopback、quick tunnel/mobile route/Bypass 不存在。
  - **CORRECTIVE_TDD**：首次真实执行中，Access 已认证 SPA 到达 origin，但 Uvicorn
    把 loopback peer 改写为 `X-Forwarded-For` 公网地址，API 在 JWT 前错误返回
    `FRONT_DOOR_ORIGIN_NOT_LOOPBACK`。先在
    `apps/gateway/tests/test_main.py` 以
    `F150_UVICORN_PEER_INTEGRITY_MISSING` 建 production-entry RED，再只让唯一
    `uvicorn.run` 显式 `proxy_headers=False`；完成 GREEN/REFACTOR 与本地回归后，
    重新执行本任务的 live transaction。首次失败不生成 attestation。
  - **PASS**：满足 Spec §4 全部十项；只保存脱敏 attestation。
  - **FAIL**：任何一项失败即不完成 F150；不得通过延长阈值、跳过 Access、保存 secret 或把手机浏览器当 iOS 假绿。
  - **RESULT**：首次live因Uvicorn信任转发头而在JWT前误判非loopback，按上方
    corrective TDD完成修复且未生成失败attestation。修复后production Gateway经既有
    named tunnel完成桌面SPA、REST、SSE、logout、One-time PIN重新认证、刷新与错误恢复；
    T003的5-run时序证据继续逐字节引用。脱敏attestation位于
    `evidence/live/T015-production-web/attestation.v1.json`，SHA-256
    `e219136a51dd9f5855d7a77c8c96e33bc82817e270da8e49532f7312ffa9148d`，
    1814 bytes，secret/raw identity扫描为0。临时Gateway与runtime已清理，origin端口无监听；
    named tunnel service继续运行且配置SHA未变化。经用户当次授权新增One-time PIN登录方式，
    未改变DNS、tunnel、Access policy/session duration，也未增加Bypass或mobile route。

- [x] **T016 [ARTIFACT] 固化 F153 原生 iOS handoff 与 Claude Design 双端基线**
  - **输入**：F150 final Web contract、F149 Design export、Cloudflare 官方限制与 T015 attestation。
  - **动作**：更新 Blueprint/Milestone/remote-access 文档，明确 F153 真机候选、device key/short credential/rotation/revoke、service token prohibition、mobile route/Bypass absent；明确 iOS 保留 Claude Design 视觉语言但使用 SwiftUI/Apple 原生交互与无障碍，不复制 Web 组件结构。
  - **PASS**：F150 文件/测试/路由中 iOS production path=0；F153 handoff 有单一 owner；当前 Web UI 被称为设计基线的 active statement=0。
  - **RESULT**：权威Blueprint、Milestone、deployment与remote-access导览在T016开始前已包含完整
    handoff，因此本任务为诚实的artifact validation/no-op，没有为制造diff重写文档。机械核验：
    changed Swift/iOS production path=0；F153正式owner行=1；真实iPhone、设备密钥、短期凭证、
    轮换、单设备撤销、App内service token禁令、mobile route/Access Bypass禁令、SwiftUI与
    Claude Design双端基线全部存在；把当前Web UI当设计基线的active statement=0。

- [x] **T017 [VERIFY] 全量验证、事实同步与提交前主审**
  - **动作**：
    1. 运行所有 F150 L4/L3/L1 selectors、现有 Gateway/front-door 回归、runtime architecture gate、frontend unit/lint/build。
    2. 复算 manifest schema、authority scope、secret-negative、数据库 migration absence、run/evidence hash 与 T015 attestation。
    3. 同步 `tasks.md`、Trace、Checklist、Blueprint、Milestone 与代码架构导览；记录 production/test LOC、最大函数、McCabe、职责簇和重复分支。
  - **PASS**：FR-1～FR-13 和 Spec §4 逐项有证据；行为任务 R→G→R 完整；T015 live PASS；must-fix=0；F149 T000 获得可消费的 F150 stable commit。
  - **提交边界**：主审通过前不得 stage/commit/push；禁止 force push。
  - **LOCAL_REVIEW_RESULT**：提交前验证已通过。Python聚焦回归
    `363 passed / 1 skipped`，F150 authority/security精确门`22/22`，前端
    unit`17/17`、Playwright`2/2`、complexity与production build均通过；
    manifest exact 7字段、changed migration=0、T003/T015 attestation散列和
    secret-negative复算通过。新增cohesive production文件共1345行，Python最大函数
    50行、McCabe最高8、exact函数体重复组0；职责保持6簇。全局F151-only
    `architecture all`会按设计拒绝F150 feature路径，适用的F150 exact authority
    已通过。主审后新增一条`F150_REMOTE_ACCESS_STATUS_MISSING`纠正RED，证明Guard、
    dependency与status projection仍会在请求期重复解析启动配置；GREEN只把
    Cloudflare生产路径接到同一启动期`FrontDoorConfig`对象，REFACTOR复验完整聚焦
    回归与静态门，非Cloudflare旧模式保持原语义。产品实现已形成提交
    `bf29d6be7d7a86c298cd45699488a8640065a566`；仓库级门禁配套提交及当前稳定点为
    `5e6f4846703b7126cd104c8b9678e0c2f5300cc8`，F149 T000已获得可消费基线。

## 依赖图

```text
T000
  └─ T001 authority
      ├─ T002 probe tool ─ T003 early live spike
      └─ T004 config/manifest
          ├─ T005 classifier/exposure
          └─ T006 JWT/JWKS
              └─ T007 Guard/mutation/HTTP+SSE
                  ├─ T008 status/doctor ─ T009 API ─ T010 Settings adapter
                  └─ T011 L3
                      └─ T012 adversarial
                          └─ T013 desktop Web L1
                              └─ T014 deployment
                                  └─ T015 final live
                                      └─ T016 iOS handoff
                                          └─ T017 final verify
```

T003 已经用户单次授权并以真实 named tunnel、Access application 与受管 service 完成 5/5 SSE spike；T015最终production live、T016原生iOS handoff与T017最终验证均已通过。产品实现提交为`bf29d6be7d7a86c298cd45699488a8640065a566`，仓库级门禁配套提交及当前稳定点为`5e6f4846703b7126cd104c8b9678e0c2f5300cc8`；F150已稳定并解除F149 T000前置阻断，仍禁止force push。
