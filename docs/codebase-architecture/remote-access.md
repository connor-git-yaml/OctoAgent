# Remote Access（F130 安全远程触达 · Tailscale）

> 让 Connor **手机经互联网（Tailscale WireGuard 私网）安全访问完整 Web UI**——不公网暴露、不反代、
> 契合 Blueprint §0 单用户锁定。**认证零重造**（复用 F084 `FrontDoorGuard` 三态），F130 只做
> 「网络层编排（serve）+ 选对模式 + 防裸奔校验 + 诊断」。依赖 F129 常驻服务地基。
> 状态：**已完成**（Codex 4 轮 + Opus 自审 0 HIGH）。

## 1. 核心定位：编排 + 校验，不是造认证

front_door 认证在 master 已是完整三态实现（F084），**F130 绝不重造**：

| 层 | F130 做什么 | 落点 |
|----|------------|------|
| **认证** | 复用既有 `FrontDoorGuard`（loopback/bearer/trusted_proxy） | `gateway/services/frontdoor_auth.py`（不改） |
| **网络编排** | Tailscale serve 三态 helper（检测/建议/接管，DI exec 零 sudo） | `provider/dx/tailscale_helper.py` |
| **模式切换** | `octo remote` 一键切 front_door.mode（loopback↔bearer） | `provider/dx/remote_commands.py` |
| **防裸奔校验** | host↔mode 纯函数判定 + 启动期 fail-fast | `gateway/services/frontdoor_exposure.py` + `gateway/main.py` |
| **诊断** | doctor 2 check（tailscale 连通 + 暴露面） | `provider/dx/doctor.py` |
| **secret 防落盘** | log_redaction 补 tskey 前缀 | `core/log_redaction.py` |

## 2. ★ 最高优先级约束：loopback 模式与 Tailscale serve 的 X-Forwarded-* 互斥

- `frontdoor_auth.py`：**loopback 模式**下，请求即使来自 127.0.0.1，只要带任一 proxy forwarding header
  （`x-forwarded-for` 等）→ **403 拒绝**（防本地代理工具注入 XFF 冒充本机）。
- Tailscale serve **从本机 loopback 代理进来**（gateway 保持绑 127.0.0.1），且**可能注入 `X-Forwarded-*`**。
- **后果**：serve 场景若保持 `mode=loopback`，手机经 Tailscale 访问会被 front_door **全部 403**（功能 100% 不通）。
- **硬结论**（非偏好）：**Tailscale serve 触达必须配 `mode=bearer`**（bearer 分支不检查 forwarding header）。
  `octo remote enable` 因此把 `front_door.mode` 切成 `bearer`，bearer 是 tailnet ACL 之外的纵深第二道闸。

## 3. Tailscale 三态 helper（`tailscale_helper.py`）

DI exec 契约复用 `service_manager.CommandRunner`（`Callable[[list[str], float], CommandOutcome]`），
hermetic 测试零真实 tailscale 调用。

| 函数 | 命令（argv） | 说明 |
|------|-------------|------|
| `find_tailscale_binary()` | — | `shutil.which` → macOS App 固定路径 |
| `probe_tailscale_status()` | `status --json`（**只读**） | noisy JSON 容错 → 三态 + DNSName（去尾点）+ ipv4 |
| `enable_tailscale_serve(port)` | `serve --bg --yes <port>` | 接管；permission→给手动命令（**零 sudo**）；HTTPS 未启用→给 admin 链接（**不代启用**） |
| `disable_tailscale_serve(port=)` | `serve --https=443 off`（传 port）/ `serve reset`（回退） | **只关本功能映射**，不清整机他人 serve 配置 |

**三态**：`NOT_INSTALLED`（binary 找不到）/ `INSTALLED_NOT_READY`（无 Self.DNSName / 未登录）/ `READY`。
**降级红线**（Constitution #6）：所有函数缺失/失败返回三态或结构化 error 对象，绝不抛未捕获异常。

## 4. host↔mode 防裸奔校验（`frontdoor_exposure.py`）

host 与 mode 分属两个来源，校验必须**跨源读**：host 在 uvicorn CLI 层（`OCTOAGENT_HOST` env，**不进
FrontDoorConfig**），mode 在 config 层（`front_door.mode`）。纯函数 `validate_front_door_exposure(host, mode)`
判定矩阵（startup 只知 host+mode，不知 serve 是否启用）：

