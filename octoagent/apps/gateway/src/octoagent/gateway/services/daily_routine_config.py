"""F102 Proactive Followup — DailyRoutine 配置解析与 Payload schema。

本模块包含：
- USER.md 机器可读字段的解析函数（spec FR-D2 + F115 时区接入）：
  - extract_daily_summary_time_from_user_md → "HH:MM"
  - extract_routine_active_from_user_md → bool
  - extract_summary_channels_from_user_md → frozenset[str]
  - extract_user_timezone_from_user_md → str | None（F115）
- DailyRoutineConfig dataclass（spec FR-DI1 配置抽象）
- Routine 事件 payload schema（spec FR-E2/E3）

USER.md 机器可读字段清单（F102 handoff 洞察：USER.md 既有人类可读"时区/地点"，
也需机器可读字段供运行时解析；二者并存）：
- 通知偏好：active_hours（F101，NotificationService quiet hours）
- Daily Routine：daily_summary_time / routine_active / summary_channels（F102）
  + user_timezone（F115，本模块新增；缺失/非法时由 DailyRoutineService 降级
  到 env OCTOAGENT_USER_TIMEZONE → UTC）

设计要点（spec SD-1 + SD-6 + SD-10 + plan Phase A 实测）：
- 解析函数所有非法值 fallback 到默认值 + WARNING log（Constitution C6 graceful degrade）
- summary_channels 用户友好写法 "telegram,web" → 内部 frozenset({"telegram","web_sse"})
  （channel.channel_name 实测值，plan A-4/A-8 决议）
- daily_summary_time 不做格式严格校验（允许 "8:30" 与 "08:30" 等价），cron 表达式生成时归一化
"""

from __future__ import annotations

import re
import zoneinfo
from dataclasses import dataclass
from typing import Final, Literal

import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)


# ============================================================
# 默认值（SD-1 + SD-5）
# ============================================================

DEFAULT_DAILY_SUMMARY_TIME: Final[str] = "08:30"
DEFAULT_ROUTINE_ACTIVE: Final[bool] = True
DEFAULT_SUMMARY_CHANNELS: Final[frozenset[str]] = frozenset({"telegram", "web_sse"})

# USER.md 用户友好值 + 内部值 → channel.channel_name 映射（plan A-4/A-8 + Codex M3）
# 用户写法：
#   "telegram" → "telegram"（TelegramNotificationChannel.channel_name）
#   "web"      → "web_sse" （SSENotificationChannel.channel_name 友好别名）
# 开发者写法（直接用内部值，与 channel_name 实测一致）：
#   "web_sse"  → "web_sse"
_USER_VISIBLE_TO_INTERNAL_CHANNEL: Final[dict[str, str]] = {
    "telegram": "telegram",
    "web": "web_sse",
    "web_sse": "web_sse",  # Codex review M3：接受内部值直写，避免误 fallback
    # F105 v0.2（D15）：新平台 channel_name 与用户可见值同名直映
    "slack": "slack",
    "discord": "discord",
}
_VALID_INTERNAL_CHANNELS: Final[frozenset[str]] = frozenset(
    _USER_VISIBLE_TO_INTERNAL_CHANNEL.values()
)


# ============================================================
# 解析正则（Codex review H1 BLOCKER 修复）
# ============================================================
#
# 设计：key prefix MUST 出现在每行内才提取 value（强制 "key: value" 形式），
# 否则 key 字符串本身可能被当作 value 匹配（如 "summary_channels" 被当作 channel
# 名 fallback 到全渠道）。原 (?:...)? 可选 prefix 会被绕过裸值，已知 bug。
#
# 支持的合法 USER.md 写法（每个字段独立分支）：
#   - **daily_summary_time**: "08:30"      # 标准 USER.md 列表
#   - daily_summary_time: 08:30            # 裸 key:value
#   - daily_summary_time: "8:30"           # 单数字小时 + 引号
#

