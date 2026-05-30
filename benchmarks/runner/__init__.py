"""F103d OctoBench runner — Phase D 主体实现入口。

Exports:
- BenchmarkStore         : SQLite append-only 持久化（store.py）
- run_daily_bench        : asyncio Semaphore(8) + gradual ramp（worker.py）
- run_single_task        : 单 task 执行 + retry（worker.py）
- generate_report        : JSON + Markdown + --compare delta（reporter.py）
- score                  : 统一 scorer 接口（按 tier 分发，scorer.py）

CLI 入口（方案 A，独立命令 `octo-bench`）：
  apps/gateway/src/octoagent/gateway/cli/bench_commands.py 通过 thin wrapper
  调用本模块；不修改任何现有 CLI 文件（FR-H01）。
"""
