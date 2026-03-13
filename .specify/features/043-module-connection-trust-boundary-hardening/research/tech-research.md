# Tech Research: Feature 043 Module Connection Trust-Boundary Hardening

## 本地实现现状

### 1. USER_MESSAGE metadata 直接进入控制链

证据：

- `octoagent/apps/gateway/src/octoagent/gateway/routes/message.py`
- `octoagent/apps/gateway/src/octoagent/gateway/services/task_service.py`
- `octoagent/apps/gateway/src/octoagent/gateway/services/task_runner.py`
- `octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py`

结论：

- 当前 `metadata` 同时承担“输入提示”和“运行控制”两种角色。
- `get_latest_user_metadata()` 采用历史累积覆盖，导致旧控制字段可能跨轮残留。
- `AgentContext` 直接把 `dispatch_metadata` 原样放进 runtime system block，prompt 注入面过宽。

### 2. chat send 在新建 task 失败时 fail-open

证据：

- `octoagent/apps/gateway/src/octoagent/gateway/routes/chat.py`

结论：

- 新会话分支在 `create_task()` 或 enqueue 异常时只打日志，仍返回 `accepted`。
- 这违反 Durability First 和 Observability is a Feature，因为调用方拿不到真实失败状态。

### 3. delegation dispatch 在连接点丢失 typed metadata

证据：

- `octoagent/apps/gateway/src/octoagent/gateway/services/delegation_plane.py`
- `octoagent/packages/core/src/octoagent/core/models/orchestrator.py`
- `octoagent/packages/protocol/src/octoagent/protocol/models.py`

结论：

- `DispatchEnvelope.metadata` / `OrchestratorRequest.metadata` / `A2ATaskPayload.metadata` 仍以 `dict[str, str]` 为主。
- delegation plane 会把请求 metadata 强制 `str()` 化。
- 结果是 bool / int / object 语义在跨模块传递时被压扁，只能靠后续代码反向猜。

### 4. control-plane snapshot 缺少 section 级异常隔离

证据：

- `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py`

结论：

- `get_snapshot()` 顺序 await 所有资源，任意一个抛错就会中断整个 snapshot。
- 当前文档模型已经支持 `status/degraded/warnings`，但 snapshot 聚合层没有真正利用这套降级面。

## 本地参考对照

### OpenClaw

- `_references/opensource/openclaw/docs/channels/discord.md`
- `_references/opensource/openclaw/docs/web/control-ui.md`

结论：

- 外部 channel 数据被视为 untrusted context，而不是 system-level control。
- 控制台强调分资源调用与局部异常可见，不把单个失败升级成整页不可用。

### Agent Zero

- `_references/opensource/agent-zero/python/extensions/tool_execute_before/_10_unmask_secrets.py`
- `_references/opensource/agent-zero/python/extensions/tool_execute_after/_10_mask_secrets.py`

结论：

- 关键控制链会在执行前后做显式边界收敛，而不是把原始输入直接送入后续执行面。

## 技术设计结论

1. 需要把 `USER_MESSAGE` payload 正式拆成：
   - `metadata`: input metadata（渠道输入提示）
   - `control_metadata`: trusted control envelope

2. 需要引入 control metadata registry
   - 明确哪些键是 `turn-scoped`
   - 哪些键是 `task-scoped`
   - 哪些空值代表显式清除

3. orchestrator / delegation / A2A TASK payload 的 canonical metadata 必须改为 `dict[str, Any]`
   - 同时允许保留 `selected_tools_json` / `runtime_context_json` 之类 string-only 兼容字段
   - 但它们不能再是 canonical source

4. runtime prompt 中只能输出 sanitizer 后的 control summary
   - 不再直接输出原始 `dispatch_metadata`

5. snapshot 聚合层需要资源级 try/catch + degraded fallback document
   - 这样前端即使遇到 `memory/imports/diagnostics` 单点失败，也仍能打开其余区域
