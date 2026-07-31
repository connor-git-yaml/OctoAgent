# F156 Threat Model

## 保护对象

- F153 Secure Enclave 设备私钥、ThisDeviceOnly Keychain 短期凭证与 request proof；
- 对话、任务、审批、Memory candidate 以及 Health/Calendar 的正文；
- APNs device token、通知导航对象与单设备撤销状态；
- 用户在前后台、断网、token 轮换与被撤销时的决定权。

## 威胁与构造性措施

| ID | 威胁 | 构造性措施 | 验证 owner |
|---|---|---|---|
| TM-01 | 把 Cloudflare service token、Web Cookie 或长期 bearer 放入 App | App 只复用 F153 设备密钥、短 token 和 request proof；source/bundle/log scan 必须 0 命中 | L4 + bundle scan + device |
| TM-02 | 通过 WebView/第二 HTTP client 绕过设备身份 | WebView/browser-cookie bridge 物理缺席；唯一 generated adapter 使用 F153 `DeviceTrustClient` | architecture gate |
| TM-03 | APNs payload 泄漏消息、审批、Memory 或 Apple 数据 | payload exact 只含 type、opaque object id 与 collapse id；extra field fail closed | L4 + captured payload |
| TM-04 | 恶意/过期通知直接执行危险动作 | notification 只导航；必须重新验证设备、capability 和服务器对象状态；不支持 approve/reject/cancel/send | L3/L1/device |
| TM-05 | 后台刷新触发 Health/Calendar/LLM/审批/发送 | background task 只允许 badge/摘要刷新；敏感 coordinator 和 command 不在后台 capability set | architecture + lifecycle E2E |
| TM-06 | 敏感正文进入 SceneStorage/AppStorage/snapshot | 只持久化 tab/navigation id 与非敏感 preference；正文/token 禁止；snapshot scan | L4 + package/snapshot scan |
| TM-07 | revoked/token-expired/offline 状态竞争留下可写 UI | revoked 优先级最高；写动作由 canonical state + capability 双门决定；前台恢复重拉服务器事实 | reducer L4 + network/device |
| TM-08 | 审批与 Memory candidate 共用模糊“允许”造成误决策 | 两类 typed projection/command 物理分离，展示 source/target/impact，conflict/expired 以服务器为准 | L4/L3/L1 |
| TM-09 | unknown/future state 被当作已授权或可操作 | finite enum unknown 只读 fail closed；不通过字符串/HTTP status 推断 | L4 adversarial |
| TM-10 | 视觉还原导致低对比、小点击区或 VoiceOver 不可用 | Claude early 只定义视觉语言；iOS 原生 44pt、Dynamic Type、VoiceOver、Reduce Motion 和 Differentiate Without Color 为不可退化门 | visual/a11y E2E |

## 明确排除

- 不用“单用户”理由弱化设备撤销、请求签名、通知正文最小化或快照扫描。
- 不把 Simulator 或静态 preview 当作 Secure Enclave、APNs、系统权限、锁屏或
  Wi-Fi↔蜂窝切换证据。
- 不为了复用 Web 视图而加入 WebView、第二 session/device registry 或兼容转换层。
