# F130 → 后续 Feature Handoff

> F130 交付了「手机经 Tailscale 安全触达完整 Web UI」+ host↔mode 防裸奔。
> 以下是可复用资产与必须注意的缝（给 F133 voice 剥离 / F134 bearer 加固 / 未来远程增强）。

## 1. 可直接复用的资产

| 资产 | 位置 | 用法 |
|------|------|------|
| `read_instance_effective_env(root)` | `gateway/services/frontdoor_exposure.py` | **单一事实源**：托管服务实际生效 env（实例 .env 覆盖 shell）。任何从 CLI 诊断托管服务的命令都该用它，别再读裸 `os.environ`（会与运行时不一致） |
| `validate_front_door_exposure(host, mode)` 纯函数 + `FrontDoorExposureVerdict` | 同上 | host↔mode 安全判定（§E 矩阵），启动期 + doctor + `octo remote status` 三处共用 |
| `tailscale_helper`（DI exec 三态） | `provider/dx/tailscale_helper.py` | 复用 `service_manager.CommandRunner` 契约；hermetic 测试用 `FakeCommandRunner` 记录 argv + 红线断言 |
| doctor DI 缝 `tailscale_probe` | `provider/dx/doctor.py` `DoctorRunner.__init__` | 新增 tailscale 相关 check 沿用同款注入保 hermetic |
| `CONFIG_ERROR_EXIT_CODE=78` | `service_manager.py` | 确定性配置错启动期 fail-fast 复用此常量（对齐 systemd RestartPreventExitStatus） |
| `_resolve_startup_host()` | `gateway/main.py` | 启动期解析绑定 host（env 优先 + argv `--host` 兜底）——新增启动期校验可复用 |

## 2. 必须注意的缝

1. **serve 必配 bearer**（§0.2 硬约束）：任何"从 loopback 反代进来"的远程方案（Tailscale serve / 未来其它隧道）
   都不能用 `mode=loopback`（XFF 会全 403）。切模式统一走 `octo remote enable`（已封装此约束）。
2. **exit(78) launchd 不对称**：launchd 会重启裸奔误配（无 RestartPreventExitStatus 等价）。若未来加更多启动期
   fail-fast，注意 stderr 双写（F129 err.log 是唯一诊断出口），别只依赖 supervisor 不重启。
3. **host↔mode 校验非万能**：`_resolve_startup_host` 只看 env + argv `--host`；gunicorn / 编程式 uvicorn 传入的
   host 看不到。生产 run-octo-home.sh 用 `--host "${OCTOAGENT_HOST:-127.0.0.1}"` 二者恒同步——若未来改启动脚本
   要保持这个同步不变式。
4. **tskey 零落盘**：新增任何 Tailscale 相关 secret（tskey-*），确保 env 名含 KEY/AUTH（F129
   `_is_sensitive_env_key` 天然拦进 descriptor）+ log_redaction 已有 `tskey-` 前缀规则覆盖日志。
5. **serve reset 是全局的**：`disable_tailscale_serve` 默认传 port 用 scoped `--https=443 off`；只有不传 port 才
   回退全局 `serve reset`（会清整机他人 serve 配置）——调用方尽量传 port。

## 3. 给 F134（bearer 加固，M8 P2）的输入

- **query token 泄露**：SSE `?access_token=` 经 Tailscale serve 理论可能进访问日志（保守假设）——F134 的 SSE
  ticket 化正是收这个。当前 F130 记为 limitation。
- **限流 / 强 token 生成**：`octo remote enable` 已给强 token 生成建议（`secrets.token_urlsafe(32)`），但不强制;
  F134 可加服务端限流 + token 强度校验。
- **私网已挡**：F134 从 P1 降 P2 正因 Tailscale 私网 + bearer 纵深已足够；F134 是"再加一层"非必须。

## 4. 给未来远程增强的输入

- **PWA**（manifest/SW/add-to-home-screen）：独立前端 Feature，serve 已给 HTTPS，浏览器直接可用。
- **identity-header 模式**（`Tailscale-User-*` 免 token）：威胁模型 + `tailscale whois` 验证要求见 spec 附录 A;
  须先实测目标 Tailscale 版本是否注入 XFF（官方不承诺）。
- **named Tailscale Service**（`svc:xxx`）：需 tagged node + admin 审批，单用户过重，v0.1 用设备 hostname serve。
