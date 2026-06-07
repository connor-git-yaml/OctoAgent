# F115 修复任务清单

> 方案 A。串行执行（同文件改动避免冲突）。每任务标注绑定 AC（SDD 强化）。

## T1 — daily_routine_config.py 解析层接入 timezone【AC-5 / AC-6】
- [ ] T1.1 新增 `_USER_TIMEZONE_PATTERN`（强制 `user_timezone` key prefix，仿现有 pattern）
- [ ] T1.2 新增 `extract_user_timezone_from_user_md(content) -> str | None`（解析 + zoneinfo 校验，缺失→None，非法→WARNING+None）
- [ ] T1.3 `DailyRoutineConfig` 加 `user_timezone: str | None` 字段 + docstring
- [ ] T1.4 `from_user_md` 接入；`__all__` 导出
- [ ] T1.5 模块 docstring 补"机器可读字段清单"说明

## T2 — daily_routine.py 降级链 + 移除 stale 缓存【AC-1 / AC-2 / AC-3 / AC-7】
- [ ] T2.1 `_resolve_user_timezone(user_md_tz=None)` 改降级链 USER.md→env→UTC（无参=env→UTC 兼容）
- [ ] T2.2 删 `__init__` 的 `self._user_timezone`（L125）+ 陈旧注释（L120-124），改写注释说明新解析顺序
- [ ] T2.3 `_register_cron(config, user_timezone)` 加参数
- [ ] T2.4 `_compute_yesterday_range_utc(now_utc, user_timezone)` 加参数
- [ ] T2.5 `startup`：读 config 后派生 `effective_tz` 传 `_register_cron` + log
- [ ] T2.6 `_run_daily_summary`：读 config 后派生 `effective_tz` 传 `_compute_yesterday_range_utc`

## T3 — USER.md 模板机器可读字段【AC-1】
- [ ] T3.1 Daily Routine 区增 `user_timezone` 字段 + HTML comment（IANA 名 / 默认降级 env→UTC）
- [ ] T3.2 增机器可读字段清单注释（active_hours / daily_summary_time / routine_active / summary_channels / user_timezone）

## T4 — 测试【全 AC 验收】
- [ ] T4.1 config: `TestExtractUserTimezone`（valid/missing/invalid/带引号/裸值）+ `test_config_includes_user_timezone`
- [ ] T4.2 service: `_resolve_user_timezone` 优先级（USER.md>env / env兜底 / 均缺UTC / USER.md=None降级env）+ 现有 4 用例保留
- [ ] T4.3 service 集成: `test_user_md_timezone_affects_yesterday_window`（USER.md 时区改变昨日窗口边界）

## T5 — 验证 + 文档闭环
- [ ] T5.1 focused 套件全 PASS
- [ ] T5.2 全量回归 0 regression vs 543a93b baseline（PYTHONPATH 锁 worktree）
- [ ] T5.3 e2e_smoke PASS（worktree venv，必要时 SKIP_E2E=1 + 手动 PYTHONPATH 验证）
- [ ] T5.4 living-docs 漂移闸：blueprint 时区描述比对 → drift 列 completion-report
- [ ] T5.5 completion-report.md
