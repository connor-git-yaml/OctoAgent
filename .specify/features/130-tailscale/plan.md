# Implementation Plan: F130 安全远程触达（Tailscale）

**Feature ID**: F130 / `tailscale`
**Spec**: 本目录 `spec.md`（v0.1 草案）
**Research**: 本目录 `research.md`（openclaw + Octo 现状，带 file:line）
**Status**: **设计先行——待用户拍板 spec §7 设计岔路后再进入实施**。本 plan 是拍板后的执行蓝图。
**规模**: M

> ⚠️ 本 Feature 命中「重大架构变更」节点（远程访问 + 安全：触碰 gateway 认证/启动层 + 新增 tailscale 网络编排 + host↔mode 防裸奔校验）→ 强制 **Codex（`codex review --base`，scoped 小 diff）+ Opus 双评审 panel**；每 Phase 后 0 regression vs master 1e64ecd3 baseline；e2e_smoke 必过；worktree PYTHONPATH 锁禁 uv sync；不主动 push 等用户拍板。

---

## 0. 前置：worktree 验证环境（沿用 M7/F129 教训）

- worktree `.venv` 是 symlink 指向主仓 → **裸 `pytest` 跑的是 master src**。验证本 worktree 代码必须 **PYTHONPATH 锁 worktree**（memory `project_worktree_venv_symlink`），禁 `uv sync`。
- 跑测试用 `uv run --no-sync python -m pytest`（memory `project_pytest_invocation_env_pollution` + `project_precommit_hook_execution_model`：裸 `uv run pytest` 会逃逸 venv，须 `python -m pytest`）。
- **pre-commit hook 跑 master 版本**（非 worktree 编辑）——commit 时 e2e_smoke 用主仓 src；worktree 代码验证靠上面 PYTHONPATH 锁。
- baseline：进 Phase A 前先记 master 1e64ecd3 的 `pytest` passed 数（回归护栏）。

---

## 1. 依赖与顺序

```
Phase 0 研究闭环（已完成：spec + research.md）
        │
        ▼
Phase A  Tailscale serve helper（三态 + DI exec）        ← 核心地基，最独立，最先
        │  tailscale_helper.py（借 openclaw C 清单）
        │
        ├───────────────┬───────────────┐
        ▼               ▼               ▼
Phase B  doctor 2 check   Phase C  log_redaction    （B/C 文件不冲突可并行；
  (check_tailscale_*        tskey 规则               都依赖/独立 A：B 用 helper probe，
   照 F129 范式 + DI)       (~10 行 + 测试)            C 完全独立）
        │               │
        └───────┬───────┘
                ▼
Phase D  host↔mode 校验（跨源 + 启动期 fail-fast + doctor exposure check）  ← 触碰启动路径，较敏感
        │  §E 矩阵 + FrontDoorExposureVerdict + exit(78) 边界
        │
        ▼
Phase E  一键切 front_door 模式命令（编排 A/D，改 config + 调 helper + dry-run）  ← 最后，编排前面所有
        │  octo remote enable/disable/status（或挂 service 组）
        │
        ▼
Phase F  双评审 panel（Codex + Opus，全量回归 0 regression + e2e_smoke）
        │
        ▼
Phase G  文档 + living-docs 漂移闸 + completion-report + handoff(F131)
```

**顺序理由**（先简后难 + 先建 baseline 信心，沿用 F091/F129 Phase 优化经验）：
- **A 最先**：helper 最独立（纯 CLI 交互 + DI exec，无 gateway 依赖），可独立单测建信心。
- **B/C 并行**：都依赖或独立于 A，文件不冲突（doctor.py vs log_redaction.py）。B 用 A 的 probe，C 完全独立。
- **D 中段**：host↔mode 校验触碰 gateway 启动路径（`main.py` 或 harness），敏感（exit(78) 会挡启动）——放 A/B/C 之后，helper + doctor 已稳。
- **E 最后**：切模式命令是编排层（读 config + 调 helper + 触发 serve + 提示），依赖 A（helper）+ D（校验逻辑复用）全部就位。
- **F/G**：双评审 + 文档收尾。

**并行机会**：B（doctor.py）与 C（log_redaction.py）文件不冲突可并行。

---

## 2. Phase 明细

### Phase A — Tailscale serve helper（三态 + DI exec）

**落点**：`packages/provider/src/octoagent/provider/dx/tailscale_helper.py`（新，与 service_manager/doctor 同层）。

