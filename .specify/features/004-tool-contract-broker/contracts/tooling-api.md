# 接口契约: Feature 004 — Tooling API

**Feature Branch**: `feat/004-tool-contract-broker`
**日期**: 2026-03-01
**状态**: LOCKED -- 变更需经 Feature 005/006 利益方评审
**消费方**: Feature 005 (Skill Runner), Feature 006 (Policy Engine), Feature 007 (端到端集成)

---

## 1. 锁定项清单（FR-025a）

以下项目已锁定，变更需经 005/006 利益方评审：

| 类别 | 锁定项 | 值 |
|------|--------|---|
| 枚举值 | `SideEffectLevel` | `none` / `reversible` / `irreversible` |
| 枚举值 | `ToolProfile` | `minimal` / `standard` / `privileged` |
| 默认值 | 大输出裁切阈值 | 500 字符 |
| 默认值 | Hook 默认 fail_mode | `open` |
| 强制值 | PolicyCheckpoint fail_mode | `closed`（不可覆盖） |
| 方法签名 | `ToolBrokerProtocol.execute()` | `execute(tool_name, args, context) -> ToolResult` |
| 方法签名 | `PolicyCheckpoint.check()` | `check(tool_meta, params, context) -> CheckResult` |
| ToolResult 必含字段 | 5 个 | `output`, `is_error`, `error`, `duration`, `artifact_ref` |

---

## 2. ToolBrokerProtocol

Feature 005/006 基于此 Protocol 编写 mock 实现。

```python
from typing import Any, Protocol

class ToolBrokerProtocol(Protocol):
    """ToolBroker 接口契约 -- 对齐 spec FR-023

    所有工具调用必须经过 Broker，确保 hook 链路完整执行。
    Feature 005/006 可基于此 Protocol 编写 mock 实现。

    行为约定:
    - register(): 注册工具，名称冲突时抛出 ToolRegistrationError
    - discover(): 按 profile/group 过滤工具集
    - execute(): 执行工具，完整运行 hook 链
    - add_hook(): 注册 before/after hook
    """

    async def register(
        self,
        tool_meta: "ToolMeta",
        handler: "ToolHandler",
    ) -> None:
        """注册工具到 Broker

        Args:
            tool_meta: 工具元数据（由 Schema Reflection 生成）
            handler: 工具执行处理函数

        Raises:
            ToolRegistrationError: 名称冲突或缺少必填元数据

        行为约定:
        - 名称唯一性检查：重复名称拒绝注册
        - side_effect_level 必须已声明（在 Schema Reflection 阶段已校验）
        """
        ...

    async def discover(
        self,
        profile: "ToolProfile | None" = None,
        group: str | None = None,
    ) -> "list[ToolMeta]":
        """发现可用工具

        Args:
            profile: 按 Profile 过滤（含该级别及以下）
            group: 按逻辑分组过滤

        Returns:
            匹配的 ToolMeta 列表

        行为约定:
        - profile=None: 返回所有工具
        - profile=minimal: 仅返回 minimal 工具
        - profile=standard: 返回 minimal + standard
        - profile=privileged: 返回所有
        - group 和 profile 可同时指定（AND 关系）
        - 无匹配工具时返回空列表（不抛异常）
        """
        ...

    async def execute(
        self,
        tool_name: str,
        args: dict[str, Any],
        context: "ExecutionContext",
    ) -> "ToolResult":
        """执行工具调用

        Args:
            tool_name: 目标工具名称
            args: 调用参数
            context: 执行上下文（含 task_id, trace_id, profile）

        Returns:
            ToolResult 结构化结果

        行为约定:
        1. 查找工具 -> 未找到则返回 is_error=True
        2. 检查 Profile 权限 -> context.profile 不满足则拒绝
        3. irreversible 工具无 PolicyCheckpoint hook 时 -> 强制拒绝（FR-010a）
        4. 运行 before hooks（按优先级从低到高）
           - 任一 before hook 返回 proceed=False -> 中止，返回拒绝 ToolResult
           - before hook 异常 -> 按 fail_mode 处理
        5. 执行工具（含超时控制）
           - 同步函数 -> asyncio.to_thread() 包装
           - 超时 -> 返回超时 ToolResult
        6. 运行 after hooks（按优先级从低到高）
           - after hook 异常 -> 按 fail_mode 处理（默认 log-and-continue）
        7. 生成事件（TOOL_CALL_STARTED / COMPLETED / FAILED）
        8. 返回 ToolResult
        """
        ...

    def add_hook(self, hook: "BeforeHook | AfterHook") -> None:
        """注册 Hook 扩展点

        Args:
            hook: BeforeHook 或 AfterHook 实例

        行为约定:
        - 根据 hook 类型（BeforeHook/AfterHook）自动分类
        - 同一 hook 不可重复注册
        - 注册后立即生效（影响后续所有 execute 调用）
        """
        ...

    async def unregister(self, tool_name: str) -> bool:
        """注销工具

        Args:
            tool_name: 工具名称

        Returns:
            True 如果成功注销，False 如果工具不存在

        行为约定:
        - 注销后该工具不再可发现和执行
        - 不影响正在执行的工具调用
        """
        ...
```

