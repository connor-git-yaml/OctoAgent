# Feature Specification: F130 安全远程触达（Tailscale）

**Feature ID**: F130
**Slug**: tailscale
**Milestone**: M8（部署与日常使用）— P0，波次 2（依赖 F129，F129→F130 严格串行）
**规模**: **M**（复核见 §8）
**Status**: **设计先行草案 v0.1**（研究闭环，spec/plan 待用户拍板 §7 设计岔路后进入实施）
**Base**: master 1e64ecd3 / 分支 `feature/130-tailscale`（worktree `.claude/worktrees/F130-tailscale/`）
**上游依据**: `CLAUDE.local.md` §M8 + `docs/blueprint/milestones.md` M8 节（line 580-610）+ 本目录 `research.md`（含 file:line 证据）+ `.specify/features/129-service-foundation/handoff.md`

---

## 0. 设计基础说明（实测核实，均带证据，详见 research.md）

### 0.1 ★ 核心定位：F130 是「Tailscale serve 编排 + front_door 模式切换 + host↔mode 防裸奔校验」，不是造认证系统

- 让 Connor **手机经互联网（Tailscale WireGuard 私网）安全访问完整 Web UI**——不公网暴露、不反代、契合 §0 单用户锁定。
- **front_door 认证已实现 85%**（research.md §A.1）：三态（loopback/bearer/trusted_proxy）100% 实现 + 测试，19 router 全覆盖 `dependencies=protected`，SSE query-token + 常数时间比对齐全。**F130 绝不重造认证**——F130 做的是"网络层编排（serve）+ 选对模式 + 防误配校验 + 诊断"。

### 0.2 ★★ 最高优先级技术约束：loopback 模式与 Tailscale serve 的 X-Forwarded-* 互斥（research.md §A.2）

- `frontdoor_auth.py:55-64`：loopback 模式下，请求来自 127.0.0.1 **且带任一 proxy forwarding header**（`x-forwarded-for` 等）→ **403 拒绝**。
- Tailscale serve **从本机 loopback 代理进来**（gateway 保持绑 127.0.0.1），且**可能注入 `X-Forwarded-*`**（research.md §B.2：openclaw 观察到 serve 带 XFF，但 Tailscale 官方未把 XFF 列为稳定承诺）。
- **后果**：若 serve 场景保持 `mode=loopback`，手机经 Tailscale 访问会因 XFF 被 front_door **全部 403**（功能 100% 不通）。
- **F130 硬结论**（非偏好，是约束）：**Tailscale serve 触达必须配 `mode=bearer`**（bearer 不检查 forwarding header）。这是 §7 岔路②的技术内核，spec 必须显式写明，否则最容易踩的坑、后果最严重。

### 0.3 ★ 最小暴露面 = serve + host 保持 127.0.0.1（research.md §B.6 / §D.4）

- serve 模式下 gateway **保持绑 127.0.0.1**，Tailscale daemon 从 loopback 代理 + 终止 TLS（tailnet 证书）→ 手机访问 `https://<magicdns>/`。**gateway 端口不监听任何外部网卡（连 tailnet 网卡都不监听）= 暴露面最小**。
- 对比：绑 `0.0.0.0` = 监听全部网卡（含物理/WiFi）暴露面最大；绑 tailnet IP（100.x）= 监听 tailnet 网卡（比 0.0.0.0 小但失去 HTTPS + loopback 不可用）。
- **F130 采 serve + host 127.0.0.1**。→ 这直接定义 host↔mode 校验的"安全基线"：serve 时 host 仍 loopback；绑 0.0.0.0 是危险误配。

### 0.4 实测核实的可复用资产（勿重造，research.md §A/§F）

