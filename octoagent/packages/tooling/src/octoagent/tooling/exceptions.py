"""异常类型 -- Feature 004 Tool Contract + ToolBroker

对齐 contracts/tooling-api.md §8。
"""


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
    """irreversible 工具缺少 PolicyCheckpoint hook（FR-010a）"""

    pass


class SchemaReflectionError(Exception):
    """Schema 反射错误（缺少类型注解、缺少装饰器等）"""

    pass
