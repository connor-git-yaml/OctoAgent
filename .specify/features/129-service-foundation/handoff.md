# F129 → F130（Tailscale 手机触达）Handoff

> F129 已交付「进程一直在、日志可诊断」的地基。F130 做 host↔mode 校验 +
> front_door 模式切换 + Tailscale 触达时，以下是可直接复用的资产与必须注意的缝。

## 1. 可直接复用的资产

| 资产 | 位置 | F130 用法 |
|------|------|-----------|
| `ServiceManager` + `ServiceBackend`（launchd/systemd） | `packages/provider/src/octoagent/provider/dx/service_manager.py` | 改 ExecStart host 参数时走 `install --force` 自愈重写（definitions_equivalent 会检出 ExecStart 差异自动 refreshed）|
| 三态 status（installed/loaded/running + ready + last_error） | 同上 `ServiceManager.status()` | F130 doctor/status 扩展直接加维度，别重造探测 |
| `octo service install/uninstall/status` + `octo logs` CLI | `dx/service_commands.py` | F130 新 CLI 子命令挂同一 group |
| 日志落盘 + 脱敏层 | `gateway/middleware/logging_config.py` + `log_redaction.py` | Tailscale 相关日志（auth key 等）**自动被脱敏**——若 Tailscale key 形状不在现有规则（`tskey-` 前缀），**需在 `_RULES` 补一条**（成本 ~5 行 + 测试） |
| doctor DI 缝（service_manager_factory / sleep_risk_probe） | `dx/doctor.py` | F130 新增 check（如 tailscale 连通性）沿用同款注入模式保 hermetic |
| `RestartStrategy.OS_SERVICE` 分层委托 | `core/models/update.py` + `dx/update_service.py` | F130 切换 host 后 `octo restart` 已可直接委托 OS 拉起（不要求旧 pid 存活） |

## 2. F130 必须处理的缝

1. **host 绑定**：F129 的服务定义沿用 `run-octo-home.sh` 现有 `--host 127.0.0.1` 默认（`OCTOAGENT_HOST` env 可覆盖）。F130 切 front_door 模式（如绑 Tailscale IP / 0.0.0.0）时：
   - 方案 A：descriptor `environment_overrides` 加 `OCTOAGENT_HOST`（`OCTOAGENT_*` 前缀会进服务定义 env，非 OCTOAGENT_* 会被 build_spec 跳过——**这是 Constitution #5 防 secret 落盘的守卫，别放宽**）；
   - 改完跑 `octo service install`（归一化比对检出 env 差异 → 自动 refreshed + reload）。
2. **host↔mode 校验**：F129 未做（显式 deferred）。落点建议：`ServiceManager.build_spec()` 内（有 descriptor + 违规消息管道现成）或 doctor 新 check。
3. **`/ready` 探测 URL**：`descriptor.verify_url` 当前指 127.0.0.1。host 换绑后 verify_url 需同步更新（`update_status_store` 写 descriptor），否则 start gate / status ready 探测会探错地址。
4. **退出码 78 熔断**：systemd `RestartPreventExitStatus=78` 已声明，但 **gateway 侧尚未主动 `exit(78)`**（确定性配置错时仍以普通非零退出 → 会被重启到 StartLimit 熔断）。F130 若做配置校验 fail-fast，启动期确定性错误应 `sys.exit(78)` 以命中该字段。
5. **超时分层**（Codex 十一轮）：生命周期命令（stop/restart/bootout/kickstart）用 `LIFECYCLE_TIMEOUT_SECONDS=120`（drain 可合法等 90s），只读探测 5s——F130 改 ExecStart 触发 `install --force` 重启时同样受此保护，别把新命令误挂到 probe 超时。
6. **迁移交接**（Codex 十一轮 P1）：`install` 会优雅停掉旧的**自管**（COMMAND/SELF_SIGNAL）gateway 进程再激活（防旧进程占端口 + 共享 /ready 假接管）；策略已 OS_SERVICE 的 pid 属 supervisor 管理绝不碰。F130 切 host 重装时此逻辑自动生效。
5. **公网暴露红线**：Blueprint §0 锁单用户——Tailscale 是私网触达，不是 front-door 公网暴露；service 定义 env 只放 OCTOAGENT_*，Tailscale auth key 走 `~/.octoagent/.env`（run-octo-home.sh source，不进 plist/unit）。

## 3. 已知 limitations（可能影响 F130 决策）

- service 层 out/err 原始日志不脱敏（0600 缓解）——若 F130 引入新 secret（tskey），启动期崩溃 traceback 理论可能带出，建议 F130 顺手在 redaction 规则补 `tskey-` 前缀。
- doctor service check 的 ready 维度依赖 descriptor.verify_url 存在（root 未对准时缺失，三态不受影响）。
- launchd/systemd 真机集成未在 CI 覆盖（hermetic 红线）——AC-1 崩溃自愈是人工验收步骤，F130 改 ExecStart 后同样需人工 `octo service status` 复核一次。
