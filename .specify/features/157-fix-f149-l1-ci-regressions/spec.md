# F157：修复 F149 L1 CI 回归

## 背景

F149 合并后的权威 `master` CI（run `30196329042`）在 `l1-playwright` 出现四项失败：

1. 两个聊天场景的模型回复在 Web 中变成“已收到回复，但没有可显示的正文”。
2. A-wave 固定的历史时间触发 watchdog，导致预期事件链多出 drift 事件。
3. 390px Approvals 空状态没有页面内交互控件，被通用无障碍断言误判。

修复上述四项后，第二轮权威 `master` CI（run `30197077191`）确认
`l1-playwright`、frontend、architecture 与 benchmark 已通过，但
`backend-deterministic` 暴露五项 F151 gate 测试对开发机本地 ignored
evidence 的隐式依赖，以及一项 F150 临时实现 fixture 与当前合同不同步的问题。

本 Fix 只恢复已批准行为，不新增页面、产品能力、协议入口或视觉方案。

## 需求

### FR-001 安全聊天 SSE 投影

- Gateway 必须继续以 `task_sse_contract.py` 作为唯一 Task SSE 输出合同。
- 已知 `MODEL_CALL_STARTED`、`MODEL_CALL_COMPLETED`、`MODEL_CALL_FAILED` 必须只投影 Chat 实际消费的有限字段。
- 投影必须保留区分内部 skill 调用与用户可见调用所需的 `skill_id` / `artifact_ref`。
- 完成事件必须保留非空 `response_summary`；失败事件必须提供用户可见的有限 `error`。
- 原始 token usage、provider、model、tool call、任意额外字段和敏感诊断不得穿透。
- 缺少最低有效字段的旧事件、历史事件或畸形事件必须继续降级为有界、净化后的 Advanced diagnostic。

### FR-002 TaskDetail 边界

- TaskDetail 的业务状态仍只由 state transition 驱动，artifact 事件仍只触发局部刷新。
- Chat model-call 投影不得驱动 TaskDetail 阶段、状态、artifact 刷新或新界面。
- 未知、历史和扩展事件仍只进入 Advanced diagnostic。

### FR-003 A-wave 确定性 fixture

- A-wave L1 fixture 必须在场景启动时生成非过期的运行中任务，避免 watchdog 合法地产生与场景无关的 drift 事件。
- 不得通过关闭 watchdog、放宽生产检测或吞掉 drift 事件修复测试。

### FR-004 390px 空状态无障碍

- 390px Web 窄窗口仍需可键盘操作。
- 页面业务区存在操作时，至少一个操作必须可聚焦。
- 合法只读空状态可以没有业务操作，但全局 shell 必须仍提供可聚焦操作。
- 不得为满足测试给空状态添加无业务意义的 CTA，也不得改变 Claude Design 最初方案的视觉层级、留白、卡片节奏或排版。

### FR-005 Gate 测试的干净检出可移植性

- F151 gate 测试不得依赖仅存在于开发机、被 Git 忽略的 `evidence/local/**` 原始文件。
- 需要校验 committed truth 时，测试必须读取已提交的 anchor/index 元数据或指定 Git baseline。
- 需要原始 JUnit/tree/invocation 的场景必须在独立临时仓库构造 hermetic fixture，不得伪造 production checker 的兼容分支。
- F150 临时实现 fixture 必须与当前受保护合同完整一致，仍需独立验证 sibling drift。
- 不得放宽 F151 checker、evidence schema、run↔index 闭包或 no-growth ceiling。

## 非目标

- 不调整 Claude Design 视觉方案。
- 不把 390px Web 当作手机产品；手机产品仍只走原生 iOS App。
- 不新增 SSE endpoint、第二 decoder、第二 transport、store 或兼容旁路。
- 不修改 watchdog 生产阈值。

## 成功标准

1. 四个原失败 L1 场景全部通过。
2. 新增合同测试证明 Chat 可见字段存在、额外原始字段不会穿透、畸形历史事件仍进入 diagnostic。
3. F149 前端单元测试、Gateway 合同测试、完整 L1、F151 gate、架构门和权威 `master` CI 全绿。
4. UI 像素与设计制品不发生变化。