---

## 3. ToolHandler Protocol

工具执行处理函数的类型约束。

```python
from typing import Any, Protocol

class ToolHandler(Protocol):
    """工具执行处理函数 Protocol

    工具的实际执行逻辑。ToolBroker 在 hook 链完成后调用此函数。
    支持同步和异步两种形式。
    """

    async def __call__(self, **kwargs: Any) -> Any:
        """执行工具

        Args:
            **kwargs: 工具参数（与 ToolMeta.parameters_json_schema 对齐）

        Returns:
            工具输出（任意可序列化类型，ToolBroker 将其转换为 str 存入 ToolResult.output）
        """
        ...
```

**说明**: ToolHandler 同时接受同步和异步 Callable。ToolBroker 在执行时自动检测并包装同步函数为异步（FR-013）。

---

## 4. 核心数据模型

### 4.1 ToolMeta

```python
from typing import Any
from pydantic import BaseModel, Field

class ToolMeta(BaseModel):
    """工具元数据 -- 完整定义见 data-model.md §2.1"""

    name: str
    description: str
    parameters_json_schema: dict[str, Any]
    side_effect_level: SideEffectLevel
    tool_profile: ToolProfile
    tool_group: str
    version: str = "1.0.0"
    timeout_seconds: float | None = None
    is_async: bool = False
    output_truncate_threshold: int | None = None
```

### 4.2 ToolResult（锁定字段）

```python
class ToolResult(BaseModel):
    """工具执行结果 -- 必含字段已锁定（FR-025a）"""

    # === 锁定字段 ===
    output: str                          # 输出内容或 artifact 引用摘要
    is_error: bool = False               # 是否错误
    error: str | None = None             # 错误信息
    duration: float                      # 执行耗时（秒）
    artifact_ref: str | None = None      # Artifact 引用 ID

    # === 扩展字段 ===
    tool_name: str = ""
    truncated: bool = False
```

### 4.3 ExecutionContext

```python
class ExecutionContext(BaseModel):
    """工具执行上下文 -- 完整定义见 data-model.md §2.4"""

    task_id: str
    trace_id: str
    caller: str = "system"
    profile: ToolProfile = ToolProfile.MINIMAL
```

### 4.4 CheckResult

```python
class CheckResult(BaseModel):
    """PolicyCheckpoint 检查结果"""

    allowed: bool
    reason: str = ""
    requires_approval: bool = False
```

---

## 5. PolicyCheckpoint Protocol

Feature 006 基于此 Protocol 实现 PolicyCheckHook。

```python
class PolicyCheckpoint(Protocol):
    """Policy 检查点 Protocol -- 对齐 spec FR-024

    Feature 004 定义此接口，Feature 006 实现。
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
        ...
```

**Mock 实现示例**（供 Feature 005/006 并行开发使用）:

```python
class MockPolicyCheckpoint:
    """Mock PolicyCheckpoint -- 始终允许（仅供测试）"""

    async def check(
        self,
        tool_meta: ToolMeta,
        params: dict[str, Any],
        context: ExecutionContext,
    ) -> CheckResult:
        return CheckResult(allowed=True, reason="mock: always allow")
```

---

## 6. BeforeHook / AfterHook Protocol

```python
class BeforeHookResult(BaseModel):
    """before hook 执行结果"""

    proceed: bool = True
    rejection_reason: str | None = None
    modified_args: dict[str, Any] | None = None


class BeforeHook(Protocol):
    """before hook Protocol -- 对齐 spec FR-019/020/021"""

    @property
    def name(self) -> str: ...

    @property
    def priority(self) -> int: ...

    @property
    def fail_mode(self) -> FailMode: ...

    async def before_execute(
        self,
        tool_meta: ToolMeta,
        args: dict[str, Any],
        context: ExecutionContext,
    ) -> BeforeHookResult: ...


class AfterHook(Protocol):
    """after hook Protocol -- 对齐 spec FR-019/022"""

    @property
    def name(self) -> str: ...

    @property
    def priority(self) -> int: ...

    @property
    def fail_mode(self) -> FailMode: ...

    async def after_execute(
        self,
        tool_meta: ToolMeta,
        result: ToolResult,
        context: ExecutionContext,
    ) -> ToolResult: ...
```

---

## 7. 枚举值定义

```python
from enum import StrEnum

class SideEffectLevel(StrEnum):
    NONE = "none"
    REVERSIBLE = "reversible"
    IRREVERSIBLE = "irreversible"

class ToolProfile(StrEnum):
    MINIMAL = "minimal"
    STANDARD = "standard"
    PRIVILEGED = "privileged"

class FailMode(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
```

---

## 8. 错误类型

