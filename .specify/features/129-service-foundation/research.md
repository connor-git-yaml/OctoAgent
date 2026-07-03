# F129 常驻服务地基 — 研究笔记

> 设计先行研究。所有 Octo 现状结论带 file:line 证据（worktree 内 `octoagent/` 子目录为真实源码根）。
> 三块参考实现（Hermes / OpenClaw）+ Octo 现状核实。**结论用于 spec.md 的 §0 设计基础说明。**

---

## A. Octo 现状核实（直接源码证据，非 agent）

### A.1 现有进程启停机制（`octo restart/stop/update`）

- **CLI 命令面**（`octoagent/packages/provider/src/octoagent/provider/dx/cli.py`）：`main` click group 注册的命令有 `auth / config / behavior / backup / restore / export / import / stop / update / restart / verify / project / secrets / cleanup / memory / e2e / setup / init / doctor / onboard`（cli.py:85-108, 111, 297, 345, 391）。**无 `serve` 命令**——gateway 靠脚本/uvicorn 直起，不经 CLI。
- **`octo restart/stop/update/verify`** → 都委托 `UpdateService`（update_commands.py:133-155 restart / 52-99 stop / 102-130 update）。
- **进程 spawn 真相**：`UpdateService._run_start_phase`（restart 内部）用
  `subprocess.Popen(descriptor.start_command, cwd=start_cwd, env=env, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)`（update_service.py:576-583）。**另一处** update 路径同款（update_service.py:108-122）。
  → **证实报告两大 gap**：①**无 OS 级守护**——裸 detached 进程（`start_new_session=True`），崩溃后无东西拉起；②**stdout/stderr→DEVNULL**——运行日志 + 崩溃 traceback 全丢。
- **restart 要求"已托管"**：`_require_descriptor` 若 `managed-runtime.json` 不存在则 raise `RESTART_UNAVAILABLE`"当前 runtime 未托管，无法执行 restart/update"（update_service.py:616-624）。restart 还要求旧 pid 存活/可控（update_service.py:560-567）。→ 证实"人在外面失联时 restart 无法拉起"。

### A.2 ★ 关键复用资产：已有"托管 runtime"模型 + 稳定启动脚本

- **`ManagedRuntimeDescriptor`**（`octoagent/packages/core/src/octoagent/core/models/update.py:84-96`）：字段 `project_root / runtime_mode / restart_strategy / start_command / verify_url / verify_profile / workspace_sync_command / frontend_build_command / environment_overrides`。持久化在 `~/.octoagent/data/ops/managed-runtime.json`。
- **`RuntimeManagementMode`** 枚举（update.py:47-49）：`MANAGED` / `UNMANAGED`。
- **`RestartStrategy`** 枚举（update.py:52-54）：`COMMAND` / `SELF_SIGNAL`。→ **F129 的 launchd/systemd 是新增第三种策略**（如 `SUPERVISED`/`OS_SERVICE`），不是推翻现有模型。
- **`RuntimeStateSnapshot`**（update.py:99-106）：`pid / project_root / started_at / heartbeat_at / verify_url / management_mode / active_attempt_id`，落 `~/.octoagent/data/ops/runtime-state.json`。
- **稳定启动脚本 `run-octo-home.sh`**（`octoagent/scripts/run-octo-home.sh`）：解析 `OCTOAGENT_INSTANCE_ROOT`（默认 `$HOME/.octoagent`）→ 设 `OCTOAGENT_PROJECT_ROOT/DATA_DIR` → source `.env` → `cd PROJECT_ROOT` → `exec uv run uvicorn octoagent.gateway.main:app --host ${OCTOAGENT_HOST:-127.0.0.1} --port ${OCTOAGENT_PORT:-8000}`。
  → **这就是 launchd/systemd 的 `ExecStart` 目标**（"stable-working-dir 脚本"已存在，Hermes 概念天然对齐）；**默认 host 127.0.0.1**（证实报告"默认已 loopback"）。
