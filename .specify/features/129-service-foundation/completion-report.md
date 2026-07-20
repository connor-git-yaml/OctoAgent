# F129 常驻服务地基 — Completion Report

**Feature**: F129 / `service-foundation`（M8 P0，服务基础）
**分支**: `feature/129-service-foundation`（base master 30ea77ce）
**状态**: 实施完成，双评审闭环 0 HIGH 残留，**未 push origin，等用户拍板**
**规模复核**: spec 定 M —— 实际吻合（8 个实现 Phase commit，净增 ~4900 行含测试/文档，无 DB schema / 无 event schema / 无跨包大重构）

---

## 1. Phase 对照（计划 vs 实际）

| Phase | 计划（plan.md） | 实际 | Commit |
|-------|----------------|------|--------|
| A 研究闭环 | spec + research + plan | ✅ 一致 | 386ee5c9 |
| B ServiceBackend + 模板 + 幂等 | service_manager.py + 43 测试 | ✅ 一致 + **RestartStrategy.OS_SERVICE 提前并入**（原排 C；为保 install/uninstall 幂等测试自洽，偏离已在 commit 归档） | b5174f2f |
| C restart/stop 分层 | update_service 委托 + stop 提示 | ✅ 一致（5 新测试，FR-C4 COMMAND 路径零变更回归断言） | 68e93bb9 |
| D 日志落盘 + 脱敏 + 崩溃兜底 | logging_config + log_redaction + 2 测试文件 | ✅ 一致 + 2 处偏离（见 §3） | d7fcba9a |
| E service CLI + logs | service_commands + cli.py 注册 | ✅ 一致（20 新测试） | 9697b03f |
| F doctor 2 check | sleep_probe + doctor DI 缝 + 30 新测试 | ✅ 一致 | aacce64a |
| G 双评审 | Opus 自审 + Codex review | ✅ **Opus 2 轮（1 finding + 收口 0）+ Codex 11 轮 31 finding + 测试自抓 1**，全接受修复（见 §2）| e5fee387 → 51e80d38 共 11 个修复 commit |
| H 文档 + 收口 | completion-report + handoff + living-docs | ✅ 本文档 + handoff.md + deployment-and-ops.md §12.5.6/§12.7 + 用户上手指南 | （本 commit） |

## 2. 双评审 finding 闭环表

**Opus 对抗自审**（1 finding，修复 e5fee387）：

| # | 级别 | 内容 | 处理 |
|---|------|------|------|
| G-1 | MED-HIGH | service 层 out/err 日志文件由 launchd/systemd 按 umask 创建（0644），而这层内容**未脱敏**——与进程内 0600 脱敏文件权限模型不一致 | ✅ install 预创建 0600 + 目录 0700（init 系统 append 保留权限）+ 1 测试 |

**Codex review 一轮**（`codex review --base master`，2 P1 + 2 P2，全接受，修复 ff0cd90e）：

| # | 级别 | 内容 | 处理 |
|---|------|------|------|
| P1-1 | P1 | `.claude/worktrees/...`（无 `.worktrees` 子串）绕过 stable-working-dir 校验——本 feature 自己的 worktree 就是这形态 | ✅ 双重检测：子串 + `worktrees` 路径段；2 测试 |
| P1-2 | P1 | excepthook 调原 hook 把**未脱敏** traceback 写 stderr → service 层 err.log 绕过 FR-E | ✅ 默认 hook 时改写脱敏后 traceback；第三方 hook 保持链式；1 测试 |
| P2-3 | P2 | skipped install 分支 gate 失败不透传 `repair_required`（CLI exit 0 假成功）+ 策略位不补切 | ✅ gate 透传 + OS_SERVICE 补切（descriptor 漂移自愈）；2 测试 |
| P2-4 | P2 | status `with ThreadPoolExecutor` 退出 `shutdown(wait=True)` join 卡死线程，外层 timeout 失效 | ✅ `shutdown(wait=False, cancel_futures=True)` + timeout 可注入；1 行为测试（真挂起线程 <5s 返回） |

**修 P1-2 测试时测试自身抓出第 5 个真问题**（同 commit ff0cd90e）：