| 资产 | 位置（证据）| F130 如何用 |
|------|------------|-------------|
| front_door 三态认证 `FrontDoorGuard` | `apps/gateway/.../services/frontdoor_auth.py`（全局 dependency，`main.py:349-384`）| **复用 bearer 模式**，不改主体 |
| `FrontDoorConfig` schema + CIDR 校验 + env 覆盖 | `services/config/config_schema.py:329-393` | 切模式改的是这里的 `mode`（+ env override）|
| F129 service descriptor `environment_overrides` + `install --force` 自愈重写 + `verify_url` | `core/models/update.py:87-100` + `dx/service_manager.py:846-892` | 切 host（若需）+ verify_url 换绑；`OCTOAGENT_*` 前缀 + 非敏感词过滤（防 secret 落盘）|
| F129 doctor 框架 `CheckResult{name,status,level,message,fix_hint}` + `run_all_checks` + DI 缝 | `dx/doctor.py:50-132` + `dx/models.py:17-40` | append `check_tailscale_*`，DI 缝加 `tailscale_probe` |
| F129 `octo service` CLI group（Click）+ `octo logs` | `dx/service_commands.py:82-184` + `dx/cli.py:81-102` | 新命令挂载模式参考 |
| log_redaction `_RULES` 框架 | `packages/core/.../log_redaction.py:127-166` | 补 `tskey-` 前缀一条（顺手，§A.6）|
| openclaw tailscale 完整实现（binary 定位/status 解析/serve 命令/三态）| `_references/opensource/openclaw/src/infra/tailscale.ts` + `gateway/server-tailscale.ts` + `shared/tailscale-status.ts` | helper 设计蓝本（research.md §C 清单）|

### 0.5 实测核实的真实缺口（F130 新建，research.md §F）

1. **无 Tailscale serve 编排 helper**（全仓 grep tailscale → 0 生产代码，仅 _references）。
2. **无 `octo` 一键切 front_door 模式命令**（切模式现在只能手改 yaml + 重启，无引导 + 无 dry-run）。
3. **host↔mode 校验缺失**（research.md §A.3：grep 0 命中；setup_review 只检 trusted_proxy cidrs）——**防裸奔核心主责**。
4. **doctor 无 tailscale check**（无远程触达诊断）。
5. **log_redaction 无 tskey 规则**（handoff §3 已提醒补）。

### 0.6 三条哲学守界（H1 / Constitution，research.md §G）

- **H1**：F130 是运维/网络地基，**不碰 Agent 决策环**——主 Agent 仍唯一 user-facing speaker；手机访问的是既有 Web UI（H1 不变）。
- **Constitution #5**：Tailscale key（tskey）走 `~/.octoagent/.env`，**绝不进 plist/unit/config yaml/LLM 上下文**（tskey 含 "KEY" → F129 `_is_sensitive_env_key` 天然拦截进 descriptor，双保险）。log_redaction 补 tskey 前缀防落盘。
- **Constitution #7（User-in-Control）**：serve 自动化只在就绪态接管，未就绪给指令**不代跑**；**绝不静默改系统**（不自动 sudo、不代启用 HTTPS Certificates、不改电源设置——延续 F129 红线）；切 front_door 模式是可逆运维动作 + dry-run 预览。
- **Constitution #6（降级）**：tailscale binary 不存在 / status 失败**不得阻塞主流程**——helper 返回三态由调用方决策；doctor check 失败降级 SKIP。
- **Constitution #10（Policy-Driven）**：认证仍收敛在单一 `FrontDoorGuard`，F130 不加认证旁路。

---

## 1. 目标（Why）

让 Connor 能从**手机经互联网安全打开完整 Octo Web UI**（不只是 Telegram 文本），走 Tailscale serve 私网隧道——零证书配置、不公网暴露、依托 F129 已常驻的服务。同时**防止误配裸奔**（绑错 host + 认证模式不匹配导致 API 无保护或功能不通）。

**用户可感知的改变**：
- `octo remote enable`（或等价命令）一步 → 检测 Tailscale 三态 → 就绪则跑 serve + 切 bearer 模式 → 打印手机可访问的 `https://<magicdns>/` URL + 提示设 token。
- `octo doctor` → 明确告诉"Tailscale 通不通 / 手机能不能访问 / 你的 host+mode 组合安不安全"。
- 误配（如绑 0.0.0.0 + loopback 模式）→ 启动期/诊断期被明确拦下或警告，不再无声裸奔。

---

## 2. 范围声明

### 2.1 In Scope（v0.1，§7 决策收窄）

1. **Tailscale serve helper（三态：检测/建议/接管）**：Python 版，DI exec。
   - 三态检测（`not_installed` / `installed_not_ready` / `ready`，research.md §B.5）：多策略 binary 定位（`shutil.which` + macOS 固定路径 + 探活）+ `status --json` 解析（Self.DNSName/IPs，noisy JSON 容错）。
   - 建议：未装/未就绪时给**可操作指令**（装 Tailscale / `tailscale up` / 启用 MagicDNS+HTTPS Certificates 的 admin console 链接），**不代跑**。
   - 接管：就绪态跑 `tailscale serve --bg --yes <port>`（非交互后台）；捕获失败（如 HTTPS 未启用）给精确提示；返回 published URL（`https://<magicdns>/`）。
   - **默认不自动 sudo**（遇权限失败给手动命令，延续 F129 零 sudo 红线）。
