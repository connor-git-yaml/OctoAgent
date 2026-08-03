# F152 实施计划

## Gate

- Research：通过
- Design：通过
- Tasks：通过
- Implement：T001-T013 已完成
- Verify：通过；报告见 `verification/verification-report.md`

## 实施顺序

### Phase 0：authority 与 RED

1. 将 F152 exact paths/symbols 纳入 F151 单一 architecture checker。（已完成）
2. 先建立 protocol model、capability、consent、audit、TTL、删除链与 secret-negative
   RED。
3. RED 必须在同一 selector 上因能力缺失失败，不接受 import/collection/path 错误。

### Phase 1：纯合同

1. 在现有 core package 增加唯一 domain Pydantic models、canonical hashing 与纯状态机。
2. 在既有 policy 边界增加 capability 与 consent validation；protocol 只投影同一
   core authority，不反向拥有领域状态。
3. 复用现有 audit/memory review owner；不创建平行 database 或 registry。

### Phase 2：持久化语义

1. 定义 review bundle、approved packet、deletion receipt 的 durable store contract。
2. 删除采用可重入状态机；正文删除后只保留 non-sensitive audit。
3. expiry/revoke 在读取和写入两侧都 fail closed。

### Phase 3：Gateway projection

1. 只发布 F153/F154/F155 后续需要的 typed contract seam。
2. 未获 owner 的 HealthKit/EventKit/device registration route 必须 absent。
3. API schema 与 Pydantic model 由同一 authority 生成。

### Phase 4：Adversarial / Verify

1. unknown capability、越 scope、重放、过期、错误 hash、跨 owner/device、自动 Memory、
   raw/log/token 泄漏、删除不完整逐项拒绝。
2. Ruff、format、type、architecture、complexity、focused/full regression。
3. verification report 通过后才开放 F153 production。

## 架构边界

- `core`：唯一领域模型、canonical hash、状态机与持久化 owner。
- `protocol`：跨端数据合同投影，不复制领域状态。
- `policy/application`：capability/consent 决策。
- `gateway`：transport projection，不解释隐私策略。
- `memory`：继续使用既有 review/write owner，敏感候选没有直写旁路。
- iOS/F153：消费合同，不复制 Python 业务状态机。

## 设计依据

- Apple Keychain/Secure Enclave 负责设备侧私钥安全，不使用源码或普通文件保存。
- App Attest 只能作为整体风险信号，不能替代 Octo device key、challenge、revoke 与
  capability。
- HealthKit 读取授权不可被 App 完整区分，产品必须诚实呈现“无可读数据/权限受限”。
- EventKit 读取要求系统 full access；“产品 read-only”必须由写路径物理缺席证明。
