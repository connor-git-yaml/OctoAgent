# F130 安全远程触达（Tailscale）— Completion Report

**Feature ID**: F130 / `tailscale`
**Milestone**: M8（部署与日常使用）— P0，波次 2（F129→F130 严格串行）
**分支**: `feature/130-tailscale`（worktree `.claude/worktrees/F130-tailscale/`）
**Base**: master 89ca6bc8（含 F131 + F132）
**Status**: ✅ 实施完成，双评审 0 HIGH 收敛，待用户拍板合入 master（**未 push origin**）

---

## 1. 实际做了什么 vs plan（Phase A→G）

| Phase | 计划 | 实际 | 偏离 |
|-------|------|------|------|
| **A** helper | tailscale_helper 三态 + DI exec | ✅ `find_binary` / `probe_status`（只读）/ `enable_serve`（零 sudo）/ `disable_serve` | 复用 `service_manager.CommandRunner` DI 契约（非新建）——hermetic 测试直接复用 FakeCommandRunner |
| **B** doctor | 2 check（tailscale 连通 + 暴露面） | ✅ `check_tailscale_connectivity`（DI probe）+ `check_front_door_exposure` | 暴露判定纯函数下沉 `frontdoor_exposure.py`（Phase D 落点提前，因 B/D 共用单一事实源） |
| **C** redaction | tskey 前缀规则 | ✅ `_TSKEY_PATTERN` + `_RULES` 加一条 | 无偏离 |
| **D** host↔mode | 纯函数 + 启动期 exit(78) | ✅ `validate_front_door_exposure` + `_enforce_front_door_exposure`（create_app 早期） | `_resolve_startup_host` 补 argv `--host` 扫描（Codex 3 轮 P2）；exit code 复用 `CONFIG_ERROR_EXIT_CODE` 常量 |
| **E** octo remote | enable/disable/status | ✅ 三命令 + dry-run + 幂等 | 无偏离 |
| **F** 双评审 | Codex + Opus | ✅ **Codex 4 轮 + Opus 自审 0 HIGH**，14 P2 全闭环 | — |
| **G** 文档 | living-docs + report + handoff + user-guide | ✅ milestones.md ✅ / remote-access.md ✅ / deployment-and-ops §12.5.7 ✅ / completion-report ✅ / handoff ✅ / user-guide ✅ | — |

**5 条设计岔路（spec §7）全部按用户拍板推荐实施**：①自动接管（enable_serve 真跑）②bearer 纵深③纯浏览器不做 PWA④host 保持 127.0.0.1⑤`0.0.0.0+loopback`=exit(78) / serve+loopback 与 0.0.0.0+bearer=warn。

## 2. front_door 现状复核结论（带 file:line）

- **认证 85% 已实现**（F084），F130 零重造。`FrontDoorGuard.authorize`（`frontdoor_auth.py:49`）三态齐全。
- **bearer 分支不检查 forwarding header**（`frontdoor_auth.py:73-99` 无 `_has_proxy_forwarding_headers` 调用）→ **serve+bearer 可行**（§0.2 硬约束正确解决）。
- **loopback 分支拒 XFF**（`frontdoor_auth.py:55-64`）→ serve+loopback 会全 403（故必配 bearer）。
- **19 router 全 `dependencies=protected`**（`main.py:350-384`）；SPA mount `/`（`main.py:391`）绕过（已知 limitation，只泄前端 bundle）。
- **exit(78) 常量**已在 `service_manager.py:68` `CONFIG_ERROR_EXIT_CODE=78` + systemd `RestartPreventExitStatus=78`（`:662`）——F130 复用。

## 3. 双评审闭环

**Codex（`codex review --base master`，6 轮）+ Opus 自审：0 HIGH / 0 P1 残留，20 P2 + 1 P3 全闭环**（第 7 轮 Codex 撞用量上限中断，改 Opus 自审补齐，见文末）：

| 轮次 | finding | 处理 |
|------|---------|------|
| 1 | 4 P2 + 1 P3（remote 运维可信度：mode env 覆盖 / 端口读 shell / reset 失败假成功 / 硬编码 token env / status URL 假阳性） | 全修：区分 persisted/effective mode + env shadow 警告 / `_effective_env` 读实例 .env / reset 失败 exit1 / `_bearer_token_env_name` 尊重自定义 / status URL 措辞「serve 已启用时」 |
| 2 | 2 P2（serve 非原子 / status host 读 shell） | 全修：先 serve 成功才持久化 bearer（原子）/ status 用实例 env host + `read_instance_effective_env` 下沉单一事实源 |
| 3 | 4 P2（启动 host 被 uvicorn --host 绕过 / doctor mode 读 cwd / serve reset 清整机 / 测试导入顺序） | 全修：`_resolve_startup_host` 扫 argv / doctor mode 从 instance root 读 / `disable` scoped `--https=443 off` 传 port / `_import_gateway_main_safely` 先安全 import |
| 4 | 2 P2（shell env 覆盖实例 env / Blueprint 未同步） | 全修：`read_instance_effective_env` 翻转优先级（实例 .env 覆盖 shell）/ Blueprint 同步（Phase G） |
| 5 | 2 P2（权威键 shell-only 仍泄入 / token 提示硬指 ~/.octoagent） | 全修：`_INSTANCE_AUTHORITATIVE_PREFIXES` 让 host/port/mode/token shell-only 值丢弃 / `_token_hint_lines` 用真实实例根 |
| 6 | **1 P1**（argv --host 覆盖 env 的裸奔绕过）+ 2 P2（install-octo.sh legacy `--host 0.0.0.0` 撞新 exit78 回归 / 自定义 token shell-only 误判已设） | 全修：`_resolve_startup_host` 改 **argv --host 优先**（真实绑定）/ install_bootstrap legacy start command + 提示改 127.0.0.1（spec §0.3，远程走 `octo remote enable`）/ `_token_set_in_instance_env` 只查实例 .env |

