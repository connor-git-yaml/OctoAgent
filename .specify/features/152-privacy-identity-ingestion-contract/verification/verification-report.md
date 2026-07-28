# F152 Verification Report

## 结论

- 日期：2026-07-28
- 结果：`PASS`
- `GATE_VERIFY=true`
- F152 T001-T014 已完成；F153 可以开始 production implementation，但仍必须遵守
  F152 的 device/capability/consent/TTL/deletion/audit 合同。
- 本报告只证明当前工作树中的 F152 合同与回归门通过；尚未代表 F158 总 Goal 已完成，
  也不代表分支已提交、CI 已通过或原生 iOS 已交付。

## 验证环境

- HEAD：`db3214fff722c6f969baf99528a76fc03a1e21a1`
- HEAD tree：`f5b06159128a894796455d6f7728e03a3040c700`
- 验证完成 UTC：`2026-07-28T09:53:59Z`
- Python 命令均使用仓库锁定的 pre-SDK
  `core:provider:protocol:tooling:skills:policy:memory:gateway` PYTHONPATH、
  `PYTHONNOUSERSITE=1`、`LITELLM_LOCAL_MODEL_COST_MAP=True` 与
  `uv run --project octoagent --no-sync`。

## 行为与回归

### F152 blast radius

命令覆盖完整 Core、Policy、Protocol 测试及 F152 architecture authority：

```text
714 passed, 1 warning in 4.41s
```

唯一 warning 位于既有
`apps/gateway/src/octoagent/gateway/harness/tool_registry.py:38`：
`ToolEntry.schema` 遮蔽 `BaseModel` 属性；不是 F152 新增回归。

### 全仓库 Gate

```text
208 passed in 320.05s
```

首次执行曾在 F151 clean-wheel relocation 测试处被误判为挂起并人工中断。只读诊断确认
它不是死锁，而是每条 import occurrence 都重新遍历完整 site-packages 的确定性算法
退化。修复先在既有 import-classification selector 上取得
`repeated import owner was rescanned` RED，再加入 transaction-local owner cache：

- relocation：`138.68s → 21.34s`，行为保持 PASS；
- clean-wheel 全文件：`9 passed in 175.75s`；
- 随后全 Gate：`208 passed`。

缓存只去重同一 classification transaction 内相同 module 的 owner lookup；没有放宽
target/host owner、lock version、workspace source leak 或 unknown import 的
fail-closed 语义。

## 静态与安全门

- F152 11 个 production/test 文件：Ruff check `PASS`。
- 同一 11 个文件：Ruff format check `PASS`。
- runtime/clean-wheel checker 与 tests：Ruff、format、`py_compile`、C901≤10 均
  `PASS`。
- `git diff --check`：`PASS`。
- credential/private-key/Cloudflare service-secret/真实样式 owner email 扫描：
  `PASS`（11 个 F152 production/test 文件）。
- audit model/SQLite schema 的敏感正文、token、signature、nonce、email 与系统
  identifier 负向合同由 Store 测试覆盖；raw/normalized 服务端表物理不存在。

## 冻结实现 SHA-256

| Authority | SHA-256 |
|---|---|
| Core models | `6afd8a043ac573969eb0584ccf81d51d41a6b449de2e801ecd8fd548ab0154cb` |
| Core state machine | `c2711457d0d905161a5537a8b3b91b9ed63c77d45763f812af8d34a4dafc4885` |
| SQLite store | `7d8388045f977d4f044105f647a597cf8d3c2d9ac59abf76cf687a00703a5f0f` |
| Policy | `b6dce338718f9fe2d7e2bd902b56470adf609555d2f4e0c74e952dca5e743994` |
| Protocol projection | `6a8fc35589ac9fb849b80bfb95622743274b3bf8117d2566d01c8e476701bd85` |
| F152 architecture test | `e0fa617346635c8c28c14f85568a4fdd115920bc22c1c563cde3caa27ed1802b` |
| Runtime architecture checker | `44a70d5f65206934cd17f6cf4e673fb58ef73e3a16b440f07b777ba1ce3457db` |

## TDD、分层、架构与坏味道

- TDD：T001-T012 均先有稳定缺失能力或单缺陷 RED，再取得 GREEN；T012 提炼删除
  transaction 后复验 REFACTOR。T014 使用 focused + full Gate 收口。
- 分层：F152 只覆盖 L4 纯合同和 L3 SQLite transaction；没有 UI、真 LLM、网络或
  iOS transport。原生 transport 从 F153 开始。
- 架构：Core 是唯一领域/状态/store authority，Policy 只决策，Protocol 只投影；
  Gateway/iOS/HealthKit/EventKit owner 仍物理缺席。
- 坏味道：无第二 Memory/audit/identity/session/registry，无 broad exception，
  F152 production function≤50、McCabe≤10；clean-wheel 性能修复使用 transaction-local
  cache，没有 mutable global 或第二 resolver。

## 仍未完成

- F153 原生 iOS device trust、Keychain/Secure Enclave、真机 transport 尚未实施。
- F154 HealthKit、F155 EventKit 决策、F156 SwiftUI 产品体验尚未实施。
- F158 的提交、CI、个人部署和 Web+iOS 全场景/视觉 E2E 总验收尚未完成。
