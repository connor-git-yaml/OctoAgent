"""ToolTargetTracker 单元测试。"""

from __future__ import annotations

import pytest
from octoagent.skills.models import ToolTargetTracker
from pydantic import BaseModel


class FakeToolCall(BaseModel):
    tool_name: str = ""
    arguments: dict = {}


class TestTargetExtraction:
    """测试从工具参数中提取操作目标。"""

    def test_extract_file_path(self) -> None:
        tracker = ToolTargetTracker()
        targets = tracker._extract_targets(
            "terminal.exec",
            {"command": "cat octoagent/config/settings.yaml"},
        )
        assert any("octoagent/config/settings.yaml" in t for t in targets)

    def test_extract_named_params(self) -> None:
        tracker = ToolTargetTracker()
        targets = tracker._extract_targets(
            "file.read",
            {"path": "/etc/hosts", "encoding": "utf-8"},
        )
        assert "/etc/hosts" in targets

    def test_fallback_to_args_fingerprint(self) -> None:
        tracker = ToolTargetTracker()
        targets = tracker._extract_targets("some_tool", {"value": "hello"})
        assert len(targets) >= 1


class TestTargetRepeatDetection:
    """测试同一目标重复操作检测。"""

    def test_no_loop_under_threshold(self) -> None:
        tracker = ToolTargetTracker(target_repeat_threshold=5)
        for _ in range(4):
            result = tracker.record([
                FakeToolCall(
                    tool_name="terminal.exec",
                    arguments={"command": "cat config/app.yaml"},
                ),
            ])
        assert result is None

    def test_loop_at_threshold(self) -> None:
        tracker = ToolTargetTracker(target_repeat_threshold=5)
        results = []
        for _ in range(6):
            r = tracker.record([
                FakeToolCall(
                    tool_name="terminal.exec",
                    arguments={"command": "cat config/app.yaml"},
                ),
            ])
            results.append(r)
        # 第 5 次应该触发
        triggered = [r for r in results if r is not None]
        assert len(triggered) >= 1
        assert "config/app.yaml" in triggered[0]

    def test_different_tools_same_file_counted_separately(self) -> None:
        """不同工具操作同一文件，按 (tool_name, target) 分别计数。"""
        tracker = ToolTargetTracker(target_repeat_threshold=5)
        for _ in range(3):
            tracker.record([
                FakeToolCall(
                    tool_name="terminal.exec",
                    arguments={"command": "cat src/main.py"},
                ),
            ])
        for _ in range(3):
            r = tracker.record([
                FakeToolCall(
                    tool_name="file.write",
                    arguments={"file": "src/other.py"},
                ),
            ])
        # terminal.exec 3 次、file.write 3 次，各自未到阈值 5
        assert r is None

    def test_different_files_no_loop(self) -> None:
        tracker = ToolTargetTracker(target_repeat_threshold=5)
        for i in range(10):
            r = tracker.record([
                FakeToolCall(
                    tool_name="terminal.exec",
                    arguments={"command": f"cat file_{i}.yaml"},
                ),
            ])
        assert r is None


class TestAlternationDetection:
    """测试 A→B→A→B 交替循环检测。"""

    def test_alternation_detected(self) -> None:
        tracker = ToolTargetTracker(target_repeat_threshold=100)
        tracker._alternation_window = 8
        result = None
        for i in range(10):
            tool = "grep_tool" if i % 2 == 0 else "read_tool"
            result = tracker.record([
                FakeToolCall(
                    tool_name=tool,
                    arguments={"command": f"step_{i}"},
                ),
            ])
            if result:
                break
        assert result is not None
        assert "交替循环" in result

    def test_no_alternation_with_variety(self) -> None:
        tracker = ToolTargetTracker(target_repeat_threshold=100)
        tracker._alternation_window = 8
        tools = ["a", "b", "c", "d", "e", "f", "g", "h"]
        for i, t in enumerate(tools):
            r = tracker.record([
                FakeToolCall(tool_name=t, arguments={"x": str(i)}),
            ])
        assert r is None


class TestSummary:
    def test_summary_returns_top(self) -> None:
        tracker = ToolTargetTracker()
        for _ in range(3):
            tracker.record([
                FakeToolCall(
                    tool_name="exec",
                    arguments={"path": "a.txt"},
                ),
            ])
        s = tracker.summary()
        assert len(s) >= 1
