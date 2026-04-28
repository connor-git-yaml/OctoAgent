"""tool_broker.py：Gateway 层 ToolBroker — ToolRegistry dispatch 包装（Feature 084 Phase 3 T043）。

架构角色（plan.md 架构图）：
  TB[ToolBroker] 位于服务层，下游接 ToolRegistry.dispatch()。
  PolicyGate 检查在 dispatch 前执行：PolicyGate → ThreatScanner → ApprovalGate → dispatch。

本模块是 apps/gateway 层新建的薄包装器，职责：
1. execute(tool_name, args, ...)：
   - 从 ToolRegistry 查找工具（不依赖 CapabilityPack）
   - dispatch 前调用 PolicyGate.check()
   - BLOCK → 拒绝；WARN → 继续（记录日志）
   - 通过 → ToolRegistry.dispatch(tool_name, args)
2. schema_for(tool_name)：返回 ToolEntry.schema（Constitution C3 单一事实源）
3. try_register(schema, handler)：向 packages/tooling ToolBroker 注册（保持外层 API 兼容）

外层 API（schema_for、execute 函数签名）不变，不破坏现有测试（T043 验收要求）。

注意：本模块不替代 packages/tooling.ToolBroker——它是 gateway 层的上层协调器，
旧有的 packages/tooling.ToolBroker 继续负责事件写入和 hook 链；
本模块在 dispatch 决策（PolicyGate check + 路由到正确 registry）层面增加逻辑。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from octoagent.gateway.harness.tool_registry import ToolRegistry

log = structlog.get_logger(__name__)


class GatewayToolBroker:
    """Gateway 层 ToolBroker——在 ToolRegistry.dispatch() 前插入 PolicyGate 检查。

    设计约定：
    - 不直接持有 handler，通过 ToolRegistry 查找
    - PolicyGate 是内容安全统一入口（Constitution C10）
    - 外层 execute 签名兼容 packages/tooling.ToolBroker 的 execute 语义
    - schema_for 通过 ToolRegistry 查找（Constitution C3）

    使用方式（gateway/main.py lifespan 接入后）：
        gateway_broker = GatewayToolBroker(
            tool_registry=tool_registry,
            event_store=stores.event_store,
            task_store=stores.task_store,
        )
        result = await gateway_broker.execute("user_profile.update", args)
    """

    def __init__(
        self,
        *,
        tool_registry: ToolRegistry,
        event_store: Any | None = None,
        task_store: Any | None = None,
        approval_gate: Any | None = None,
    ) -> None:
        """初始化 GatewayToolBroker。

        Args:
            tool_registry: 全局 ToolRegistry 实例（harness 层）。
            event_store: EventStore 实例（PolicyGate 审计用）。
            task_store: TaskStore 实例（PolicyGate audit task 防 F24 用）。
            approval_gate: ApprovalGate 实例（F25 修复：WARN 级 + reversible+ 工具
                必须经过审批，否则高风险但非 BLOCK 内容会绕过人工审批直接执行；
                None 时降级到"WARN 仅日志"行为，但写日志警告该路径未启用审批）。
        """
        self._tool_registry = tool_registry
        self._event_store = event_store
        self._task_store = task_store
        self._approval_gate = approval_gate

    async def execute(
        self,
        tool_name: str,
        args: dict[str, Any],
        *,
        task_id: str = "",
        session_id: str = "",
        content_to_scan: str | None = None,
    ) -> Any:
        """执行工具调用，dispatch 前执行 PolicyGate 内容安全检查（T043 / FR-1.4）。

        执行链路：
        1. 从 ToolRegistry 查找工具（不依赖 CapabilityPack）
        2. PolicyGate.check()（Constitution C10 统一入口）
           - BLOCK → 拒绝执行，抛出 RuntimeError
           - WARN → 记录日志，继续执行（不阻断）
        3. ToolRegistry.dispatch(tool_name, args)（async 兼容）
        4. 返回工具 handler 的原始返回值

        Args:
            tool_name: 目标工具名称。
            args: 工具调用参数字典。
            task_id: 当前任务 ID（PolicyGate 审计事件用）。
            content_to_scan: 要扫描的内容字符串（如 user_profile.update 的 content 字段）；
                             None 时从 args 尝试提取 "content" 键。

        Returns:
            工具 handler 的返回值（可能是 WriteResult 子类、str、None 等）。

        Raises:
            ValueError: 工具不存在时。
            RuntimeError: PolicyGate BLOCK 时（调用方应处理为 rejected 状态）。
        """
        from octoagent.gateway.harness.tool_registry import ToolNotFoundError
        from octoagent.gateway.services.policy import PolicyGate

        # 步骤 1：从 ToolRegistry 查找工具
        if tool_name not in self._tool_registry:
            raise ValueError(f"工具不存在：{tool_name!r}")

        # 步骤 2：PolicyGate 内容安全检查（Constitution C10）
        # content_to_scan 为 None 时尝试从 args["content"] 提取
        scan_content = content_to_scan
        if scan_content is None:
            scan_content = str(args.get("content", ""))

        if scan_content:
            gate = PolicyGate(
                event_store=self._event_store,
                task_store=self._task_store,
            )
            check_result = await gate.check(
                content=scan_content,
                tool_name=tool_name,
                task_id=task_id,
            )
            if not check_result.allowed:
                # BLOCK：拒绝执行
                raise RuntimeError(
                    f"PolicyGate blocked: {check_result.reason} "
                    f"[tool={tool_name}, pattern={check_result.scan_result.pattern_id if check_result.scan_result else None}]"
                )

            # F25 修复：WARN 级威胁不能绕过 ApprovalGate 直接执行（FR-4.1）
            # PolicyGate.check 在 WARN 时返回 allowed=True 但 scan_result.severity == "WARN"，
            # 此处必须走 ApprovalGate 异步审批路径
            scan_result = check_result.scan_result
            if scan_result is not None and getattr(scan_result, "severity", None) == "WARN":
                operation_type = f"{tool_name}:warn:{scan_result.pattern_id}"

                # 已批准过的同 session + 同 operation_type 不重复弹卡片（FR-4.3）
                if self._approval_gate is None:
                    log.error(
                        "tool_broker_warn_no_approval_gate",
                        tool_name=tool_name,
                        pattern_id=scan_result.pattern_id,
                        hint=(
                            "F25 风险：WARN 级威胁应经 ApprovalGate，但 broker 未注入；"
                            "lifespan 接入 ApprovalGate 后此路径自动启用"
                        ),
                    )
                elif not self._approval_gate.check_allowlist(session_id, operation_type):
                    handle = await self._approval_gate.request_approval(
                        session_id=session_id,
                        tool_name=tool_name,
                        scan_result=scan_result,
                        operation_summary=(
                            f"WARN 级威胁命中 [{scan_result.pattern_id}]: "
                            f"{scan_result.matched_pattern_description or '未知 pattern'}"
                        ),
                        diff_content=None,
                        task_id=task_id,
                    )
                    decision = await self._approval_gate.wait_for_decision(handle)
                    if decision == "rejected":
                        raise RuntimeError(
                            f"ApprovalGate rejected WARN-level threat: "
                            f"[tool={tool_name}, pattern={scan_result.pattern_id}, "
                            f"handle_id={handle.handle_id}]"
                        )
                    # decision == "approved"：resolve_approval 已加 allowlist
                # else：在 allowlist，跳过审批继续执行（FR-4.3）

        # 步骤 3：ToolRegistry.dispatch
        try:
            return await _dispatch_async(self._tool_registry, tool_name, args)
        except ToolNotFoundError:
            raise ValueError(f"工具在 ToolRegistry 中不存在：{tool_name!r}")

    def schema_for(self, tool_name: str) -> type | None:
        """返回 ToolEntry.schema（Constitution C3 单一事实源）。

        Args:
            tool_name: 工具名称。

        Returns:
            Pydantic BaseModel 类型，或 None（工具不存在时）。
        """
        with self._tool_registry._lock:
            entry = self._tool_registry._entries.get(tool_name)
        return entry.schema if entry else None

    def try_register(self, schema: Any, handler: Any) -> None:
        """向 ToolRegistry 注册工具（保持外层 API 兼容，T043 要求）。

        此方法为兼容现有调用方（packages/tooling ToolBroker 的 try_register 语义），
        实际委托到 ToolRegistry 注册。

        Args:
            schema: 工具输入 schema（Pydantic BaseModel 类型或 ToolMeta）。
            handler: 工具 handler 可调用对象。
        """
        from octoagent.gateway.harness.tool_registry import SideEffectLevel, ToolEntry

        # 兼容性：从 schema 对象提取工具名
        tool_name = getattr(schema, "name", None) or getattr(handler, "__name__", "unknown")
        entry = ToolEntry(
            name=tool_name,
            entrypoints=frozenset({"agent_runtime"}),
            toolset="registered",
            handler=handler,
            schema=type(schema) if not isinstance(schema, type) else schema,
            side_effect_level=SideEffectLevel.NONE,
        )
        self._tool_registry.register(entry)
        log.debug("gateway_tool_broker_try_register", tool_name=tool_name)


async def _dispatch_async(registry: Any, tool_name: str, args: dict[str, Any]) -> Any:
    """将 ToolRegistry.dispatch() 包装为 async（handler 可能是同步或异步函数）。

    ToolRegistry.dispatch() 直接调用 entry.handler(**args)，
    但 handler 可能是 async def。此函数统一 await 处理。
    """
    import asyncio
    import inspect

    # 直接从 registry 获取 entry 以便检测 handler 是否是协程函数
    with registry._lock:
        entry = registry._entries.get(tool_name)

    if entry is None:
        from octoagent.gateway.harness.tool_registry import ToolNotFoundError
        raise ToolNotFoundError(tool_name)

    if inspect.iscoroutinefunction(entry.handler):
        return await entry.handler(**args)
    else:
        return await asyncio.to_thread(entry.handler, **args)
