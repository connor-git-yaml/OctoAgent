# PoC 脚本归档（已完成使命）

> 归档于 2026-05-31（F103d Phase F）

本目录的 PoC 脚本（install_check / poc_concurrent / poc_gaia / poc_t1 / poc_t1_verify /
poc_t3 / poc_tau / run_all_poc.sh）是 **Phase 0 PoC 阶段**的一次性验证脚本，使命已完成：

- 实测结论已固化进 `../phase-0-poc-report.md`（4 假设 + 5 task 耗时 + 8 并发压测）
- 正式 benchmark 实现已在 `benchmarks/` 目录（runner / scorer / adapter），不依赖本目录脚本
- 本目录脚本**仅作历史可追溯保留**，不再维护，不进 CI

如需复现 PoC，参考 phase-0-poc-report.md §"生成方式"。
