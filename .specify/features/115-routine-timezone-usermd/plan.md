# F115 修复规划 — daily_routine 时区接入 USER.md SoT

> 基于 fix-report.md 推荐方案 A。规模 S，最小化变更范围 + 行为向后兼容。

## 变更清单（方案 A）

### C1 — `daily_routine_config.py`：解析层接入 timezone
- 新增正则 `_USER_TIMEZONE_PATTERN`（仿 `_DAILY_SUMMARY_TIME_PATTERN`，强制 `user_timezone` key prefix）
- 新增 `extract_user_timezone_from_user_md(content: str | None) -> str | None`：
  - 无内容 / 无字段 → `None`
  - 提取值经 `zoneinfo.ZoneInfo()` 校验：有效 → 返回 IANA 名；非法 → WARNING + `None`
- `DailyRoutineConfig` 加 `user_timezone: str | None` 字段
- `from_user_md` 接入新解析函数
- `__all__` 导出新函数

### C2 — `daily_routine.py`：解析降级链 + 移除 stale 缓存
- `_resolve_user_timezone(user_md_tz: str | None = None) -> str`：
  - `user_md_tz` 非 None（已由 C1 校验有效）→ 返回它
  - 否则读 env `OCTOAGENT_USER_TIMEZONE`，有效 → 返回，非法 → WARNING + 继续降级
  - 最终 → `"UTC"`
  - **契约**：无参调用 == 原 env→UTC 行为（保兼容）
- 删除 `__init__` 的 `self._user_timezone` 字段（L125）及陈旧注释（L120-124）
- `_register_cron(config, user_timezone: str)`：加参数，cron 时区用传入值
- `_compute_yesterday_range_utc(now_utc, user_timezone: str)`：加参数，昨日窗口用传入值
- `startup`：读 config 后 `effective_tz = _resolve_user_timezone(config.user_timezone)`，传给 `_register_cron` + log
- `_run_daily_summary`：读 config 后派生 `effective_tz`，传给 `_compute_yesterday_range_utc`

### C3 — `behavior_templates/USER.md`：机器可读字段 + 字段清单
- Daily Routine 区（L35-44）增 `user_timezone` 字段 + HTML comment 说明（IANA 名，默认降级 env→UTC）
- 增"机器可读字段清单"注释（F102 handoff 洞察）：列出 active_hours / daily_summary_time / routine_active / summary_channels / user_timezone

### C4 — 测试
- `test_f102_daily_routine_config.py`：新增 `extract_user_timezone_from_user_md` 用例 + config 字段用例
- `test_f102_daily_routine_service.py`：新增 `_resolve_user_timezone(user_md_tz=...)` 优先级用例 + service 级集成（USER.md 时区影响昨日窗口）

## 回归风险评估

| 风险 | 评估 | 缓解 |
|------|------|------|
| `_resolve_user_timezone` 签名变更破坏现有 4 测试 | 低 | 加**可选**参数默认 None，无参调用行为不变 |
| 移除 `self._user_timezone` 破坏消费点 | 低 | 仅 2 消费点（均本文件内），改 caller 传参；无测试依赖该字段/方法签名（已 grep 确认） |
| 优先级翻转（env→USER.md 优先）影响现有部署 | 无 | 现有 USER.md 无 user_timezone 字段→None→降级 env，100% 等价 |
| `_register_cron`/`_compute_yesterday_range_utc` 签名变更 | 低 | 无测试直接调用（已 grep 确认），唯一 caller 同步更新 |

## 修复验证方案

1. 单测：C4 新增用例全 PASS
2. 0 regression：focused（daily_routine 套件）+ 全量 vs 543a93b baseline
3. e2e_smoke：`pytest -m e2e_smoke`（worktree venv，必要时 SKIP_E2E=1）
4. living-docs 漂移闸：blueprint 时区描述比对

## AC ↔ Test 绑定（SDD 强化）

| AC | 描述 | 绑定测试 |
|----|------|----------|
| AC-1 | USER.md 有效 `user_timezone` 字段生效（优先 env） | `test_f102_daily_routine_service.py::TestUserTimezoneResolver::test_user_md_overrides_env` |
| AC-2 | USER.md 无字段时 env 兜底 | `...::test_env_fallback_when_no_user_md` |
| AC-3 | USER.md + env 均缺 → UTC | `...::test_default_fallback_to_utc`（现有，保留） |
| AC-4 | USER.md 非法时区 → 解析 None → 降级 env/UTC | `test_f102_daily_routine_config.py::test_user_timezone_invalid_returns_none` + `service` 降级用例 |
| AC-5 | `extract_user_timezone_from_user_md` 正确解析/缺失/非法 | `test_f102_daily_routine_config.py::TestExtractUserTimezone` |
| AC-6 | config 携带 user_timezone | `test_f102_daily_routine_config.py::test_config_includes_user_timezone` |
| AC-7 | USER.md 时区影响昨日窗口（service 集成） | `test_f102_daily_routine_service.py::test_user_md_timezone_affects_yesterday_window` |