2. **`octo` 一键切 front_door 模式命令**（loopback ↔ bearer）：
   - 改 `octoagent.yaml` 的 `front_door.mode`（或 env override，二选一由 plan 定）。
   - **serve 场景切 bearer**（§0.2 硬约束）；提示用户设 `OCTOAGENT_FRONTDOOR_TOKEN`（生成强 token 建议，token 走 .env 不落 config）。
   - **dry-run 预览**（Constitution #7）+ 幂等。
   - 若涉改 host（本 spec 默认 serve 保持 127.0.0.1，**不改 host**——见 §0.3；若用户选 bind-tailnet 方案才改 host，属岔路④备选）。
3. **host↔mode 校验（防裸奔核心）**（research.md §E 判定矩阵）：
   - 跨源读 `OCTOAGENT_HOST`（env，默认 127.0.0.1）+ `config.front_door.mode`。
   - **启动期 fail-fast**：命中"确定裸奔"组合（如 host=0.0.0.0 + mode=loopback：既暴露全网卡 loopback 又靠 source IP 挡不住外网带 XFF）→ 清晰错误 + `sys.exit(78)`（命中 systemd `RestartPreventExitStatus=78` 熔断，handoff §2.4）。**具体哪些组合 fail-fast vs 仅警告 = §7 岔路子决策**。
   - **doctor check**（跨源读，报警不阻塞）：所有"⚠️/❌"组合给 WARN/FAIL + fix_hint（纵深，即使启动期没拦也能诊断）。
4. **doctor 扩展远程健康检查**：
   - `check_tailscale_connectivity`（tailscale 三态 + tailnet 连接状态，DI `tailscale_probe`，异常软化 SKIP）。
   - `check_front_door_exposure`（host↔mode 组合安全性，§E 矩阵，跨源读）。
5. **log_redaction 补 tskey 规则**（顺手，research.md §A.6）：`_RULES` 加 `tskey-` 前缀 mask + 测试。

### 2.2 Out of Scope（v0.1 明确不做，记 limitation / 归后续）

- **Tailscale funnel（公网）**：违 §0 单用户 + 用户"不公网暴露"拍板。**永不做**（research.md §B.1）。
- **identity-header 认证模式**（`Tailscale-User-*` 免 token）：research.md §B.3 有"同机进程伪造"威胁 + 依赖 XFF/whois 不确定行为。**v0.1 用 bearer**，identity-header 记为**可选未来增强**（spec §附录 A 记威胁模型 + whois 验证要求，供未来立项）。
- **手机 PWA（manifest/service worker/add-to-home-screen）**：属前端体验增强，不同关注点（research.md §D.3）。纯浏览器访问 `https://<magicdns>/` 已满足用户拍板目标。**归 M8 后续独立小 Feature**。
- **SPA 静态资源鉴权加固**（`main.py:391` mount `/` 绕过 front_door）：Tailscale 私网场景风险极低（只泄露前端 bundle 非凭证）；强制加鉴权有"登录页自锁死"风险。**记 limitation + 可选**（research.md §A.4）。
- **named Tailscale Service（`svc:xxx`）**：需 tagged node + admin 审批，单用户过重（research.md §C 剔除）。v0.1 用设备 hostname serve。
- **bearer 加固（限流 / SSE ticket 化 / query token 泄露收敛）**：既定 **F134**（M8 P2）。
- **Windows / Linux 桌面深度适配**：F130 v0.1 **macOS 优先**（Connor 用 Mac mini）；Linux serve 命令兼容但不做深度验证；Windows 范围外（对齐 F129 平台边界）。

---

## 3. 功能需求（FR）

> 每条附 `[@test]` 绑定意向（AC↔test 显式绑定，SDD 强化）。测试路径为建议，实施时确认。