| host 绑定 | mode | verdict | 理由 |
|-----------|------|---------|------|
| loopback（127.0.0.1/::1/localhost） | 任意 | **safe** | 纯本机 / serve 从 loopback 代理（推荐 loopback+bearer），暴露面最小 |
| 非 loopback（0.0.0.0/LAN/tailnet IP） | loopback | **reject** | 暴露全网卡 + loopback 认证靠 source IP 挡不住带 XFF 的外网 = **裸奔** |
| 非 loopback | bearer / trusted_proxy | **warn** | 暴露面大但有认证；建议改 serve+loopback-host |

**两个消费者**（单一事实源）：
- **启动期 fail-fast**（`main.create_app` → `_enforce_front_door_exposure`）：verdict=reject → stderr 双写错误 +
  `sys.exit(78)`（复用 `service_manager.CONFIG_ERROR_EXIT_CODE`）。host 解析 `_resolve_startup_host`：`OCTOAGENT_HOST`
  env 优先，回退扫 `sys.argv` 的 `--host`（兜住手动 `uvicorn --host 0.0.0.0` 不设 env 的绕过）。
- **doctor check**（`check_front_door_exposure`）：verdict → PASS/WARN/FAIL（**此处 FAIL 不 exit**，纵深诊断）。

**exit(78) 语义（跨平台不对称，已知 limitation）**：systemd `RestartPreventExitStatus=78` 识别此码熔断不刷重启；
launchd 无等价字段会重启（其自身节流兜底），但 F129 err.log 每次清晰暴露误配 → `octo logs` 可诊断。
**默认组合 `127.0.0.1+loopback`=safe**，e2e_smoke 守（否则 gateway 起不来连本机都用不了）。

**校验只读 + 判定异常保守放行**（不因校验 bug 挡启动，FR-C4）。

## 5. `octo remote` 命令（`remote_commands.py`）

| 命令 | 行为 |
|------|------|
| `octo remote enable` | 探测三态 → 未就绪打印指引**不改配置** → 就绪：**先跑 serve → token 未设时自动生成写入实例 `.env`（F134，见 §5c）→ 才**持久化 `mode=bearer`（原子，避免 bearer-without-serve / bearer-without-token）+ 打印手机 URL |
| `octo remote disable` | 切回 `mode=loopback` + `serve --https=443 off`（只关本功能映射）；reset 失败红色 + exit 1（不假报成功） |
| `octo remote status` | 当前 mode + 三态 + host↔mode 暴露判定 + 就绪但非 bearer 时提示（serve+loopback 会因 XFF 拒绝） |

`--dry-run` 预览不落地 + 幂等（比对**持久化**值非 env 生效值）。

**托管服务 env 语义**（`read_instance_effective_env`）：`octo remote`/`doctor` 从任意 shell 诊断托管服务时，
**实例 `~/.octoagent/.env` 覆盖当前 shell env**——OS 服务（launchd/systemd）不继承 CLI shell 的临时 export，
故 host/port/mode/token 以实例 `.env` 为服务真实生效值。

### 5b. `octo attest remote` 验收探针（F144，`attest_commands.py`）

`octo remote status` 只给「serve 已启用时的预期 URL」**不验活**；`octo attest remote`
补验活半边（吸收 F130 AC-1 链路验收）：mode==bearer（enabled 信号）→ tailscale READY
→ token（**值只从实例 `.env` 读**，防 shell-only 假通过）→ 真请求 published URL：
`/ready` → SPA → bearer 纵深（无 token 必 401 / 带 token 200）→ SSE（借最近任务真
流式握手；空实例退化 404 认证判别）。三态 `pass/not_enabled/fail`（exit 0/0/1），
**bearer 下 tailscale 断链 = fail**（已启用链路断，非「未启用」）。只读 GET、token
零泄漏（report 只含布尔）、`--json` 供 F141 release lane。语义半边（bearer×XFF 矩阵）
在 `test_frontdoor_auth.py::TestFrontDoorModeHeaderMatrix`。

### 5c. F134 bearer 加固（限流 / token 自动生成 / SSE 泄露收敛）

三件全收敛既有单入口（#10），零新认证旁路；制品 `.specify/features/134-bearer-hardening/`。

