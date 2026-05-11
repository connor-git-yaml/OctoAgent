"""Execution runtime context helpers for Feature 019."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from octoagent.core.models import RuntimeControlContext

if TYPE_CHECKING:
    from .execution_console import ExecutionConsoleService


_CURRENT_EXECUTION_CONTEXT: ContextVar[ExecutionRuntimeContext | None] = ContextVar(
    "octoagent_execution_runtime_context",
    default=None,
)


@dataclass
class ExecutionRuntimeContext:
    """运行中暴露给 worker/LLM 的 execution helper。"""

    task_id: str
    trace_id: str
    session_id: str
    worker_id: str
    backend: str
    console: ExecutionConsoleService
    work_id: str = ""
    runtime_kind: str = ""
    agent_session_id: str = ""
    # F094 B0: agent_runtime_id 接线（Codex plan MED-2 闭环）
    # 工具层（如 memory.write）需要按 agent_runtime_id 解析 worker 私有 namespace。
    # 来源：dispatch_metadata / compiled_context.effective_agent_runtime_id；
    # 由 orchestrator + worker_runtime 两个构造点填充。
    agent_runtime_id: str = ""
    runtime_context: RuntimeControlContext | None = None
    resume_state_snapshot: dict[str, Any] | None = None
    # F099 Codex Final F1 修复：区分"真实 worker dispatch"与"owner-self 主 Agent 自执行"。
    # worker_runtime.py 构造 ExecutionRuntimeContext 时设 True；
    # orchestrator _register_owner_self_execution_session 构造时保持 False（默认值）。
    # _spawn_inject.py 通过此字段决定是否注入 source_runtime_kind=worker，
    # 而非依赖 runtime_kind（target 侧字段，owner-self 路径也为 "worker"）。
    is_caller_worker: bool = False
    _resume_input_consumed: bool = field(default=False, init=False, repr=False)

    async def emit_log(self, stream: str, chunk: str) -> None:
        await self.console.emit_log(
            task_id=self.task_id,
            session_id=self.session_id,
            stream=stream,
            chunk=chunk,
        )

    async def emit_step(self, step_name: str, summary: str = "") -> None:
        await self.console.emit_step(
            task_id=self.task_id,
            session_id=self.session_id,
            step_name=step_name,
            summary=summary,
        )

    async def request_input(
        self,
        prompt: str,
        *,
        approval_required: bool = False,
    ) -> str:
        return await self.console.request_input(
            task_id=self.task_id,
            session_id=self.session_id,
            prompt=prompt,
            actor=f"worker:{self.worker_id}",
            approval_required=approval_required,
        )

    async def consume_resume_input(self) -> str | None:
        if self._resume_input_consumed:
            return None
        self._resume_input_consumed = True
        artifact_id = None
        if self.resume_state_snapshot:
            artifact_id = self.resume_state_snapshot.get("human_input_artifact_id")
        if not artifact_id:
            return None
        return await self.console.load_text_artifact(str(artifact_id))


def get_current_execution_context() -> ExecutionRuntimeContext:
    """获取当前 execution runtime context。"""
    ctx = _CURRENT_EXECUTION_CONTEXT.get()
    if ctx is None:
        raise RuntimeError("execution runtime context is not available")
    return ctx


@contextmanager
def bind_execution_context(
    context: ExecutionRuntimeContext | None,
):
    """临时绑定 execution runtime context。"""
    token: Token[ExecutionRuntimeContext | None] | None = None
    if context is not None:
        token = _CURRENT_EXECUTION_CONTEXT.set(context)
    try:
        yield
    finally:
        if token is not None:
            _CURRENT_EXECUTION_CONTEXT.reset(token)