**内容**（借 openclaw research.md §C 清单，Python 化）：
- `find_tailscale_binary() -> str | None`：`shutil.which("tailscale")` → macOS 固定路径 `/Applications/Tailscale.app/Contents/MacOS/Tailscale` → `--version` 探活（timeout）。（openclaw `tailscale.ts:35-119`）
- `probe_tailscale_status(exec=...) -> TailscaleProbeResult`：`tailscale status --json`，noisy JSON 容错（截首`{`末`}`），解析 `Self.DNSName`（去尾点）/ `Self.TailscaleIPs[0]` → 返回三态（NOT_INSTALLED / INSTALLED_NOT_READY / READY）+ dns_name + ipv4。DI exec 参数可注入。（`tailscale.ts:121-162` + `tailscale-status.ts:38-46`）
- `enable_tailscale_serve(port, exec=...) -> TailscaleServeResult`：`tailscale serve --bg --yes <port>`；捕获非零退出/permission/HTTPS-missing → 结构化 error_code + hint（去 admin console 启用 HTTPS Certificates）；**默认不 sudo**（遇 permission 给手动命令，不 `sudo -n`）。返回 published_url（`https://<dns_name>/`）。（`tailscale.ts:270-285`）
- `disable_tailscale_serve(exec=...)`：`tailscale serve reset`（供 E 切回本机模式用）。（`tailscale.ts:380-391`）
- 数据类：`TailscaleState` enum / `TailscaleProbeResult` / `TailscaleServeResult`（spec §5）。
- **降级红线**（Constitution #6）：所有函数 binary 缺失/status 失败返回三态或 error 对象，**不抛未捕获异常**。

**测试**（`packages/provider/tests/dx/test_tailscale_helper.py`）：DI 注入 fake exec（记录 argv + 返回预设 stdout），断言：
- 命令 argv 正确（`serve --bg --yes <port>` / `serve reset` / `status --json`）。
- noisy JSON 前后垃圾行仍解析出 DNSName。
- binary 不存在 → NOT_INSTALLED；status 失败 → INSTALLED_NOT_READY；有 DNSName → READY。
- **零真实 tailscale 调用**（hermetic，照 openclaw `server-tailscale.test.ts` mock exec 范式）。
- **红线机械断言**：enable 失败路径不出现 `sudo` token。

**验收**：`uv run --no-sync python -m pytest packages/provider/tests/dx/test_tailscale_helper.py` 全 PASS + 全量 0 regression。

---

### Phase B — doctor 2 check（依赖 A）

**落点**：`packages/provider/src/octoagent/provider/dx/doctor.py`（append，照 F129 `check_service_status`/`check_sleep_settings` 范式，research.md §doctor 勘察）。

**内容**：
- `check_tailscale_connectivity(self) -> CheckResult`：DI `tailscale_probe`（默认 `probe_tailscale_status`）→ 三态映射：NOT_INSTALLED → SKIP（RECOMMENDED，非 blocking）；INSTALLED_NOT_READY → WARN + `tailscale up` 指引；READY → PASS + dns_name/ipv4。异常软化 SKIP。
- `check_front_door_exposure(self) -> CheckResult`：跨源读 `OCTOAGENT_HOST`（env）+ `config.front_door.mode` → 按 spec §E 矩阵判定 → safe=PASS / warn=WARN / reject=FAIL（此处 doctor 报 FAIL 但**不 exit**，exit 在 Phase D 启动期）+ fix_hint。异常软化 SKIP。
- DoctorRunner `__init__` 加 DI 缝 `tailscale_probe: Callable | None = None`（默认 `probe_tailscale_status`）。
- `run_all_checks` append 两 check。

**测试**（`packages/provider/tests/dx/test_doctor_tailscale.py`）：照 F129 `test_doctor_service_checks.py` FakeStatusManager 范式——注入 fake probe，断言三态→CheckStatus 映射；未装不 blocking（`_compute_overall != FAIL`）。
- **只读红线**（FR-D3）：机械断言 doctor 的 tailscale check 只走只读命令（`status`），无 `serve`/`up`/`sudo`（照 F129 `test_darwin_only_runs_readonly_commands` 白名单范式）。

**验收**：test PASS + 全量 0 regression。

---

### Phase C — log_redaction tskey（独立，可与 B 并行）

**落点**：`packages/core/src/octoagent/core/log_redaction.py`（`_RULES:127-166` append 一条）。

