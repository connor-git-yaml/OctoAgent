# OctoAgent 常驻服务上手指南（F129，Connor 真实启用步骤）

> 前提：`~/.octoagent` 托管实例已引导完成（`scripts/install-octo-home.sh` 跑过，
> `octo doctor` 基础项绿）。以下命令均在任意目录可执行（命令自动定位托管实例）。

## 1. 安装为常驻服务（一次性）

```bash
octo service install
```

做了什么：生成 `~/Library/LaunchAgents/com.octoagent.gateway.plist`（Mac）并
load + 启动 + 等 `/ready` 就绪校验。如果之前用 `octo restart` 起过自管
gateway，install 会**先优雅停掉旧进程再交接**（防端口冲突，输出里会有
"交由 OS 服务接管" 提示）。之后：

- **崩溃自愈**：gateway 进程挂了，launchd 10 秒内自动拉起；
- **开机自启**：重启 Mac 后自动运行；
- **关终端无感**：服务不依赖任何终端会话。

变体：

```bash
octo service install --dry-run      # 只预览将写入的 plist 内容，不落地
octo service install --force        # 服务定义坏了/想强制重写时
octo service install --keep-awake   # 运行期防系统闲置睡眠（见 §5）
```

重复执行安全（幂等）：内容一致会跳过；检测到过时定义会自动重写。
如果输出里出现 `repair-required`，按提示查 `octo service status` 与日志。

## 2. 查看服务状态

```bash
octo service status            # 三态：已安装 / 已注册 / 运行中 + pid + /ready
octo service status --verbose  # 加服务定义路径等技术细节
octo service status --json     # 机器可读
```

## 3. 看日志（脱敏后落盘）

```bash
octo logs                # 最近 200 行
octo logs -n 50          # 最近 50 行
octo logs -f             # 实时跟随（Ctrl-C 退出）
octo logs --level error  # 只看 error 及以上
```

- 日志在 `~/.octoagent/logs/octoagent.log`（10MB × 5 自动轮转），provider
  key / Telegram token 等已自动脱敏（`sk-abc…fXYZ` 形态）。
- gateway 若在**启动瞬间**就崩（连日志系统都没起来），`octo logs` 会自动
  回退展示 `octoagent.err.log`（launchd 捕获的原始 stderr），启动期
  traceback 不会丢。
- 注意：日志文件仍属敏感（正则脱敏非万能），勿直接外发。

## 4. 日常操作语义变化（装了服务之后）

- `octo restart`：委托 launchd 重启服务——**进程死了也能用**（以前要求进程存活）。
- `octo stop`：优雅停止当前进程；**开机自启/`octo restart` 仍会拉起**。
  - ⚠️ `octo stop --force`（SIGKILL）会被 launchd 视为崩溃**立即拉起新进程**，
    等于没停——临时停止请用不带 `--force` 的 stop。
- **彻底停用**：`octo service uninstall`（unload + 删 plist + 复位 restart
  策略 + 清 runtime-state；重复执行安全）。

## 5. 防睡眠（doctor 会主动提醒）

```bash
octo doctor
```

新增两项检查：

- `service_status`：服务没装会给 WARN + 建议（不阻断）；
- `sleep_settings`：检测到 Mac 会自动睡眠时 WARN，给三条建议——
  1. 系统设置 → 显示器/节能 → 开启「接通电源时防止自动进入睡眠」（推荐，一次到位）；
  2. **诚实边界**：MacBook 合盖睡眠软件挡不住——要么外接电源+显示器合盖用，
     要么部署在 Mac mini（无此问题）；
  3. 或 `octo service install --keep-awake`：服务运行期用用户级 `caffeinate`
     防闲置睡眠（零 sudo、卸载即止；同样挡不住合盖）。

doctor **只检测和建议，绝不改你的系统设置**。

## 6. 验证崩溃自愈（可选，1 分钟）

```bash
octo service status                        # 记下 pid
kill -9 <pid>                              # 模拟崩溃
sleep 12 && octo service status            # launchd 已拉起新 pid
octo logs -n 20                            # 看重启痕迹
```

重启 Mac 后直接 `octo service status` 应显示运行中（开机自启）。

## 7. 故障排查速查

| 现象 | 动作 |
|------|------|
| install 报 `repair-required` | `octo service status` + `octo logs`；修复配置后 `octo service install --force` |
| install 报路径含 worktree 被拒 | descriptor 指向了临时 worktree——在稳定源码位置重跑 `scripts/install-octo-home.sh` |
| install 提示"脚本位于实例根之外" | 能用，但源码目录移动/删除会让服务失败；建议源码长期放 `~/.octoagent/app` 下 |
| 服务反复崩溃 | `octo logs --level error`；launchd 有 10s 退避不会刷爆盘；`octo service uninstall` 可先止血 |
| 日志看不到刚发生的启动崩溃 | `octo logs` 已自动回退 err.log；也可直接看 `~/.octoagent/logs/octoagent.err.log` |