- 实测 `~/.octoagent/data/ops/managed-runtime.json` 的 `start_command` = `["/bin/bash", ".../scripts/run-octo-home.sh"]`，`restart_strategy: "command"`，`verify_url: "http://127.0.0.1:8000/ready?profile=core"`。

  ⚠️ **注意矛盾点**：`install_bootstrap._build_runtime_descriptor` 生成的 default descriptor 用 `--host 0.0.0.0`（install_bootstrap.py:58-66，instance_root=None 分支）；但实例里实际 descriptor 指向 `run-octo-home.sh`（默认 127.0.0.1）。host 绑定语义是 F130 的事，F129 只需知道 ExecStart 目标脚本已存在。

- **"install" 语义已被占用**：`install-octo-home.sh` / `install_bootstrap.py`（Feature 024）做的是**app 级 bootstrap**（装依赖、写 descriptor、生成脚本），**不涉 launchd/systemd**。→ F129 的 `octo service install`（OS 守护安装）与之**语义不同**，命名要区分（设计岔路）。

### A.3 日志现状（证实无落盘 + 无脱敏）

- **唯一日志初始化**：`octoagent/apps/gateway/src/octoagent/gateway/middleware/logging_config.py` 的 `setup_logging()`（16-65）+ `setup_logfire()`（68-102），在 app 创建时调用（main.py:343-344）。
- `setup_logging()` 只加 **`logging.StreamHandler()`**（logging_config.py:59-64，清空 root handlers 后仅加这一个 stream handler）→ **无任何 FileHandler / RotatingFileHandler**。全仓 grep `RotatingFileHandler|TimedRotating|logging.FileHandler` 在 packages+apps 非测试代码 **0 命中**。→ 证实"日志无落盘"。
- 结合 A.1：进程被 Popen 以 DEVNULL 起 → StreamHandler 的 stdout 直接进黑洞。**双重丢失**。
- **env 控制**：`OCTOAGENT_LOG_FORMAT`（dev/json，默认 dev）、`OCTOAGENT_LOG_LEVEL`（默认 INFO）。
- **无脱敏 processor**：structlog processor 链（logging_config.py:27-34）无任何 secret/token redaction。→ F129 落盘时必须补脱敏（Constitution #5：secrets 不进日志），且这是 F129 新引入的风险面（一落盘就可能把 secret 写进文件）。
- Logfire 默认关（`LOGFIRE_SEND_TO_LOGFIRE` != "true" 直接 return，logging_config.py:80-82）——远程 APM 不是本地落盘方案。
- **实例内已有 `~/.octoagent/logs/`** 目录（但只含 `e2e/` 测试日志）；`~/.octoagent/data/ops/litellm.log`（907KB）是**已退役 LiteLLM** 的遗留，非 gateway 日志。→ `~/.octoagent/logs/` 是 F129 落盘的天然落点（目录已在）。

### A.4 doctor 现状（扩展点）

- `octoagent/packages/provider/src/octoagent/provider/dx/doctor.py` **556 行**（报告"557 行"基本准，误差 1）。
- **检查项是硬编码有序列表**（`run_all_checks`，doctor.py:66-108），非注册式：`check_python_version / check_uv_installed / check_env_file / check_db_writable / check_credential_valid / check_credential_expiry / check_octoagent_yaml_valid / check_telegram_config / check_telegram_token / check_secret_bindings`（10 项）+ `--live` 时 `check_telegram_readiness`（1 项）= **实测 11 项**（报告"13 检查"略高，但框架真实、非空壳、可扩展——核心判断成立）。
- **加新 check 的方式**：新增一个 `async def check_xxx(self) -> CheckResult` 方法 + 在 `run_all_checks` 列表 append（doctor.py:78-99）。返回 `CheckResult(name=, status=CheckStatus.PASS/WARN/FAIL/SKIP, level=CheckLevel.REQUIRED/RECOMMENDED, message=, fix_hint=)`（模型见 `dx/models.py`）。
- **`fix_hint` 字段**（doctor.py:125 等）= 天然承载"你的 Mac 会睡→建议 X"的补救文案。→ F129 的"禁睡只检测+建议"方案可直接用 WARN + fix_hint 落地，零系统改动。

