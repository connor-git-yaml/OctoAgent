# F153 实施计划

## Gate

- Research：通过
- Design：通过
- Tasks：通过
- Implement：从 T001 开放
- Verify：关闭

## Phase 0：authority

1. 在 F151 单一 architecture checker 中登记 exact F153 paths/symbols。
2. 继续拒绝 HealthKit/EventKit、WebView、service token、第二 tunnel/HTTP client/
   registry/store。
3. RED 必须因 authority 缺失失败，不接受 import/collection/path 错误。

## Phase 1：Gateway device trust

1. Protocol 增加 F153 transport DTO，不复制 F152 domain models。
2. Core 在现有 SQLite/StoreGroup 下增加唯一 device-trust store。
3. Gateway 增加 mobile manifest/Host classifier、P-256 verifier、owner-assisted
   registration、opaque token 与 proof dependency。
4. 只发布 `/ready` 与 device profile，HealthKit/EventKit route 保持 absent。

## Phase 2：native iOS

1. 创建单一 SwiftUI Xcode project，deployment target iOS 17。
2. Secure Enclave key + ThisDeviceOnly Keychain；test signer只能通过 DI。
3. 单一 URLSession client 生成 exact envelope/proof。
4. 只实现 registration/connected/revoked/offline UI；视觉遵循 Claude 初稿与 Apple
   原生语义。

## Phase 3：运行证据

1. Python/Swift unit、Gateway integration、generic device build。
2. 安装 iOS Simulator runtime 后执行 UI/visual/a11y；当前 runtime 安装需要 macOS
   管理员授权，未安装前不得声称完成。
3. 真 iPhone 执行 Secure Enclave、Keychain、Wi-Fi/蜂窝、背景、撤销与轮换。
4. 独立授权后配置个人部署 mobile hostname，验证同一 tunnel、exact Bypass path、
   Web/mobile negative routes。

## Phase 4：Verify

1. secret/package/log/audit scan。
2. full Gate 与 blast-radius regression。
3. verification report、Blueprint/F158 trace；成功才解锁 F154。

## Architecture

- `core`：唯一 durable device-trust store，复用 F152 identity/audit。
- `policy`：继续使用 F152 authorize seam，不复制 capability engine。
- `protocol`：F153 transport DTO。
- `gateway`：edge classifier、crypto verification、application orchestration/routes。
- `ios`：Secure Enclave/Keychain/URLSession/registration UI；不拥有服务端 policy。
- Cloudflare：同一 named tunnel 的 edge routing，不是设备 identity authority。
