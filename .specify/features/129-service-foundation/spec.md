# Feature Specification: F129 常驻服务地基（进程守护 + 日志落盘）

**Feature ID**: F129
**Slug**: service-foundation
**Milestone**: M8（部署与日常使用）— P0，服务基础
**规模**: **M**（复核见 §8）
**Status**: **设计先行草案 v0.1**（研究闭环，spec/plan 待用户拍板 §7 设计岔路后进入实施）
**Base**: master 30ea77ce / 分支 `feature/129-service-foundation`
**上游依据**: `CLAUDE.local.md` §M8 战略规划 + `docs/blueprint/milestones.md` M8 节 + 本目录 `research.md`（Hermes/OpenClaw + Octo 现状核实，均带 file:line 证据）

---

## 0. 设计基础说明（实测核实，均带证据，详见 research.md）

### 0.1 ★ 核心定位：F129 是「把前台进程做成 OS 托管常驻服务 + 补进程级日志落盘」，不是造 supervisor

- 让「关终端 / 崩溃 / 重启开机」后 Octo gateway **自动被 OS 拉起**（launchd/systemd），且**运行日志 + 崩溃 traceback 落盘可查**。
- **两参考交叉印证的第一结论**（research.md §C.7）：Hermes + OpenClaw **都不自写 supervisor 循环**，守护自愈/开机自启**全靠 OS 原生**（launchd `KeepAlive` / systemd `Restart`）。F129 同样**不写 Python while-loop 拉 uvicorn**——生成/安装 OS 服务定义即可，OS 更可靠。

### 0.2 实测核实的可复用资产（勿重造，research.md §A）

| 资产 | 位置（证据）| F129 如何用 |
|------|------------|-------------|
| **托管 runtime 模型** `ManagedRuntimeDescriptor` + `RuntimeManagementMode{MANAGED,UNMANAGED}` + `RestartStrategy{COMMAND,SELF_SIGNAL}` | `packages/core/src/octoagent/core/models/update.py:47-96` | **扩展**（新增 `RestartStrategy` 变体，如 `OS_SERVICE`），非推翻 |
| **稳定启动脚本** `run-octo-home.sh`（解析 INSTANCE_ROOT→source .env→`exec uv run uvicorn ... --host 127.0.0.1`）| `octoagent/scripts/run-octo-home.sh` | 作 launchd/systemd 的 **ExecStart 目标**（stable-working-dir 概念天然对齐，见 0.4）|
| **运行状态** `RuntimeStateSnapshot`（pid/heartbeat/verify_url/management_mode）+ store | `update.py:99-106` + `dx/update_status_store.py:42-47`（落 `~/.octoagent/data/ops/`）| status 命令读它判 pid 存活 |
| **健康端点** `GET /health`（liveness）+ `GET /ready?profile=core`（readiness，503 on not-ok）| `routes/health.py:83-199`（不带 front_door 鉴权）| status 命令复用判就绪，**别重造健康探测** |
| **日志初始化入口** `setup_logging()`（structlog，仅 StreamHandler）| `apps/gateway/.../middleware/logging_config.py:16-65`（main.py:343 调）| **加 RotatingFileHandler + 脱敏 processor 的落点** |
| **doctor 框架**（`CheckResult{name,status,level,message,fix_hint}` + `run_all_checks` 硬编码列表）| `dx/doctor.py:66-108` + `dx/models.py` | append 2 个 `check_*` 方法；`fix_hint` 承载禁睡建议 |
| **既有停止/信号先例** `os.kill(pid, sig)` + pid 存活探测 | `dx/update_service.py:556-566` + `dx/update_commands.py:41-99` | status/stop 复用 |

### 0.3 实测核实的真实缺口（F129 新建，research.md §A.1/A.3/A.7）

