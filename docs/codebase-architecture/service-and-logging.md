# Service 守护 + 日志落盘架构（F129 常驻服务地基）

> 实现级导览。设计依据 `.specify/features/129-service-foundation/`（spec/plan/
> research + completion-report）；Blueprint 对应 `deployment-and-ops.md`
> §12.5.6 / §12.7.3。

## 1. 核心定位

把前台 gateway 进程做成 **OS 托管常驻服务** + 补 **进程级日志落盘**。
两条铁律：

1. **不自写 supervisor 循环**——守护自愈/开机自启全靠 OS 原生
   （launchd `KeepAlive` / systemd `Restart=`），本域代码只负责
   生成/安装/卸载/探测服务定义（Hermes/OpenClaw 交叉印证的结论）。
2. **stable-working-dir 红线**——服务定义绝不指向 worktree/可删目录
   （目录一删 = CHDIR/exec 阶段永久崩溃循环，Hermes 惨案）。
   `WorkingDirectory` 钉实例根 `~/.octoagent`，ExecStart 指
   `run-octo-home.sh`；worktree 标记（`.worktrees` 子串 / `worktrees`
   路径段）硬拒且**不可被 --force 绕过**；实例根外的稳定源码 clone
   警告放行（知情但合法）。

## 2. 模块地图

| 模块 | 职责 |
|------|------|
| `apps/gateway/src/octoagent/gateway/services/operations/service_manager.py` | `ServiceBackend` 抽象 + `LaunchdBackend`/`SystemdUserBackend` + `ServiceManager` 编排（install 三态幂等 / uninstall 尽力清理+残留复查 / status 三态并行探测）+ `detect_init_system` + `resolve_instance_root` + 稳定路径校验 |
| `apps/gateway/src/octoagent/gateway/cli/service_commands.py` | `octo service {install,uninstall,status}` + `octo logs`（tail/-f/-n/--level + 启动期崩溃回退 err.log；全部输出过脱敏）|
| `apps/gateway/src/octoagent/gateway/services/operations/sleep_probe.py` | 睡眠风险只读探测（pmset -g / 电池检测；**绝不写系统设置**）|
| `apps/gateway/src/octoagent/gateway/services/operations/doctor.py`（扩展）| `check_service_status`（三态+linger+ready 健康映射）+ `check_sleep_settings`（WARN+平台化 fix_hint）|
| `packages/core/src/octoagent/core/log_redaction.py` | `redact_sensitive_text` 纯函数（**core 零依赖**：gateway 写侧 formatter 与 provider 读侧展示共用）|
| `apps/gateway/src/octoagent/gateway/middleware/logging_config.py`（扩展）| RotatingFileHandler 落盘 + `_RedactingProcessorFormatter` + excepthook/faulthandler 崩溃兜底 |
| `core/models/update.py` | `RestartStrategy.OS_SERVICE` 枚举值 |
| `dx/update_service.py`（扩展）| OS_SERVICE 策略下 restart 委托 `launchctl kickstart -k` / `systemctl --user restart` |

## 3. 服务定义要点（渲染模板）

- **launchd**（`~/Library/LaunchAgents/com.octoagent.gateway.plist`）：
  `RunAtLoad` + `KeepAlive{SuccessfulExit=false}`（正常 stop 不重启）+
  `ThrottleInterval=10` + `ExitTimeOut=90`（> drain 窗口）+
  `StandardOutPath/StandardErrorPath` → `~/.octoagent/logs/octoagent.{out,err}.log`。
- **systemd user unit**（`~/.config/systemd/user/octoagent.service`）：
  `Restart=on-failure` + `RestartSec=5` + `StartLimitBurst=5/60s`（崩溃风暴
  熔断）+ `RestartPreventExitStatus=78`（确定性配置错不重启；gateway 侧
  主动 `exit(78)` 拒绝不安全配置）+ `TimeoutStopSec=90` + `KillMode=control-group`。
- **PATH 确定性**：不复制 shell PATH（幂等比对剔 PATH 防误判过时）；
  `which("uv")` 目录先过稳定性校验（worktree/.venv 弃用），
  `~/.local/bin` + Homebrew 兜底。
- **env 安全**：只放 `OCTOAGENT_*` 且键名不含
  KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH（前缀不是安全边界）；
  secret 走 `~/.octoagent/.env`（run-octo-home.sh 运行期 source）。
- **keep-awake（opt-in）**：launchd ProgramArguments 前缀
  `/usr/bin/caffeinate -i -s`（用户级零 sudo；合盖挡不住——诚实边界）。

## 4. 生命周期语义

- **install 三态幂等**：一致 skip（仍保证 loaded+running+策略位）/
  过时归一化比对自愈重写 / 缺失装；`--dry-run` 零副作用预览（diff 已脱敏）；
  repair gate = activate 硬失败 || start gate 超时 || loaded 复验失败 →
  `repair-required` + exit 1（拒绝假成功）。
