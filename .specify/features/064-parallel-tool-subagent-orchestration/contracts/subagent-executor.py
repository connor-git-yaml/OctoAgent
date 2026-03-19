"""Feature 064 P1-A: SubagentExecutor 设计契约。

定义 Subagent 独立执行循环的接口和行为规范。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ============================================================
# spawn_subagent() 扩展签名
# ============================================================


class SpawnSubagentParams(BaseModel):
    """spawn_subagent() 扩展参数。

    位置: apps/gateway/src/octoagent/gateway/services/subagent_lifecycle.py::spawn_subagent

    原签名:
        async def spawn_subagent(*, store_group, parent_worker_runtime_id, name, persona_summary)
            -> tuple[AgentRuntime, AgentSession]

    新签名:
        async def spawn_subagent(
            *,
            store_group: StoreGroup,
            parent_worker_runtime_id: str,
            parent_task_id: str,               # 新增：父 Task ID
            task_description: str,             # 新增：子任务描述
            name: str = "",
            persona_summary: str = "",
            permission_preset: str = "normal", # 新增：权限 Preset（不高于父 Worker）
            usage_limits: dict | None = None,  # 新增：资源限制覆盖
            # 依赖注入
            model_client: StructuredModelClientProtocol,
            tool_broker: ToolBrokerProtocol,
            event_store: EventStoreProtocol,
            parent_manifest: SkillManifest,     # 新增：父 Worker manifest（用于衍生）
        ) -> tuple[AgentRuntime, AgentSession, SubagentExecutor]
    """

    parent_worker_runtime_id: str = Field(description="父 Worker runtime ID")
    parent_task_id: str = Field(description="父 Task ID")
    task_description: str = Field(description="子任务描述文本")
    name: str = Field(default="", description="Subagent 名称")
    persona_summary: str = Field(default="", description="Subagent 角色描述")
    permission_preset: str = Field(
        default="normal",
        description="权限 Preset（minimal/normal/full），不得高于父 Worker",
    )
    usage_limits: dict[str, Any] | None = Field(
        default=None,
        description="资源限制覆盖（覆盖默认 UsageLimits）",
    )


# ============================================================
# SubagentExecutor 设计
# ============================================================


class SubagentExecutorSpec:
    """SubagentExecutor 行为规范。

    位置（新建或扩展）: apps/gateway/src/octoagent/gateway/services/subagent_lifecycle.py

    职责:
    1. 管理 Subagent 的独立 SkillRunner 执行循环
    2. 创建 Child Task（parent_task_id 关联父 Task）
    3. 创建 A2AConversation（source=parent Worker, target=subagent）
    4. 心跳上报（每 N 步发射 TASK_HEARTBEAT）
    5. 执行完成后发送 A2A RESULT + 流转 Child Task 到终态
    6. 异常退出时发送 A2A ERROR + 流转 Child Task 到 FAILED
    7. 支持 CANCEL 优雅终止

    生命周期:
        spawn_subagent() → SubagentExecutor.start()
            → asyncio.Task 中运行 _run_loop()
                → SkillRunner.run() 执行子任务
                → 每 N 步发射 TASK_HEARTBEAT
                → 完成: A2A RESULT + SUCCEEDED
                → 异常: A2A ERROR + FAILED
                → 取消: A2A CANCEL response + CANCELLED
            → _cleanup() 释放资源

    伪代码:

    class SubagentExecutor:
        def __init__(
            self,
            *,
            child_task: Task,
            skill_runner: SkillRunner,
            manifest: SkillManifest,
            execution_context: SkillExecutionContext,
            a2a_conversation_id: str,
            parent_agent_uri: str,
            subagent_agent_uri: str,
            event_store: EventStoreProtocol,
            store_group: StoreGroup,
            heartbeat_interval: int = 5,
        ):
            self._child_task = child_task
            self._runner = skill_runner
            self._manifest = manifest
            self._context = execution_context
            self._a2a_conversation_id = a2a_conversation_id
            self._parent_agent_uri = parent_agent_uri
            self._subagent_agent_uri = subagent_agent_uri
            self._event_store = event_store
            self._store_group = store_group
            self._heartbeat_interval = heartbeat_interval
            self._cancel_event = asyncio.Event()
            self._asyncio_task: asyncio.Task | None = None

        @property
        def child_task_id(self) -> str:
            return self._child_task.task_id

        @property
        def is_running(self) -> bool:
            return self._asyncio_task is not None and not self._asyncio_task.done()

        async def start(self):
            '''启动独立 asyncio.Task 执行循环。'''
            self._asyncio_task = asyncio.create_task(
                self._run_loop(),
                name=f"subagent-{self._child_task.task_id}",
            )

        async def cancel(self):
            '''优雅取消。设置取消标志并 cancel asyncio.Task。'''
            self._cancel_event.set()
            if self._asyncio_task:
                self._asyncio_task.cancel()

        async def _run_loop(self):
            try:
                # 流转 Child Task 到 RUNNING
                await self._transition_task(TaskStatus.RUNNING)

                # 发送 A2A TASK 消息
                await self._send_a2a_task_message()

                # 执行 SkillRunner
                result = await self._runner.run(
                    manifest=self._manifest,
                    execution_context=self._context,
                    skill_input={},
                    prompt=self._context.metadata.get("task_description", ""),
                )

                # 根据结果发送 A2A 消息并流转 Task
                if result.status == SkillRunStatus.SUCCEEDED:
                    await self._send_a2a_result(result)
                    await self._transition_task(TaskStatus.SUCCEEDED)
                else:
                    await self._send_a2a_error(result)
                    await self._transition_task(TaskStatus.FAILED)

            except asyncio.CancelledError:
                await self._send_a2a_cancel_response()
                await self._transition_task(TaskStatus.CANCELLED)
                raise

            except Exception as exc:
                await self._send_a2a_error_from_exception(exc)
                await self._transition_task(TaskStatus.FAILED)

            finally:
                await self._cleanup()

        async def _cleanup(self):
            '''清理 Subagent 资源：关闭 Session、归档 Runtime。'''
            await kill_subagent(
                store_group=self._store_group,
                subagent_runtime_id=self._context.agent_runtime_id,
            )
    """

    pass


# ============================================================
# A2A URI 命名规范
# ============================================================


class A2ASubagentURISpec:
    """Subagent A2A URI 命名规范。

    格式: agent://workers/{parent_runtime_id}/subagents/{subagent_runtime_id}

    示例:
        父 Worker: agent://workers/worker-01JXYZ
        Subagent:  agent://workers/worker-01JXYZ/subagents/subagent-01JABC

    父 Worker URI 作为 A2AConversation 的 source，
    Subagent URI 作为 A2AConversation 的 target。
    """

    pass


# ============================================================
# Subagent 结果注入机制
# ============================================================


class SubagentResultInjectionSpec:
    """Subagent 结果注入父 Worker 对话的机制。

    位置: apps/gateway/src/octoagent/gateway/services/orchestrator.py

    流程:
    1. SubagentExecutor 完成 → 发送 A2A RESULT 消息
    2. Orchestrator 接收 A2A RESULT → 写入 A2A_MESSAGE_RECEIVED 事件到父 Task
    3. Orchestrator 将结果摘要放入 SubagentResultQueue
    4. 父 Worker SkillRunner 在 generate() 前检查 Queue
    5. 有结果时追加到对话历史（作为 user role message）

    注入消息格式:
        {
            "role": "user",
            "content": "[Subagent Result] Subagent '{name}' (task: {child_task_id}) completed:\n"
                       "Status: {status}\n"
                       "Summary: {summary}\n"
                       "Artifacts: {artifact_count} items"
        }

    并发安全:
    - asyncio.Queue 是 FIFO 且单线程安全
    - 多 Subagent 结果按到达顺序注入
    - 不需要额外 Lock

    关键实现点:
    1. Orchestrator 内部维护 _subagent_result_queues: dict[str, asyncio.Queue]
       key = parent_task_id
    2. SubagentExecutor 完成时调用 Orchestrator.enqueue_subagent_result()
    3. SkillRunner 通过 SkillRunnerHook.before_llm_call() 检查队列并注入
       或者：LiteLLMSkillClient.generate() 内部直接检查
    """

    pass