### A.5 健康端点（service status 会用）

- `verify_url` 指向 **`/ready?profile=core`**（managed-runtime.json + update_service._run_verify_phase 轮询该 url 等 `status in {ready,ok}`，update_service.py:600-613）。
- **两个现成端点**（`routes/health.py`，main.py:366 注册，**不带 front_door 鉴权**——少数免鉴权路由）：
  - `GET /health`（health.py:83-86）：liveness，恒 `{"status":"ok"}` 200。
  - `GET /ready?profile=core|llm|full`（health.py:89-199）：readiness，`{status, profile, checks, subsystems, diagnostics}`；`checks` = sqlite/artifacts_dir/disk_space_mb/litellm_proxy(skipped)；`subsystems` = orchestrator/worker_runtime/checkpoint/watchdog/tool_registry/recovery；**all_ok=False → 503**。
- gateway `main.py:159-166 _resolve_verify_url` helper。→ **F129 `octo service status` 别重造健康探测**：复用 `/ready`（就绪）+ `runtime-state.json` 的 pid 存活检查（update_service.py:556-566 已有 `os.kill(pid, 0)` 先例）。
- **runtime-state 持久化 helper 已存在**：main.py:207-273 `_create_runtime_state_snapshot`（写 pid/heartbeat/verify_url/management_mode）+ `_persist_runtime_state`，但**下沉 OctoHarness 后 lifespan 内是否真调 + heartbeat 是否真在跑，F129 需确认**（agent 未在 main.py lifespan 见直接调用点）。
- 运行时状态 REST：`GET /api/ops/update/status`（ops.py:391-394）暴露最近 update 摘要。

### A.6 实例根 env 解析（日志/服务路径要用）——**三套 env，无中央常量**

- **运行时权威**：`OCTOAGENT_PROJECT_ROOT` env → cwd（config_commands.py:60-72 `_resolve_project_root` CLI 侧；main.py:121-123 gateway 侧；backup_service.py:60-65）。doctor/CLI 用这个。
- **安装/脚本层**：`OCTOAGENT_INSTANCE_ROOT`（默认 `~/.octoagent`）→ run-octo-home.sh:6-8 读它并**导出为 `OCTOAGENT_PROJECT_ROOT`**；install_bootstrap 落进 descriptor.environment_overrides。
- **权限层**：`OCTOAGENT_HOME` env → `~/.octoagent`（permission.py:244，仅 workspace 权限用）。
- **data 目录**：`OCTOAGENT_DATA_DIR` → 相对 `data`（config.py:10-12）。
- **无单一路径常量模块**：最接近 `core/config.py`（get_db_path 等）；ops 路径集中在 `UpdateStatusStore.__init__`（update_status_store.py:42-47）；data/db resolve 在 backup_service.py:77-100。
- **★ `~/.octoagent/logs/` 现状**：唯一约定是 `logs/e2e/`（e2e_command.py:87-90 `_logs_dir` **硬编码**，无中央常量）；**install_bootstrap 骨架未预建 logs 目录**（install_bootstrap.py:181-189 只 mkdir data/sqlite/artifacts/ops/lancedb + bin）→ **F129 落 `~/.octoagent/logs/` 需自己 ensure 目录**。

### A.7 greenfield 确认

- 全仓 grep `launchd|systemd|LaunchAgent|.plist|launchctl|systemctl|caffeinate|pmset`：**0 真实实现命中**（仅无关 docstring：worker_runtime Docker daemon 检测 / plugin_watcher thread.daemon）。→ **OS 守护 + 防睡眠 + 日志轮转是纯净新增，无历史包袱可复用**（但 descriptor/RuntimeManagementMode 雏形可扩）。
- doctor `--live` **当前不做真实 LLM 调用**（F081 删了 check_live_ping，只剩 telegram readiness；CLI help "发送真实 LLM 调用" cli.py:344 已过时）。

