"""F103d OctoBench — 顶层 benchmark 模块（FR-H01 零侵入：不污染 production）。

子模块：
- runner/   : 单 task 执行 / 评分 / 报告 / SQLite 持久化
- tiers/    : Tier 1/2/3 task 定义（YAML + adapter）
- baselines/: M5 / M6 baseline 报告产物目录
"""