1. **无 OS 级进程守护**：`octo restart/update` 用 `subprocess.Popen(start_new_session=True, stdout=DEVNULL, stderr=DEVNULL)`（`update_service.py:576-583` / `108-122`）= 裸 detached 进程，崩溃无东西拉起；`restart` 还要求已托管+旧 pid 存活（`_require_descriptor` raise `RESTART_UNAVAILABLE`，`update_service.py:616-624`）。全仓 grep `launchd|systemd|caffeinate|pmset` **0 真实现**（§A.7）。
2. **日志不落盘**：`setup_logging()` 只挂 `logging.StreamHandler()`（`logging_config.py:59-64`）→ 结合 Popen DEVNULL = **stdout + 崩溃 traceback 双重丢失**。全仓无 `RotatingFileHandler/FileHandler`（§A.3）。
3. **日志无脱敏**：structlog processor 链无 redaction（`logging_config.py:27-34`）→ **落盘会把 provider key/Telegram token 写进磁盘**（Constitution #5 出站延伸；ThreatScanner/F124 是入站扫描不覆盖，research.md §B.2）。
4. **无 `octo logs`** / **无 `octo service` 命令组** / **`~/.octoagent/logs/` 未在 install 骨架预建**（唯一约定是 `logs/e2e/`，e2e_command.py 硬编码，§A.6）。
5. **无禁睡感知**：doctor 无睡眠检查（合盖=手机失联，报告称"睡眠是笔记本最隐蔽杀手"）。

### 0.4 ★ 最高优先级防坑：stable-working-dir（research.md §B.1.1，Hermes gateway.py:2360-2385）

- Hermes 惨痛经验：service `WorkingDirectory` 若指向源码 checkout / **git worktree**，checkout 一删/移，systemd 在 **CHDIR 步骤就失败**（`status=200/CHDIR`，Python 加载前）→ 配合 `Restart=always` = **死目录永久崩溃循环**，自愈逻辑永远跑不到。
- **对 Octo 是最高危项**：Octo 大量用 git worktree（本 Feature 就在 `.claude/worktrees/F129-service-foundation`）+ `.venv` 是 symlink 指向主仓。
- **F129 硬约束**（写进 FR-A / plan 验收）：service 定义的 `WorkingDirectory` = `~/.octoagent`（永不消失）；ExecStart 用 `run-octo-home.sh`（内部 `cd PROJECT_ROOT` + `exec uv run uvicorn`，cwd 无关）；**解释器/脚本路径必须解析到稳定安装位（`~/.octoagent/app/...` 或系统 uv），绝不写 worktree symlink venv 路径**。

### 0.5 竞品反向验证结论（剔除误称，research.md §B/§C）

- **Hermes "五态抽象"是误称**：实为 `ServiceManagerKind` 5 个**后端类型**（systemd/launchd/windows/s6/none），**非运行时状态机**（全仓无 5 成员 service-state 枚举）。→ **F129 不按状态机 spec**。状态用 OpenClaw 的 `{installed, loaded, running}` 三布尔（§C.1）。
- **Hermes ServiceManager Protocol facade / s6 容器 / Windows schtasks / `--replace` 抢占接管**：单用户单实例不需要，全避（§B.5）。
- **两参考崩溃退避分歧**：Hermes 关 start-limit（无限重启）vs OpenClaw 用 start-limit + 配置错退出码不重启 → **F129 采 OpenClaw（更安全，防坏配置刷爆盘）**（§C.3）。

### 0.6 三条哲学守界（H1/Constitution）

- **H1**：F129 是运维地基，**不碰 Agent 决策环 / 不改主 Agent user-facing 语义**（主 Agent 仍是唯一 user-facing speaker）。纯 CLI + gateway 启动/日志层。
- **Constitution #4/#7**：drift/禁睡等**检测→报警给建议为主，不静默改用户系统设置**（§C.2 印证）；install/uninstall 是可逆运维动作（非数据不可逆迁移），但仍 dry-run 可预览。
- **Constitution #5**：日志落盘**必须脱敏**（§0.3.3）。
- **Constitution #6**：日志故障 / init 系统探测失败**不得阻塞主流程**（降级）。

---

## 1. 目标（Why）

让 Connor 能把 Octo 部署成**禁睡常驻 Mac（mini）上的 OS 托管服务**——关终端/崩溃/开机后自动运行，运行与崩溃日志可查可脱敏，为本机使用和后续 Cloudflare 远程入口铺好“进程一直在、日志可诊断”的地基。

**用户可感知的改变**：
- `octo service install` 一次 → 之后崩溃自愈 + 开机自启，人在外面不再失联。
- `octo logs` → 随时看 gateway 在干什么 / 为什么崩了（脱敏后安全）。
- `octo doctor` → 明确告诉"你的 Mac 会睡、手机会失联，建议 X"。

---

## 2. 范围声明

### 2.1 In Scope（v0.1，§7 决策收窄）

