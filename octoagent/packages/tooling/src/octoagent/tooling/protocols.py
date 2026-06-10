"""Protocol 接口定义 -- Feature 004 Tool Contract + ToolBroker

对齐 contracts/tooling-api.md。定义所有 Protocol 接口，供 Feature 005/006 引用。
包含：EventStoreProtocol、ArtifactStoreProtocol、ToolBrokerProtocol、ToolHandler、BeforeHook、AfterHook、PolicyCheckpoint。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .models import (
    BeforeHookResult,
    CheckResult,
    ExecutionContext,
    FailMode,
    RegisterToolResult,
    RegistryDiagnostic,
    ToolMeta,
    ToolResult,
    ToolSecurityFinding,
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


class EventBroadcasterProtocol(Protocol):
    """事件广播接口契约 -- 用于 SSE 等实时分发。"""

    async def broadcast(self, task_id: str, event: Any) -> None:
        """广播 task 增量事件"""
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


@runtime_checkable
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


@runtime_checkable
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


@runtime_checkable
class ContentThreatScanProtocol(Protocol):
    """F124 T009：tool 结果内容威胁扫描的注入抽象。

    定义在 tooling（下层），ToolBroker 依赖此抽象、**不依赖 gateway**（plan PR2-F1，防反向依赖）；
    具体实现 `ContentThreatScanService` 在 gateway，由 gateway 装配时注入 broker。

    方法 **scope-free**：CONTEXT scope 是实现内部事，`ScanScope` 枚举不跨边界（避免 tooling 反向
    import gateway 或常量分叉）。返回命中的 `ToolSecurityFinding`（不命中空 list），调用方（broker）
    负责挂到 ToolResult.security_findings + emit 审计事件。

    **线程安全契约（F125）**：`scan_tool_context` 实现 MUST 为**同步 + 线程安全 + 无副作用**——
    broker 经 `asyncio.to_thread` 在后台线程调用它（避免大输出扫描阻塞 event loop）。实现不得
    在内部 `await`、调用 `asyncio.get_running_loop()`、或依赖线程亲和资源；否则线程内异常会被
    broker 的 fail-open 吞掉，导致恶意输出无 finding 通过（M-2）。
    """

    def scan_tool_context(
        self, content: str, source_field: str = "output"
    ) -> list[ToolSecurityFinding]:
        """扫 tool 结果内容（CONTEXT scope，单遍全文 + degraded 兜底）。

        Args:
            content: 待扫描的 tool 结果文本。
            source_field: 命中来源字段（"output"|"error"，去重键用，FR-2.7）。

        Returns:
            命中的 ToolSecurityFinding 列表（含 degraded 兜底）；clean 时空 list。
        """
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
        group: str | None = None,
    ) -> list[ToolMeta]:
        """发现可用工具

        Args:
            group: 按逻辑分组过滤

        Returns:
            匹配的 ToolMeta 列表
        """
        ...

    async def get_tool_meta(self, tool_name: str) -> ToolMeta | None:
        """按名称查询工具元数据（含 SideEffectLevel）。

        O(1) 复杂度，从内部 _registry 字典查找。
        返回 None 表示工具未注册。

        Args:
            tool_name: 工具名称

        Returns:
            ToolMeta 或 None
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
