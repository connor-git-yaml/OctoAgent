# Feature Specification: F144 验收自动吸收（Attestation Absorption）

**Feature ID**: F144
**Slug**: attestation-absorb
**Milestone**: M9（质量保证体系）— P1，与 F140/F142 并行
**规模**: S-M
**Status**: 收窄 spec v1（recon 闭环后定稿，直接进实施）
**Base**: origin/master `2bd8679b` / 分支 `feature/144-attestation-absorb`（worktree `.claude/worktrees/F144-attest/`）
**上游依据**: `docs/blueprint/milestones.md` M9 节 F144 行 + 「验证吸收原则」段（2026-07-12 用户拍板）+ `CLAUDE.local.md` §M9 尾部同名段

---

## 0. 宪章与吸收对账（Why）

**验证吸收原则（用户拍板，M9 全局约束）**：任何「请用户手工验证」的输出视为体系缺陷——必须先尝试分层吸收（L4 单元 → L3 确定性 e2e/scripted → L1 UI 自动化 → 本机 live 探针），只有物理不可自动化的残余才允许落 attestation 清单一行（F141 release lane 消费）。

F144 是首批吸收，吸收对象 = 三条挂在用户身上的手工验收：

| 手工验收项 | 原文 | 吸收去向（本 Feature）| 物理残余 |
|-----------|------|---------------------|---------|
| **F130 AC-1**（手机触达）| 「手机装 Tailscale → `octo remote enable` → 浏览器开 `https://<magicdns>/` 输 token 打开完整 Web UI」 | **语义半边** → L3/L4 矩阵（交付①：serve 注入 XFF 时 bearer 放行 / loopback 拒绝——此前只有 F130 completion-report §2 的人工复核记录，零机械格）；**链路半边** → 本机 live 探针（交付②remote：Mac 自身在 tailnet，curl published URL 验 /ready+SPA+bearer+SSE）| 手机首屏体验（optional，探针绿后基本冗余）→ 清单 ATT-130-PHONE |
| **F129 AC-1**（崩溃自愈+开机自启）| user-guide §6 手工脚本「记 pid → kill -9 → sleep 12 → 新 pid」+「重启 Mac 后 service status 运行中」 | **崩溃自愈** → 本机 live 探针（交付②service：status→kill 真 pid→poll /ready→断言新 pid≠旧 pid，把 §6 手工脚本可重复化）| 重启机器验开机自启（要真 reboot，物理不可自动化）→ 清单 ATT-129-BOOT |
| **F135 gap-1**（USER.md 初始化引导闭环）| 「让 Agent 把画像写进 USER.md → proposal → 确认 → 审批卡片 → 批准 → 落盘」（F136 completion-report §8 的真机验证步骤）| **全链** → L3 scripted 用例（交付③：F138 脚本脑驱动 behavior.write_file + F136 服务端审批全链，approve/reject 双路径，零真 LLM 零 OAuth）| 无（全吸收）|

**产出物第 4 件** = 残余 attestation 清单（交付④）：机器可读、首版仅上表两条物理残余，F141 release lane 消费。

## 1. 范围声明

### 1.1 In Scope（4 件）

1. **frontdoor mode×header L3 矩阵补格**：扩 `octoagent/apps/gateway/tests/test_frontdoor_auth.py`，参数化矩阵 ~17 格（见 §3.1），纯 hermetic。
2. **本机 live 探针 `octo attest`**：新 CLI 命令组（`attest remote` / `attest service`），探针逻辑 hermetic 单测全覆盖（DI 注入 fake），真机执行留给主 session；**绝不进 CI**。
3. **F135 gap-1 L3 scripted 用例**：新 `apps/gateway/tests/e2e_live/test_e2e_scripted_write_approval.py`（marker `e2e_scripted` + `e2e_live`，同 F138 keystone）。
4. **attestation 残余清单**：新 `docs/codebase-architecture/attestation-checklist.md`（机器可读，2 项）。

### 1.2 Out of Scope（显式不做）