### FR-A：Tailscale serve helper（三态）
- **FR-A1**：`find_tailscale_binary()` 多策略定位 tailscale CLI（`shutil.which` → macOS 固定路径 `/Applications/Tailscale.app/Contents/MacOS/Tailscale` → 探活），找不到返回 None。`[@test test_tailscale_helper.py::test_find_binary_*]`
- **FR-A2**：`probe_tailscale_status()` 跑 `tailscale status --json`，noisy JSON 容错解析（截首`{`末`}`），返回三态 + Self.DNSName（去尾点）+ TailscaleIPs。DI exec 可注入。`[@test ...::test_probe_status_*]`
- **FR-A3**：`enable_tailscale_serve(port)` 跑 `tailscale serve --bg --yes <port>`；捕获失败（HTTPS 未启用等）返回结构化错误 + 可操作提示；**默认不 sudo**。`[@test ...::test_enable_serve_*]`
- **FR-A4**：`disable_tailscale_serve()` 跑 `tailscale serve reset`（清理，供切回本机模式用）。`[@test ...::test_disable_serve_*]`
- **FR-A5**：helper 所有函数在 binary 不存在 / status 失败时**优雅降级**（返回三态/错误对象，不抛未捕获异常阻塞调用方）。Constitution #6。`[@test ...::test_degrade_*]`

### FR-B：一键切 front_door 模式命令
- **FR-B1**：新增 CLI 命令切 `front_door.mode`（loopback ↔ bearer），改 `octoagent.yaml`（或 env override，plan 定）。`[@test test_remote_commands.py::test_switch_mode_*]`
- **FR-B2**：切到 bearer 时，若 `OCTOAGENT_FRONTDOOR_TOKEN` 未设，**提示用户设 token**（给强 token 生成建议 + 强调走 .env 不落 config）；不自动写 token 到任何文件。`[@test ...::test_bearer_token_prompt]`
- **FR-B3**：`--dry-run` 预览改动（Constitution #7）；重复执行幂等。`[@test ...::test_dry_run / test_idempotent]`
- **FR-B4**：命令输出**手机可访问 URL**（serve 就绪时 `https://<magicdns>/`）+ 下一步指引。`[@test ...::test_output_url]`

### FR-C：host↔mode 校验（防裸奔）
- **FR-C1**：跨源读 `OCTOAGENT_HOST`（env，默认 127.0.0.1）+ `front_door.mode`，按 research.md §E 矩阵判定 安全/警告/拒绝。`[@test test_host_mode_validation.py::test_matrix_*]`
- **FR-C2**：**启动期 fail-fast**——命中"确定裸奔"组合（§7 拍板的清单，如 host=0.0.0.0 + mode=loopback）→ 记录清晰错误 + `sys.exit(78)`。`[@test ...::test_fail_fast_exit78]`
- **FR-C3**：非裸奔但有风险组合（如 serve+loopback 功能不通）→ **强警告不 exit**（记录 + 允许启动，doctor 兜底）。`[@test ...::test_warn_no_exit]`
- **FR-C4**：校验**只读、绝不改配置/系统**（诊断性），失败降级不阻塞（Constitution #6）。`[@test ...::test_validation_readonly]`

### FR-D：doctor 远程健康检查
- **FR-D1**：`check_tailscale_connectivity`——三态 + tailnet 连接，DI `tailscale_probe`，PASS/WARN/SKIP + fix_hint，异常软化 SKIP，RECOMMENDED 级（未装不 blocking）。`[@test test_doctor_tailscale.py::test_connectivity_*]`
- **FR-D2**：`check_front_door_exposure`——host↔mode 组合安全性（§E 矩阵），跨源读，危险组合 WARN/FAIL + fix_hint。`[@test ...::test_exposure_*]`
- **FR-D3**：两 check 挂 `run_all_checks`；**只读红线**（机械断言 check 只跑只读 tailscale 命令，无 `serve`/`up`/`sudo` 写命令 —— 照 F129 sleep probe 红线测试范式）。`[@test ...::test_doctor_tailscale_readonly_redline]`

### FR-E：log_redaction tskey
- **FR-E1**：`_RULES` 加规则 mask `tskey-auth-*` / `tskey-api-*` / `tskey-client-*`（前缀 + 尾部 mask，同 `sk-` 规则同构）。`[@test test_log_redaction.py::test_tskey_*]`

---

## 4. 验收标准（AC，P1 故事）

> AC↔test 显式绑定（verify 阶段 grep + `pytest -k` 机械校验存在且 PASS）。

