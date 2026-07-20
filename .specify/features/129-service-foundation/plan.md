# Implementation Plan: F129 常驻服务地基

**Feature ID**: F129 / `service-foundation`
**Spec**: 本目录 `spec.md`（v0.1 草案）
**Research**: 本目录 `research.md`（Hermes/OpenClaw + Octo 现状，带 file:line）
**Status**: **设计先行——待用户拍板 spec §7 设计岔路后再进入实施**。本 plan 是拍板后的执行蓝图。
**规模**: M

> ⚠️ 本 Feature 命中「重大架构变更」节点（触碰 CLI provider/dx + gateway 启动/日志层 + 新增 service/logging 模块 + 真实系统集成 launchd/systemd）→ 强制 **Codex（`codex review --base`，scoped 小 diff）+ Opus 双评审 panel**；每 Phase 后 0 regression vs master baseline；e2e_smoke 必过；worktree PYTHONPATH 锁禁 uv sync；不主动 push 等用户拍板。

---

## 0. 前置：worktree 验证环境（沿用 M7 教训）

- worktree `.venv` 是 symlink 指向主仓 → **裸 `pytest` 跑的是 master src**。验证本 worktree 代码必须 **PYTHONPATH 锁 worktree**（memory `project_worktree_venv_symlink`），禁 `uv sync`。
- 跑测试用 `uv run --no-sync python -m pytest`（memory `project_pytest_invocation_env_pollution` + `project_precommit_hook_execution_model`：裸 `uv run pytest` 会逃逸 venv，须 `python -m pytest`）。
- baseline：进 Phase B 前先记 master 30ea77ce 的 `pytest` passed 数（回归护栏）。

---

## 1. 依赖与顺序

```
A 研究闭环（已完成：spec + research.md）
        │
        ▼
B ServiceBackend + plist/unit 模板 + 三态幂等 + repair-gate   ← 核心地基，最先
        │
        ├──────────────┐
        ▼              ▼
C restart/stop 集成     D 日志落盘+脱敏+excepthook   ← C/D 文件不同可并行
  (RestartStrategy       (logging_config.py +
   OS_SERVICE +           log_redaction.py，
   update_service 委托)   不碰 service_manager)
        │              │
        └──────┬───────┘
               ▼
E `octo logs` + service CLI group（依赖 B 的 backend + D 的日志路径）
               │
               ▼
F doctor 2 check（service_status 依赖 B / sleep_settings 独立）
               │
               ▼
G 双评审 panel（Codex + Opus，全量回归）
               │
               ▼
H 文档 + living-docs 漂移闸 + completion-report
```

**并行机会**：C（restart 集成，改 update_service.py）与 D（日志，改 logging_config.py）文件不冲突可并行。B 是两者前置（C 需 OS_SERVICE 策略语义，E 需 backend）。

---

## 2. Phase 明细

### Phase A — 研究闭环 ✅（本 spec + research.md）
- 产出：spec.md（FR/AC/GATE）+ research.md（Hermes/OpenClaw + Octo 现状证据）+ 本 plan。
- **Gate**：用户拍板 §7 设计岔路（尤其 GATE-2 禁睡）→ 才进 Phase B。

### Phase B — ServiceBackend + 模板 + 幂等（核心）
- `dx/service_manager.py`：`detect_init_system()` + `ServiceBackend` 抽象 + `LaunchdBackend`/`SystemdUserBackend`。
- plist/unit f-string 模板：**WorkingDirectory=`~/.octoagent` + ExecStart=稳定 run-octo-home.sh 绝对路径 + PATH 补全 + KeepAlive/ThrottleInterval（launchd）/ Restart+StartLimit+专用退出码不重启+TimeoutStopSec>drain（systemd）**（DP-2/DP-3）。
- install 三态幂等（归一化比对剔 PATH）+ uninstall 尽力清理 + `--dry-run` + repair-gate（DP-4）。
- **FR**：FR-A1/A2/A3/A5、FR-B1~B4、FR-H1（keep-awake 定义生成，若 GATE-2 选 C）。
- **测试**：`packages/provider/tests/test_service_manager.py`（backend 探测 / **AC-2 grep 断言无 worktree 路径** / KeepAlive+StartLimit 字段 / 三态幂等 / 归一化不误判 / uninstall 残留清单空 / dry-run 不落地 / keep-awake）。
- **Gate**：Codex per-Phase review（服务定义正确性 + 稳定路径约束 + 幂等边界）；0 regression。