# daily_summary_time 强制 key prefix
_DAILY_SUMMARY_TIME_PATTERN = re.compile(
    r"""
    (?:\*\*)?                       # 可选 **
    daily_summary_time              # MUST 出现
    (?:\*\*)?                       # 可选 **
    \s*:\s*                         # MUST :
    "?                              # 可选引号
    (\d{1,2}:\d{2})                 # HH:MM（捕获组）
    "?
    """,
    re.VERBOSE,
)

# routine_active 强制 key prefix
_ROUTINE_ACTIVE_PATTERN = re.compile(
    r"""
    (?:\*\*)?
    routine_active                  # MUST 出现
    (?:\*\*)?
    \s*:\s*
    "?
    (true|false|True|False)
    "?
    """,
    re.VERBOSE,
)

# summary_channels 强制 key prefix
_SUMMARY_CHANNELS_PATTERN = re.compile(
    r"""
    (?:\*\*)?
    summary_channels                # MUST 出现
    (?:\*\*)?
    \s*:\s*
    "?
    ([a-z][a-z_,\s]+)               # 逗号分隔 channel 名（小写 + _ + 逗号 + 空格）
    "?
    """,
    re.VERBOSE,
)

# user_timezone 强制 key prefix（F115）。
# 捕获 IANA 时区名字符集：字母 + 数字 + / + _ + + + -
# （覆盖 "Asia/Shanghai" / "America/Argentina/Buenos_Aires" / "Etc/GMT+8" / "UTC"）。
_USER_TIMEZONE_PATTERN = re.compile(
    r"""
    (?:\*\*)?
    user_timezone                   # MUST 出现
    (?:\*\*)?
    \s*:\s*
    "?
    ([A-Za-z][A-Za-z0-9_/+-]+)      # IANA 时区名（捕获组）
    "?
    """,
    re.VERBOSE,
)


def _validate_time_format(value: str) -> bool:
    """验证 HH:MM 格式合法性。

    合法范围：HH ∈ [0, 23], MM ∈ [0, 59]
    """
    parts = value.split(":")
    if len(parts) != 2:
        return False
    try:
        hh, mm = int(parts[0]), int(parts[1])
        return 0 <= hh <= 23 and 0 <= mm <= 59
    except ValueError:
        return False


def extract_daily_summary_time_from_user_md(user_md_content: str | None) -> str:
    """从 USER.md 中提取 daily_summary_time 字段。

    格式：
    - ``- **daily_summary_time**: "08:30"``（标准 USER.md 列表格式）
    - ``daily_summary_time: 08:30``（简洁格式）

    非法值 fallback 到 ``"08:30"`` 并写 WARNING log（Constitution C6）。

    Args:
        user_md_content: USER.md 全文字符串

    Returns:
        ``"HH:MM"`` 格式时间字符串；解析失败或非法时返回默认 ``"08:30"``
    """
    if not user_md_content:
        return DEFAULT_DAILY_SUMMARY_TIME

    for line in user_md_content.splitlines():
        # 跳过 HTML 注释行：USER.md 中字段文档示例（如 daily_summary_time 注释）写在
        # value 行之前，会被正则误命中并 premature return。本字段当前靠占位符 "HH:MM"
        # （非法值）侥幸规避，此守卫是 defense-in-depth，与 F115 user_timezone 一致——
        # 不依赖注释文案侥幸，未来改注释也不回归。
        if line.lstrip().startswith("<!--"):
            continue
        if "daily_summary_time" not in line:
            continue
        m = _DAILY_SUMMARY_TIME_PATTERN.search(line)
        if m is None:
            continue
        value = m.group(1)
        if _validate_time_format(value):
            return value
        logger.warning(
            "daily_summary_time format invalid; falling back to default",
            raw_value=value,
            default=DEFAULT_DAILY_SUMMARY_TIME,
        )
        return DEFAULT_DAILY_SUMMARY_TIME

    return DEFAULT_DAILY_SUMMARY_TIME