- `octo service {install, uninstall, status}` 命令组 → launchd LaunchAgent plist（Mac）/ systemd user unit（Linux）。
- 崩溃自愈（KeepAlive/Restart）+ 开机自启（RunAtLoad/WantedBy）+ 退避熔断（防崩溃循环，采 OpenClaw 模型）。
- install 三态幂等（一致 skip / 过时自愈重写 / 缺失装）+ uninstall 尽力清理不残留 + `--dry-run` 预览 + start repair gate。
- `run-octo-home.sh` 作稳定 ExecStart + WorkingDirectory 钉 `~/.octoagent`（§0.4）。
- `RestartStrategy` 扩展 + `UpdateService.restart` 在 service 模式委托 launchctl/systemctl（§7 GATE-4）。
- 日志落盘：`setup_logging()` 加 `RotatingFileHandler`（`~/.octoagent/logs/octoagent.log`）+ 崩溃 traceback 落盘 + service 层 stdout/stderr 重定向。
- 日志脱敏：structlog redaction processor（Octo provider 子集前缀 + 通用 key 名 + Bearer + 连接串密码），默认 ON + import 快照防运行时关。
- `octo logs`（tail / `-f` follow / `-n` / `--level`）。
- doctor 新增 `check_service_status`（服务是否安装/loaded/running）+ `check_sleep_settings`（禁睡感知，WARN + fix_hint）。
- 可选 `octo service install --keep-awake`（用户级 `caffeinate`，opt-in，§7 GATE-2 选项 C）。

### 2.2 Explicitly Deferred（后续 / 下游）

- **Windows 支持**（schtasks + startup dropper）：非目标平台，`detect_init_system()` 留 `none` 兜底扩展缝（§7 GATE-1）。
- **系统级电源设置自动修改**（`pmset`/sudo）：**明确不做**（§7 GATE-2，违 #7）。
- **host↔mode 校验 / front_door 模式切换 / 远程入口**：不属于 F129。service 定义只沿用 `run-octo-home.sh` 现有 `--host 127.0.0.1` 默认，不改绑定语义。
- **service 内嵌 token auto-refresh 半自动重装**（OpenClaw install.ts）：Octo 用 `.env` 引用不把 secret 写进服务定义，从源头免此补救。
- **RPC/WS 深度探活 / 端口占用探测 / config 漂移审计**（OpenClaw status.gather 全维度）：v0.1 status 用 `{installed,loaded,running}` + `/ready` 即可；深度诊断维度按独立 Feature 增补。
- **restart-intent 落 SQLite handoff**：v0.1 不必（Octo restart 语义简单）；若后续 `octo update` 触发自重启需解释原因再引入。
- **业务日志搬文件**：Octo 有 Event Store（#2）+ structlog，F129 只补**进程级 stdout/traceback 兜底 + errors triage**，不重造业务可观测性（research.md §B.5）。

### 2.3 Out of Scope（明确退出）

- 自写 supervisor 守护循环（§0.1）。
- 容器化 / 多用户 / 多实例（Blueprint §0 锁单用户）。
- Hermes ServiceManager Protocol facade / s6 / `--replace` 抢占接管（§0.5）。

---

## 3. 关键设计决策（DP）

### DP-1 OS 原生守护，platform-strategy 抽象（→ GATE-1）
- 一个 `ServiceBackend` 策略抽象，两个实现 `LaunchdBackend`（Mac）/ `SystemdUserBackend`（Linux），`detect_init_system() -> Literal["launchd","systemd","none"]`（darwin→launchd / linux→systemd / 其他→none 兜底，Constitution #6 降级）。不引 Protocol facade 那层（单实例不需要）。plist/unit 用 f-string 拼（不引 jinja2，随 Hermes/OpenClaw 做法）。

### DP-2 service 定义钉稳定路径（→ §0.4 硬约束）
- `WorkingDirectory=~/.octoagent`；`ExecStart` = `run-octo-home.sh` 绝对路径（稳定安装位，非 worktree）；EnvironmentVariables 补全 PATH（含 uv，launchd 默认 PATH 极简，research.md §B.1.6）。**归一化比对时剔 PATH 易变字段**（或不塞 volatile PATH），防误判过时反复重装（§B.1.2）。

### DP-3 崩溃自愈 + 退避熔断（采 OpenClaw，→ GATE-6）
- launchd：`RunAtLoad=true` + `KeepAlive`（`SuccessfulExit=false` 只异常退出重启，正常 stop 不重启）+ `ThrottleInterval=10` + `ExitTimeOut≥drain`。
- systemd：`Restart=on-failure` + `RestartSec=5` + `StartLimitBurst=5`/`StartLimitIntervalSec=60`（崩溃风暴熔断进 failed 态）+ **专用退出码不重启**（确定性配置错，对标 OpenClaw `RestartPreventExitStatus=78`）+ `TimeoutStopSec>drain`（防优雅关闭被 SIGKILL，§B.1.5）+ `KillMode` 不留孤儿。