### Phase C — RestartStrategy 扩展 + restart/stop 集成（可与 D 并行）
- `core/models/update.py`：`RestartStrategy` 加 `OS_SERVICE`。
- FR-A4：install 后切策略 + 更新 descriptor/state。
- `dx/update_service.py`：`restart` 检测 `OS_SERVICE` → 委托 `launchctl kickstart -k` / `systemctl --user restart`（不 Popen）；`COMMAND` 路径不变（FR-C2/C4）。
- `dx/update_commands.py`：`stop` 在 service 模式加提示（FR-C3）。
- `dx/service_manager.py`：status 三态并行探测（DP-5，依赖 B backend；status CLI 在 E，但探测逻辑在 backend）。
- **FR**：FR-A4、FR-C1~C4。
- **测试**：`test_update_service.py`（OS_SERVICE restart 委托 / COMMAND 行为不变回归）；`test_service_manager.py`（三态探测超时软化）。
- **Gate**：Codex review（**向后兼容 COMMAND 路径 0 变更**是重点）；0 regression。

### Phase D — 日志落盘 + 脱敏 + 崩溃兜底（可与 C 并行）
- `middleware/log_redaction.py`：`redact_sensitive_text()` 纯函数（子集前缀 + 通用字段名 + Bearer + 连接串 + JWT；短全遮长留头尾；substring 预检；幂等）+ `_REDACT_ENABLED` import 快照（FR-E2/E3/E4）。
- `middleware/logging_config.py:setup_logging()`：加 `RotatingFileHandler`（logs/octoagent.log，ensure 目录，size 轮转 env 可配，**保留 StreamHandler**）+ redaction processor 挂链末端 + `sys.excepthook`/`faulthandler` 落盘 + handler try/catch 不阻塞 + 文件 0600（FR-D1~D5、FR-E1/E5）。
- **FR**：FR-D1~D5、FR-E1~E5。
- **测试**：`apps/gateway/tests/test_logging_file_sink.py`（FileHandler+轮转+目录 ensure / StreamHandler 仍在 / excepthook 落盘 / 磁盘故障不抛）；`apps/gateway/tests/test_log_redaction.py`（各 secret 遮 / 掩码策略 / **AC-8 import 快照防运行时关** / 非密钥不误遮 / 幂等）。
- **Gate**：Codex review（**脱敏漏网面** + 落盘不阻塞主流程 + import 快照防关是重点）；0 regression。

### Phase E — `octo logs` + service CLI group
- `dx/service_commands.py`：click `service` group（install/uninstall/status 调 B backend）+ `logs`（tail/-f/-n/--level 读 D 的日志文件）。
- 注册进 `cli.py:main`（`main.add_command`）。
- 友好输出（普通用户）+ 无文件友好提示（FR-F2）+ 技术细节 --verbose（FR-F3）。
- **FR**：FR-C1（status CLI 呈现）、FR-F1~F3。
- **测试**：`packages/provider/tests/dx/test_service_commands.py`（install/uninstall/status/logs CLI 端到端 stub 化 / logs -n/--level/无文件提示）。
- **Gate**：Codex review；0 regression。

### Phase F — doctor 2 check + 禁睡
- `dx/sleep_probe.py`（封 `pmset -g` 解析 + 电池检测）。
- `dx/doctor.py`：`check_service_status`（三态→CheckStatus，未装 RECOMMENDED 非 blocking）+ `check_sleep_settings`（会睡 WARN + fix_hint 三条建议 + 诚实告知合盖限制；非 Mac/Linux 或读不到 SKIP）；append 进 `run_all_checks`（doctor.py:95 后）。**不改任何系统设置**。
- **FR**：FR-G1~G3、FR-H2。
- **测试**：`packages/provider/tests/dx/test_doctor_service_checks.py`（三态映射 / 未装非 blocking / sleep WARN+fix_hint / SKIP 降级 / **不触发系统修改**）。
- **Gate**：Codex review（**check_sleep_settings 绝不改系统设置**是红线）；0 regression。

