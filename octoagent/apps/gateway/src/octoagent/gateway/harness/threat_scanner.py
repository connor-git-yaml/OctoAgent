"""threat_scanner.py：ThreatScanner — pattern table + invisible unicode 检测（Feature 084 Phase 1）。

架构决策（plan.md D3）：纯正则 pattern table，无 LLM，微秒级，离线。
参考 Hermes Agent approval.py 的 DANGEROUS_PATTERNS 设计模式。

FR 覆盖：
- FR-3.2（≥ 15 条 pattern table）
- FR-3.3（invisible unicode 检测）
- FR-3.4（BLOCK 时返回 pattern_id）
- Constitution C6（离线可用，无依赖）
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


# ---------------------------------------------------------------------------
# T008: pattern table（≥ 15 条）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ThreatPattern:
    """单条威胁检测 pattern。"""

    id: str
    """Pattern ID，如 "PI-001"。"""

    pattern: re.Pattern[str]
    """编译后的正则表达式。"""

    severity: Literal["WARN", "BLOCK"]
    """严重级别：BLOCK 立即拦截，WARN 记录日志后继续。"""

    description: str
    """人类可读描述，用于用户展示和事件记录。"""


def _p(
    pattern_id: str,
    regex: str,
    severity: Literal["WARN", "BLOCK"],
    description: str,
) -> ThreatPattern:
    """辅助函数：构建编译好的 ThreatPattern。"""
    return ThreatPattern(
        id=pattern_id,
        pattern=re.compile(regex, re.IGNORECASE | re.DOTALL),
        severity=severity,
        description=description,
    )


# Prompt Injection（PI）系列
# 目标：检测试图覆盖系统指令的注入攻击
_PI_001 = _p(
    "PI-001",
    r"\bignore\s+(previous|above|all\s+previous)\s+(instructions?|prompts?|rules?|guidelines?)\b",
    "BLOCK",
    "Prompt injection: 忽略前述指令",
)

_PI_002 = _p(
    "PI-002",
    r"\bdisregard\s+(your\s+)?(previous|above|all\s+previous|prior)\s+(instructions?|system\s+prompt)\b",
    "BLOCK",
    "Prompt injection: 无视系统提示",
)

_PI_003 = _p(
    "PI-003",
    r"\bforget\s+(everything|all)\s+(you\s+)?(know|were\s+told|learned)\b",
    "BLOCK",
    "Prompt injection: 重置 LLM 记忆",
)

_PI_004 = _p(
    "PI-004",
    r"\byou\s+are\s+now\s+(a|an)\s+(?!expert|good|great|helpful|better)\w",
    "WARN",
    "Prompt injection: 试图重新定义 AI 身份",
)

_PI_005 = _p(
    "PI-005",
    r"\boverride\s+(your\s+)?(safety|security|ethical|system)\s+(guidelines?|rules?|constraints?|filters?)\b",
    "BLOCK",
    "Prompt injection: 试图绕过安全约束",
)

# Role Hijacking（RH）系列
# 目标：检测试图劫持 AI 角色的攻击
_RH_001 = _p(
    "RH-001",
    r"\bpretend\s+(you\s+are|to\s+be)\s+(a\s+)?(malicious|hacker|attacker|evil|harmful)\b",
    "BLOCK",
    "Role hijacking: 强迫扮演恶意角色",
)

_RH_002 = _p(
    "RH-002",
    r"\bact\s+as\s+(if\s+you\s+have\s+no\s+(restrictions?|limits?|guidelines?)|an?\s+unrestricted)\b",
    "BLOCK",
    "Role hijacking: 无限制模式角色扮演",
)

_RH_003 = _p(
    "RH-003",
    r"\b(jailbreak|DAN|do\s+anything\s+now|developer\s+mode)\b",
    "BLOCK",
    "Role hijacking: 常见 jailbreak 关键词",
)

# Exfiltration（EX）系列
# 目标：检测通过网络工具外泄数据的命令
_EX_001 = _p(
    "EX-001",
    r"\b(curl|wget)\b[^;|\n]*\|",
    "BLOCK",
    "Exfiltration: curl/wget 管道执行（远程代码执行风险）",
)

_EX_002 = _p(
    "EX-002",
    r"\b(curl|wget)\b[^;|\n]*(malicious|evil|attacker|c2|command.and.control)",
    "BLOCK",
    "Exfiltration: curl/wget 访问恶意地址",
)

_EX_003 = _p(
    "EX-003",
    r"\bssh\b[^;|\n]+(-R\s+\d|\bautossh\b|\breverse\s+tunnel|\bbackdoor\b)",
    "BLOCK",
    "Exfiltration: SSH 反向隧道或后门",
)

# Base64 Payload（B64）系列
# 目标：检测 base64 编码的恶意 payload
_B64_001 = _p(
    "B64-001",
    r"\bbase64\s*(-d|--decode)\b",
    "BLOCK",
    "Base64 payload: base64 解码执行",
)

_B64_002 = _p(
    "B64-002",
    r"\becho\b[^;|\n]+\|\s*base64\s*(-d|--decode)",
    "BLOCK",
    "Base64 payload: echo + base64 解码管道",
)

# System Override（SO）系列
# 目标：检测试图覆盖系统配置的攻击
_SO_001 = _p(
    "SO-001",
    r"\b(you\s+must\s+|always\s+|from\s+now\s+on\s+)(follow|obey|comply\s+with|execute)\s+my\s+(instructions?|commands?|orders?)\b",
    "WARN",
    "System override: 强制命令服从声明",
)

_SO_002 = _p(
    "SO-002",
    r"\b(new\s+system\s+prompt|replace\s+your\s+system\s+prompt|update\s+your\s+instructions?)\b",
    "BLOCK",
    "System override: 试图替换系统提示",
)

# Memory Injection（MI）系列
# 目标：检测针对记忆系统的注入攻击
_MI_001 = _p(
    "MI-001",
    r"\b(add|write|inject|insert)\s+(to\s+)?(memory|USER\.md|profile)\b.*\b(ignore|bypass|override)\b",
    "BLOCK",
    "Memory injection: 试图在记忆写入中注入覆盖指令",
)

_MI_002 = _p(
    "MI-002",
    r"\[(system|assistant|user)\]\s*:",
    "WARN",
    "Memory injection: 消息角色伪造标记",
)


# 完整 pattern 列表（≥ 15 条，FR-3.2）
_MEMORY_THREAT_PATTERNS: list[ThreatPattern] = [
    _PI_001,  # BLOCK
    _PI_002,  # BLOCK
    _PI_003,  # BLOCK
    _PI_004,  # WARN
    _PI_005,  # BLOCK
    _RH_001,  # BLOCK
    _RH_002,  # BLOCK
    _RH_003,  # BLOCK
    _EX_001,  # BLOCK
    _EX_002,  # BLOCK
    _EX_003,  # BLOCK
    _B64_001, # BLOCK
    _B64_002, # BLOCK
    _SO_001,  # WARN
    _SO_002,  # BLOCK
    _MI_001,  # BLOCK
    _MI_002,  # WARN
]

assert len(_MEMORY_THREAT_PATTERNS) >= 15, "FR-3.2 要求至少 15 条 pattern"


# ---------------------------------------------------------------------------
# T009: invisible unicode 检测 + ThreatScanResult + scan()
# ---------------------------------------------------------------------------

# 零宽字符集合（FR-3.3）
_INVISIBLE_CHARS: frozenset[str] = frozenset([
    "​",  # Zero-width space
    "‌",  # Zero-width non-joiner
    "‍",  # Zero-width joiner
    "‎",  # Left-to-right mark
    "‏",  # Right-to-left mark
    "‪",  # Left-to-right embedding
    "‫",  # Right-to-left embedding
    "‬",  # Pop directional formatting
    "‭",  # Left-to-right override
    "‮",  # Right-to-left override（隐藏文本常用）
    "⁠",  # Word joiner
    "⁡",  # Function application
    "⁢",  # Invisible times
    "⁣",  # Invisible separator
    "⁤",  # Invisible plus
    "﻿",  # Zero-width no-break space / BOM（常用于混淆）
    "­",  # Soft hyphen
])


@dataclass(frozen=True)
class ThreatScanResult:
    """扫描结果值对象（data-model.md）。

    从 scan() 返回，作为 PolicyGate 决策输入。
    """

    blocked: bool
    """是否应被拦截。"""

    pattern_id: str | None
    """命中的 pattern ID，如 "PI-001"；未命中时为 None。"""

    severity: Literal["WARN", "BLOCK"] | None
    """严重级别；未命中时为 None。"""

    matched_pattern_description: str | None
    """人类可读描述，展示给用户；未命中时为 None。"""


# 无威胁时的复用单例
_CLEAN_RESULT = ThreatScanResult(
    blocked=False,
    pattern_id=None,
    severity=None,
    matched_pattern_description=None,
)


def scan(content: str) -> ThreatScanResult:
    """扫描 content 是否含威胁 pattern（FR-3.2/FR-3.3）。

    扫描流程：
    1. O(n) invisible unicode 检测（FR-3.3）：遍历字符，发现零宽字符立即返回 BLOCK
    2. 遍历 _MEMORY_THREAT_PATTERNS，BLOCK 级命中立即返回（短路）
    3. 全部未命中返回 blocked=False

    性能目标：< 1ms（Threat Scanner FR-3 验收标准）。

    Args:
        content: 要扫描的字符串。

    Returns:
        ThreatScanResult。blocked=True 时应拒绝写入，blocked=False 时允许通过。
    """
    if not content:
        return _CLEAN_RESULT

    # Step 1：零宽字符检测（O(n) 遍历）
    for char in content:
        if char in _INVISIBLE_CHARS:
            return ThreatScanResult(
                blocked=True,
                pattern_id="INVIS-001",
                severity="BLOCK",
                matched_pattern_description=f"检测到零宽字符（U+{ord(char):04X}），可能用于隐藏恶意内容",
            )

    # Step 2：pattern table 扫描
    # BLOCK 级：立即返回（不继续扫描）
    # WARN 级：记录但继续扫描（取第一个 WARN 作为结果）
    first_warn: ThreatScanResult | None = None

    for tp in _MEMORY_THREAT_PATTERNS:
        if tp.pattern.search(content):
            if tp.severity == "BLOCK":
                return ThreatScanResult(
                    blocked=True,
                    pattern_id=tp.id,
                    severity="BLOCK",
                    matched_pattern_description=tp.description,
                )
            # WARN 级：不 block，但记录第一个命中
            if first_warn is None:
                first_warn = ThreatScanResult(
                    blocked=False,
                    pattern_id=tp.id,
                    severity="WARN",
                    matched_pattern_description=tp.description,
                )

    # 有 WARN 级命中时返回（blocked=False）
    if first_warn is not None:
        return first_warn

    return _CLEAN_RESULT