| # | 级别 | 内容 | 处理 |
|---|------|------|------|
| ANSI-5 | P1 级 | rich/ConsoleRenderer 色码字母结尾（`\x1b[33m`）紧贴 secret 打断 `\b` 词边界 → `sk-` 前缀规则失配泄漏（影响所有彩色渲染路径含文件落盘） | ✅ redact 降级第二遍：剥 ANSI 后仍抓到 secret 形状则整段输出无色+脱敏版（安全>颜色）；2 测试 |

**Codex review 二轮**（修复后 re-review，1 P1 + 3 P2，全接受，修复 e8037998）：

| # | 级别 | 内容 | 处理 |
|---|------|------|------|
| P1-5 | P1 | worktree/.venv shell 里 `which("uv")` 解析到不稳定路径直接进 plist/unit PATH，且幂等比对剔 PATH 写错永不自愈 | ✅ uv 目录过 validate_stable_paths + `.venv` 段检测，不稳定弃用，`~/.local/bin` 兜底；3 参数化测试 |
| P2-6 | P2 | skipped+running+ready=False 只提示不判 repair（一轮 P2-3 修复的保守选择被推翻） | ✅ 重走 `_start_gate`（窗口容启动中恢复，超时 repair-required）；1 测试 |
| P2-7 | P2 | 启动期 import 崩溃时主日志不存在，`octo logs` 报「暂无日志」——最需要日志的时刻看不到 | ✅ 回退展示 `octoagent.err.log`（带来源标注）；2 测试 |
| P2-8 | P2 | doctor 进程在跑但 ready=False 仍 PASS（gateway 实际不可用报健康） | ✅ WARN + fix_hint；1 测试 |

**Codex review 三至十一轮**（每轮修复 commit 后 re-review，直至收敛）：

| 轮次 | Finding | 全部接受修复 |
|------|---------|------|
| 三轮 | 4 P2：P2-9 实例根外脚本（后被四轮回调为分级）/ P2-10 stop --force 假停止文案 / P2-11 err.log 未脱敏展示（**redact 下沉 core 包**，写侧+读侧共用）/ P2-12 uninstall 清 runtime-state | ea59b896 |
| 四轮 | 1 P1 + 1 P2：P1 三轮 P2-9 硬拒与 bootstrap 流程不兼容（裁决分级：worktree 硬拒 / 稳定 clone 警告放行）/ P2 OCTOAGENT_ 前缀非安全边界（敏感键名剔除）| 003b9465 |
| 五轮 | 2 P2 + 1 P3：P2-13 激活失败假成功（gate 后补验 loaded）/ P2-14 restart/stop 实例根对齐（_resolve_managed_root，保 FR-C4）/ P3-15 Linux fix_hint 平台化 | a0aa3e4c |
| 六轮 | 3 P2：P2-16 activate 硬失败直接置 repair / P2-17 systemd linger 检测+建议（不自动 enable）/ P2-18 doctor 用托管实例根（resolve_instance_root 下沉领域层三处共用）| 955d2968 |
| 七轮 | 2 P2：P2-19 supervised 模式 err.log 无界增长（StreamHandler 收窄 WARNING+）/ P2-20 先脱敏后截断（last_error_line 边界泄漏）| 95116b08 |
| 八轮 | 1 P2：P2-21 uninstall 停止失败仍报"残留清单为空"（复查 loaded/running 纳入残留 + exit 1）| 0eadd951 |
| 九轮 | 3 P2：P2-22 空/旧主日志掩盖启动崩溃（为空也回退 + mtime 提示）/ P2-23 absent 分支对称复查残留 / P2-24 dry-run diff 脱敏 | 16d8e6cd |
| 十轮 | 1 P1 + 2 P2：P1 裸 ENV 键名（TOKEN=/API_KEY=）脱敏漏网（前缀改可选）/ P2-25 status 健康判定加 loaded+ready / P2-26 doctor running-not-loaded WARN | 547d1cde |
| 十一轮 | 1 P1 + 1 P2：P1 迁移场景假接管（旧自管 COMMAND 进程占端口，共享 /ready 骗过 gate——install 前 `_stop_legacy_command_process` 优雅交接，supervisor 管理的 pid 绝不碰）/ P2-27 生命周期命令 drain 超时（LIFECYCLE_TIMEOUT=120s，probe 保持 5s 分层）| 51e80d38 |
| 十二轮 | **Codex usage limit 中断**（5:37 AM 恢复）——按 plan §Phase G 预案切 **Opus 自审收口**：对十一轮 diff 对抗审查（EPERM 误杀面 / OS_SERVICE pid 保护 / skipped 分支调用 / 超时分层）无新 finding。待 push 前用户可手动补跑 codex | — |