**内容**：加 tskey 前缀规则——probe `"tskey-" in text`，pattern 匹配 `tskey-(auth|api|client)-XXXX-YYYY` 形态，replace mask（保前缀 + mask 尾部，同 `sk-` 的 `_PREFIX_KEY_PATTERN` 同构；可复用或平行新增 `_TSKEY_PATTERN`）。

**测试**（`packages/core/tests/.../test_log_redaction.py` 加用例）：`tskey-auth-abc-def123` / `tskey-api-...` / `tskey-client-...` 被 mask；非 tskey 文本不误伤。

**验收**：test PASS + 全量 0 regression。

---

### Phase D — host↔mode 校验（防裸奔，触碰启动路径）

**落点**（两处）：
1. **校验纯函数**：`dx/` 或 gateway 共享——`validate_front_door_exposure(host: str, mode: str) -> FrontDoorExposureVerdict`（spec §E 矩阵，纯函数好测）。
2. **启动期接入**：gateway app 构造路径（`apps/gateway/.../main.py` `create_app` 或 harness bootstrap）——读 `OCTOAGENT_HOST`（env，默认 127.0.0.1）+ `config.front_door.mode` → 调纯函数 → verdict=reject（§7 岔路⑤拍板的裸奔组合）时 `sys.exit(78)` + 清晰错误；verdict=warn 时 log 强警告放行。

**关键决策依赖**（进 Phase D 前必须已拍板）：
- **spec §7 岔路⑤**：哪些组合 exit(78) vs 仅警告。**默认建议**：host 非 loopback + mode=loopback → exit(78)；serve+loopback / 0.0.0.0+bearer → 警告。
- exit(78) 命中 systemd `RestartPreventExitStatus=78`（handoff §2.4）——确定性配置错不刷重启熔断。launchd 侧无等价字段但 exit 会让 `KeepAlive{SuccessfulExit:False}` 不重启正常退出（需确认 exit 78 被 launchd 视作"非成功"→ 会重启？**实施时验证 launchd 语义**：可能需配合"启动期错误写 err.log + 退出"而非依赖 launchd 不重启）。

**测试**（`packages/provider/tests/.../test_host_mode_validation.py` + gateway 侧启动测试）：
- 纯函数矩阵测试（spec §E 全组合 → verdict）。
- 启动期：mock env + config，断言裸奔组合触发 exit(78)（`pytest.raises(SystemExit)` code==78）；警告组合不 exit。
- **校验只读**（FR-C4）：断言校验不改 config/env/系统。

**验收**：test PASS + 全量 0 regression + **e2e_smoke 必过**（校验接入启动路径，e2e_smoke 会跑真实 create_app——确认默认 host=127.0.0.1+mode=loopback 是 safe 不误 exit）。

> ⚠️ **Phase D 最高危**：校验接入启动路径，若默认组合误判 reject 会让 gateway 起不来（连本机都用不了）。必须确保 baseline 默认（127.0.0.1 + loopback）= safe，且 e2e_smoke 通过验证。

---

### Phase E — 一键切 front_door 模式命令（编排 A/D）

**落点**：`packages/provider/src/octoagent/provider/dx/`——新命令组 `remote`（或挂 `service` 组，spec §5 待定；**建议独立 `remote` 组更清晰**）+ `cli.py` `main.add_command(remote_group)`。

**内容**（Click 框架，照 F129 `service_commands.py` 范式）：
- `octo remote enable`：①调 helper `probe_tailscale_status` 检三态；②未就绪 → 打印可操作指引（装/`up`/HTTPS）**退出不改配置**；③就绪 → 切 `front_door.mode=bearer`（改 `octoagent.yaml` 或 env override，plan 内定：**建议改 yaml**，持久且用户可见；env override 是运行时临时）；④检 `OCTOAGENT_FRONTDOOR_TOKEN` 未设 → 提示设 token（强 token 生成建议 + 走 .env）；⑤调 helper `enable_tailscale_serve(port)`；⑥打印 published URL + 手机访问指引 + 下一步（重启服务生效：`octo service install`/`restart`）。`--dry-run` 预览。
- `octo remote disable`：切回 `mode=loopback` + `disable_tailscale_serve`（serve reset）+ 提示重启。`--dry-run`。
- `octo remote status`：显示当前 mode + tailscale 三态 + 若 serve 已开显示 URL + host↔mode verdict。
- **幂等**（FR-B3）：重复 enable 结果一致。
- **host 处理**：spec §0.3 决策 serve 保持 127.0.0.1 → **enable 默认不改 host**（不碰 descriptor environment_overrides 的 OCTOAGENT_HOST）；仅当用户显式选 bind-tailnet 备选（岔路④(b)）才改 host + 换绑 verify_url（handoff §2.3）。v0.1 默认路径不涉 host 改动。

