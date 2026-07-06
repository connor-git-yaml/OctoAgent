# F130 手机远程访问上手指南

> 让你的手机经互联网**安全打开完整 Octo Web UI**（不只 Telegram 文本），走 Tailscale 私网隧道——
> 不公网暴露、零证书配置。前提：F129 常驻服务已装好（`octo service status` 健康）。

## 前置准备（一次性）

1. **Mac 端装 Tailscale**：从 https://tailscale.com/download 装 Tailscale.app，`tailscale up` 登录你的 tailnet。
2. **启用 MagicDNS + HTTPS**：登录 Tailscale admin console（https://login.tailscale.com/admin/dns），
   启用 **MagicDNS** 和 **HTTPS Certificates**（serve 的前置条件）。
3. **手机端装 Tailscale**：手机装 Tailscale App，登录**同一个** tailnet 账号。

## 启用远程访问

```bash
octo remote enable
```

这一步会：
- 检测 Tailscale 是否就绪（未就绪会打印下一步指引，**不改任何配置**）；
- 就绪则**先跑 `tailscale serve`**（把本机 Web UI 发布到你的 tailnet），成功后才把认证模式切成 `bearer`；
- 提示你设一个访问 token（见下）；
- 打印手机访问地址 `https://<你的设备名>.<tailnet>.ts.net/`。

## 设置访问 token（重要）

`octo remote enable` 会提示你在 `~/.octoagent/.env` 加一行强随机 token：

```
OCTOAGENT_FRONTDOOR_TOKEN=<命令给你的强随机值>
```

- token **只放 `.env`**，绝不写进 `octoagent.yaml`（安全）。
- 手机访问 Web UI 时在页面输入这个 token 即可。

## 让改动生效

```bash
octo restart
```

（切换认证模式需要重启服务。）

## 手机访问

手机浏览器打开 `octo remote enable` 打印的 `https://<magicdns>/`，输入 token → 得到完整 Web UI。

## 查看状态

```bash
octo remote status
```

显示：当前认证模式 + Tailscale 三态 + host↔mode 安全判定 + 手机访问 URL。

也可用 `octo doctor`（新增 `tailscale_connectivity` + `front_door_exposure` 两项检查）。

## 关闭远程访问

```bash
octo remote disable
octo restart
```

切回本机-only（loopback）模式 + 只关本功能的 serve 映射（不动你为其它服务配的 Tailscale serve）。

## 常见问题

- **`octo remote enable` 说 Tailscale 未就绪**：按提示 `tailscale up` 登录 + admin console 启用 MagicDNS/HTTPS，再重试。
- **serve 启用失败提示 HTTPS**：去 admin console 启用 HTTPS Certificates（本工具不会替你启用）。
- **serve 提示权限不足**：按提示手动 `sudo tailscale serve ...`（本工具绝不自动 sudo）。
- **手机打不开 / 一直 403**：确认 `octo remote status` 显示 mode=bearer（serve 场景必须 bearer，loopback 会被拒）+ 已 `octo restart`。
- **gateway 起不来、日志说"裸奔"exit(78)**：你把 `OCTOAGENT_HOST` 设成了 `0.0.0.0` 但认证还是 loopback 模式（危险组合）。
  改回 `OCTOAGENT_HOST=127.0.0.1` + `octo remote enable`（推荐），或若确需绑外部网卡则切 bearer 模式并设 token。

## 安全说明

- Tailscale 是 **WireGuard 私网**——只有你 tailnet 里的设备能到达，不是公网暴露。
- gateway 保持绑 `127.0.0.1`，Tailscale 从 loopback 反代 + 终止 HTTPS——端口不监听任何外部网卡，暴露面最小。
- bearer token 是 tailnet 之外的纵深第二道闸（即使 tailnet 误配也拦得住）。