```python
class ToolRegistrationError(Exception):
    """工具注册错误（名称冲突、缺少必填元数据）"""
    pass

class ToolNotFoundError(Exception):
    """工具未找到"""
    pass

class ToolExecutionError(Exception):
    """工具执行错误（超时、异常）"""
    pass

class ToolProfileViolationError(Exception):
    """工具权限 Profile 违规"""
    pass

class PolicyCheckpointMissingError(Exception):
    """irreversible 工具缺少 PolicyCheckpoint hook"""
    pass
```

---

## 9. EventType 扩展

Feature 004 在 `octoagent.core.models.enums.EventType` 中新增：

```python
class EventType(StrEnum):
    # ... 现有值 ...

    # Feature 004: 工具调用事件 -- 对齐 FR-014
    TOOL_CALL_STARTED = "TOOL_CALL_STARTED"
    TOOL_CALL_COMPLETED = "TOOL_CALL_COMPLETED"
    TOOL_CALL_FAILED = "TOOL_CALL_FAILED"
```

---

## 10. @tool_contract 装饰器签名

```python
def tool_contract(
    *,
    side_effect_level: SideEffectLevel,
    tool_profile: ToolProfile,
    tool_group: str,
    name: str | None = None,
    version: str = "1.0.0",
    timeout_seconds: float | None = None,
    output_truncate_threshold: int | None = None,
) -> Callable[[F], F]:
    """工具契约声明装饰器 -- 对齐 spec FR-001/002

    将工具元数据附加到函数对象上，Schema Reflection 时自动提取。
    side_effect_level 为必填（无默认值），强制声明。

    Usage:
        @tool_contract(
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="system",
        )
        async def echo(text: str) -> str:
            '''回显输入文本。

            Args:
                text: 要回显的文本
            '''
            return text
    """
    ...
```

---

## 11. 使用示例

### 11.1 Feature 005 使用 ToolBrokerProtocol

```python
# Feature 005 Skill Runner 中使用 ToolBroker
class SkillRunner:
    def __init__(self, broker: ToolBrokerProtocol, ...):
        self._broker = broker

    async def _execute_tool_call(
        self,
        tool_call: ToolCall,
        context: ExecutionContext,
    ) -> ToolResult:
        return await self._broker.execute(
            tool_name=tool_call.tool_name,
            args=tool_call.arguments,
            context=context,
        )
```

### 11.2 Feature 006 实现 PolicyCheckpoint

```python
# Feature 006 PolicyEngine 实现 PolicyCheckpoint
class PolicyCheckHook:
    """PolicyCheckpoint 的 BeforeHook 包装"""

    def __init__(self, checkpoint: PolicyCheckpoint):
        self._checkpoint = checkpoint

    @property
    def name(self) -> str:
        return "policy_checkpoint"

    @property
    def priority(self) -> int:
        return 0  # 最高优先级

    @property
    def fail_mode(self) -> FailMode:
        return FailMode.CLOSED  # 强制 fail-closed

    async def before_execute(
        self,
        tool_meta: ToolMeta,
        args: dict[str, Any],
        context: ExecutionContext,
    ) -> BeforeHookResult:
        result = await self._checkpoint.check(tool_meta, args, context)
        return BeforeHookResult(
            proceed=result.allowed,
            rejection_reason=result.reason if not result.allowed else None,
        )
```

### 11.3 Mock ToolBroker（供 005/006 并行开发）

```python
class MockToolBroker:
    """ToolBrokerProtocol 的 mock 实现"""

    def __init__(self) -> None:
        self._tools: dict[str, tuple[ToolMeta, ToolHandler]] = {}

    async def register(self, tool_meta: ToolMeta, handler: ToolHandler) -> None:
        if tool_meta.name in self._tools:
            raise ToolRegistrationError(f"Tool '{tool_meta.name}' already registered")
        self._tools[tool_meta.name] = (tool_meta, handler)

    async def discover(
        self,
        profile: ToolProfile | None = None,
        group: str | None = None,
    ) -> list[ToolMeta]:
        results = []
        for meta, _ in self._tools.values():
            if profile and not _profile_allows(meta.tool_profile, profile):
                continue
            if group and meta.tool_group != group:
                continue
            results.append(meta)
        return results

    async def execute(
        self,
        tool_name: str,
        args: dict[str, Any],
        context: ExecutionContext,
    ) -> ToolResult:
        if tool_name not in self._tools:
            return ToolResult(
                output="",
                is_error=True,
                error=f"Tool '{tool_name}' not found",
                duration=0.0,
            )
        meta, handler = self._tools[tool_name]
        import time
        start = time.monotonic()
        try:
            result = await handler(**args)
            duration = time.monotonic() - start
            return ToolResult(
                output=str(result),
                is_error=False,
                duration=duration,
                tool_name=tool_name,
            )
        except Exception as e:
            duration = time.monotonic() - start
            return ToolResult(
                output="",
                is_error=True,
                error=str(e),
                duration=duration,
                tool_name=tool_name,
            )

    def add_hook(self, hook: "BeforeHook | AfterHook") -> None:
        pass  # mock 不执行 hooks

    async def unregister(self, tool_name: str) -> bool:
        return self._tools.pop(tool_name, None) is not None
```