### DP-4 install/uninstall 幂等 + repair gate（采 Hermes 三态 + OpenClaw gate，→ GATE-3）
- install 三态：①内容一致→skip（提示 --force）；②过时（归一化比对）→自动重写自愈；③缺失→装。`--dry-run` 打印将写/删的文件。
- uninstall：unload/bootout（check=False 尽力）+ 删服务文件 + `RestartStrategy` 复位 COMMAND + 清 service state；缺失不报错。
- repair gate（start/status）：服务指向坏/缺失路径 → 报 `repair-required` 拒绝假成功，提示 `install --force`。

### DP-5 status 三态并行探测（采 OpenClaw §C.1，→ GATE-4）
- `{installed, loaded, running}` 三独立布尔，并行探（每个 catch 软化 + timeout），加 pid + `/ready` 就绪 + 最后一条 error 日志行（status↔logging 联动）。service 模式下 `octo stop` 提示"服务会重启，`uninstall` 才彻底停"（对标 OpenClaw `stop --disable` 语义，§C.4）。

### DP-6 日志双层落盘 + 脱敏（采 Hermes+OpenClaw，→ GATE-5）
- **层 1（进程内）**：`setup_logging()` root logger 加 `RotatingFileHandler`（`~/.octoagent/logs/octoagent.log`，size 轮转 10MB×5 可 env 配）→ 抓所有 structlog/logging（**不受 Popen DEVNULL 影响**）。可选 errors-only 第二文件（triage）。
- **层 2（service 层）**：launchd `StandardOutPath/StandardErrorPath` / systemd `StandardOutput=append:` 指向 logs/ → 抓裸 stdout/print/**未捕获 traceback**/启动期崩溃（research.md §B.1.4：这才是 daemon 后不丢启动 traceback 的关键）。
- **崩溃兜底**：`faulthandler` + `sys.excepthook` 落盘。
- **脱敏**：structlog processor 包 `redact_sensitive_text()` 纯函数挂链末端（renderer 前）；子集前缀（`sk-`/Anthropic/DeepSeek/SiliconFlow + 通用 `*_KEY|*_TOKEN|*_SECRET|*_PASSWORD` + `Authorization: Bearer` + 连接串密码 + JWT `eyJ`）；短 token 全 `***` 长留头6尾4；**`_REDACT_ENABLED` import 时快照 env 防运行时 `export ..._REDACT=false` 关掉**（§B.2 安全默认）；日志文件权限 0600。故障不阻塞（handler try/catch，#6）。

### DP-7 禁睡：只检测 + 建议（默认），可选用户级 keep-awake（→ ★ GATE-2）
- **默认**：doctor `check_sleep_settings` 读 `pmset -g`（Mac）+ 检测是否笔记本（有电池）→ WARN + `fix_hint`（系统设置指引 + 诚实告知**合盖睡眠软件挡不住**）。**绝不 `pmset` 改系统设置**（需 sudo，违 #7 + 单次授权，§7 GATE-2）。
- **可选增强**：`octo service install --keep-awake` → service 生命周期内伴随 `caffeinate`（用户级，零 sudo，卸载即止），opt-in（耗电 + Mac mini 台式无需）。

---

## 4. 功能需求（FR）

> `[@test]` 标注关键 AC/FR 的目标测试文件路径（AC↔test 显式绑定，verify 阶段机械校验存在且 PASS，遵 SDD 工作流强化）。测试路径遵现有惯例：CLI 命令 → `octoagent/packages/provider/tests/dx/`；service/update 逻辑 → `octoagent/packages/provider/tests/`；日志 → gateway middleware 测试域。

### FR-A 进程守护安装（ServiceBackend + 稳定路径）

