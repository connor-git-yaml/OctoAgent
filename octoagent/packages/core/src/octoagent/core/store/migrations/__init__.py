"""Core store 存量数据迁移。

F117 起：core 表结构迁移（如 WorkerProfile/AgentProfile 合并）放此目录，
与 memory 包的 ``octoagent.memory.migrations`` 布局对齐（migration_<NNN>_<slug>.py
+ run_dry_run / run_apply / run_rollback 三段式）。
"""
