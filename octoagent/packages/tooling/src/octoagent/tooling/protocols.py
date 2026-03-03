"""Protocol 接口定义 -- Feature 004 Tool Contract + ToolBroker

对齐 contracts/tooling-api.md。定义所有 Protocol 接口，供 Feature 005/006 引用。
包含：EventStoreProtocol、ArtifactStoreProtocol、ToolBrokerProtocol、ToolHandler、BeforeHook、AfterHook、PolicyCheckpoint。
"""

from __future__ import annotations

from typing import Any, Protocol

from .models import (
    BeforeHookResult,
    CheckResult,
    ExecutionContext,
    FailMode,
    RegisterToolResult,
    RegistryDiagnostic,
    ToolMeta,
    ToolProfile,
    ToolResult,
)


class EventStoreProtocol(Protocol):
    """EventStore 接口契约 -- 事件持久化

    ToolBroker 和 EventGenerationHook 依赖此接口写入事件。
    具体实现由 octoagent.core 提供。
    """

    async def append_event(self, event: Any) -> None:
        """追加事件"""
        ...

    async def get_next_task_seq(self, task_id: str) -> int:
        """获取指定 task 的下一个序列号"""
        ...


class ArtifactStoreProtocol(Protocol):
    """ArtifactStore 接口契约 -- Artifact 存储

    LargeOutputHandler 依赖此接口存储大输出。
    具体实现由 octoagent.core 提供。
    """

    async def put_artifact(self, artifact: Any, content: bytes) -> None:
        """存储 Artifact 及其内容"""
        ...


class ToolHandler(Protocol):
    """工具执行处理函数 Protocol -- 对齐 contracts/tooling-api.md §3

    工具的实际执行逻辑。ToolBroker 在 hook 链完成后调用此函数。
    支持同步和异步两种形式。
    """

    async def __call__(self, **kwargs: Any) -> Any:
        """执行工具

        Args:
            **kwargs: 工具参数（与 ToolMeta.parameters_json_schema 对齐）

        Returns:
            工具输出（任意可序列化类型，ToolBroker 将其转换为 str）
        """
        ...


class BeforeHook(Protocol):
    """before hook Protocol -- 对齐 spec FR-019/020/021

    在工具执行前运行，可修改参数或拒绝执行。
    """

    @property
    def name(self) -> str:
        """hook 名称"""
        ...

    @property
    def priority(self) -> int:
        """优先级（从低到高执行，数值越小越优先）"""
        ...

    @property
    def fail_mode(self) -> FailMode:
        """失败模式"""
        ...

    async def before_execute(
        self,
        tool_meta: ToolMeta,
        args: dict[str, Any],
        context: ExecutionContext,
    ) -> BeforeHookResult:
        """执行前钩子"""
        ...


class AfterHook(Protocol):
    """after hook Protocol -- 对齐 spec FR-019/022

    在工具执行后运行，可修改结果。
    """

    @property
    def name(self) -> str:
        """hook 名称"""
        ...

    @property
    def priority(self) -> int:
        """优先级（从低到高执行）"""
        ...

    @property
    def fail_mode(self) -> FailMode:
        """失败模式"""
        ...

    async def after_execute(
        self,
        tool_meta: ToolMeta,
        result: ToolResult,
        context: ExecutionContext,
    ) -> ToolResult:
        """执行后钩子"""
        ...


class PolicyCheckpoint(Protocol):
    """Policy 检查点 Protocol -- 对齐 spec FR-024, contracts/tooling-api.md §5

    Feature 004 定义 Protocol，Feature 006 提供实现。
    作为 BeforeHook 注册到 ToolBroker，对 irreversible 工具触发审批流。

    行为约定:
    - 对 side_effect_level=none 的工具，返回 allowed=True
    - 对 side_effect_level=reversible 的工具，默认 allowed=True
    - 对 side_effect_level=irreversible 的工具，执行策略检查
    - fail_mode 强制为 "closed"——检查失败时拒绝执行
    """

    async def check(
        self,
        tool_meta: ToolMeta,
        params: dict[str, Any],
        context: ExecutionContext,
    ) -> CheckResult:
        """执行策略检查

        Args:
            tool_meta: 工具元数据
            params: 调用参数
            context: 执行上下文

        Returns:
            CheckResult 包含是否允许执行和原因
        """
        ...


class ToolBrokerProtocol(Protocol):
    """ToolBroker 接口契约 -- 对齐 spec FR-023, contracts/tooling-api.md §2

    所有工具调用必须经过 Broker，确保 hook 链路完整执行。
    Feature 005/006 可基于此 Protocol 编写 mock 实现。
    """

    async def register(
        self,
        tool_meta: ToolMeta,
        handler: ToolHandler,
    ) -> None:
        """注册工具到 Broker

        Args:
            tool_meta: 工具元数据（由 Schema Reflection 生成）
            handler: 工具执行处理函数

        Raises:
            ToolRegistrationError: 名称冲突或缺少必填元数据
        """
        ...

    async def try_register(
        self,
        tool_meta: ToolMeta,
        handler: ToolHandler,
    ) -> RegisterToolResult:
        """尝试注册工具（失败不抛异常，返回结构化诊断）"""
        ...

    async def discover(
        self,
        profile: ToolProfile | None = None,
        group: str | None = None,
    ) -> list[ToolMeta]:
        """发现可用工具

        Args:
            profile: 按 Profile 过滤（含该级别及以下）
            group: 按逻辑分组过滤

        Returns:
            匹配的 ToolMeta 列表
        """
        ...

    async def execute(
        self,
        tool_name: str,
        args: dict[str, Any],
        context: ExecutionContext,
    ) -> ToolResult:
        """执行工具调用

        Args:
            tool_name: 目标工具名称
            args: 调用参数
            context: 执行上下文

        Returns:
            ToolResult 结构化结果
        """
        ...

    def add_hook(self, hook: BeforeHook | AfterHook) -> None:
        """注册 Hook 扩展点

        Args:
            hook: BeforeHook 或 AfterHook 实例
        """
        ...

    async def unregister(self, tool_name: str) -> bool:
        """注销工具

        Args:
            tool_name: 工具名称

        Returns:
            True 如果成功注销，False 如果工具不存在
        """
        ...

    @property
    def registry_diagnostics(self) -> list[RegistryDiagnostic]:
        """获取工具注册诊断列表（只读快照）"""
        ...
