# F154 Threat Model

| 威胁 | 后果 | 控制 | 验证 |
|---|---|---|---|
| 冷启动偷弹健康权限 | 用户被迫授权 | 只允许明确按钮触发 | Simulator/真机 launch counter |
| 把空结果说成拒绝 | 隐私事实错误 | 合并为 no-readable-data/limited | L4 + UI 文案 negative |
| 读取过多类型 | 敏感面扩大 | exact step/sleep allowlist | AST/entitlement/runtime request set |
| HealthKit 写路径出现 | 修改用户健康数据 | toShare empty；save/delete=0 | source/symbol scan + test double |
| raw sample/identifier 上传 | 高敏数据泄漏 | local aggregation + schema forbid | payload/log/audit adversarial |
| sleep 重叠重复累计 | 分析错误 | interval clipping/union | property tests |
| 时区/DST 日边界错误 | 步数归错日期 | injected calendar/timezone | DST fixtures |
| preview 与批准包漂移 | 用户未同意的数据被发送 | canonical hash + F152 consent | mutation/replay tests |
| Web identity 冒充设备 | 绕过设备信任 | F153 device proof/capability | Host/auth negative matrix |
| 撤销后仍上传 | 权限撤销失效 | revoke before expiry | L3/真机 |
| 模型失败进入 Echo | 假分析/敏感误路由 | ProviderRouter auth-fatal | L2 + deterministic auth failure |
| 日志/crash/snapshot 留 raw | 二次泄漏 | structured safe metadata only | package/log/snapshot scan |
| 删除只删一层 | 派生数据残留 | F152 provenance cascade | SQLite failure/reentry |
| 自动写 Memory | 健康事实永久化 | candidate + second confirmation | state machine negative |
| 后台连续监控 | 越过当次同意 | observer/background=0 | AST/entitlement/lifecycle |
| UI 复制 Web/后期设计 | 原生体验与视觉退化 | Claude early tokens + SwiftUI native | visual baseline + review |

## 信任边界

```text
HealthKit store
  -> HealthDataStore adapter（raw 临时）
  -> local normalized preview（内存）
  -> user review + F152 consent
  -> F153 signed mobile request
  -> Gateway F152 store/policy/audit
  -> ProviderRouter single analysis
```

每个箭头都必须有 exact schema/hash；不存在“可信 App 所以可传 raw”的例外。