- **FR-A1**：`octo service install` 按 `detect_init_system()` 生成并安装 launchd LaunchAgent plist（Mac，`~/Library/LaunchAgents/<label>.plist`）或 systemd user unit（Linux，`~/.config/systemd/user/<name>.service`）；`none` 平台优雅报错（提示不支持，非 crash，Constitution #6）。
- **FR-A2（★ §0.4 硬约束）**：生成的服务定义 `WorkingDirectory`/工作目录 = `~/.octoagent`；ExecStart = 稳定 `run-octo-home.sh` 绝对路径（**不含任何 worktree / symlink venv 路径**）；EnvironmentVariables 补全含 uv 的 PATH。
- **FR-A3**：崩溃自愈 + 开机自启 + 退避熔断按 DP-3（launchd KeepAlive+ThrottleInterval / systemd Restart+StartLimit+专用退出码不重启 + TimeoutStopSec>drain）。
- **FR-A4**：安装后把 `RestartStrategy` 切到 `OS_SERVICE`（新枚举值）+ 更新 `ManagedRuntimeDescriptor`/state，标记"已被 OS 托管"。
- **FR-A5**：install 后自动 load + kickstart（launchctl bootstrap+enable / systemctl --user enable+start），并跑一次 repair-gate 校验真起来了（`/ready` 或 pid），起不来报 `repair-required`。

  - `[@test]` → `octoagent/packages/provider/tests/test_service_manager.py`（backend 探测 / plist+unit 内容含稳定路径不含 worktree / KeepAlive+StartLimit 字段 / RestartStrategy 切换）；`octoagent/packages/provider/tests/dx/test_service_commands.py`（install CLI 端到端 stub 化）

### FR-B install/uninstall 幂等 + 不残留（DP-4）

- **FR-B1**：重复 `octo service install` 三态幂等——内容一致 → skip 提示；过时（归一化比对当前应生成 vs 已装，剔 PATH 易变字段）→ 自动重写 + reload 自愈（无需 --force）；缺失 → 装。
- **FR-B2**：`--dry-run` 打印将写/删的文件路径与内容 diff，不落地。
- **FR-B3**：`octo service uninstall` unload/bootout（忽略 not-loaded 错误）+ 删服务文件 + `RestartStrategy` 复位 `COMMAND` + 清 service 相关 state；**残留清单显式枚举验证**（plist/unit 文件 + descriptor 策略位）；文件缺失返回成功（幂等）。
- **FR-B4**：`--force` 强制重装（覆盖，即使内容一致）。

  - `[@test]` → `octoagent/packages/provider/tests/test_service_manager.py`（三态幂等：一致 skip / 过时自愈 / 缺失装 / 归一化剔 PATH 不误判 / uninstall 后残留清单为空 / dry-run 不落地）

### FR-C status 三态 + 与现有 restart/stop 关系（DP-5，→ GATE-4）

- **FR-C1**：`octo service status` 输出 `{installed, loaded, running}` 三态（并行探测，每个 catch 软化 + timeout，防 wedged systemctl 挂死）+ pid + `/ready` 就绪 + 最近一条 error 日志行（面向普通用户友好，技术细节 --verbose）。
- **FR-C2**：service 模式下（`RestartStrategy==OS_SERVICE`）`octo restart` 委托 `launchctl kickstart -k` / `systemctl --user restart`（**不再自己 Popen**）——彻底解决"restart 要求进程已存活"（OS 会拉起）。
- **FR-C3**：service 模式下 `octo stop` 停当前实例并**提示**"服务已安装，重启/开机会再起；彻底停用请 `octo service uninstall`"（防用户困惑，对标 OpenClaw `stop --disable` 语义）。
- **FR-C4（向后兼容）**：未 install service 的用户（`RestartStrategy==COMMAND`）`octo restart/stop` 行为**完全不变**（仍走 UpdateService Popen 路径）。

  - `[@test]` → `octoagent/packages/provider/tests/test_service_manager.py`（三态探测 + 超时软化）；`octoagent/packages/provider/tests/test_update_service.py`（OS_SERVICE 策略 restart 委托 launchctl/systemctl / COMMAND 策略行为不变回归）

### FR-D 日志落盘 + 崩溃 traceback（DP-6 层 1 + 崩溃兜底）

