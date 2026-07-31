# F156 技术调研

## 复用边界

- F153：registration、credentials、request signing、single URLSession、connection states。
- F152：capability/consent/audit/deletion 和 cross-feature Protocol。
- Gateway：现有 conversation/task/approval/Memory contract；T001 必须从 OpenAPI/
  runtime inventory 重新确认 exact mobile projection，不能手写猜测。
- F154/F155：敏感 slice adapter/consent/analysis owner。

## App 结构

- `CompanionRoot`：根据 F153 connection state 选择 registration 或 shell。
- `CompanionTab`：四个 finite tab。
- 每个 feature 有 Model/Coordinator/Projection/View；共享 transport 只在 F153 client。
- typed `AppDestination` 只保存 lightweight id，不保存正文。
- notification deep link 先 fetch/authorize，再导航。

## APNs

- device token 不是 F153 device id，也不是认证 secret。
- server 保存 token 与 existing device identity 的映射；revoke/rotation 清理。
- APNs provider credential 只能来自部署 secret owner，不进 App/repo/log。
- payload 只有 type + opaque id；正文从 authenticated API 获取。

## 测试

- L4 Swift：reducers/projections/navigation/actions/dedup/unknown state。
- L4 Python：mobile projections/capabilities/payload allowlist。
- L3：real Gateway/SQLite/SSE/APNs fake server、proof/revoke/conflict。
- L1 Simulator：真实 App process、Gateway fixture、function E2E + screenshot diff。
- L1 Device：real network lifecycle/APNs/background/notification tap。
- L2：approved single-model paths from F154/F155，不在 F156 新建 model runner。

## 风险

- 手写 DTO 与 OpenAPI 漂移；
- 多 coordinator 重复连接状态；
- notification 越过当前授权直接执行动作；
- message/SSE 重连重复；
- SceneStorage/snapshot 泄漏敏感正文；
- 同实现自产 baseline 导致视觉假绿；
- Web 组件结构反向主导 iOS。