---

## B. Hermes 参考（service_manager + hermes_logging）

> 源码：`_references/opensource/hermes-agent/`。**"五态抽象"是误称**——是 `ServiceManagerKind` 5 个**后端类型**（systemd/launchd/windows/s6/none，`hermes_cli/service_manager.py:23`），**不是运行时状态机**（全仓无 5 成员 service-state 枚举，运行态是散落字符串 starting/startup_failed/stopped）。别按状态机 spec。真正的模板/install 逻辑在 `hermes_cli/gateway.py`（service_manager.py 只是薄 Protocol 门面，OctoAgent 单实例不需要这层）。

### B.1 ★ 必抄（高价值 + 契合 Octo）

1. **stable-working-dir（最高优先级防坑）**（gateway.py:2360-2385）：service `WorkingDirectory` 必须钉在**永不消失**的路径（`~/.hermes`），**绝不能**指向源码 checkout / worktree。否则 checkout 一删/移，systemd 在 **CHDIR 步骤就失败**（`status=200/CHDIR`，Python 加载前）→ 配合 `Restart=always` 变成**死目录永久崩溃循环**，自愈逻辑永远跑不到。实现：`ExecStart` 用绝对 python + `-m module`（不依赖 cwd），cwd 钉 HOME。
   → **对 Octo 是最高危项**：Octo 大量用 git worktree（本会话就在 `.claude/worktrees/`）+ `.venv` 是 symlink 指向主仓。plist/unit 若把 WorkingDirectory 或 python 钉在 worktree/symlink venv，worktree 删了服务永久崩溃循环。**F129 spec 显式约束：WorkingDirectory=`~/.octoagent`，ExecStart `python -m octoagent...`（cwd 无关），python 解释器解析到稳定安装位而非 worktree symlink venv。**

2. **install 三态幂等**（gateway.py:2816-2828 systemd / 3534-3573 launchd）：①已装且内容一致 → skip（提示 --force）；②已装但**过时**（归一化文本比对当前应生成模板 vs 已装）→ **自动重写自愈**（无需 --force，daemon-reload/re-bootstrap）；③不存在 → 装。归一化要剔 PATH 等易变字段（否则 shell PATH 一变就误判过时反复重装）——或更简单：plist 里**不塞** volatile PATH，从源头避免。→ 服务「可恢复」，升级后 unit 里 python 路径变了能自愈。

3. **uninstall 尽力清理**（gateway.py:2868-2884 / 3576-3589）：systemd stop(check=False)→disable→unlink→daemon-reload；launchd `launchctl bootout`(check=False)→unlink plist。全程 `check=False`，服务不在也不报错 → 幂等。

4. **崩溃 traceback / stdout / stderr 落盘是 SERVICE 层做的，不是 logging 模块**（关键架构认知）：launchd plist `StandardOutPath`/`StandardErrorPath` → `~/.hermes/logs/gateway.log`/`gateway.error.log`（gateway.py:3431-3435）；systemd `StandardOutput=journal`。Python logging 只抓走 logger 的记录；裸 stdout/print/**未捕获异常 traceback**/启动期崩溃靠 init 系统级 fd 重定向。→ **这正是 F129 核心**：前台跑正常的 uvicorn 做成 daemon 后，启动期 traceback（配置错/端口占用）必须靠 plist StandardErrorPath 落盘才看得到。

5. **`TimeoutStopSec` > 优雅关闭窗口**（gateway.py:2409-2416，`max(60, drain_timeout)+30`）：否则 systemd 在 agent 优雅退出前 SIGKILL 整个 cgroup，误杀工具子进程。→ Octo 有 drain/watchdog，同样怕 uvicorn 优雅关闭被截断。