**合计：Opus 自审 2 轮（1 finding + 十二轮收口 0 finding）+ 测试自抓 1（ANSI）+ Codex 11 轮 31 finding（6 P1 + 24 P2 + 1 P3）—— 共 33 项，全部接受修复，0 拒绝，0 HIGH（P1）残留。**
**收敛趋势**：P1 分布在 1/2/4/10/11 轮（后期 P1 为正则边界/迁移交互面，非架构问题）；P2 4→1 递减震荡；每轮修复 commit 后必 re-review（F099 教训贯彻，本 Feature 实证其必要性——一轮 Final review 只能抓到 ~15% 的问题面）。

## 3. Spec 偏离归档（全部带理由）

1. **FR-D1 日志目录解析**：spec 字面「默认 `~/.octoagent/logs` ensure 创建」→ 实现收窄为 env 驱动（`OCTOAGENT_LOG_DIR` 显式 → `$OCTOAGENT_PROJECT_ROOT/logs` → 都缺省不落盘）。**理由**：`setup_logging()` 在 `create_app()` 必经路径，数千现有单测触发——无条件默认写 `~/.octoagent/logs` 会让单测污染用户真实例（hermetic 硬约束优先）。所有托管路径（service / `octo restart`）必经 `run-octo-home.sh` 恒 export `OCTOAGENT_PROJECT_ROOT`，FR-D2「日志不随终端消失」语义完整达成。
2. **FR-E1 脱敏挂载层**：spec 字面「processor 链渲染器前」→ 实现在 **formatter 最终字符串层**（`_RedactingProcessorFormatter.format()` 后跑 redact）。**理由**：覆盖面更大——stdlib 拼接的 exception 文本一并脱敏；Hermes 同款（RedactingFormatter），research §B.2 亦允许。
3. **Phase B 提前并入 RestartStrategy.OS_SERVICE**（原排 Phase C）：install/uninstall 的策略切换/复位测试需要枚举值存在，为测试自洽提前；Phase C 只做 restart/stop 委托。
4. **FR-G2 Linux 睡眠检测**：spec「检测 systemd suspend 目标类似」→ 实现 `will_sleep=None` 诚实不猜（Linux 无统一睡眠策略只读探针），电池检测 + detail 指引人工检查 logind.conf；有电池 WARN / 无电池 SKIP。

## 4. AC 验收状态

| AC | 状态 | 证据 |
|----|------|------|
| AC-1 崩溃自愈实证 | ⏳ **人工步骤**（合入后 Connor 真机 opt-in，见用户上手指南 §验证） | 测试层 hermetic 红线：绝不真装 |
| AC-2 稳定路径硬验收 | ✅ | `test_service_manager.py` 渲染断言无 `.worktrees`/`worktrees` 段 + PATH 过滤 3 参数化测试 |
| AC-3 幂等 + 残留清单 | ✅ | 三态幂等 8 测试 + uninstall 残留枚举测试 |
| AC-4 轮转 + 脱敏实证 | ✅ | `test_logging_file_sink.py` 轮转/假 sk-/Telegram token 遮蔽断言 |
| AC-5 启动期 traceback 可查 | ✅ | excepthook 落盘测试 + P2-7 logs 回退 err.log 测试；service 层 StandardErrorPath 模板断言 |
| AC-6 doctor 禁睡 + 不改系统 | ✅ | WARN+fix_hint 测试 + **命令白名单机械断言**（只跑 `pmset -g`/`pmset -g batt`，无 sudo/写参数） |
| AC-7 向后兼容 0 regression | ✅ | COMMAND 路径行为断言（Popen 仍调用 + factory 绝不构造）+ 全量 4480 passed / 0 failed |
| AC-8 脱敏防运行时关 | ✅ | import 快照测试（运行时 export false 仍脱敏） |

## 5. 回归对账