**测试**（`packages/provider/tests/dx/test_remote_commands.py`）：照 F129 `test_service_commands.py` FakeServiceManager monkeypatch 范式——mock helper（probe/serve）+ mock config 读写，`CliRunner().invoke`，断言：
- 三态分支（未就绪不改配置；就绪切 bearer + 调 serve）。
- `--dry-run` 不改文件。
- 幂等。
- 输出含 URL + token 提示。

**验收**：test PASS + 全量 0 regression + e2e_smoke 过。

---

### Phase F — 双评审 panel（Codex + Opus）

- **Codex**：`codex review --base master`（scoped diff）—— 挑战：serve 命令注入是否安全（argv 拼接）、host↔mode 校验是否有绕过、exit(78) 边界是否合理、bearer 切换是否真规避 loopback+XFF 冲突、secret（tskey）是否零落盘。
- **Opus**：spec 对齐专项 review —— §0.2 loopback+serve 冲突是否真解决、§E 矩阵是否覆盖全组合、identity-header out-of-scope 是否清晰、H1/Constitution 守界。
- **两者分歧项**列"必须人裁"清单回主 session。
- 处理到 **0 HIGH 残留**。全量回归 0 regression vs master baseline + e2e_smoke 8/8。

---

### Phase G — 文档 + 收尾

- **living-docs 漂移闸**：更新 `docs/blueprint/milestones.md` M8 节 F130 状态 + `docs/codebase-architecture/`（新增或扩 `remote-access.md` / `harness-and-context.md`，记 front_door 模式 + Tailscale 编排 + host↔mode 校验矩阵）+ `deployment-and-ops.md`（远程部署形态：serve + bearer + 手机访问步骤）。
- **completion-report.md**：对照 spec Phase 列表标"实际做了 vs 计划" + Codex/Opus finding 闭环表 + 已知 limitations（PWA / SPA 鉴权 / query token 泄露 / identity-header 未做）。
- **handoff.md**：给 F131（Telegram 可靠性）——F130 触碰的 gateway 启动路径 + doctor 扩展点 + 若有渠道相关接线。
- **user-guide.md**：手机远程访问上手（装 Tailscale → `tailscale up` → 启用 MagicDNS/HTTPS → `octo remote enable` → 设 token → 手机开 URL）。

---

## 3. 关键不变量（每 Phase 守）

1. **认证收敛单一入口**：F130 不在 `FrontDoorGuard` 之外加认证旁路（Constitution #10）。
2. **secret 零落盘**：tskey 只在 `~/.octoagent/.env`，不进 plist/unit/config/LLM 上下文（`_is_sensitive_env_key` 含 "KEY" 天然拦 + redaction + env-only 路径）。
3. **零 sudo / 零系统设置写**（延续 F129 红线）：helper/doctor/命令**绝不** `sudo`、不代启用 HTTPS、不改电源——机械断言测试守。
4. **降级不阻塞**（Constitution #6）：tailscale 缺失/失败 → 三态/SKIP，不崩主流程。
5. **默认组合 safe**：127.0.0.1 + loopback = safe，Phase D 校验不误 exit（e2e_smoke 守）。
6. **0 regression vs master 1e64ecd3** + e2e_smoke 8/8，每 Phase 后。
7. **不主动 push**——等用户拍板归总报告后决定。

---

## 4. Phase 拍板前置（进 Phase 前必须已定的 spec 岔路）

| Phase | 前置拍板（spec §7）|
|-------|-------------------|
| A | 岔路①（自动化程度）——决定 helper 是否含 enable_serve 接管 |
| D | **岔路⑤（fail-fast 边界）**——决定哪些组合 exit(78)；岔路④（host）——决定是否涉 host 改动 |
| E | 岔路②（bearer）——决定切 bearer（已强推荐）；岔路③（PWA）——确认 v0.1 纯浏览器不做 PWA |

→ **建议用户一次性拍板全部 5 条岔路**，再进实施（避免 Phase 中途卡）。
