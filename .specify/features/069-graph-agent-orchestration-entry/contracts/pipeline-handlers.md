# 契约：通用 Pipeline Handler

**Feature**: 065
**模块**: `octoagent/packages/skills/src/octoagent/skills/pipeline_handlers.py`

---

## Handler 注册协议

所有通用 handler 实现 `PipelineNodeHandler` 协议（已定义在 `pipeline.py`）：

```python
class PipelineNodeHandler(Protocol):
    async def __call__(
        self,
        *,
        run: SkillPipelineRun,
        node: SkillPipelineNode,
        state: dict[str, Any],
    ) -> PipelineNodeOutcome: ...
```

---

## 内置 Handler 清单

### terminal.exec

**用途**: 在终端执行命令。
**handler_id**: `terminal.exec`

**输入（从 state 读取）**:
- `state["terminal_commands"]`: dict，key 为节点 ID，value 为命令字符串
- 或 `node.metadata["command"]`: 硬编码在 PIPELINE.md 节点定义中

**行为**:
1. 从 state 或 node.metadata 获取要执行的命令
2. 检查 `side_effect_cursor`：如果已有该节点的 cursor 且标记完成，返回 RUNNING + "skipped (idempotent)"
3. 通过 `asyncio.create_subprocess_shell` 执行命令
4. 执行成功（exit code 0）→ 返回 `PipelineNodeOutcome(status=RUNNING, summary="...", side_effect_cursor="{node_id}:done")`
5. 执行失败（exit code != 0）→ 返回 `PipelineNodeOutcome(status=FAILED, summary="exit code {code}: {stderr}")`

**安全约束**: 此 handler 的使用必须经过 Policy Engine 审核（Constitution 原则 4）。

### approval_gate

**用途**: 审批门禁，暂停 Pipeline 等待人工决策。
**handler_id**: `approval_gate`

**行为**:
1. 从 `node.metadata` 读取审批描述（`approval_description`）和审批选项
2. 返回 `PipelineNodeOutcome(status=WAITING_APPROVAL, summary=approval_description, approval_request={...})`
3. Pipeline 进入 WAITING_APPROVAL 暂停

**审批恢复后**:
- 由 `SkillPipelineEngine.resume_run()` 处理，跳过此节点，执行 `next_node_id`

### input_gate

**用途**: 用户输入门禁，暂停 Pipeline 等待用户提供数据。
**handler_id**: `input_gate`

**行为**:
1. 从 `node.metadata` 读取所需输入字段描述（`input_fields`）
2. 返回 `PipelineNodeOutcome(status=WAITING_INPUT, summary="...", input_request={...})`
3. Pipeline 进入 WAITING_INPUT 暂停

**输入恢复后**:
- 用户通过 `graph_pipeline(action="resume", input_data={...})` 提供数据
- `resume_run(state_patch=input_data)` 将数据合并到 state，继续执行

### transform.passthrough

**用途**: 透传节点（测试/调试用），不执行任何操作。
**handler_id**: `transform.passthrough`

**行为**:
1. 返回 `PipelineNodeOutcome(status=RUNNING, summary="passthrough")`

---

## Handler 幂等性约定

所有产生 side-effect 的 handler **必须**遵循以下约定：

1. **检查 cursor**：在执行前检查 `state` 中是否已有 `side_effect_cursor` 标记该操作已完成
2. **设置 cursor**：执行成功后，在 `PipelineNodeOutcome.side_effect_cursor` 中设置唯一标记
3. **跳过已完成**：如果 cursor 表明操作已完成，返回成功结果但不重复执行

```python
async def _terminal_exec_handler(*, run, node, state) -> PipelineNodeOutcome:
    cursor_key = f"{node.node_id}:done"
    if run.state_snapshot.get("_cursor_" + node.node_id) == cursor_key:
        return PipelineNodeOutcome(status=PipelineRunStatus.RUNNING, summary="skipped (idempotent)")

    # ... 执行命令 ...

    return PipelineNodeOutcome(
        status=PipelineRunStatus.RUNNING,
        summary=f"command executed: {output[:200]}",
        side_effect_cursor=cursor_key,
        state_patch={"_cursor_" + node.node_id: cursor_key},
    )
```

---

## Handler 注册时机

```python
# GraphPipelineTool.__init__() 中
self._engine.register_handler("terminal.exec", terminal_exec_handler)
self._engine.register_handler("approval_gate", approval_gate_handler)
self._engine.register_handler("input_gate", input_gate_handler)
self._engine.register_handler("transform.passthrough", passthrough_handler)
```

后续可通过插件机制或 PIPELINE.md 配置动态注册更多 handler。