- **最终全量**（packages + apps/gateway，除 e2e_live）：**4513 passed / 0 failed** / 3 skipped / 1 xfailed / 1 xpassed（每轮修复后重跑共 11 次全量，从 4464 → 4513 全程 0 failed）
- **唯一 deselect**：`test_plugin_watcher.py::test_start_degrades_without_watchdog` —— **已在 master src 复现同样失败**（F106 引入 watchdog 依赖后，该测试「watchdog 未装」假设在共享 venv 失效），baseline 环境既有，非 F129 回归
- pre-commit hook e2e_smoke 8/8 每 commit PASS（共 17 次 commit）
- ruff：F129 触碰文件 0 新增违规（仅剩 doctor.py I001 / update_commands.py B904 —— 均 master 既有，stash 对照确认，不越权修；cli.py I001 同）
- F129 自身测试面：**216 tests**（service_manager / update_service / update_commands / service_commands / doctor+sleep / core log_redaction / gateway file sink）
- SDD [@test] 机械校验：6 个绑定测试文件全部存在且 PASS（`gateway/tests/test_log_redaction.py` 随 redact 下沉 core 迁移至 `packages/core/tests/`，偏离归档 §2 三轮 P2-11）

## 6. 已知 limitations（诚实边界）

1. **service 层 out/err 原始日志文件本体不脱敏**（DP-6 层 2 设计使然——OS fd 重定向在 Python 之外，这层的存在意义就是抓 logging 之前的启动期崩溃）。缓解链：文件预创建 0600 + 目录 0700（G-1）+ excepthook 默认链改写脱敏文本（P1-2）+ supervised 模式 StreamHandler 收窄 WARNING+ 防常规日志双写（P2-19）+ **全部出站展示口已脱敏**（`octo logs` 回退 / status last_error_line / dry-run diff，P2-11/P2-20/P2-24）→ 残余暴露面 = 用户直接 cat 该 0600 文件里 setup_logging 之前的启动期崩溃 traceback。文档已注明「日志文件仍属敏感、勿外发」（FR-E5）。
2. **octoagent-crash.log（faulthandler）无轮转**：段错误 dump 罕见且小（栈帧列表，无 secret 值面），不接 RotatingFileHandler（faulthandler 直写 fd，轮转 rename 会破坏）。
3. **`octo logs --level` 是文本 best-effort 过滤**：消息正文含级别词（如 "error"）的 info 行会被误包含。结构化查询由 Event Store 承担（#2），此处只服务人肉 triage。
4. **正则脱敏非万能**（FR-E5）：自定义格式 secret 可能漏网；ANSI 降级第二遍已兜住彩色路径,但未知转义形态理论上仍有缝。
5. **doctor 的 service check 用 DoctorRunner.project_root**（cwd fallback）而非 CLI 的 `~/.octoagent` 兜底——三态探测不受影响（backend 路径全局唯一），仅 ready/last_error 增强维度在 root 未对准时缺失。
6. **Blueprint drift**：deployment-and-ops.md §12.1/§12.2 的 Docker Compose 拓扑仍是 aspirational 旧蓝图（实际部署 = `~/.octoagent` 托管实例）——F129 只加 §12.5.6/§12.7 reality 节，Docker 叙事全面校正超范围，留 M8 后续或独立文档 Feature。
7. **测试环境既有问题**（非 F129 引入）：`test_start_degrades_without_watchdog` 在 watchdog 已装的 venv 恒失败（见 §5）——建议独立小 fix（改为 monkeypatch 隐藏 watchdog import）。

## 7. GATE 兑现核对（用户 6 项拍板）

| GATE | 拍板 | 兑现 |
|------|------|------|
| GATE-1 | Mac launchd + Linux systemd user unit | ✅ LaunchdBackend / SystemdUserBackend + `none` 优雅降级 |
| GATE-2 | 禁睡=检测+建议，绝不 sudo/pmset 写 | ✅ doctor WARN+fix_hint（含合盖诚实说明）+ 命令白名单机械断言 + `--keep-awake` 用户级 caffeinate opt-in |
| GATE-3 | 三态幂等 + 尽力 uninstall + dry-run | ✅ Phase B 实现，后续 Phase 未破坏（P2-3/P2-6 反而加严了 gate 透传） |
| GATE-4 | restart 分层委托 | ✅ Phase C（OS_SERVICE 委托 / COMMAND 不变） |
| GATE-5 | 日志 `~/.octoagent/logs/octoagent.log` 10MB×5 env 可配 + 脱敏默认 ON import 快照 + 文本格式 + octo logs | ✅ Phase D+E 全兑现 |
| GATE-6 | 退避+熔断 | ✅ Phase B 模板（ThrottleInterval=10 / StartLimitBurst=5/60s / RestartPreventExitStatus=78）；gateway 侧主动 exit(78) 拒绝不安全配置 |