6. **launchd PATH 补全**（gateway.py:3355-3374）：launchd 默认 PATH 极简（`/usr/bin:/bin:...`）缺 Homebrew/uv → 需把完整 PATH 拼进 plist EnvironmentVariables。Octo uvicorn 需要 uv 在 PATH。

7. **平台探测 + 兜底降级**（service_manager.py:86-126 `detect_service_manager`）：Mac→launchd / Linux→systemd / 都不行→前台兜底。契合 Constitution #6。Octo 写成 `detect_init_system() -> Literal["launchd","systemd","none"]` 即可。

### B.2 ★ 日志脱敏（F129 最该抄的东西，`agent/redact.py`）

- **脱敏在 formatter 层**（`RedactingFormatter`，redact.py:489-497）：`format()` 先 super 再 `redact_sensitive_text()`（redact.py:327-428），所有 handler 一处覆盖。
- **正则覆盖面广**：37 个厂商 key 前缀正则（`_PREFIX_PATTERNS` redact.py:70-108：`sk-`/`ghp_`/`github_pat_`/`xox[baprs]-`/`AIza`/`pplx-`/`AKIA`/`sk_live_`/`xai-` 等）+ ENV 赋值（KEY 含 API_KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH，redact.py:110-114）+ JSON 字段 + `Authorization: Bearer` + Telegram bot token + 私钥块 + DB 连接串密码 + JWT `eyJ...` + E.164 电话。
- **掩码**：短(<18)全 `***`，长留头6尾4（`_mask_token` redact.py:244-249）。
- **性能**：每正则前廉价 substring 预检（`"eyJ" in text` 才跑 JWT 正则），无密钥行 5.6μs→1.8μs。
- **安全默认**：`_REDACT_ENABLED` import 时快照 env（redact.py:67）→ **防 LLM 中途 `export ..._REDACT=false` 关掉脱敏**；默认 ON。契合 Octo「单次授权/禁令优于指令」哲学。
- **刻意不脱敏 URL query/userinfo**（redact.py:406-413）：magic-link/OAuth callback 靠 query 传 opaque token，盲脱会打断 skill；URL 里已知密钥形状仍被前缀/JWT 正则抓。
- → **对 Octo**：常驻服务 stdout/traceback 落盘极易把 provider key（SiliconFlow/codex/DeepSeek）、Telegram token 写进磁盘——这是 Constitution #5 的**出站延伸**。ThreatScanner(F084)/F124 是**入站**扫描（防 injection），**不覆盖"secret 写进日志文件"这个出站面**。F129 需独立落盘脱敏层。用 structlog processor 包 `redact_sensitive_text()` 纯函数（不必照搬 Formatter 类）挂链末端。前缀列表按 Octo provider 裁剪（确认 SiliconFlow key 是否 `sk-` 开头，不是则补一条）。

### B.3 轮转（`hermes_logging.py`）

- stdlib `RotatingFileHandler` **按大小**轮转：`agent.log` 5MiB×3 / `errors.log`（WARNING+，快速 triage）2MiB×2 / 可 config 覆盖（`logging.max_size_mb`/`backup_count`）。
- 单一入口 `setup_logging()` 幂等（`_logging_initialized` sentinel）。
- `agent.log`（全量）+ `errors.log`（WARNING+ triage）双文件模式实用。

### B.4 崩溃自愈取向（Hermes 选"无限重启"，与 F129 可商榷分歧）

- launchd：`KeepAlive=true` **无退避配置**，靠 launchd 内置 10s throttle（gateway.py:3428）。
- systemd：`Restart=always`+`RestartSec=5` 固定间隔 + **`StartLimitIntervalSec=0` 关掉 start-limit**（gateway.py:2440,2454-2455）= 允许无限快速重启。指数退避指令（RestartMaxDelaySec/RestartSteps）**未启用**（仅 staleness 比对时被剔）。
- Hermes 防"死目录崩溃循环"靠的是 **stable-working-dir（B.1.1）+ start 前 refresh_unit_if_needed 自愈**，不是退避。
- → **F129 分歧点**：Hermes 无退避对个人 always-on OS 合理（崩了就该一直拉起）；但意味着**确定性启动失败（配置错）会无限刷日志崩溃循环**。**建议 F129 保留宽松 start-limit（如 systemd 60-120s 内超 5 次进 failed 态 + launchd ThrottleInterval 调高）**——防配置错刷爆盘 + status 能看到"failed N 次"引导排查。详见 §GATE-6。

