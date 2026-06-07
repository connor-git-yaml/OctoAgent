# F115 验证报告

> baseline = origin/master @ 543a93b（= d2936e0 + docs-only commit，测试等价）。
> 所有 pytest 调用均 PYTHONPATH 锁 worktree src（防 symlink venv 跑主仓 master 的假 0 regression）。

## 工具链验证

| 项 | 命令 | 结果 |
|----|------|------|
| import 判别式 | `python -c "import ...daily_routine; print(__file__)"` | 解析到 `.claude/worktrees/F115-routine-tz/...`（锁 worktree ✓）|
| focused 套件 | daily_routine config + service + budget | 87 passed |
| 全量回归 baseline | `pytest -m "not e2e_*"` @ clean 543a93b | 3772 passed, 0 failed |
| 全量回归 post-fix #1 | 同上（修 budget 前）| 3791 passed, **1 failed**（budget 超限）→ 自修 |
| 全量回归 post-fix #2 | 同上（修 budget 后）| **3793 passed, 0 failed** |
| e2e_smoke | `pytest -m e2e_smoke` | **8 passed** |

**0 regression 确认**：3772（baseline）→ 3793（post-fix）= +21 新增用例，无既有用例失败。

## AC 验收（全绑定测试 PASS）

| AC | 测试 | 结果 |
|----|------|------|
| AC-1 USER.md 优先 env | TestUserTimezoneResolver::test_user_md_overrides_env(_even_when_env_absent) | ✅ |
| AC-2 env 兜底 | TestUserTimezoneResolver::test_env_fallback_when_user_md_none | ✅ |
| AC-3 均缺 → UTC | TestUserTimezoneResolver::test_utc_when_both_absent / test_default_fallback_to_utc | ✅ |
| AC-4 非法 → None → 降级 | TestExtractUserTimezone::test_invalid_timezone_returns_none | ✅ |
| AC-5 extract 正确性 | TestExtractUserTimezone（12 用例）| ✅ |
| AC-6 config 携带 | TestConfigIncludesUserTimezone（3 用例）| ✅ |
| AC-7 影响昨日窗口 | TestUserMdTimezoneAffectsYesterdayWindow | ✅ |
| 守卫 出厂模板不硬编码时区 | TestExtractUserTimezone::test_shipped_template_user_timezone_is_unset | ✅ |

## 向后兼容确认

- 现有部署 USER.md 无 `user_timezone` 字段 → 解析 None → 降级 env → 与修复前 100% 等价（仅当用户主动填字段才覆盖 env）。
- `_resolve_user_timezone()` 无参调用 == 原 env→UTC 行为 → 原 4 个测试零修改通过。
- `DailyRoutineConfig.user_timezone` 末位 + `= None` 默认 → 既有 4 处直接构造零改动。

## GATE_VERIFY

`[GATE] GATE_VERIFY | policy=on_failure | decision=AUTO_CONTINUE | reason=0 CRITICAL（全量回归 0 regression + e2e_smoke 8/8 + 8 AC 全绑定 PASS）`

## 已知 limitations

见 completion-report.md「已知 limitations」：① routine_active 注释行潜在 bug（F102 遗留，建议独立立件）；② cron 触发时刻改时区需重启（v0.1 限制）。
