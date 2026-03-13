# Research Synthesis: Feature 043 Module Connection Trust-Boundary Hardening

## 核心判断

043 不是新增产品对象，而是把现有 `message -> task -> orchestrator -> delegation -> runtime -> control-plane` 连接主链从“约定驱动”升级成“治理驱动”。

要让这条链真正可信，至少要同时补四层：

1. **Ingress trust split**  
   输入 metadata 和控制 metadata 分仓，默认只让 trusted control 进入执行主链。

2. **Execution ack hardening**  
   chat send 必须对 task create / enqueue 失败 fail-fast，不能继续返回 `accepted`。

3. **Typed contract continuity**  
   delegation / orchestrator / A2A task payload 保持 typed metadata，不再在连接点全部转字符串。

4. **Control-plane partial degrade**  
   snapshot 聚合层支持局部失败、局部回退和明确的 degraded sections。

## 设计取舍

### 取舍 1：不重做整个消息模型

保留现有 `NormalizedMessage.metadata` 作为 input metadata，新增 `control_metadata`。

原因：

- 这样能最小化对现有 Telegram / message route / task creation 的破坏
- 同时给 trusted internal flows 一个正式控制通道

### 取舍 2：生命周期用 scope + explicit clear，而不是引入复杂时钟 TTL

043 先实现：

- `turn` 级 control keys：只对最新一轮生效
- `task` 级 control keys：允许跨 follow-up 继承
- `None / 空字符串` 作为显式清除

原因：

- 能直接解决“旧 profile/tool_profile 残留”问题
- 不会引入额外定时器、后台清理和 projection 复杂度

### 取舍 3：兼容字段保留，但降为 secondary

继续保留：

- `selected_tools_json`
- `runtime_context_json`

但 canonical source 改为 typed metadata / dedicated runtime field。

原因：

- 这样能避免一次性打断既有 frontend/backend 消费方
- 同时停止继续扩大 string-only 技术债

### 取舍 4：snapshot 用 fallback document，而不是直接缺省 section

原因：

- `ControlPlaneDocument` 已经有 `status/degraded/warnings`
- fallback document 能让现有前端继续渲染，而不是因为 key 缺失再次炸掉

## MVP 范围

043 本轮最小闭环包含：

- `UserMessagePayload` trust split
- `TaskService.get_latest_user_metadata()` 改为 control-only merge
- `AgentContext` runtime summary sanitizer
- `/api/chat/send` fail-fast
- `DispatchEnvelope / OrchestratorRequest / A2ATaskPayload` metadata typed 化
- `/api/control/snapshot` partial degrade
- 对应 regression tests

## 风险

- 如果 trusted internal message creators 没同步迁到 `control_metadata`，会出现 lineage/profiles 丢失。
- 如果 lifecycle 策略定义不清，可能一边修掉旧字段残留，一边误伤 child work lineage 继承。
- 如果 snapshot fallback document 和前端既有资源结构不兼容，partial degrade 仍可能表现为 UI 崩溃。

## 建议实施顺序

1. 先冻住 trust-boundary data model 和 contract
2. 再改 task/chat/delegation/control_plane backend
3. 最后补 regression tests 和 verification report