### B.5 必避（Hermes 特有，与 Octo 单用户哲学冲突）

- ServiceManager Protocol facade + `ServiceManagerKind` 5 后端抽象 → Octo 只需 launchd+systemd+nohup 兜底。
- s6 容器 backend + per-profile 动态注册（service_manager.py:601-1081 大半篇幅）——多 profile 容器部署，冲突单实例。
- Windows Scheduled Task（gateway_windows.py，`schtasks /SC ONLOGON` + startup .cmd dropper 降级）——非目标平台。
- `--replace` 抢占式 PID 接管（gateway.py:690-701）——单实例用 PID 锁 + 守卫足够。
- 组件多文件（gateway.log/gui.log）+ `_ManagedRotatingFileHandler` 外部轮转检测 + NixOS chmod ——对应多进程/NixOS/logrotate，Octo 单 uvicorn 进程不需要，用 stdlib RotatingFileHandler 原样。
- 把业务日志搬去文件——Octo 有 Event Store（Constitution #2）+ structlog，F129 只补**进程级 stdout/traceback 兜底 + errors triage 文件**，不重造业务可观测性。

---

## C. OpenClaw 参考（daemon + 日志）

> 源码：`_references/opensource/openclaw/`（TypeScript，重架构思路非 API）。**结论：OpenClaw 有成熟守护+日志（非薄），但守护自愈/自启全靠 OS（launchd KeepAlive / systemd Restart），自己不写 supervisor 循环**——`src/infra/process-respawn.ts` 只是"无 OS supervisor 时兜底自 spawn"，检测到 supervisor 就退让（`RespawnMode="supervised"`）。别误解成"自研 supervisor"。与 Hermes 同构，交叉印证方向正确。

### C.1 ★ 三态状态模型（比 Hermes 干净，强烈可借鉴 `octo service status`）

- `GatewayServiceState = { installed, loaded, running }` 三独立布尔（service-types.ts:59-66）：
  - `installed` = 服务定义文件存在且可解析（plist/unit）。
  - `loaded` = OS supervisor 已注册（launchd loaded / systemd enabled）。
  - `running` = 进程真在跑。
- **并行探三态**（service.ts:184-206），每个探测 `.catch(fallback)` **失败软化** + 传 `timeoutMs`（防 wedged 的 systemctl 把状态查询挂死）。→ F129 status 直接采纳这三态 + 超时软化。
- 状态收集还含：PID / bindMode / bindHost / port + **端口来源** / **端口占用探测**（真看谁在监听）/ config 漂移 / **从日志捞最后一条错误 `readLastGatewayErrorLine`**（status↔logging 联动 UX）/ restart handoff / 版本。

### C.2 ★ drift → 分级建议（recommended/aggressive），报警不自动改 —— 直接印证 GATE-2 推荐

- `auditGatewayServiceConfig`（service-audit.ts:620-652）产 `ServiceConfigIssue[]`，每条 `level: "recommended" | "aggressive"`。检测：launchd 缺 RunAtLoad/KeepAlive / systemd 缺 network-online.target / **服务里内嵌 token 应改运行时加载** / 端口与 config 不符 / 指向旧版本/临时路径。
- **默认不自动改服务文件**，给文本 hint（"Run `openclaw gateway install --force` to sync"）→ **完全契合 Constitution #4 Two-Phase + #7 User-in-Control**。
- start 路径 repair gate：服务指向临时/缺失路径 → 返回 `outcome:"repair-required"` **拒绝假装启动成功**，提示 `install --force`。
- 唯一"半自动"：非 --force 重装时检测到内嵌 token 漂移/路径变，**自动刷新安装**并打印原因（仅"重写服务定义"，不涉不可逆）。

