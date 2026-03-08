"""OctoAgent Tooling -- 工具契约 + Schema Reflection + ToolBroker

Feature 004: Tool Contract + ToolBroker
提供工具声明、反射、注册、发现、执行的完整基础设施。
"""

from __future__ import annotations

# Broker
from .broker import ToolBroker

# 装饰器
from .decorators import tool_contract

# 异常
from .exceptions import (
    PolicyCheckpointMissingError,
    SchemaReflectionError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolProfileViolationError,
    ToolRegistrationError,
)

# Hook 实现
from .hooks import EventGenerationHook, LargeOutputHandler

# 枚举
# 数据模型
# 辅助函数
from .models import (
    BeforeHookResult,
    CheckResult,
    ExecutionContext,
    FailMode,
    HookType,
    RegisterToolResult,
    RegistryDiagnostic,
    SideEffectLevel,
    ToolCall,
    ToolMeta,
    ToolProfile,
    ToolResult,
    profile_allows,
)

# Protocol 接口
from .protocols import (
    AfterHook,
    ArtifactStoreProtocol,
    BeforeHook,
    EventStoreProtocol,
    PolicyCheckpoint,
    ToolBrokerProtocol,
    ToolHandler,
)

# 脱敏
from .sanitizer import sanitize_for_event

# Schema Reflection
from .schema import reflect_tool_schema
from .tool_index import InMemoryToolIndexBackend, LanceDBToolIndexBackend, ToolIndex

__all__ = [
    # 枚举
    "FailMode",
    "HookType",
    "RegisterToolResult",
    "RegistryDiagnostic",
    "SideEffectLevel",
    "ToolProfile",
    # 数据模型
    "BeforeHookResult",
    "CheckResult",
    "ExecutionContext",
    "ToolCall",
    "ToolMeta",
    "ToolResult",
    # 辅助函数
    "profile_allows",
    # Protocol
    "AfterHook",
    "ArtifactStoreProtocol",
    "BeforeHook",
    "EventStoreProtocol",
    "PolicyCheckpoint",
    "ToolBrokerProtocol",
    "ToolHandler",
    # 装饰器
    "tool_contract",
    # Schema Reflection
    "reflect_tool_schema",
    # Broker
    "ToolBroker",
    # Hook 实现
    "EventGenerationHook",
    "LargeOutputHandler",
    # 脱敏
    "sanitize_for_event",
    "ToolIndex",
    "InMemoryToolIndexBackend",
    "LanceDBToolIndexBackend",
    # 异常
    "PolicyCheckpointMissingError",
    "SchemaReflectionError",
    "ToolExecutionError",
    "ToolNotFoundError",
    "ToolProfileViolationError",
    "ToolRegistrationError",
]