- **FR-D1**：`setup_logging()` 给 root logger 加 `RotatingFileHandler` → `~/.octoagent/logs/octoagent.log`（size 轮转，默认 10MB × 5 backup，env `OCTOAGENT_LOG_MAX_BYTES`/`OCTOAGENT_LOG_BACKUP_COUNT` 可覆盖）；`~/.octoagent/logs/` 不存在则 ensure 创建（install 骨架未预建，§A.6）。**保留现有 StreamHandler**（stdout 仍要，前台开发用）。
- **FR-D2**：即使进程被 Popen DEVNULL 起（现有 restart 路径），FileHandler 仍写盘（进程内 handler 不受 stdout 重定向影响）——**这是"日志不再随终端消失"的核心机制**。
- **FR-D3**：未捕获异常 + 崩溃 traceback 落盘（`sys.excepthook` 写日志文件 + `faulthandler.enable(file=...)`）。
- **FR-D4**：service 层（FR-A plist/unit）`StandardOutPath/StandardErrorPath` 指向 logs/ → 抓裸 stdout/启动期 import 崩溃 traceback（DP-6 层 2）。
- **FR-D5**：日志故障（磁盘满/权限）**不阻塞主流程**（handler 异常 try/catch 吞掉，Constitution #6）；日志文件权限 0600。

  - `[@test]` → `octoagent/apps/gateway/tests/test_logging_file_sink.py`（FileHandler 挂载 + 轮转触发 + logs 目录 ensure / StreamHandler 仍在 / excepthook traceback 落盘 / 磁盘故障不抛）

### FR-E 日志脱敏（DP-6 脱敏，Constitution #5 出站延伸）

- **FR-E1**：新增 structlog redaction processor（包 `redact_sensitive_text()` 纯函数），挂 `setup_logging()` processor 链**渲染器前**，对**所有** handler（含 FileHandler）生效。
- **FR-E2**：脱敏覆盖（Octo provider 子集，非全 80 条）：厂商前缀 `sk-`/Anthropic/DeepSeek/SiliconFlow（确认其 key 格式，非 `sk-` 则补）+ 通用 ENV/JSON 字段名含 `API_KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH` + `Authorization: Bearer` + Telegram bot token + 连接串密码 + JWT `eyJ`；短 token（<18）全 `***`，长留头6尾4。
- **FR-E3（安全默认，§B.2）**：脱敏默认 ON；开关状态在 **import 时快照 env**（如 `OCTOAGENT_LOG_REDACT`，默认 true）→ 防 LLM/运行时中途 `export` 关掉（契合「单次授权 / 禁令优于指令」）。
- **FR-E4**：脱敏纯函数带廉价 substring 预检（无密钥行不跑全量正则，性能，§B.2）；`***` 幂等不重复遮。
- **FR-E5（诚实边界）**：正则脱敏非万能（自定义格式 secret 可能漏）→ 文档注明"日志文件仍属敏感、勿外发"，且文件 0600。

  - `[@test]` → `octoagent/apps/gateway/tests/test_log_redaction.py`（各类 secret 前缀/字段被遮 / 长短掩码策略 / import 快照后运行时改 env 不生效 / 非密钥文本不误遮 / 幂等）

### FR-F `octo logs` 查看（DP-6）

- **FR-F1**：`octo logs`（默认 tail 最近 N 行，如 200）；`octo logs -n <N>`；`octo logs -f`（follow 实时）；`octo logs --level error`（可选级别过滤）。读 `~/.octoagent/logs/octoagent.log`（+ 轮转文件按序）。
- **FR-F2**：日志文件不存在（服务未跑过）→ 友好提示"暂无日志，`octo service install` 后运行会生成"，非报错。
- **FR-F3（面向普通用户）**：默认输出干净可读；路径等技术细节 `--verbose` 才显（Web UI/UX 规范：debug 术语放 advanced）。

  - `[@test]` → `octoagent/packages/provider/tests/dx/test_service_commands.py`（logs tail / -n / --level 过滤 / 无文件友好提示）

### FR-G doctor 扩展：服务 + 禁睡检查（DP-7，→ GATE-2）

- **FR-G1**：doctor 新增 `check_service_status`（`run_all_checks` append，doctor.py:95 后）——报告服务 installed/loaded/running；未安装 → RECOMMENDED + fix_hint `octo service install`（非 blocking，未部署用户不该 FAIL）。
- **FR-G2**：doctor 新增 `check_sleep_settings`——Mac 读 `pmset -g`（sleep/disablesleep）+ 检测是否笔记本（有电池）→ 若会睡 WARN + `fix_hint`：①系统设置→电池→接通电源防自动睡眠；②合盖需外接电源+显示器或用 Mac mini；③或 `octo service install --keep-awake`。**诚实告知合盖睡眠软件挡不住**。Linux 检测 systemd suspend 目标类似。**不改任何系统设置**。
- **FR-G3**：`check_sleep_settings` 非 Mac/Linux 或读不到 pmset → SKIP（降级，不报错）。

  - `[@test]` → `octoagent/packages/provider/tests/dx/test_doctor_service_checks.py`（service check 三态映射 CheckStatus / 未安装 RECOMMENDED 非 blocking / sleep check WARN+fix_hint / pmset 读不到 SKIP / 不触发任何系统修改）