def extract_routine_active_from_user_md(user_md_content: str | None) -> bool:
    """从 USER.md 中提取 routine_active 字段。

    格式：
    - ``- **routine_active**: "true"``（标准）
    - ``- **routine_active**: false``（裸值）

    非法值 fallback 到 ``True`` 并写 WARNING log（Constitution C6）。

    Args:
        user_md_content: USER.md 全文字符串

    Returns:
        bool；解析失败或非法时返回默认 ``True``
    """
    if not user_md_content:
        return DEFAULT_ROUTINE_ACTIVE

    for line in user_md_content.splitlines():
        # 跳过 HTML 注释行：USER.md 的 routine_active 字段注释（line 39）含字面
        # `routine_active: "true"`，写在 value 行之前。无此守卫时注释行的 "true" 会被
        # 正则先命中并立即 return → 用户把 value 行改 "false" 也关不掉 routine（F102 真 bug）。
        # 与 F115 user_timezone 同范式：先于字段匹配跳过注释行。
        if line.lstrip().startswith("<!--"):
            continue
        if "routine_active" not in line:
            continue
        m = _ROUTINE_ACTIVE_PATTERN.search(line)
        if m is None:
            continue
        value = m.group(1).lower()
        if value == "true":
            return True
        if value == "false":
            return False
        logger.warning(
            "routine_active value invalid; falling back to default",
            raw_value=value,
            default=DEFAULT_ROUTINE_ACTIVE,
        )
        return DEFAULT_ROUTINE_ACTIVE

    return DEFAULT_ROUTINE_ACTIVE


def extract_summary_channels_from_user_md(
    user_md_content: str | None,
) -> frozenset[str]:
    """从 USER.md 中提取 summary_channels 字段。

    格式（用户友好写法）：
    - ``- **summary_channels**: "telegram,web"``
    - ``- **summary_channels**: telegram``
    - ``- **summary_channels**: "telegram, web"``（允许空格）

    返回值为内部 channel.channel_name 集合（plan A-4/A-8 实测映射）：
    - ``"telegram"`` → ``"telegram"``
    - ``"web"``      → ``"web_sse"``

    非法值（含未知 channel 名）或空集 fallback 到全渠道
    ``frozenset({"telegram", "web_sse"})`` + WARNING log（Constitution C6）。

    Args:
        user_md_content: USER.md 全文字符串

    Returns:
        frozenset[str]，元素来自 {"telegram", "web_sse"}
    """
    if not user_md_content:
        return DEFAULT_SUMMARY_CHANNELS

    for line in user_md_content.splitlines():
        # 跳过 HTML 注释行：USER.md 的 summary_channels 字段注释含字面 channel 名
        # （"telegram" / "web"），会被正则误命中并 premature return。本字段当前靠注释冒号后
        # 接中文（正则 [a-z] 不命中）侥幸规避，此守卫是 defense-in-depth，与 F115 一致。
        if line.lstrip().startswith("<!--"):
            continue
        if "summary_channels" not in line:
            continue
        m = _SUMMARY_CHANNELS_PATTERN.search(line)
        if m is None:
            continue
        raw_value = m.group(1).strip()
        # 拆分为 user-visible tokens
        user_tokens = [tok.strip().lower() for tok in raw_value.split(",") if tok.strip()]
        # 映射到内部 channel name
        internal: set[str] = set()
        invalid_tokens: list[str] = []
        for tok in user_tokens:
            mapped = _USER_VISIBLE_TO_INTERNAL_CHANNEL.get(tok)
            if mapped is None:
                invalid_tokens.append(tok)
            else:
                internal.add(mapped)

        if invalid_tokens:
            logger.warning(
                "summary_channels contains unknown channel(s); falling back to default",
                invalid=invalid_tokens,
                default=sorted(DEFAULT_SUMMARY_CHANNELS),
            )
            return DEFAULT_SUMMARY_CHANNELS

        if not internal:
            logger.warning(
                "summary_channels resolved to empty set; falling back to default",
                raw_value=raw_value,
                default=sorted(DEFAULT_SUMMARY_CHANNELS),
            )
            return DEFAULT_SUMMARY_CHANNELS

        return frozenset(internal)

    return DEFAULT_SUMMARY_CHANNELS