**① 认证失败限流**（`frontdoor_auth._FailureRateLimiter`，guard 实例属性=app 单例内存态）：
60s 窗口内同源「带错凭证」失败达 10 次 → lockout 300s，期间错误凭证一律
`429 FRONT_DOOR_RATE_LIMITED` + `Retry-After`。**verify-first 语义**（与 OpenClaw
check-before-verify 的显式差异）：正确凭证恒放行并清计数——serve 场景全远程共享
127.0.0.1 一个桶（TCP 层 key，不用可伪造的 XFF），锁定式会让 tailnet 内任一失控设备
DoS 唯一用户。缺凭证（SPA 首屏裸 401）不计数；loopback 模式不接（无凭证可爆破）；
loopback 源**不豁免**（serve 主入口即 127.0.0.1）。参数为常量不进配置面；clock 构造
注入（测试无 sleep）。`octo attest remote` 负向断言同步接受 {401,429}（两者都证明在挡）。

**② enable 强 token 自动生成**：token 未在实例 `.env` 时 `secrets.token_urlsafe(32)`
追加写入（0600，追加保既有内容 + 尾部补换行）；写失败**不切 mode + exit 1**（防
bearer 无 token 让受保护 API 全 503 的半完成态）。**输出零明文**（替代 F130 的
"打印建议值让用户复制"——那把 token 送进终端 scrollback/service 落盘）；用户经
`grep <token_env> ~/.octoagent/.env` 自查后在手机输入。

**③ SSE query token 泄露收敛（选 (b) 归档）**：取证三分——唯一实锤面是
**uvicorn access log**（自带 handler 直写 stdout 绕过 F129 root 脱敏链 → launchd
`StandardOutPath` fd 级落盘明文）；Tailscale 日志面维持理论定性（tailscaled 标准
日志不含 per-request URL）；Referer/history 面否证（EventSource URL 非导航 URL）。
修复=`logging_config` 给 `uvicorn.access` 挂 logger 级脱敏 filter（保留原
AccessFormatter 格式，`access_token=` 值经 log_redaction 掩码，该命中由
`test_log_redaction.py` 契约钉住）。**ticket 化 (a) 完整设计归档 spec §6**（TTL 60s
多次可用防 EventSource 重连断链），触发条件=front_door 走出 Tailscale 私网。

## 6. 哲学守界

- **H1**：F130 是运维/网络地基，**不碰 Agent 决策环**（无 orchestrator/agent_context 依赖）——主 Agent 仍唯一
  user-facing speaker；手机访问的是既有 Web UI。
- **Constitution #5**：tskey 走 `~/.octoagent/.env`，绝不进 plist/unit/config/LLM 上下文（F129 `_is_sensitive_env_key`
  含 "KEY"/"AUTH" 天然拦 + log_redaction 补 `tskey-auth/api/client-` 前缀防落盘）。bearer token 值只落 0600 `.env`
  （F134 翻转 F130「绝不写 token 到文件」——原句意图是防进 config/版本管理面，`.env` 本就是 secrets 分区的家；
  CLI 代写消除了"明文打印 stdout 让用户复制"这个更差的中转面），绝不进 octoagent.yaml / stdout / 日志。
- **Constitution #7**：serve 自动化只就绪态接管，未就绪给指令**不代跑**；绝不静默改系统（零 sudo / 不代启用
  HTTPS / 不改电源）；切模式可逆 + dry-run 预览。
- **Constitution #10**：认证仍收敛单一 `FrontDoorGuard`，F130 **不加认证旁路**（helper/校验/CLI 均无 auth 路径）。

## 7. 已知 limitations（v0.1）

- **PWA**（manifest/service worker/add-to-home-screen）不做——纯浏览器访问 `https://<magicdns>/` 已满足目标，归后续独立小 Feature。
- **SPA 静态资源鉴权**：`main.py` mount `/` 绕过 front_door（只泄露前端 bundle 非凭证，私网风险极低）；强制加鉴权有登录页自锁死风险 → 记 limitation。
- **identity-header 模式**（`Tailscale-User-*` 免 token）：有"同机进程伪造"威胁 + 依赖 XFF/whois 不确定行为 → v0.1 用 bearer；identity-header 记为可选未来增强（威胁模型见 spec 附录 A）。
- ~~**query token 泄露**~~ → **F134 已收敛**（§5c③）：唯一实锤面（uvicorn access log 明文落盘）已闭环；
  Tailscale 日志面维持理论定性归档；ticket 化设计存 F134 spec §6 备 v0.2（触发=走出私网）。
- **exit(78) launchd 不对称**（§4）：launchd 会重启裸奔误配，靠 err.log + 节流兜底诊断。
- **host↔mode 校验非万能**：`_resolve_startup_host` 只覆盖 env + argv `--host`；gunicorn / 编程式启动传入的 host 看不到。生产路径 run-octo-home.sh 二者恒同步。