### FR-H 可选 keep-awake（DP-7 增强，opt-in）

- **FR-H1**：`octo service install --keep-awake`（默认关）→ service 生命周期内伴随用户级 `caffeinate`（Mac）保持唤醒（零 sudo，卸载即止）；Linux 对应 `systemd-inhibit` 或跳过（SKIP + 提示）。
- **FR-H2**：不加 `--keep-awake` 时零副作用（仅 doctor 建议）。

  - `[@test]` → `octoagent/packages/provider/tests/test_service_manager.py`（--keep-awake 生成的定义含 caffeinate 伴随 / 不加时无 / uninstall 后 caffeinate 不残留）

---

## 5. 数据 / 接口设计

### 5.1 模型扩展（复用现有，非新表）

- `RestartStrategy`（`packages/core/src/octoagent/core/models/update.py:52-54`）新增值 `OS_SERVICE = "os_service"`（launchd/systemd 托管）。现有 `COMMAND`/`SELF_SIGNAL` 保留。
- 可选新增 `ServiceInstallResult` Pydantic（`{backend, action: installed|refreshed|skipped, service_file_path, dry_run, repair_required, messages[]}`）用于 CLI 回显（对齐 F084 WriteResult 回显惯例，非强制）。
- 可选新增 `ServiceStatus` Pydantic（`{installed, loaded, running, pid, ready, last_error_line, backend}`）。
- **无数据库 schema 变更**（复用 `~/.octoagent/data/ops/` 的 managed-runtime.json / runtime-state.json）。

### 5.2 新模块（建议落点）

- `octoagent/packages/provider/src/octoagent/provider/dx/service_manager.py`：`ServiceBackend` 抽象 + `LaunchdBackend`/`SystemdUserBackend` + `detect_init_system()` + plist/unit 模板 + install/uninstall/status/幂等/repair-gate。
- `octoagent/packages/provider/src/octoagent/provider/dx/service_commands.py`：click `service` group（install/uninstall/status）+ `logs` command。注册进 `cli.py:main`（对齐现有 `main.add_command`）。
- 日志脱敏纯函数：`octoagent/apps/gateway/src/octoagent/gateway/middleware/log_redaction.py`（或并入 logging_config.py）。
- 日志落盘：改 `middleware/logging_config.py:setup_logging()` 加 FileHandler + redaction processor + excepthook/faulthandler。
- doctor 新 check：改 `dx/doctor.py`（+ 可能新 helper `dx/sleep_probe.py` 封 `pmset`）。

### 5.3 CLI 命令面（新增）

```
octo service install [--dry-run] [--force] [--keep-awake]
octo service uninstall [--dry-run]
octo service status [--verbose] [--json]
octo logs [-n N] [-f] [--level LEVEL] [--verbose]
```
- 现有 `octo restart/stop` 语义在 service 模式下按 FR-C2/C3 委托 OS；未装 service 时不变（FR-C4）。

---

## 6. 验收标准（AC）+ 成功度量（SC）

- **AC-1**：`octo service install` 在 Mac 生成 plist、load 成功、`octo service status` 报 installed+loaded+running；kill 掉 gateway 进程后 launchd 在 ThrottleInterval 内自动拉起（崩溃自愈实证）。
- **AC-2（★）**：生成的 plist/unit 的 WorkingDirectory=`~/.octoagent`、ExecStart 指向稳定 `run-octo-home.sh`，**grep 断言不含任何 `.worktrees` / worktree symlink venv 路径**（§0.4 防坑硬验收）。
- **AC-3**：重复 install 幂等（一致 skip / 手动改坏服务文件后 install 自愈重写）；uninstall 后 plist/unit 文件不存在 + `RestartStrategy` 复位 COMMAND（残留清单空）。
- **AC-4**：gateway 运行后 `~/.octoagent/logs/octoagent.log` 有内容且轮转到 backupCount；**注入一条含假 `sk-xxxx` / Telegram token 的日志 → 文件里被遮成 `***`/头尾**（脱敏实证，Constitution #5）。
- **AC-5**：制造启动期 traceback（如坏配置）→ traceback 落盘可查（service 层 StandardErrorPath 或 excepthook）。
- **AC-6**：`octo logs -f` 实时跟随；`octo doctor` 在会睡的笔记本上 `check_sleep_settings` WARN + 给出建议命令，**且不修改任何系统设置**（doctor 跑前后 `pmset -g` 输出一致）。
- **AC-7（向后兼容 / 0 regression）**：未 install service 时 `octo restart/stop` 行为不变；全量回归 0 regression vs master baseline；e2e_smoke 8/8。
- **AC-8（脱敏防关）**：运行时 `export OCTOAGENT_LOG_REDACT=false` 后新日志仍脱敏（import 快照，FR-E3）。

