# 技术调研报告: M2 Integration Acceptance

**特性分支**: `codex/feat-023-m2-integration-acceptance`  
**调研日期**: 2026-03-07  
**调研模式**: full（本地 references + 代码基线）  
**产品调研基础**: [product-research.md](product-research.md)

## 1. 调研目标

**核心问题**:

- 当前代码里哪些路径已经具备联合验收所需的真实组件，哪些还停留在分段测试
- 023 应该通过哪些“最小断点修补”把首次使用链接上，而不新增业务能力
- A2A、JobRunner、operator inbox、Memory/Import/Recovery 应该如何在 023 中被同一套测试和报告消费

## 2. 当前代码基线

### 2.1 首次使用与 DX 基线

当前相关代码已经存在：

- `octoagent/packages/provider/src/octoagent/provider/dx/config_bootstrap.py`
- `octoagent/packages/provider/src/octoagent/provider/dx/config_commands.py`
- `octoagent/packages/provider/src/octoagent/provider/dx/doctor.py`
- `octoagent/packages/provider/src/octoagent/provider/dx/onboarding_service.py`
- `octoagent/packages/provider/src/octoagent/provider/dx/telegram_verifier.py`

但存在三个明确缺口：

1. `config init` 写的是 `octoagent.yaml` / `litellm-config.yaml`，而 `doctor` 仍保留对 `.env` 的强前置判断，导致首次入口分叉。
2. Telegram channel 默认 `enabled=false`，且 `config` 侧没有把 channel 设定纳入可操作闭环。
3. `TelegramOnboardingVerifier.verify_first_message()` 当前只验证 bot 出站送达，不验证 gateway 侧是否真的接到了用户入站消息。

### 2.2 Telegram / operator inbox 基线

相关能力已经具备：

- `octoagent/apps/gateway/src/octoagent/gateway/services/telegram.py`
- `octoagent/apps/gateway/src/octoagent/gateway/routes/telegram.py`
- `octoagent/apps/gateway/src/octoagent/gateway/routes/operator_inbox.py`
- `octoagent/apps/gateway/src/octoagent/gateway/services/operator_actions.py`
- `octoagent/apps/gateway/src/octoagent/gateway/services/operator_inbox.py`

当前状况：

- pairing request、Telegram 入站创建 task、Web operator inbox、operator actions 都已存在
- 但 023 仍缺“同一 item 在 Web / Telegram 两端等价处理”的联合验收用例

### 2.3 A2A / JobRunner / interactive execution 基线

相关能力已经具备：

- `octoagent/packages/protocol/src/octoagent/protocol/adapters.py`
- `octoagent/packages/protocol/src/octoagent/protocol/mappers.py`
- `octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py`
- `octoagent/apps/gateway/src/octoagent/gateway/services/task_runner.py`
- `octoagent/apps/gateway/src/octoagent/gateway/services/worker_runtime.py`
- `octoagent/apps/gateway/tests/test_task_runner.py`
- `octoagent/packages/protocol/tests/test_a2a_models.py`

当前状况：

- A2A TASK / RESULT / ERROR、state mapper、artifact mapper、replay guard 已完成
- JobRunner / WorkerRuntime / checkpoint / human input / cancel / resume 已完成
- 但还缺“协议消息进入执行链”的联合验收，即没有用 `A2AMessage` 真正驱动执行面并回传结果的证据

### 2.4 Memory / Chat Import / Recovery 基线

相关能力已经具备：

- `octoagent/packages/memory/src/octoagent/memory/service.py`
- `octoagent/packages/memory/src/octoagent/memory/imports/service.py`
- `octoagent/packages/provider/src/octoagent/provider/dx/chat_import_service.py`
- `octoagent/packages/provider/src/octoagent/provider/dx/backup_service.py`
- `octoagent/packages/provider/tests/test_chat_import_service.py`
- `octoagent/packages/provider/tests/test_backup_service.py`

当前状况：

- `WriteProposal -> validate -> commit` 已冻结
- `octo import chats`、`ImportReport`、`backup create`、`export chats`、`restore dry-run` 已交付
- 但还缺“导入后的持久化数据被 backup/export/restore 联合消费”的验收线

## 3. 方案对比

### 方案 A：只补验证报告，不做任何代码修补

- 优点：范围最小
- 缺点：
  - 无法解决 `config init` / `doctor` 不一致
  - 无法解决 onboarding 首条消息误判完成
  - 很多联合验收测试会被现有断点卡住

### 方案 B：少量 DX 修补 + 联合验收测试 + 验收报告（推荐）

- 优点：
  - 与 023 “不引入新能力”的边界一致
  - 能修复真正影响用户闭环的断点
  - 能把 015-022 已有能力稳定编织成 M2 证据链
