"""渠道任务完成回复的共享文本构造（F105 v0.2）。

telegram 的 ``_build_result_text``（telegram.py）按 task 终态从事件流提取
回复文本——该逻辑与平台无关（status + events → text）。v0.2 抽出共享版本
供 Slack / Discord 复用；telegram 侧保持原 static method 不动（行为零变更
红线），其逻辑与本模块等价。

注：不抽 reply-target 解析——各平台 USER_MESSAGE metadata 键集不同
（telegram 4 键 / slack 2 键 / discord 1 键），per-platform 自持（D3 边界）。
"""

from __future__ import annotations

from typing import Any

from octoagent.core.models import TaskStatus


def event_type_name(event: Any) -> str:
    """事件类型名归一（enum / str 双形态，telegram._event_type_name 同语义）。"""
    event_type = getattr(event, "type", "")
    return str(getattr(event_type, "value", event_type))


def build_task_result_text(status: str, events: list[Any]) -> str:
    """按 task 终态构造用户可读回复文本（telegram._build_result_text 同语义）。"""
    if status == TaskStatus.SUCCEEDED.value:
        for event in reversed(events):
            if event_type_name(event) == "MODEL_CALL_COMPLETED":
                summary = str(event.payload.get("response_summary", "")).strip()
                if summary:
                    return summary
        return "任务已成功完成。"

    if status == TaskStatus.FAILED.value:
        for event in reversed(events):
            if event_type_name(event) == "MODEL_CALL_FAILED":
                message = str(event.payload.get("error_message", "")).strip()
                if message:
                    return f"任务失败：{message}"
        return "任务失败，请查看系统日志。"

    if status == TaskStatus.CANCELLED.value:
        return "任务已取消。"
    if status == TaskStatus.REJECTED.value:
        return "任务已被拒绝。"
    return f"任务状态已更新：{status}"
