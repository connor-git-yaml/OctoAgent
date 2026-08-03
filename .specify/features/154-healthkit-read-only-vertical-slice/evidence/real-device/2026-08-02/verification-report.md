# F154 真 iPhone HealthKit 阶段性验证报告

## 结论

- 日期：2026-08-02（Asia/Shanghai）；
- 设备：真 iPhone 17 Pro Max / iOS 27.0 beta / arm64；
- 状态：`T013_PASS / T016_PENDING`；
- 本轮只获得一次“上传 7 天 canonical 健康摘要”的用户明确授权；没有再次上传，
  没有输出健康值、source hash、device identifier、token 或 owner identity；
- 用户动作权限、真实 24 小时/3 天/7 天本地预览、7 天批准→分析→删除、前后台恢复、
  server-side provenance cascade 已通过；
- 健康页面 preview-only 锁屏→解锁已于 2026-08-03 通过；T013 完成，T016 最终 Verify
  仍待完成。

## 真实 HealthKit 读取

同一真机、同一 production `AppleHealthDataStore` 与同一 live XCUI selector：

```text
HealthImportFlowUITests.test_live_health_read_preview_and_optional_approval
```

验证事实：

1. App 冷启动和进入“健康概览”都没有自动弹系统权限；
2. 只有用户点按“从 Apple 健康读取”后才出现系统权限 sheet；
3. read set 只含 step count 与 sleep analysis，write set 为空；
4. 真实 24 小时、3 天、7 天窗口都形成了诚实本地结果（有可读 preview 或明确无可读数据）；
5. preview 在 Home→前台恢复后仍属于当前未批准 session；
6. preview-only transaction 只执行“删除本地预览”，没有网络提交。

三组 preview result bundle 均为 `1 passed / 0 failed / 0 skipped`：

| 窗口 | result bundle | files | bytes | directory byte-map aggregate |
|---|---|---:|---:|---|
| 24 小时 | `/tmp/f158-f154-health-24h-preview-result.Ipt8Vn/HealthPreview.xcresult` | 40 | 678633 | `9f98d55ee23f0b6ce6d3d83c48ffa6f6b735aa5a145be8cb52110e8c5a6d4d21` |
| 3 天 | `/tmp/f158-f154-health-3d-preview-result.trCWWu/HealthPreview.xcresult` | 40 | 693828 | `8bd66d49c29aee0a6225a95ab9ef7f184652d6a742a498ceeee9dcad06415296` |
| 7 天 | `/tmp/f158-f154-health7d-preview4-result.wQq4xe/Health7dPreview.xcresult` | 40 | 703180 | `878f68644daed478c6d8fdee95256b528b330365e4e1f2fdf1e2cb9111d8a869` |

result bundle 可能包含只供本机复核的聚合 preview screenshot，因此没有复制进仓库，
也没有在报告中记录或转写任何健康值。

## 一次经授权的批准、分析与删除

首次批准尝试暴露真实 timeout 缺口：Gateway 分析预算为 60 秒，而 iOS 的通用 request
timeout 只有 15 秒。该尝试在 UI 超时，不能计为有效证据。服务端稍后完成的两条遗留
source chain 已通过正式 `health.source.delete` API 清理，没有直接改数据库。

修正后保持普通请求 15 秒，仅将 exact analysis path 设为 75 秒、URLSession resource
预算设为 90 秒。重新构建同一真机 App 后，只执行一次获批 7 天 transaction：

- XCUI：`1 passed / 0 failed / 0 skipped`，35.154 秒；
- review route：HTTP 201；
- analysis route：HTTP 201；
- delete route：HTTP 200；
- UI 完成后返回“尚未读取”；
- 服务端 review/approved packet/analysis result/Memory candidate：`0/0/0/0`；
- deletion receipt：3 个，全部 `completed`；
- metadata-only audit 顺序：`packet_sent` → `analysis_completed` →
  `deletion_started` → `deletion_completed`。

有效 result bundle：

```text
/tmp/f158-f154-health7d-green.ulx4aj/F154Health7dGreen.xcresult
files=42
bytes=1093030
directory byte-map aggregate=73f6ad1750e697bf1d5fcb5a1847ac22fe222e3c2cd3156c9c4f1111f8af67f8
```

遗留 source cleanup 的幂等复验同样由真机 Secure Enclave credential 和 production
signed transport 完成，`1 passed / 0 failed / 0 skipped`：

```text
/tmp/f158-f154-health-cleanup.QL97g5/F154HealthSourceCleanupOnly.xcresult
files=38
bytes=277100
directory byte-map aggregate=9f066f9f1d3ebe3d034fc99ceb98829c8a9f2f2caaba868dc53884a66190db5e
```

## 回归与边界

- Simulator focused：25 total / 21 passed / 4 个 live-only 默认 skip / 0 failed；
- HealthImportTests：11/11 PASS；
- DeviceTrustTests 非 live：10/10 PASS；
- Simulator result aggregate：
  `d45da8b4bcce963543b4ff229e7abf10f6bebe92d6a019a578c3e654b6ddf819`；
- generic iPhoneOS Release arm64 warnings-as-errors：PASS；
- repository architecture gate：PASS；
- Info.plist 只有 Health read usage，没有 Health update usage；
- production source 中 HealthKit save/delete/background/observer/anchored/clinical：0。

Wi-Fi、纯蜂窝 4G、background、进程重启、owner revoke 与整机重启后的 revoke
persistence 已由同一 App、同一 `DeviceTrustClient` 的 F153 真机链证明；F154 的
coordinator/Gateway 单元与集成层继续证明 offline/revoked 不能提交；F154 自身的物理
锁屏 UI 证据已由下面的独立 preview-only transaction 补齐。

## 未完成

1. 完成 T016 最终 evidence inventory、当前 truth CI、Blueprint/F158 同步；
2. 通过后才可设置 `GATE_VERIFY=true` 并解锁 F155。

## 2026-08-03 锁屏尝试

当前签名 app/runner 与 lock-cycle seam 已通过真机 build-for-testing、完整 Simulator
scheme（36 total / 27 passed / 9 个 live-only skip / 0 failed）、Release arm64
warnings-as-errors 和 repository architecture gate。首次 lock-cycle 行为 transaction
成功形成 7 天本地 preview 并进入锁屏等待点，但设备在 180 秒内始终保持前台，因此
测试按 fail-closed assertion 失败，不能计为证据。

该 transaction 的 approve flag 缺失、screenshot capture 关闭；失败后已终止 App，清除
session-only preview。没有再次上传健康摘要，也没有修改服务端。它只保留为一次无效
物理操作历史，不计证据。

## 2026-08-03 有效锁屏→解锁 transaction

恢复既有受管 cloudflared connector 后，同一签名 App、同一 exact live node 与同一
preview-only 合同执行一次并通过：

```text
/tmp/f158-f154-lockcycle-valid-result.U5jlbf/F154LockCycle.xcresult
1 total / 1 passed / 0 failed / 0 skipped
duration=20.308s
files=38
bytes=362425
directory byte-map aggregate=ac92cab0b27b00ce73cac0cadb8c3fa0f3004b96ef85d0359717bbbb626d37a3
```

测试真实观察到 `preview ready → App 离开前台 → App 恢复前台`。恢复后未批准 preview
仍存在，且没有自动进入提交、分析或完成；测试随后删除本地 preview 并确认回到“尚未读取”。
approve flag 缺失、screenshot capture 关闭、summary upload=false。T013 至此完成；原始
xcresult 继续只保存在本机 `/tmp`，仓库只保留不含健康值、设备标识、凭证或截图的结构化
attestation。