### C.3 崩溃自愈配置（OpenClaw **用了** start-limit，与 Hermes 分歧——F129 采 OpenClaw 更安全）

- launchd（launchd-plist.ts:294）：`RunAtLoad=true` + `KeepAlive=true` + **`ThrottleInterval=10`**（崩溃回退避免每秒疯狂重启）+ `ExitTimeOut=20`。
- systemd（systemd-unit.ts:68-98）：`Restart=always` + `RestartSec=5` + **`StartLimitBurst=5` + `StartLimitIntervalSec=60`**（崩溃风暴限流）+ **`RestartPreventExitStatus=78`**（配置错误码不重启，避免坏配置无限重启）+ `OOMPolicy=continue` + `KillMode=control-group`（重启不留孤儿 worker）。
- → **GATE-6 定音**：Hermes 关 start-limit（无限重启）vs OpenClaw 用 start-limit + 配置错误退出码不重启。**F129 采 OpenClaw**：`ThrottleInterval`(launchd) + `StartLimitBurst/IntervalSec`(systemd) + 用专用退出码标记"确定性配置错误 → 不重启进 failed 态"，防坏配置刷爆盘 + status 可见"failed"引导排查。

### C.4 supervisor env-marker 自证 + 重启前置校验

- 进程读 env marker 自证"我被托管"：systemd 注入 `INVOCATION_ID/JOURNAL_STREAM`，launchd 用自设 `OPENCLAW_LAUNCHD_LABEL`（supervisor-markers.ts:4-84）→ 决定重启走 self-exec 还是让 supervisor 拉。F129 可设 `OCTOAGENT_SUPERVISED=launchd|systemd`。
- **重启前置配置校验**（lifecycle-core.ts:273-286）：重启前先校验 config 合法（坏配置不启动，防 crash loop）；restart intent 落 SQLite（60s TTL handoff）→ 新进程重启后能解释原因。→ Octo 已 SQLite WAL，可对标。
- **`stop --disable`**（register-service-commands.ts:141-148）：一个独立态——"持久抑制 KeepAlive/RunAtLoad 让服务下次 start 前不再自愈重启"，区别于"临时 stop 后 KeepAlive 又拉起"。→ **直接回答 GATE-4"为何 stop 了又活"**：F129 的 `octo stop` 在 service 模式要么提示"服务会重启，用 uninstall 彻底停"，要么提供 `--disable` 语义。

### C.5 日志（JSONL + 双维度轮转 + 强制脱敏，印证 Hermes）

- 落 `/tmp/openclaw/openclaw-YYYY-MM-DD.log`（Octo 应落 `~/.octoagent/logs/`），**JSONL**（顶层机器可过滤字段 hostname/agent_id/session_id/channel + 保留结构化）。
- **双维度轮转**：按大小（默认 100MB → `.1..5.log` 保 5 个）+ 按日期（prune 24h 以上）；**轮转失败也继续写不丢日志**；每 transport try/catch 吞异常 → **日志故障永不阻塞主流程**（Constitution #6）。
- 服务 stdout/stderr 另落 `~/Library/Logs/openclaw/gateway.log`（launchd）/ `<stateDir>/logs/`（systemd）供 supervisor handoff 诊断——再次印证 Hermes B.1.4（stdout 落盘是 service 层）。
- **脱敏写盘前强制**（logger.ts:614 每 transport 边界）+ **双路径**：①`redactSecrets` 结构化递归（按敏感 key 名遮值，含循环引用保护 + 路径感知如 error.code 不遮）；②`redactSensitiveText` 文本正则（**~80+ 条**厂商前缀 + ENV/URL/JSON/Bearer/连接串密码/PEM/表单 body 含不可见 Unicode 对抗）。**prefilter 快路**（合并正则粗筛命中才跑全量）+ bounded 分块 + `***` 幂等不重复遮。
- **可关但安全面永不关**：`logging.redactSensitive:"off"` 只关通用日志脱敏；工具事件/session 历史/诊断导出/审批提示**强制脱敏关不掉**。用户 `redactPatterns` 只增不减。

