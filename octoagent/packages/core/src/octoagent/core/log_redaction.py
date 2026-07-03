"""F129 日志脱敏（FR-E，Constitution #5 出站延伸）。

常驻服务把 stdout / traceback 落盘后，provider key（OpenAI/Anthropic/DeepSeek/
SiliconFlow 均 ``sk-`` 前缀）、Telegram bot token 等极易被写进磁盘。
ThreatScanner（F084）/ F124 是**入站**扫描（防 injection），不覆盖
「secret 写进日志文件」这个**出站**面——本模块补这一层。

落在 core 包（纯 stdlib 零依赖）：gateway 的日志 formatter（写侧）与
provider dx 的 `octo logs` / status ``last_error_line``（读侧，展示 service
层**未脱敏**原始输出时）共用同一实现——Codex review P2（三轮）。

设计（research.md §B.2，参考 Hermes ``agent/redact.py``）：

- **纯函数** ``redact_sensitive_text()``：厂商前缀 + ENV/JSON 字段赋值 +
  ``Authorization: Bearer`` + Telegram bot token + 连接串密码 + JWT。
- **掩码策略**：短 token（<18 字符）全 ``***``；长 token 留头 6 尾 4，
  中间用 ``…``（U+2026，不在任何 token 字符类里 → 重跑正则不再命中，幂等）。
- **性能**：每条规则带廉价 substring 预检，无密钥形状的行不跑全量正则。
- **安全默认**（FR-E3）：``_REDACT_ENABLED`` 在 **import 时快照** env
  ``OCTOAGENT_LOG_REDACT``（默认 ON）——防运行时 ``export ..._REDACT=false``
  中途关掉脱敏（契合「单次授权 / 禁令优于指令」）。公共入口不提供
  enabled 覆盖参数（不留绕过缝）。
- **诚实边界**（FR-E5）：正则脱敏非万能，自定义格式 secret 可能漏网——
  日志文件仍属敏感（0600 权限，见 logging_config），勿外发。
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from re import Match, Pattern

# ---------------------------------------------------------------------------
# FR-E3：import 时快照 env（默认 ON；运行时改 env 不生效）
# ---------------------------------------------------------------------------

_REDACT_ENABLED: bool = (
    os.environ.get("OCTOAGENT_LOG_REDACT", "true").strip().lower() != "false"
)

_MASK = "***"
#: 长 token 掩码分隔符。刻意选不属于任何 token 字符类的字符（非 [A-Za-z0-9_.-]），
#: 保证掩码后的文本重跑同一批正则不再命中（幂等，FR-E4）。
_ELLIPSIS = "…"

#: 短于此长度的 token 全遮（信息量低，留头尾反而增加还原面）；
#: 达到此长度的留头 6 尾 4（FR-E2）。
_FULL_MASK_THRESHOLD = 18


def _mask_token(token: str) -> str:
    if len(token) < _FULL_MASK_THRESHOLD:
        return _MASK
    return f"{token[:6]}{_ELLIPSIS}{token[-4:]}"


def _mask_value(secret: str) -> str:
    """值位置掩码（ENV/JSON/Bearer 的捕获组）：已脱敏的值原样保留（幂等）。

    形状类规则（sk-/JWT/Telegram）的幂等由掩码破坏 token 形状结构性保证；
    值位置规则的捕获组是「任意非空白串」，会把上一轮的掩码再当值抓一次，
    必须显式短路。
    """
    if secret == _MASK or _ELLIPSIS in secret:
        return secret
    return _mask_token(secret)


# ---------------------------------------------------------------------------
# 规则表：(预检 substring 判定, 编译正则, 替换函数)
# ---------------------------------------------------------------------------

# 1) 厂商 key 前缀（FR-E2 子集）：OpenAI / Anthropic(sk-ant-) / DeepSeek /
#    SiliconFlow 实测均为 ``sk-`` 前缀，一条规则全覆盖。
_PREFIX_KEY_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}")

# 2a) ENV 赋值：字段名含 API_KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH（大小写
#     不敏感），值到下一个空白/引号为止。安全优先：AUTH_MODE=api_key 这类
#     低敏值也会被遮（可读性让位安全，spec FR-E2 显式列 AUTH）。
_ENV_ASSIGN_PATTERN = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH)"
    r"[A-Za-z0-9_]*)\s*=\s*([\"']?)([^\s\"']+)",
    flags=re.IGNORECASE,
)

# 2b) JSON/dict 字段：``"api_key": "value"`` 形态。
_JSON_FIELD_PATTERN = re.compile(
    r"([\"'])([A-Za-z0-9_\-]*(?:api[_\-]?key|token|secret|password|credential|auth)"
    r"[A-Za-z0-9_\-]*)\1(\s*[:=]\s*)([\"'])([^\"']+)\4",
    flags=re.IGNORECASE,
)

# 3) Authorization: Bearer <token>
_BEARER_PATTERN = re.compile(r"(?i)\b(Bearer\s+)([A-Za-z0-9_\-.=+/]{8,})")

# 4) Telegram bot token：8-10 位 bot id + ':' + 30-64 位 token 段
#    （实测经典样本 34 位、新签发 35 位，取宽容区间）。
_TELEGRAM_TOKEN_PATTERN = re.compile(r"\b(\d{8,10}):([A-Za-z0-9_\-]{30,64})\b")

# 5) 连接串密码：scheme://user:password@host
_CONNECTION_URI_PATTERN = re.compile(
    r"\b([a-zA-Z][a-zA-Z0-9+.\-]*://[^:/\s@]+):([^@/\s]+)@"
)

# 6) JWT：eyJ 开头三段式。
_JWT_PATTERN = re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+")

_KEYWORD_PROBES = ("key", "token", "secret", "password", "credential", "auth")


def _has_keyword(text_lower: str) -> bool:
    return any(keyword in text_lower for keyword in _KEYWORD_PROBES)


#: ANSI CSI 转义序列（rich/ConsoleRenderer 彩色输出）。
#: 色码以字母结尾（如 ``\\x1b[33m``）紧贴 secret 时会打断 ``\\b`` 词边界，
#: 让前缀规则失配（实测 rich traceback 源码行泄漏 sk- key）——见
#: ``redact_sensitive_text`` 的降级第二遍。
_ANSI_CSI_PATTERN = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

_RuleProbe = Callable[[str, str], bool]
_RuleReplace = Callable[[Match[str]], str]

_RULES: list[tuple[_RuleProbe, Pattern[str], _RuleReplace]] = [
    (
        lambda text, _lower: "sk-" in text,
        _PREFIX_KEY_PATTERN,
        lambda match: _mask_token(match.group(0)),
    ),
    (
        lambda text, lower: "=" in text and _has_keyword(lower),
        _ENV_ASSIGN_PATTERN,
        lambda match: f"{match.group(1)}={match.group(2)}{_mask_value(match.group(3))}",
    ),
    (
        lambda text, lower: (":" in text or "=" in text) and _has_keyword(lower),
        _JSON_FIELD_PATTERN,
        lambda match: (
            f"{match.group(1)}{match.group(2)}{match.group(1)}{match.group(3)}"
            f"{match.group(4)}{_mask_value(match.group(5))}{match.group(4)}"
        ),
    ),
    (
        lambda _text, lower: "bearer " in lower,
        _BEARER_PATTERN,
        lambda match: f"{match.group(1)}{_mask_value(match.group(2))}",
    ),
    (
        lambda text, _lower: ":" in text,
        _TELEGRAM_TOKEN_PATTERN,
        lambda match: f"{match.group(1)}:{_mask_token(match.group(2))}",
    ),
    (
        lambda text, _lower: "://" in text,
        _CONNECTION_URI_PATTERN,
        lambda match: f"{match.group(1)}:{_MASK}@",
    ),
    (
        lambda text, _lower: "eyJ" in text,
        _JWT_PATTERN,
        lambda match: _mask_token(match.group(0)),
    ),
]


def _apply_rules(text: str) -> str:
    lower = text.lower()
    for probe, pattern, replace in _RULES:
        if probe(text, lower):
            text = pattern.sub(replace, text)
    return text


def _redact_with_flag(text: str, *, enabled: bool) -> str:
    """脱敏核心纯函数（enabled 显式传入，仅供本模块与测试使用）。

    任何内部异常都不得抛出（FR-D5 日志链路不阻塞主流程）；异常时**宁可
    丢弃原文**返回占位符，也不把可能含 secret 的原文放行（安全优先）。

    ANSI 降级第二遍：彩色渲染（rich traceback 等）的色码以字母结尾、
    紧贴 secret 时会打断 ``\\b`` 词边界让规则失配——若剥掉 ANSI 后仍能
    抓到 secret 形状，整段降级输出「无色 + 脱敏」版本（安全 > 颜色）。
    """
    if not enabled or not text:
        return text
    try:
        redacted = _apply_rules(text)
        if "\x1b[" in redacted:
            stripped = _ANSI_CSI_PATTERN.sub("", redacted)
            stripped_redacted = _apply_rules(stripped)
            if stripped_redacted != stripped:
                return stripped_redacted
        return redacted
    except Exception as exc:  # pragma: no cover - 正则纯函数极难触发
        return f"[log-redaction-error: {type(exc).__name__}]"


def redact_sensitive_text(text: str) -> str:
    """对单条日志文本脱敏（公共入口，开关取 import 时快照，FR-E3）。"""
    return _redact_with_flag(text, enabled=_REDACT_ENABLED)


__all__ = ["redact_sensitive_text"]