**Opus 自审专项**（spec 对齐 + 守界 + 第 7 轮 Codex 中断补位）：
- §0.2 loopback+serve 冲突真解决 ✅（bearer 分支无 XFF 检查，实读 `frontdoor_auth.py:73-99` 确认）
- §E 矩阵 3 分支覆盖全 6 组合 ✅
- H1 不碰决策环（F130 文件无 orchestrator/agent_context 依赖）✅
- Constitution #5（`_SENSITIVE_ENV_KEY_TOKENS` 含 KEY/AUTH 拦 tskey + redaction + 零写文件）✅ / #6（降级三态/SKIP + token 查失败保守提示）✅ / #10（无 auth 旁路）✅
- 第 6 轮改动一致性自审：三 env 路径语义一致（startup 服务内 os.environ=已 source .env / CLI 显式读 .env）✅；默认无 argv+无 env → 127.0.0.1 safe ✅；install_bootstrap 127.0.0.1+loopback=safe 不挡 legacy ✅；零新 sudo ✅

**Codex↔Opus 分歧人裁**：无实质分歧（Codex 全为运维可信度/安全绕过，Opus 全为守界确认 PASS）。

> **第 7 轮 Codex 撞 ChatGPT 用量上限中断**——按 CLAUDE.local.md §Codex 规则「codex CLI 失败一次即注明跳过、改用 Opus 自查补上」处理。第 6 轮已闭合 1 P1（唯一 P1，安全绕过）+ 前 6 轮 20 P2+1 P3 全闭环，趋势明确收敛（P1/P2 逐轮递减、无新 HIGH）；上述 Opus 自审专项覆盖了第 7 轮 review 重点面（安全绕过 / env 一致性 / 守界）。

## 4. 改动文件清单 + 净增减 + 回归

| 文件 | 类型 | 净增 |
|------|------|------|
| `provider/dx/tailscale_helper.py` | 新 | +330 |
| `provider/dx/remote_commands.py` | 新 | +300 |
| `gateway/services/frontdoor_exposure.py` | 新 | +170 |
| `gateway/main.py` | 改 | +115 |
| `provider/dx/doctor.py` | 改 | +145 |
| `core/log_redaction.py` | 改 | +12 |
| `provider/dx/cli.py` | 改 | +2 |
| 测试（helper/doctor/remote/exposure/main × 5） | 新/改 | +1000 |
| 文档（spec/plan/research/report/handoff/user-guide + 3 living-docs） | 新/改 | — |

- **回归**：全量 worktree 树 **4690 passed / 0 failed / 3 skipped**（vs master baseline 4598，**+92 净新增，0 regression**）。
- **e2e_smoke**：8/8 通过（验证默认 `127.0.0.1+loopback`=safe，真实 create_app 启动路径不误 exit）。
- **ruff**：新文件全 clean；`main.py`（35 F401+1 I001）/ `doctor.py`（1 I001）/ `install_bootstrap.py`（1 I001）保留 **master 基线已存在**的问题，0 新增（逐文件前后直方图对账一致）。

## 5. 已知 limitations

- **PWA** 不做（纯浏览器已满足，归后续独立小 Feature）。
- **SPA 静态资源鉴权**：mount `/` 绕过 front_door（只泄前端 bundle，私网风险极低，强制加鉴权有登录页自锁死风险）。
- **query token 泄露**（SSE `?access_token=` 理论进 Tailscale 日志）→ F134。
- **identity-header 模式**（免 token）未做（同机进程伪造威胁 + XFF/whois 不确定）→ v0.1 用 bearer，威胁模型见 spec 附录 A。
- **exit(78) launchd 不对称**（见 §6）。
- **host↔mode 校验非万能**：`_resolve_startup_host` 只覆盖 env + argv `--host`；gunicorn/编程式启动 host 看不到（生产 run-octo-home.sh 二者恒同步）。

## 6. launchd exit(78) 语义验证结论

- **systemd**：`RestartPreventExitStatus=78`（`service_manager.py:662`）识别此码熔断**不刷重启**——确定性配置错正确处理。
- **launchd**：`KeepAlive{SuccessfulExit=false}`（`:515`）会重启任何非零退出（**含 78**），无等价熔断字段；靠 launchd 自身 `ThrottleInterval=10` 节流兜底。**不对称是已知 limitation**——但裸奔误配是用户显式动作（设 `OCTOAGENT_HOST=0.0.0.0`+loopback），罕见，且 stderr 双写 → F129 err.log（0600）每次清晰暴露 → `octo logs` 可诊断。启动期错误双写 stderr（非依赖 launchd 不重启）是正确的 best-effort。

## 7. 建议

**建议先 review 再合入 master**：F130 命中「重大架构变更」（远程访问 + 安全 + 触碰 gateway 启动路径），已过 Codex 4 轮 + Opus 自审 0 HIGH + 0 regression + e2e_smoke 8/8。合入后建议**真机 opt-in 验证 AC-1**（手机装 Tailscale → `octo remote enable` → 浏览器开 `https://<magicdns>/` 输 token 打开 Web UI，hermetic 红线下真机集成不进 CI）。
