"""APScheduler CronTrigger DOW 语义钉住（F142 件1 / L4 真库）。

钉住对象：`cron_tools.py` DP-3（Codex P1-1）「拒绝纯数字 DOW」的**根据**——
APScheduler 星期字段从 Monday=0 计，与常见 Unix cron（Sunday=0 / Monday=1）
不同。该假设此前只被我们自家校验器的单测覆盖（`test_cron_tools.py` 测
`_cron_field_is_numeric_dow` 拒数字），全仓 0 个测试 import 真 apscheduler
验证语义本身——若 APScheduler 升级改用标准 cron 语义（4.x 曾讨论），我们的
拒绝理由文案与 LLM 引导（`cron_tools.py:189` "星期从 Monday=0 计"）会静默失真。

真库消费点：`automation_scheduler.py:193` `CronTrigger.from_crontab(expr, timezone)`。

语义实证（apscheduler 3.11.2，2026-07-12，本文件断言与之逐值对齐）：
    '0 9 * * 0'   → 下次触发落周一（Unix cron 会是周日——off-by-one 陷阱本体）
    '0 9 * * mon' → 周一（命名星期无歧义，DP-3 引导用户用这个）
    '0 9 * * sun' ≡ '0 9 * * 6' → 周日
    '0 9 * * 1'   → 周二（Unix cron 会是周一）
"""

from __future__ import annotations

from datetime import UTC, datetime

from apscheduler.triggers.cron import CronTrigger

# 固定参考时刻：2026-01-07 12:00 UTC 是周三（weekday()==2），一周内任何目标
# 星期的 next_fire_time 都无歧义。
_REF_NOW = datetime(2026, 1, 7, 12, 0, 0, tzinfo=UTC)


def _next_weekday(expr: str) -> int:
    """真 CronTrigger 计算下次触发日的 Python weekday()（Monday=0）。"""
    trigger = CronTrigger.from_crontab(expr, timezone="UTC")
    nxt = trigger.get_next_fire_time(None, _REF_NOW)
    assert nxt is not None, f"{expr!r} 应有下次触发时间"
    return nxt.weekday()


class TestApschedulerDowIsMondayZero:
    """APScheduler 数字 DOW = Monday=0（cron_tools DP-3 拒数字的根据）。"""

    def test_numeric_zero_fires_on_monday_not_sunday(self) -> None:
        """陷阱本体：数字 0 在 APScheduler 落**周一**；Unix cron 语义是周日。

        若本断言变红 = APScheduler 改成了标准 cron 语义 → cron_tools DP-3 的
        拒绝理由文案 + LLM 引导（"数字会导致每周提醒错一天"）需要同步改写。
        """
        assert _next_weekday("0 9 * * 0") == 0  # Python weekday 0 == Monday

    def test_numeric_one_fires_on_tuesday(self) -> None:
        """数字 1 → 周二（Unix cron 语义会是周一）——off-by-one 的另一面。"""
        assert _next_weekday("0 9 * * 1") == 1  # Tuesday

    def test_named_mon_fires_on_monday(self) -> None:
        """命名星期 mon 无歧义落周一——DP-3 引导用户改用命名星期的根据。"""
        assert _next_weekday("0 9 * * mon") == 0

    def test_named_sun_equals_numeric_six(self) -> None:
        """sun ≡ 数字 6（APScheduler 计法的另一端），两者同落周日。"""
        assert _next_weekday("0 9 * * sun") == 6  # Python weekday 6 == Sunday
        assert _next_weekday("0 9 * * 6") == 6

    def test_named_dow_full_week_alignment(self) -> None:
        """全周命名星期 → Python weekday() 一一对齐（升级后任何一天漂移都红）。"""
        named = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        for idx, name in enumerate(named):
            assert _next_weekday(f"0 9 * * {name}") == idx, (
                f"{name} 应落 Python weekday()=={idx}"
            )