- **uninstall 尽力 + 诚实**：unload/停止失败**忽略继续**（幂等），但删文件后
  复查 loaded/running——仍在 = 失管残留，列入 FR-B3 残留清单 + exit 1；
  runtime-state 同步清理（防旧 pid 被 COMMAND 模式误用）。
- **restart 分层（GATE-4）**：`RestartStrategy.OS_SERVICE` → 委托 OS
  （不要求旧 pid 存活）；`COMMAND` 路径字节级不变（FR-C4）。
- **stop 语义**：优雅 SIGTERM（exit 0）不触发自动重启；`--force` SIGKILL
  被 supervisor 判异常**立即拉起**——CLI 文案显式区分。
- **status 三态**：`{installed, loaded, running}` 并行探测（双层 timeout
  软化 + `shutdown(wait=False)` 不 join 卡死线程）+ pid + `/ready` +
  last_error_line（脱敏后截断）。

### 4b. `octo attest service` 崩溃自愈探针（F144，`attest_commands.py`）

F129 AC-1「崩溃自愈」的可重复化（user-guide §6 手工脚本吸收进命令）：status 健康
→ **SIGKILL** 真 pid（上面 stop 语义即证据：SIGTERM 优雅 exit 0 **不**触发自动重启，
SIGKILL 才被 supervisor 判异常立即拉起——故探针必须 SIGKILL）→ poll `status()`
恢复 → 断言新 pid ≠ 旧 pid（+ ready==True，无 verify_url 时降级 pid 判定）。
`--dry-run` 只检不杀；真跑先声明「服务秒级闪断」（`--json` 时声明走 stderr）。
三态 `pass/not_enabled/fail`（exit 0/0/1）。**不进 CI**（真副作用）；探针逻辑由
`apps/gateway/tests/cli/test_attest_commands.py` hermetic 守（fake manager /
kill 记录 / 虚拟时钟）。开机自启半边物理不可自动化 →
`docs/codebase-architecture/attestation-checklist.md` ATT-129-BOOT。

## 5. 日志双层模型

```
层 1（进程内，脱敏）   RotatingFileHandler → logs/octoagent.log（10MB×5，0600）
层 2（service 层，原始）StandardOut/ErrPath → logs/octoagent.{out,err}.log（0600 预创建）
崩溃兜底               excepthook（脱敏落盘+脱敏 stderr）+ faulthandler → octoagent-crash.log
```

- 目录解析（写侧）：`OCTOAGENT_LOG_DIR` → `$OCTOAGENT_PROJECT_ROOT/logs`
  （run-octo-home.sh 恒设）→ 缺省不落盘（hermetic：数千单测经 create_app
  触发 setup_logging，绝不隐式写用户实例）。
- supervised 模式（`OCTOAGENT_SUPERVISED` env，模板注入）+ 落盘可用时
  StreamHandler 收窄 WARNING+（防 info 洪流双写进无轮转 err.log）。
- **脱敏**（`core/log_redaction.py`）：厂商前缀 sk- / ENV 赋值（含裸键名
  `TOKEN=`）/ JSON 字段 / Bearer / Telegram bot token / 连接串密码 / JWT；
  短 token 全遮、长留头 6 尾 4（`…` 分隔保结构性幂等）；ANSI 色码打断
  词边界时降级输出无色+脱敏版；`OCTOAGENT_LOG_REDACT` **import 时快照**
  （运行时 export false 不生效）；层 2 原始输出在唯一出站口（`octo logs`
  回退 / last_error_line / dry-run diff）展示前统一再脱敏。
- `octo logs`：跨轮转 tail / `-f` 轮转感知 follow / `--level` best-effort；
  主日志缺失**或为空**回退 err.log（启动期 import 崩溃场景）。

## 6. 哲学守界

- **H1**：纯运维地基，不碰 Agent 决策环/user-facing 语义。
- **#4/#7（GATE-2）**：禁睡/linger 只检测 + WARN + fix_hint，
  **绝不 sudo / 绝不写系统设置**（sleep_probe 有命令白名单机械断言）。
- **#5**：secret 不进服务定义 / 日志落盘脱敏默认 ON 防运行时关。
- **#6**：日志故障/探测失败全降级不阻塞主流程。

## 7. 测试约束（hermetic 红线）

单测**绝不真装**用户 `~/Library/LaunchAgents` / systemd：服务目录 tmp 注入、
launchctl/systemctl 经 `CommandRunner` stub、`/ready` fake prober、
descriptor 存储 tmp。真机 install 冒烟是合入后用户手动 opt-in（AC-1，
见 user-guide.md）。