- **SC-1**：Connor 在 Mac mini 上 `octo service install` 一次后，重启机器 / 杀进程 / 关终端，gateway 均自动恢复运行（人工验收）。
- **SC-2**：崩溃后能从 `octo logs` 定位原因（traceback 可见 + 脱敏）。
- **SC-3**：doctor 明确提示禁睡，用户按建议一步设置后合盖外接场景不失联（或明确知道限制）。

---

## 7. ★ 设计岔路（回用户拍板，每条含选项 + 推荐 + 理由）

> 详见本回报正文 (c) 节。摘要（推荐加粗）：

- **GATE-1 跨平台**：A Mac-only / **B Mac(launchd)+Linux(systemd user)** / C +Windows。**推荐 B**（Mac 是 P0 当前部署；Linux 边际成本低+NAS 迁移前瞻；Windows ROI 负，留 `none` 兜底扩展缝）。
- **GATE-2 ★ 禁睡（最关键）**：**A 只检测+建议（doctor WARN+fix_hint，零系统改动）** / B 自动改系统电源（pmset/sudo）/ C 折中（可选用户级 caffeinate）。**推荐 A 为默认 + C 作显式 opt-in（`--keep-awake`），绝不选 B**（B 需 sudo 违 #7+单次授权；合盖睡眠软件根本挡不住给虚假安全感；改了难还原不可移植）。
- **GATE-3 install/uninstall 幂等**：**全量幂等（三态 install + 尽力 uninstall + dry-run + 显式残留清单）**（唯一合理，采 Hermes 三态 + OpenClaw repair gate）。
- **GATE-4 与现有 restart/stop 关系**：**A 并存分层**（service 模式 restart 委托 launchctl/systemctl，未装则不变）/ B 完全取代。**推荐 A**（最小破坏 + 向后兼容 + 正好解决"restart 要求进程存活"；stop 加提示防困惑）。
- **GATE-5 日志**：落盘 `~/.octoagent/logs/octoagent.log`；size 轮转 10MB×5；脱敏子集前缀默认 ON+import 快照；`octo logs` tail/-f/-n/--level。（细节见 DP-6，倾向文本格式给人读 triage，因结构化查询已由 Event Store 覆盖。）
- **GATE-6 崩溃退避**：A 无脑重启（Hermes 关 start-limit）/ **B 退避+熔断（OpenClaw：ThrottleInterval + StartLimitBurst + 配置错退出码不重启）**。**推荐 B**（防坏配置 busy-loop 刷爆盘 + status 可见 failed 引导排查；两参考此处分歧，OpenClaw 更安全）。

---

## 8. 规模复核

**M（中）**。范围横跨 CLI（provider/dx：service_manager + service_commands + doctor 2 check）+ core models（update.py RestartStrategy 扩展）+ gateway（logging_config 落盘+脱敏+excepthook）+ update_service（OS_SERVICE 策略委托）。**无数据库 schema 变更**（复用 ops json）、**无 event schema**（不新增 EventType，日志≠事件）、**无跨包大重构**、**无不可逆迁移**。真实系统集成（launchd/systemd）需真机验证但代码面可控。介于 S 与 L 之间，定 **M**（与 milestones.md 标注一致）。

**风险点**：①真机 launchd/systemd 集成需实机验证（CI 难全覆盖，测试以 stub backend + 内容断言为主，真装真起走人工 AC）；②stable-working-dir 防坑（§0.4，最高优先级，AC-2 硬验收）；③脱敏漏网（诚实边界 FR-E5）。

---

## 9. Phase 拆分（详见 plan.md）

A 研究闭环（本 spec + research.md）→ B ServiceBackend + 模板 + 幂等 → C RestartStrategy 扩展 + restart/stop 集成 → D 日志落盘 + 脱敏 + excepthook → E `octo logs` + service CLI → F doctor 2 check + 禁睡 → G 双评审（Codex + Opus）→ H 文档 + living-docs 漂移闸 + completion-report。**不主动 push，等用户拍板 §7。**