- **AC-1（手机触达主路径）**：Tailscale 就绪 + 跑切换命令 → serve 启用 + mode=bearer + 输出 `https://<magicdns>/` URL；手机浏览器带 token 访问该 URL 能打开完整 Web UI（**人工验收**，同 F129 AC-1 launchd 人工验收范式——hermetic 红线下真机集成不进 CI）。`[@test 人工 + test_remote_commands.py::test_enable_full_flow（stub serve）]`
- **AC-2（loopback↔serve 冲突防护）**：serve 场景下 mode 若仍是 loopback，切换命令/doctor **明确警告**"loopback 模式 + serve 会因 X-Forwarded 拒绝，请用 bearer"（§0.2）。`[@test test_host_mode_validation.py::test_serve_loopback_warns]`
- **AC-3（防裸奔 fail-fast）**：配 host=0.0.0.0 + mode=loopback → 启动期 exit(78) + 错误信息含"暴露面/裸奔"关键词。`[@test test_host_mode_validation.py::test_naked_exposure_fail_fast]`
- **AC-4（三态检测）**：未装 tailscale → helper 返回 `not_installed` + doctor SKIP + 装 Tailscale 指引；装了未登录 → `installed_not_ready` + `tailscale up` 指引。`[@test test_tailscale_helper.py::test_three_states / test_doctor_tailscale.py::test_not_installed_skip]`
- **AC-5（不代跑不改系统）**：HTTPS Certificates 未启用时接管 serve 失败 → 给"去 admin console 启用"提示，**不代启用**；helper/doctor **零 sudo、零系统设置写**（机械断言）。`[@test test_tailscale_helper.py::test_serve_https_missing_hint / FR-D3 红线]`
- **AC-6（secret 不落盘）**：tskey 出现在日志 → 被 redaction mask；tskey 不进 plist/unit/config（`_is_sensitive_env_key` 拦截 + env-only 路径验证）。`[@test test_log_redaction.py::test_tskey_masked / test_service_manager 已有 sensitive key 测试]`
- **AC-7（幂等 + dry-run）**：切模式命令重复执行结果一致；`--dry-run` 不改任何文件。`[@test test_remote_commands.py::test_idempotent / test_dry_run]`

---

## 5. 数据模型 / 接口草案（供 plan 细化）

- `TailscaleState` enum：`NOT_INSTALLED / INSTALLED_NOT_READY / READY`。
- `TailscaleProbeResult`（doctor DI 用）：`supported: bool / state: TailscaleState / dns_name: str | None / ipv4: str | None / detail: str`。
- `TailscaleServeResult`：`ok: bool / published_url: str | None / error_code: str | None / hint: str | None`。
- `FrontDoorExposureVerdict`：`verdict: Literal["safe","warn","reject"] / host: str / mode: str / reason: str / fix_hint: str`。
- helper 模块落点：`packages/provider/src/octoagent/provider/dx/tailscale_helper.py`（新，与 service_manager/doctor 同层，供 CLI + doctor 复用）。
- 校验落点：host↔mode 校验函数放 `dx/` 或 gateway 启动路径（plan 定：启动期在 `apps/gateway/.../main.py` 或 harness bootstrap；doctor 侧在 doctor.py check）。

---

## 6. 依赖与风险

- **依赖**：F129（已完成）——service descriptor / doctor 框架 / CLI group / log_redaction 下沉。
- **风险**（research.md §H）：
  1. loopback↔serve XFF 冲突（§0.2）——最严重，spec 已显式化。
  2. X-Forwarded-* 官方不承诺——认证不依赖 XFF（走 bearer 规避）。
  3. host↔mode fail-fast exit(78) 边界（§7 拍板）——太激进挡启动 / 太宽松漏网。
  4. query token 泄露 Tailscale 日志——记 limitation，F134 收。
  5. HTTPS Certificates 前置——helper 捕获失败给提示。
  6. 真机集成不进 CI（hermetic 红线）——AC-1 人工验收。

---

## 7. 设计岔路（回用户拍板，每条：选项 + 推荐 + 理由）

> 详细技术依据见 research.md §D。此处给决策清单。

### 岔路①：Tailscale 自动化程度
- **(a)** 纯检测 + 打印手动命令 / **(b)** 检测 + 就绪时自动跑 serve，未就绪给指令 / **(c)** 全自动含引导启用 HTTPS。
- **推荐 (b)**。理由：符合 prompt 明确要的"检测/建议/接管三态"；就绪态自动化省手动、失败可控；未就绪不越界（HTTPS 启用给链接不代跑，Constitution #7 + F129"检测报警不静默改系统"）。

### 岔路②：Tailscale 模式下 bearer 还要不要？
- **(a)** serve + bearer（token 认证，纵深）/ **(b)** serve + identity-header 模式（免 token）/ **(c)** serve + loopback（私网已挡）。
- **推荐 (a)**。理由：**(c) 技术上不可行**（§0.2 loopback+serve XFF 冲突，硬约束非偏好）；bearer 已 100% 实现零新代码风险；纵深防御（token 是 tailnet ACL 之外第二道闸）；不依赖 Tailscale header 不确定行为；identity-header (b) 有"同机进程伪造"威胁（research.md §B.3），作可选未来增强不作 v0.1 主路径。