- **F140 地盘**：frontend/**、playwright、CI workflow —— 零触碰。
- **F142 地盘**：packages 库钉住/wire 测试、pyproject backend 并行参数、925bc29b 标的 4 个 CI-skip 测试文件 —— 零触碰。
- **pyproject**：`e2e_scripted` marker 已存在（F138），本 Feature **不加新 marker、不改 pyproject**（探针是 CLI 命令非 pytest 测试；探针单测是普通 L4 无需 marker）。
- **`.github/workflows/`**：零触碰（探针不进 CI；F141 才做 lane 编排）。
- **探针代跑 `octo remote enable` / 改任何配置 / sudo / 改系统设置**：探针只读探测 + （service 探针）kill 自己的 gateway 进程；enable 是用户/主 session 决策。
- **真 SSE 流内容断言**（探针）：只验握手/认证语义（见 §3.2 D-4），流内容质量归 L2/真使用。
- **F136 审批超时路径的 e2e**（300s 不可 L3 化；F136 单测已覆盖超时分支）。

## 2. 关键设计决策（recon 定稿，含一处对任务书的显式偏离）

### D-1 探针落点 = 独立 `octo attest` 命令组（非 e2e live 域）

- 语义区分：attest = **真机验收探针**（在用户真实托管实例上跑、有真实副作用如 kill 进程），e2e = **测试套件**（hermetic 红线、tmp 实例）。混入 e2e 会破坏 e2e_live 的 hermetic autouse fixture 语义（其 `_hermetic_environment` 恰恰把 OCTOAGENT_* 路径重定向到 tmp——探针要的是相反语义：钉住真实实例根）。
- 范式沿用 F129 `service_commands` / F130 `remote_commands`：Click group + rich panel 输出 + 纯逻辑函数与 CLI 呈现分离 + DI 注入缝。
- 落点：`packages/provider/src/octoagent/provider/dx/attest_commands.py`，`cli.py` 挂 `main.add_command(attest_group)`。

### D-2 探针三态报告协议（非二元）

```
status ∈ {pass, not_enabled, fail}
exit code：pass=0 / not_enabled=0 / fail=1
--json：机器可读（F141 release lane 消费；status + checks[] + next_steps[]）
```
- `not_enabled` 不是失败（远程触达是 optional 能力；服务未安装同理）——输出结构化「未启用 + 下一步指引」。
- `fail` = 已启用但链路断（这才是回归信号）。
- F141 lane 语义（handoff 写明）：release lane 跑 `octo attest remote --json` + `octo attest service --json`，`fail` 阻断；`not_enabled` 按 lane 策略（remote optional → 记录；service → 部署机上应为 fail 级警告）。

### D-3 service 探针用 SIGKILL，**显式偏离任务书的 SIGTERM**（带证据）

任务书写「SIGTERM → poll /ready 恢复」。recon 证据链推翻 SIGTERM：
- launchd plist `KeepAlive{SuccessfulExit=false}`（F130 completion-report §6 / service_manager.py:515）：**只在非成功退出时拉起**；
- uvicorn 收 SIGTERM 走优雅关闭、exit 0 → launchd 视为成功退出、**不拉起**——这正是 F129 user-guide §4 `octo stop` 的设计语义（「优雅停止；octo restart 仍会拉起」= 手动拉，不是自动）；
- F129 user-guide §6 钦定的崩溃模拟就是 `kill -9`（SIGKILL），§4 明确「`--force`（SIGKILL）会被 launchd 视为崩溃**立即拉起**」。
- 结论：SIGTERM 探针在健康系统上必假失败（等 /ready 恢复直到超时）；SIGKILL 是「崩溃自愈」语义的忠实模拟。探针默认 SIGKILL，不提供 SIGTERM 选项（避免用户误选踩语义坑）。

### D-4 remote 探针检查链（enabled 信号 + 检查项 + token 零泄漏）

- **enabled 信号 = 实例生效 mode == bearer（单独作信号，Codex spec 评审 P2-1 修正）**：`octo remote enable` 只在 serve 成功后才持久化 bearer，故 bearer 是「用户启用过远程触达」的持久信号。三态映射：
  - mode ≠ bearer → `not_enabled` + 指引（tailscale 三态只作 next_steps 参考）；
  - mode == bearer 且 tailscale 非 READY → **`fail`**（已启用但链路断——正是 F130 回归信号，绝不能归 not_enabled 被 lane 忽略）；
  - mode == bearer 且 READY → 进入 HTTP 检查链。
- **token**：按 `_bearer_token_env_name` 语义解析**变量名**（`read_instance_effective_env` 只用于 mode/变量名/port 解析）；token **值**只从实例 `.env`/`.env.litellm` 经 `dotenv_values` 读（同 `remote_commands._token_set_in_instance_env` 语义，Codex spec 评审 P2-2 修正——防自定义非 OCTOAGENT_ 前缀变量的 shell-only 值让探针假通过、服务重启后 503）。enabled 但 token 未设 → `fail`（坏配置）。**token 值只存内存传给 HTTP header/query，绝不进 report/stdout/日志/异常文本**（Constitution #5；report 只含布尔「已设」）。
- **HTTP 检查链**（httpx client DI；base = `https://<dns_name>/`）：
  1. `GET /ready` 无 token → 200 + status ready/ok（health.router 未挂 front_door protected——先证 serve 反代链 + gateway 活）；
  2. `GET /` → 200 + HTML（SPA index 可取；mount `/` 绕过 front_door 是 F130 已知 limitation，探针如实按此语义测）；
  3. `GET /api/control/snapshot` **无 token** → 401（bearer 纵深真在挡——防「serve 通了但认证裸奔」）；
  4. 同路径带 `Authorization: Bearer <token>` → 200（token 有效）;
  5. **SSE 半边**（实施期经 Codex final/re-review 加固为「负向 + 正向」两段）：**负向**先行——错 token（真 token 派生后缀）访问 stream 路径必须 401（防 guard 丢失/query-token 校验回归时「任意 404」骗过正向判别）；**正向**——`GET /api/tasks`（带 token）取最近 task_id → 存在则流式握手（200 + `text/event-stream` + **至少读到一个 chunk**，零字节断流判失败）；无任务则退化为合成 id 断言 **404**（TASK_NOT_FOUND = 认证已通过）。token 一律经 `params=` percent-encoding（防 `+`/`&`/`#` 损坏）。无副作用，绝不 POST 造任务。
- 任一检查不符 → `fail` + 每项 check 的结构化结果 + fix_hint。

### D-5 service 探针检查链

1. `build_service_manager(resolve_instance_root()).status()`（DI factory，同 doctor 范式）→ `installed=False` → `not_enabled` + 「octo service install」指引；
2. 健康前置：running + pid + ready≠False，否则 `fail`（自愈探针前提是当前健康）；
3. `--dry-run` → 到此为止，报告「将 kill pid=X 并等待 /ready 恢复（服务秒级闪断）」；
4. 真跑：**输出先声明「服务将秒级闪断」**（打印在 kill 之前）→ `kill_fn(pid, SIGKILL)`（DI）→ poll `/ready`（ReadyProber DI + sleep DI，预算 90s / 2s 间隔；URL 取 descriptor.verify_url，缺失则由实例 env host/port 构造）→ 恢复后 `status()` 取新 pid；
5. 断言新 pid ≠ 旧 pid 且 running+ready → `pass`；超时未恢复 / pid 未变（kill 失败）→ `fail` + 指引（查 `octo logs` / `octo service status`）。

### D-6 矩阵补格用「guard 聚焦轻量 app」fixture（新增），既有 8 格全栈 fixture 不动

- 既有 8 格（full create_app + lifespan）继续守「guard 挂在真实路由树上」的 wiring 层——**零修改**。
- 新矩阵 ~17 格聚焦 FrontDoorGuard 决策表本身（mode×header×credential）：minimal FastAPI（一条受 guard 保护的普通路由 + 一条 `/api/stream/` 前缀路由）+ `ASGITransport(client=…)` 模拟源 IP。每格免 lifespan/DB，速度 L4 级。
- guard 的 config 读取走既有 env override 机制（`OCTOAGENT_FRONTDOOR_MODE` 等 monkeypatch），与既有测试同款。

### D-7 gap-1 scripted 用例 = keystone 范式 + approvals 真 REST 路由 + 并发批准

- fixture 仿 F138 keystone（OctoHarness + ScriptedModelClient + resolve_for_alias bomb + 空 CredentialStore + importorskip 防御），**外加** `app.include_router(approvals.router)`——harness bootstrap 已把真 ApprovalManager/ApprovalGate 放 `app.state`，`routes/approvals.py` 的 `Depends(get_approval_manager/get_approval_gate)` 直接可用 → 批准走 **真 REST**（`POST /api/approve/{id}`，与 Web 审批卡片同一条路），不是直调内部方法。
- **`permission_preset: "full"` 是特性不是绕过**：F136 的 gate 在 handler 内部、对 REVIEW_REQUIRED 文件无条件触发——最宽 preset 下审批仍拦住 = 「服务端审批绑定不可被 policy 放宽绕过」的**最强形态断言**（docstring 写明）。broker 层 policy 审批是另一层通用机制，收窄出去避免双审批干扰确定性。
- **approve 路径**：脚本 `[tool_call(behavior.write_file{file_id:"USER.md", content, confirmed:true}), complete]` → `asyncio.create_task(llm_service.call…)` → poll `approval_manager.get_pending_approvals()` 出现 behavior.write_file 审批（≤10s，e2e_live conftest 有 30s SIGALRM 预算）→ **批准前断言文件未落盘**（「不批不写」的 e2e 半边）→ ASGI `POST /api/approve/{id}` `{"decision":"allow-once"}` → await loop → 断言：①USER.md 真落盘含脚本内容 ②result.content=脚本第 2 步（零真调用防御）③事件链含 APPROVAL_REQUESTED + TOOL_CALL_STARTED/COMPLETED（tool=behavior.write_file）④审批记录含 diff 的 risk_explanation（F136 P1 语义回归）。
- **reject 路径**：`{"decision":"deny"}` → 断言：①文件不写 ②决策环**继续**（脚本第 2 轮仍被消费、result 正常完成——F136 DP-4「显式拒绝恢复 RUNNING 对话继续」的 e2e 证据）③工具结果 status=rejected / reason 含 APPROVAL_REJECTED。

### D-8 attestation 清单 = markdown 文档内嵌 fenced YAML（机器可读单一事实源）

- `docs/codebase-architecture/attestation-checklist.md`：正文说明宪章（验证吸收原则）+ 消费方（F141 release lane）+ 一个 ```yaml fenced block 作机器可读源。
- 每项字段：`id / source_ac / why_physical（为什么物理不可自动化）/ action（验收动作一行）/ frequency / last_attested（YYYY-MM-DD 或 null）/ optional`。
- 首版两项：`ATT-129-BOOT`（重启机器验开机自启；真 reboot 无法自动化）/ `ATT-130-PHONE`（optional 手机首屏；跨设备人眼体验，attest remote 绿后基本冗余）。
- 增项纪律写进文档：新增行须先给「为什么 L4/L3/L1/探针都吸收不了」的理由。

## 3. 功能需求（FR）+ AC↔test 绑定

### FR-A 矩阵（交付①）`[@test octoagent/apps/gateway/tests/test_frontdoor_auth.py::TestFrontDoorModeHeaderMatrix]`

| # | 格 | 期望 |
|---|-----|------|
| A1 | loopback × 5 个 proxy hint header 逐个注入（`forwarded`/`x-forwarded-for`/`x-forwarded-host`/`x-forwarded-proto`/`x-real-ip`）| 403 `FRONT_DOOR_LOOPBACK_PROXY_REJECTED`（5 格；现仅 x-forwarded-for 一格）|
| A2 | bearer + 正确 token × 同 5 header 逐个注入 | 200（5 格；**核心缺格**——「serve 注入转发头时 bearer 放行」此前只有人工复核记录）|
| A3 | bearer + 错 token（无 header / 带 XFF 两格）| 401 `FRONT_DOOR_TOKEN_INVALID`（verdict 由 token 决定，不受 header 扰动）|
| A4 | trusted_proxy + 源 IP 不在 CIDR | 403 `FRONT_DOOR_TRUSTED_PROXY_REQUIRED` |
| A5 | trusted_proxy + 源 IP 在 CIDR + 错共享 header 值 | 403 `FRONT_DOOR_PROXY_TOKEN_INVALID` |
| A6 | bearer + SSE 路径 query token 正确 + XFF | 200（serve 场景 SSE 半边）|
| A7 | bearer + SSE 路径 query token 错误 + XFF | 401 `FRONT_DOOR_TOKEN_INVALID` |
| A8 | bearer + query token 用在**非** `/api/stream/` 路径 | 401 `FRONT_DOOR_TOKEN_REQUIRED`（query token 仅 SSE 路径有效的边界）|

- **AC-A**：上表 17 格全绿；既有 8 格零修改仍绿。

### FR-B `octo attest remote`（交付②a）`[@test octoagent/packages/provider/tests/dx/test_attest_commands.py::TestAttestRemote*]`

- FR-B1 三态（§D-4 修正后）：mode≠bearer → `not_enabled`（exit 0）；mode==bearer 且 tailscale 非 READY → `fail`（exit 1，已启用链路断）；mode==bearer 且 READY → 进 HTTP 检查链。
- FR-B2 检查链 §D-4 五项，全过 → `pass` + 打印 published URL。
- FR-B3 token 零泄漏：report/stdout/JSON 任何字段不含 token 值（机械断言：以高熵 sentinel token 跑 fake 链路，扫描全部输出无命中）。
- FR-B4 优雅降级：httpx 异常/超时 → 对应 check `fail` + hint，探针不抛未捕获异常（Constitution #6）。
- FR-B5 只读红线：探针全程无 `serve`/`up`/写配置命令、零 sudo（机械断言 FakeCommandRunner 记录）。
- **AC-B**：hermetic 单测覆盖 pass / not_enabled(mode≠bearer) / fail(bearer+tailscale 断链、缺 token、shell-only token 不采信、/ready 非 200、snapshot 无 token 非 401、SSE 401) + FR-B3/B5 机械断言。

### FR-C `octo attest service`（交付②b）`[@test …::TestAttestService*]`

- FR-C1 三态：未安装 → `not_enabled` + 指引；安装但不健康 → `fail`。
- FR-C2 `--dry-run` 只检不杀（机械断言 kill_fn 零调用）。
- FR-C3 真跑序列 §D-5：声明闪断 → SIGKILL → poll /ready 恢复 → 新 pid≠旧 pid → `pass`。
- FR-C4 失败面：超时未恢复 → `fail` + `octo logs` 指引；新 pid==旧 pid → `fail`。
- FR-C5 零 sudo / 不改配置 / 只 kill status() 返回的 gateway pid。
- **AC-C**：hermetic 单测覆盖上述全分支（fake manager/kill/prober/sleep，零真 kill 零真 HTTP）。

### FR-D gap-1 scripted（交付③）`[@test octoagent/apps/gateway/tests/e2e_live/test_e2e_scripted_write_approval.py]`

- FR-D1 approve 路径（§D-7）；FR-D2 reject 路径（§D-7）。
- **AC-D**：两用例在零真 LLM/零宿主 OAuth 下稳定绿（30s SIGALRM 内）；批准动作走真 REST `POST /api/approve/{id}`。

### FR-E 清单（交付④）+ 文档

- FR-E1 `attestation-checklist.md` 按 §D-8 落地。
- FR-E2 living-docs：milestones.md F144 行 ✅；`e2e-testing.md` 新「本机 live 探针」节；`remote-access.md` / `service-and-logging.md` 各补一小段指向 attest。
- FR-E3 `handoff-to-F141.md`：探针 exit/JSON 契约 + release lane 编排建议 + 清单消费格式。

## 4. 验收门（Gate）

1. 全量回归 0 regression vs 本 worktree 实测 baseline（PYTHONPATH 锁 + `uv run --no-sync python -m pytest -m "not e2e_live"`）。
2. e2e_smoke 8/8；`test_e2e_scripted_decision_loop.py`（F138 keystone）不回归；新 scripted 用例全绿。
3. 双评审：Codex final（挑战面：矩阵是否真补了 bearer×XFF 缺格 / 探针 token 是否零泄漏 / gap-1 是否真走了 F136 审批没绕过 / 探针 fake 是否掩盖真命令语义）+ Opus 对抗自审，0 HIGH。
4. completion-report + living-docs 漂移闸 + handoff-to-F141。
5. **不 push origin**，等用户拍板。

## 5. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 探针 fake 掩盖真命令语义（评审挑战点）| fake 只替换「执行」层（CommandRunner/httpx/kill/sleep），命令 argv、URL、header 全在断言面；SIGKILL 语义有 F129/F130 文档证据链（§D-3）而非 fake 推断 |
| gap-1 用例 30s SIGALRM 超时 | keystone 先例单 case <3s；poll 审批 ≤10s 预算；两 case 独立 fixture |
| 新模块被 pre-commit hook 以 master venv 树收集炸 | 新测试文件顶部 `pytest.importorskip`（F138 §3.5 同款防御）|
| e2e_live conftest hermetic fixture 与 attest 语义冲突 | attest 是 CLI 非 pytest 测试，天然不进 e2e_live；其单测在 provider/tests/dx 常规域 |
| `/api/tasks` 空实例无任务 → SSE 真握手不可得 | 退化 404-判别（认证语义仍验），report 注明 streaming untested（§D-4.5）|