def extract_user_timezone_from_user_md(user_md_content: str | None) -> str | None:
    """从 USER.md 中提取 user_timezone 机器可读字段（F115）。

    格式（机器可读，与人类可读"时区/地点"字段并存）：
    - ``- **user_timezone**: "Asia/Shanghai"``（标准 USER.md 列表格式）
    - ``user_timezone: America/New_York``（简洁格式）

    返回值经 ``zoneinfo.ZoneInfo`` 合法性校验。与其他三个 extract 函数不同，
    本字段**返回 None 而非默认值**——因为时区的完整降级链（USER.md → env
    OCTOAGENT_USER_TIMEZONE → UTC）由 ``DailyRoutineService._resolve_user_timezone``
    统一裁决；None 表示"USER.md 未提供有效时区"，交由上层降级。

    Args:
        user_md_content: USER.md 全文字符串

    Returns:
        合法 IANA 时区名；字段缺失或非法时返回 ``None``（上层降级到 env / UTC）
    """
    if not user_md_content:
        return None

    for line in user_md_content.splitlines():
        # 跳过 HTML 注释行：user_timezone 的文档示例（如 "Asia/Shanghai"）是合法
        # IANA 名，会被正则当作真实值误命中并 premature return；其他三字段靠占位符
        # （"HH:MM" 等非法值）天然规避，本字段示例是真值，必须显式跳过注释行。
        if line.lstrip().startswith("<!--"):
            continue
        if "user_timezone" not in line:
            continue
        m = _USER_TIMEZONE_PATTERN.search(line)
        if m is None:
            continue
        value = m.group(1)
        try:
            zoneinfo.ZoneInfo(value)
        except (zoneinfo.ZoneInfoNotFoundError, ValueError):
            logger.warning(
                "user_timezone value invalid; ignoring (fall back to env/UTC)",
                raw_value=value,
            )
            return None
        return value

    return None


# ============================================================
# DailyRoutineConfig（聚合配置）
# ============================================================


@dataclass(frozen=True, slots=True)
class DailyRoutineConfig:
    """DailyRoutine 运行时配置快照。

    由 ``DailyRoutineService._read_config()`` 从 USER.md 读取并构造（spec §7.4）。
    使用 ``frozen=True`` 保证读取后不可变（避免运行期改 cron 表达式的隐性问题）。

    Attributes:
        daily_summary_time: ``"HH:MM"`` 24h 格式（用户本地时区）
        routine_active: 是否启用 daily routine
        summary_channels: 内部 channel.channel_name 集合
            （元素来自 {"telegram", "web_sse"}）
        user_timezone: USER.md 中的机器可读 IANA 时区名（F115）；``None`` 表示
            USER.md 未提供有效时区，由 ``DailyRoutineService._resolve_user_timezone``
            降级到 env OCTOAGENT_USER_TIMEZONE → UTC
    """

    daily_summary_time: str
    routine_active: bool
    summary_channels: frozenset[str]
    # F115：末位 + 默认 None，使既有直接构造（不传时区，如 crontab 测试）零改动；
    # None 语义 = "USER.md 未提供有效时区"，由 service 降级 env → UTC
    user_timezone: str | None = None

    @classmethod
    def from_user_md(cls, user_md_content: str | None) -> DailyRoutineConfig:
        """从 USER.md 全文构造 config（spec FR-DI1 / FR-B2 步骤 2 + F115 时区）。"""
        return cls(
            daily_summary_time=extract_daily_summary_time_from_user_md(user_md_content),
            routine_active=extract_routine_active_from_user_md(user_md_content),
            summary_channels=extract_summary_channels_from_user_md(user_md_content),
            user_timezone=extract_user_timezone_from_user_md(user_md_content),
        )

    def to_crontab(self) -> str:
        """daily_summary_time ``"HH:MM"`` → crontab 表达式 ``"MM HH * * *"``。

        Returns:
            "MM HH * * *" 格式 crontab 表达式
        """
        hh, mm = self.daily_summary_time.split(":")
        return f"{int(mm)} {int(hh)} * * *"


