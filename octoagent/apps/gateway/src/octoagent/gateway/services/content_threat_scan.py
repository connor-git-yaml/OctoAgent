"""F124 T014: ContentThreatScanService —— 内容威胁扫描的统一 service（C10 单一 scanner 入口）。

plan DP-6 / FR-6.3：内容威胁扫描统一经本 service。
- **拦截入口**：PolicyGate 经 `scan_memory()`（MEMORY scope，命中可 block）。
- **标注入口**：ToolBroker 经 `scan_tool_context()`（CONTEXT scope，命中只标注不 block）。

实现 tooling 的 `ContentThreatScanProtocol`（scope-free `scan_tool_context`），broker 依赖该抽象、
不依赖 gateway（plan PR2-F1）；gateway 装配时构造**单实例**注入 broker + 共享给 PolicyGate。
service 无状态（纯转发 gateway 的 threat_scanner），可安全单例。
"""

from __future__ import annotations

from octoagent.gateway.harness.threat_scanner import (
    ScanScope,
    ThreatScanResult,
    scan,
    scan_context,
)
from octoagent.tooling.models import ToolSecurityFinding


class ContentThreatScanService:
    """内容威胁扫描统一 service（实现 ContentThreatScanProtocol）。"""

    def scan_tool_context(
        self, content: str, source_field: str = "output"
    ) -> list[ToolSecurityFinding]:
        """CONTEXT scope 有界全覆盖扫描（tool 结果路径，标注入口）。

        返回命中 finding（first-hit + degraded 兜底）；clean 空 list。永不 block。
        """
        return scan_context(content, source_field=source_field)

    def scan_memory(self, content: str) -> ThreatScanResult:
        """MEMORY scope 扫描（memory/profile 写入路径，拦截入口）。

        PolicyGate 据 `result.blocked` 决定是否拒绝写入；超输入上限 → degraded BLOCK。
        与 baseline `threat_scan(content)` 行为字节级等价（默认 scope=MEMORY）。
        """
        return scan(content, ScanScope.MEMORY)