- 缺点：需要在 provider/gateway/protocol/integration tests 同时动一些文件

### 方案 C：顺势扩一层更完整的 onboarding / ops UI

- 优点：表面体验更好
- 缺点：范围失控，已经超出 023 的里程碑定义

## 4. 推荐架构

**推荐**: 方案 B，采用“最小 DX 修补 + 联合验收层”策略。

### 4.1 修补层

只允许修三类阻塞断点：

1. **首次配置断点**
   - 统一 `config init` 与 `doctor` 的前置假设
   - 让 Telegram channel 配置能进入可操作闭环

2. **首次入站断点**
   - onboarding 的 first message 必须以“检测到入站 task / 等价证据”为完成标准，而不是单纯 `sendMessage()`

3. **联合链断点**
   - 为 A2A -> runtime
   - Web/Telegram operator parity
   - import -> recovery
   增加真正的联合测试与最小胶水代码

### 4.2 验收层

在 `octoagent/tests/integration/` 或等价集成测试层新增 023 测试，至少包括：

- 首次使用场景
- operator parity 场景
- A2A + JobRunner 场景
- Memory / Import / Recovery 场景

### 4.3 报告层

输出一份 M2 验收报告，内容至少包括：

- 验收矩阵
- 自动化测试命令
- 每条链的通过证据
- 剩余风险
- 明确不在 023 范围内的项

## 5. 关键技术设计

### 5.1 首次使用验收线

建议以以下真实组件为主：

- `config_commands.py`
- `DoctorRunner`
- `OnboardingService`
- `TelegramGatewayService`
- `OperatorInboxService` / `OperatorActionService`

外部 Telegram API 允许通过 mock transport / fake bot client 替身，但：

- pairing 状态
- task 创建
- operator action 审计
- onboarding resume

必须使用真实本地组件。

### 5.2 A2A + JobRunner 验收线

建议的最小联合链：

1. 创建 task / dispatch request
2. 生成 `DispatchEnvelope`
3. `build_task_message()`
4. `dispatch_envelope_from_task_message()`
5. 进入 `WorkerRuntime` / `TaskRunner`
6. 产出 `WorkerResult`
7. 映射为 A2A `RESULT` / `ERROR`

023 不需要新增新的 A2A API，但必须证明协议契约和执行面之间无断层。

### 5.3 operator parity 验收线

建议至少覆盖：

- pairing approve / reject
- approval approve / deny
- retry task
- cancel task
- alert acknowledge

而且要验证：

- item_id 一致
- 审计事件写入同一 operational task
- 两端操作都能得到相同的 outcome 语义

### 5.4 Memory / Import / Recovery 验收线

建议最小链：

1. `octo import chats`
2. Memory commit / fragment / artifact 落盘
3. `octo export chats`
4. `octo backup create`
5. `octo restore dry-run`
6. 检查恢复摘要与导入数据证据

这样能证明 020、021、022 不是平行存在，而是进入同一 durability boundary。

## 6. 风险清单

| # | 风险描述 | 概率 | 影响 | 缓解策略 |
|---|---|---|---|---|
| 1 | 023 变成“顺手做更多 UX” | 高 | 高 | spec/tasks 明确写死只允许修阻塞断点 |
| 2 | 首次使用链仍依赖手工编辑状态文件 | 中 | 高 | 明确 Web pairing 为主路径，手工 state 仅作降级 |
| 3 | A2A 与 JobRunner 继续停留在分段测试 | 中 | 高 | 增加一条协议到执行面的集成测试 |
| 4 | operator parity 只验证 approve，不验证 retry/cancel/ack | 中 | 中 | 验收矩阵显式列出动作全集 |
| 5 | import / backup / restore 仍各测各的 | 中 | 高 | 增加联合验收与一体化报告 |

## 7. 产品-技术对齐度

| 产品目标 | 技术方案覆盖 | 说明 |
|---|---|---|
| 首次 working flow | ✅ | 通过 DX 修补 + onboarding/gateway 联合验收 |
| 控制面等价 | ✅ | 通过 operator parity 测试与审计链验证 |
| 协议到执行面联通 | ✅ | 通过 A2A -> runtime 联合验收 |
| 导入数据可恢复 | ✅ | 通过 import -> recovery 联合验收 |
| 不扩业务能力 | ✅ | 仅修补现有断点，不引入新域模型/新入口面 |

## 8. 结论与建议

023 的核心实现不是“搭一个新的体验层”，而是承认当前代码已经足够接近 M2 完整态，然后用很克制的修补和很严格的验收，把这些能力真正收束成用户闭环与里程碑证据。
