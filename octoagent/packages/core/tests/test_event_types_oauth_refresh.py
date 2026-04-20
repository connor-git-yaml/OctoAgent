"""Feature 078 Phase 4 —— 新 OAuth refresh EventType 枚举值。

验证 5 个新枚举值存在、字面量正确、可序列化。
"""

from __future__ import annotations

from octoagent.core.models.enums import EventType


def test_new_event_types_exist() -> None:
    """5 个新枚举值可通过 EventType.XXX 访问。"""
    assert EventType.OAUTH_REFRESH_TRIGGERED.value == "OAUTH_REFRESH_TRIGGERED"
    assert EventType.OAUTH_REFRESH_FAILED.value == "OAUTH_REFRESH_FAILED"
    assert EventType.OAUTH_REFRESH_RECOVERED.value == "OAUTH_REFRESH_RECOVERED"
    assert EventType.OAUTH_REFRESH_EXHAUSTED.value == "OAUTH_REFRESH_EXHAUSTED"
    assert (
        EventType.OAUTH_ADOPTED_FROM_EXTERNAL_CLI.value
        == "OAUTH_ADOPTED_FROM_EXTERNAL_CLI"
    )


def test_existing_oauth_event_types_preserved() -> None:
    """既有 4 个 OAuth 事件未被破坏（回归保护）。"""
    assert EventType.OAUTH_STARTED.value == "OAUTH_STARTED"
    assert EventType.OAUTH_SUCCEEDED.value == "OAUTH_SUCCEEDED"
    assert EventType.OAUTH_FAILED.value == "OAUTH_FAILED"
    assert EventType.OAUTH_REFRESHED.value == "OAUTH_REFRESHED"


def test_new_event_types_are_roundtrip_serializable() -> None:
    """字符串 value 能还原为枚举（Event payload 序列化场景）。"""
    for et in (
        EventType.OAUTH_REFRESH_TRIGGERED,
        EventType.OAUTH_REFRESH_FAILED,
        EventType.OAUTH_REFRESH_RECOVERED,
        EventType.OAUTH_REFRESH_EXHAUSTED,
        EventType.OAUTH_ADOPTED_FROM_EXTERNAL_CLI,
    ):
        restored = EventType(et.value)
        assert restored is et


def test_all_new_values_follow_upper_snake_case() -> None:
    for et in (
        EventType.OAUTH_REFRESH_TRIGGERED,
        EventType.OAUTH_REFRESH_FAILED,
        EventType.OAUTH_REFRESH_RECOVERED,
        EventType.OAUTH_REFRESH_EXHAUSTED,
        EventType.OAUTH_ADOPTED_FROM_EXTERNAL_CLI,
    ):
        assert et.value == et.value.upper()
        assert " " not in et.value