### Phase G — 双评审 panel（Codex + Opus）
- Codex final cross-Phase review（输入 spec + 全 Phase diff）：检查是否漏 Phase / 偏离 / 隐性技术债 / 稳定路径约束真守住 / 脱敏真覆盖 / 向后兼容真不破。
- Opus spec-对齐专项 review（重大架构变更多评审 panel，SDD 强化）：分歧项列"必须人裁"。
- 全量回归 0 regression + e2e_smoke 8/8。
- **★ 环境提醒**（memory `project_openai_codex_oauth_renewal` + F127 先例）：Codex OAuth 可能断链（AuthorizationRequired）→ 若不可用，Opus 自审为主审闸 + 待 push 前用户手动跑 codex。
- finding 闭环到 0 HIGH 残留。

### Phase H — 文档 + 漂移闸 + 收口
- 新增 `docs/codebase-architecture/service-and-logging.md`（service 守护模型 + 日志落盘/脱敏架构 + 跨平台边界 + 禁睡策略）。
- **living-docs 漂移闸**（SDD 强化）：对触碰模块 code↔doc 比对，drift 列 completion-report「已知 limitations」。
  - 同步 `docs/blueprint/` 相关（milestones.md M8 F129 状态 / 若涉部署运维文档 deployment-and-ops.md 加 service 章节）。
  - `CLAUDE.md` CLI 命令面 / `octo service` 新命令若需登记。
- `completion-report.md`：实际做了 vs 计划（Phase 对照）+ Codex/Opus finding 闭环表（N high/M med/K low）+ 已知 limitations + 规模复核。
- 后续远程入口继续沿用 `--host 127.0.0.1`；status 三态与日志脱敏层可复用，不需要修改 service 的监听语义。
- **不主动 push**，归总回报等用户拍板。

---

## 3. 关键不变量（每 Phase 守）

1. **稳定路径**（§0.4 / AC-2）：任何生成的服务定义 grep 断言不含 `.worktrees` / worktree symlink venv 路径。**最高优先级红线**。
2. **向后兼容**（FR-C4 / AC-7）：未 install service 的 `octo restart/stop` 走 COMMAND 路径行为字节级不变。
3. **脱敏默认 ON 且防运行时关**（FR-E3 / AC-8）：import 快照。
4. **日志不阻塞主流程**（FR-D5 / #6）：handler 异常吞掉。
5. **禁睡不改系统设置**（FR-G2 / AC-6）：doctor 跑前后 `pmset -g` 一致，只 WARN+建议。
6. **0 regression vs master baseline** + e2e_smoke 8/8（每 Phase）。
7. **无 event schema / 无 DB schema 变更**（日志≠事件，复用 ops json）。

---

## 4. 测试文件清单（[@test] 目标，verify 阶段机械校验存在 + PASS）

| 测试文件 | 覆盖 FR | Phase |
|----------|---------|-------|
| `octoagent/packages/provider/tests/test_service_manager.py` | FR-A/B/C(探测)/H | B/C |
| `octoagent/packages/provider/tests/test_update_service.py`（扩） | FR-A4/C2/C4 | C |
| `octoagent/apps/gateway/tests/test_logging_file_sink.py` | FR-D | D |
| `octoagent/apps/gateway/tests/test_log_redaction.py` | FR-E | D |
| `octoagent/packages/provider/tests/dx/test_service_commands.py` | FR-C1/F | E |
| `octoagent/packages/provider/tests/dx/test_doctor_service_checks.py` | FR-G | F |

---

## 5. 待用户拍板项汇总（进 Phase B 前）

见 spec §7（GATE-1~6）。**最关键 GATE-2（禁睡自动改 vs 只建议）** —— 推荐「A 只检测+建议 默认 + C 可选 --keep-awake，绝不 B 自动改系统设置」。其余 GATE 推荐均为窄路径，用户可逐条确认或调整后再启动实施。