# ============================================================
# Routine 事件 Payload Schemas（spec FR-E2 / FR-E3）
# ============================================================


class RoutineTriggeredPayload(BaseModel):
    """ROUTINE_TRIGGERED 事件 payload。

    在每次 cron 触发的最开始写入，用于审计 cron 调度本身的发生。
    """

    routine_type: Literal["daily"] = Field(default="daily", description="Routine 类型；v0.1 固定 daily")
    trigger_ts: str = Field(description="触发时间戳 ISO 8601 UTC")


class RoutineCompletedPayload(BaseModel):
    """ROUTINE_COMPLETED 事件 payload（spec FR-E2 完整字段）。

    覆盖 LLM 路径与 fallback 路径，通过 ``fallback`` 字段区分。
    """

    routine_type: Literal["daily"] = Field(default="daily", description="Routine 类型；v0.1 固定 daily")
    date: str = Field(description="昨日日期 YYYY-MM-DD（用户本地时区）")
    worker_count: int = Field(ge=0, description="昨日 Worker 任务总数")
    failed_count: int = Field(ge=0, description="昨日失败任务数（status=failed）")
    attention_count: int = Field(
        ge=0,
        description="昨日开始且当前仍处于 attention 状态的任务数（SD-7 算法）",
    )
    elapsed_ms: int = Field(ge=0, description="routine 执行总耗时（毫秒）")
    # Codex Phase B review L5：默认 None 让 fallback 路径与 LLM 真 0ms 完成的边界
    # 可区分（LLM 路径成功时 MUST 设置具体毫秒数；fallback 路径必为 None）
    llm_elapsed_ms: int | None = Field(
        default=None,
        ge=0,
        description="LLM 调用耗时（毫秒）；None 表示走 fallback 路径未调用 LLM",
    )
    fallback: bool = Field(
        default=False,
        description="是否走 deterministic fallback 路径（spec SD-4 / FR-B3）",
    )
    summary_length: int = Field(
        ge=0, description="摘要字符数（空数据时为 0，对应 SD-8）"
    )
    channels: list[str] | None = Field(
        default=None,
        description="本次推送实际过滤的 channel 集合（None 表示全推；SD-6 / AC-D3）",
    )


class RoutineFailedPayload(BaseModel):
    """ROUTINE_FAILED 事件 payload（spec FR-E3）。

    不可恢复异常时写入。错误信息不含 traceback 原始文本，避免 PII 泄露。
    """

    routine_type: Literal["daily"] = Field(default="daily", description="Routine 类型；v0.1 固定 daily")
    error_type: str = Field(
        description="异常类名（短字符串，如 'cron_register_failed' / 'TimeoutError'）"
    )
    error_msg: str = Field(description="精简错误说明（不含 traceback 原始文本）")


class RoutineSkippedPayload(BaseModel):
    """ROUTINE_SKIPPED 事件 payload（spec AC-B2）。

    routine_active=False 或运行时跳过条件触发时写入。
    """

    routine_type: Literal["daily"] = Field(default="daily", description="Routine 类型；v0.1 固定 daily")
    reason: str = Field(
        description="跳过原因（如 'routine_disabled' / 'no_user_timezone'）"
    )


__all__ = [
    "DEFAULT_DAILY_SUMMARY_TIME",
    "DEFAULT_ROUTINE_ACTIVE",
    "DEFAULT_SUMMARY_CHANNELS",
    "DailyRoutineConfig",
    "RoutineCompletedPayload",
    "RoutineFailedPayload",
    "RoutineSkippedPayload",
    "RoutineTriggeredPayload",
    "extract_daily_summary_time_from_user_md",
    "extract_routine_active_from_user_md",
    "extract_summary_channels_from_user_md",
    "extract_user_timezone_from_user_md",
]
