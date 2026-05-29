"""benchmarks/tiers/tier2 — Tier 2 (业界 benchmark) adapter 集合。

20 task 分布:
- τ-bench airline 15 (FR-E01, FR-E02)
- GAIA Level 2 5 (FR-E03, FR-E04; PoC-H1 FAIL → 走 gaia_fallback_adapter)

实施依赖 (Phase B 启动 worktree 内已 uv pip install)：
- tau-bench (git+https://github.com/sierra-research/tau-bench.git)
- datasets (HuggingFace)

任何 import 前调用 benchmarks.runner.preflight.check_or_fail() 做环境自检.
"""