### C.6 对 Octo 适配判断（去重 Hermes 后新增）

- ✅ 三态 status（installed/loaded/running）+ 并行探测超时软化 —— 采纳。
- ✅ drift 分级建议报警不自动改 —— 采纳（GATE-2 印证）。
- ✅ start repair gate（指向坏路径拒绝假成功 repair-required）—— 采纳，防"install 了但其实起不来"。
- ✅ `stop --disable` 持久抑制态 —— 采纳（GATE-4）。
- ✅ 重启前置 config 校验 + restart-intent 落 SQLite —— 采纳。
- ✅ 日志故障不阻塞主流程（每 handler try/catch）—— 采纳（Constitution #6）。
- ⚠️ 80+ 厂商正则全量维护成本高 —— Octo 先取子集（sk-/Anthropic/DeepSeek/SiliconFlow + 通用 `*_KEY/*_TOKEN/*_SECRET` + Bearer + 连接串密码）。
- ⚠️ Windows schtasks / tslog / commander / 服务内嵌 token auto-refresh —— 不适配砍掉（Octo 不把 secret 写进服务定义，用 env-file/.env 引用从源头免此补救）。

### C.7 两参考交叉印证的一致结论

| 维度 | Hermes | OpenClaw | F129 采纳 |
|------|--------|----------|-----------|
| 守护方式 | OS 原生（无自研 loop）| OS 原生（无自研 loop）| **OS 原生 launchd/systemd** |
| WorkingDirectory | 钉 HOME 稳定目录（防 CHDIR 死循环）| （path drift 检测）| **钉 `~/.octoagent`** |
| install 幂等 | 三态（一致/过时自愈/缺失）| 三态 + repair gate | **三态幂等 + repair gate** |
| status 模型 | installed+running 双维 | installed/loaded/running 三态并行 | **三态并行超时软化** |
| 崩溃退避 | 无 start-limit（无限重启）| start-limit + 配置错退出码不重启 | **OpenClaw（更安全）** |
| stdout/traceback 落盘 | plist StandardOutPath / journal | 同 | **service 层 fd 重定向** |
| 脱敏 | formatter 层 37 前缀 + import 快照防关 | 写盘前双路径 80+ + 安全面永不关 | **structlog processor + 子集前缀 + 默认 ON 防运行时关** |
| drift 处理 | 自愈重写 | 分级建议报警不自动改 | **报警建议为主（#4/#7），仅服务定义重写可 --force 自愈** |

---

## D. 现状 vs F129 范围结论（别重造）

| F129 想做 | 现状 | 结论 |
|-----------|------|------|
| OS 级进程守护（崩溃自愈+开机自启）| 无（裸 Popen detached）| **真缺口，F129 新建**——但复用 `run-octo-home.sh` 作 ExecStart + 新增 RestartStrategy 变体 |
| `managed-runtime.json` 托管模型 | **已有** ManagedRuntimeDescriptor + RuntimeManagementMode | 复用/扩展，不推翻 |
| 日志落盘（rotating）| 无 FileHandler | 真缺口，往 setup_logging() 加 |
| 日志脱敏 | 无 redaction | 真缺口，落盘前必补（Constitution #5）|
| `octo logs` 查看 | 无 | 新建 |
| doctor 服务/睡眠检查 | doctor 框架已在（11 check，硬编码列表）| 加 check_* 方法，`fix_hint` 承载建议 |
| `/ready` 健康端点 | **已有** | 复用做 status |
| app 级 install（依赖/descriptor）| **已有** install-octo-home.sh | 与 `octo service install`（OS 守护）语义区分 |