### 岔路③：手机 PWA 要不要一并做？
- **(a)** 纯浏览器 v0.1 / **(b)** 一并加 PWA manifest + service worker。
- **推荐 (a)**。理由：serve 已给 HTTPS，浏览器直接可用完整 UI（用户目标已达成）；PWA 是前端体验增强属不同关注点，涉前端构建（manifest/SW/图标/离线策略）+ 与 SSE/token 交互需单独设计，塞进 F130 范围爆炸。→ 归 M8 后续独立小 Feature。

### 岔路④：host 绑定 0.0.0.0 vs tailscale IP vs 127.0.0.1？
- **(a)** serve + host 保持 127.0.0.1 / **(b)** 绑 tailnet IP 100.x（bind-tailnet 方案）/ **(c)** 0.0.0.0。
- **推荐 (a)**。理由：serve 从 loopback 代理无需监听外部网卡——暴露面最小（连 tailnet 网卡都不监听）；保留 HTTPS（serve 终止 TLS）；本机 loopback 仍可用（调试友好）；(c) 0.0.0.0 是最大暴露面 = host↔mode 校验要防的危险组合。→ **v0.1 不改 host**（serve 保持 127.0.0.1），(b) bind-tailnet 仅作备选记录。

### 岔路⑤（新增，§0.2 派生）：host↔mode 校验的 fail-fast 边界
- 哪些组合**启动期 exit(78) 拒绝** vs 哪些**仅强警告放行**？
- **推荐**：
  - **exit(78) 拒绝**：host 非 loopback（0.0.0.0 / LAN IP）+ mode=loopback —— 既暴露又靠 source IP 挡不住外网带 XFF 的请求 = 真裸奔风险。
  - **强警告放行**：serve + mode=loopback（功能不通但不裸奔，源 IP 仍 loopback）；host=0.0.0.0 + mode=bearer（有 token 但暴露面大，建议改 serve）。
  - 理由：exit 只对"确定裸奔"（认证挡不住 + 暴露面大）用，避免误挡；"功能不通/暴露大但有认证"降警告让用户自决（Constitution #7）。**请用户确认此边界**。

---

## 8. 规模复核（M，与 milestones 标注一致）

- **helper**（tailscale_helper.py，~250-350 行）：借 openclaw 完整参考，主要是 Python 化 + DI exec + 三态。中等。
- **切模式命令**（~150 行 + 测试）：复用 config 读写 + 调 helper，小。
- **host↔mode 校验**（~100 行 + 矩阵测试）：跨源读 + 判定表 + 启动期接入，中等（启动期接入需谨慎）。
- **doctor 2 check**（~150 行 + 测试）：照 F129 check 范式，小。
- **log_redaction tskey**（~10 行 + 测试）：顺手。
- **总计**：新增 ~700-900 行（含测试），触碰 dx/（helper/doctor/cli/service_commands）+ gateway 启动路径（校验接入）+ core/log_redaction。**规模 M 成立**（比 F129 略小——无 launchd/systemd 真实系统集成的复杂度，认证主体已有）。
- **重大架构变更判定**：远程访问 + 安全 → 命中 Codex + Opus 双评审节点（实施时，非本设计阶段）。

---

## 附录 A：identity-header 模式威胁模型（供未来立项，v0.1 out-of-scope）

若未来做 `mode=tailscale_serve`（`Tailscale-User-*` 免 token 认证）：
- **必须**：①仅接受 loopback 来源；②用 `tailscale whois <x-forwarded-for>` 反查验证身份头与真实 tailnet peer 匹配（research.md §B.3 openclaw 范式）；③先实测目标 Tailscale 版本是否注入 XFF（官方不承诺，§B.2）。
- **威胁**：同机恶意进程可直连 loopback 后端伪造 `Tailscale-User-Login` 头（两者 source IP 都 127.0.0.1，后端无法区分"来自 Serve"vs"来自恶意进程"）。openclaw 官方免责："assumes gateway host is trusted; if untrusted local code may run, require token/password instead"。
- **对单用户 Mac mini**：威胁可接受但非零 → 这正是 v0.1 选 bearer（token 门槛 + 不依赖 XFF/whois）的理由。
