"""DX 数据模型 -- 对齐 data-model.md SS6, contracts/dx-cli-api.md

octo init 配置结果、octo doctor 检查结果和报告模型。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from ..auth.credentials import Credential


class CheckStatus(StrEnum):
    """诊断检查状态"""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"


class CheckLevel(StrEnum):
    """检查项级别"""

    REQUIRED = "required"  # 必须通过（阻断）
    RECOMMENDED = "recommended"  # 建议通过（警告）


class CheckResult(BaseModel):
    """单项诊断检查结果"""

    name: str = Field(description="检查项名称")
    status: CheckStatus = Field(description="检查状态")
    level: CheckLevel = Field(description="检查级别")
    message: str = Field(description="状态描述")
    fix_hint: str = Field(default="", description="修复建议")


class DoctorReport(BaseModel):
    """诊断报告"""

    checks: list[CheckResult] = Field(default_factory=list)
    overall_status: CheckStatus = Field(description="总体状态")
    timestamp: datetime = Field(description="诊断时间")


class InitConfig(BaseModel):
    """octo init 配置结果"""

    llm_mode: Literal["echo", "litellm"] = Field(description="LLM 运行模式")
    provider: str = Field(default="", description="Provider 标识")
    auth_mode: Literal["api_key", "token", "oauth"] = Field(
        default="api_key",
        description="认证模式",
    )
    credential: Credential | None = Field(
        default=None,
        description="关联的凭证",
    )
    master_key: str = Field(default="", description="LiteLLM Master Key")
    docker_available: bool = Field(
        default=False,
        description="Docker 是否可用",
    )
