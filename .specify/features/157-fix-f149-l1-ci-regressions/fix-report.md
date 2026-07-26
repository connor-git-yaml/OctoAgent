# F157 故障诊断报告

## 权威失败

- GitHub Actions run：`30196329042`
- 失败 job：`l1-playwright`
- 结果：4 failed / 15 passed

## 根因

### 1. 聊天正文丢失

**现象**：后台任务成功、artifact 已写入，但 Web 显示空正文兜底。

**5-Why**：

1. Web 为什么显示兜底？`MODEL_CALL_COMPLETED.payload.response_summary` 不存在。
2. 为什么不存在？F149 Task SSE 有限合同把所有 model-call 事件统一投影为 diagnostic。
3. 为什么 diagnostic 不可用于 Chat？Chat reducer 只从顶层 `response_summary` 读取用户回复。
4. 为什么 F149 测试没有发现？合同只覆盖 state、artifact 和未知历史事件，没有覆盖共享 SSE endpoint 上的既有 Chat consumer。
5. 为什么是架构问题？同一 Gateway SSE endpoint 同时服务 Chat 与 TaskDetail，有限投影只按 TaskDetail 消费面设计，遗漏了另一个正式 consumer。

### 2. A-wave 多出 drift 事件

fixture 使用固定过去时间创建 RUNNING task。CI 执行时间晚于 watchdog 阈值，生产 watchdog 正确发出 `TASK_DRIFT_DETECTED`。问题在 fixture 时间语义，不在 watchdog。

### 3. Approvals 空状态被误判

通用 390px 断言要求每个 `<main>` 都有交互元素，但 Approvals 的只读空状态合法无 CTA。键盘可达性应由页面业务操作或全局 shell 操作满足，而不是制造无意义页面按钮。

## 影响范围

- Gateway Task SSE 有限投影及合同测试。
- TaskDetail raw decoder 对合法 model-call projection 的识别与明确忽略。
- A-wave L1 fixture 时间。
- 390px 无障碍 L1 helper。
- 前端架构说明。

不影响生产数据模型、OpenAPI endpoint、视觉设计、iOS 范围、watchdog 阈值或部署协议。

## 第二轮权威 CI：干净检出差异

- GitHub Actions run：`30197077191`
- 已通过：`l1-playwright`、frontend、architecture、benchmark
- 失败 job：`backend-deterministic`
- 失败摘要：5 failed；其余 `Event loop is closed` 为 pytest 退出阶段日志噪声，不是额外失败。

五项根因均位于测试 fixture：

1. atomic namespace 测试读取被忽略的本地 BEFORE snapshot；改为从 manifest
   `base_sha` 的 Git blob 读取并 AST 校验。
2. cross-role 临时仓库复制本地 bootstrap `tree.json`；改为生成 exact 12-field
   hermetic tree，并同步临时 anchor/index hash。
3. F150 临时实现 fixture 缺少当前受保护的
   `_validate_request_front_door_config` sibling；补齐后继续独立验证 sibling drift。
4. quarantine rerun report 直接读取 canonical local JUnit；改为在独立临时仓库按
   committed index 的 nodeids 物化 clean JUnit，再调用原 checker 报告链路。
5. historic formal invocation 检查把 ignored raw 当成 committed truth；改为始终校验
   immutable index metadata，并仅在完整 raw artifact set 存在时做逐字节交叉验证。

生产 `check-runtime-architecture.py` 保持原字节，不增加干净检出的兼容旁路。

## 在线调研

跳过。失败由本仓库 CI log、trace、源代码和本地可重复合同完整解释，不依赖时效性外部事实。
