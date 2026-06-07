# F115 完成报告 — daily_routine 时区接入 USER.md SoT

> 模式：spec-driver-fix｜规模：S｜分支：feature/115-routine-timezone-usermd｜baseline：origin/master @ 543a93b

## 一句话结论

USER.md 新增机器可读 `user_timezone` 字段，DailyRoutine 生效时区改为 **USER.md → env → UTC** 降级链；移除 `__init__` 阶段 env-only 的 stale 缓存 `self._user_timezone`，时区每次从重读的 config 派生——cron 触发时刻 + "昨日"窗口现真正受 USER.md（SoT）控制。

## 解决的问题（用户视角）

修复前：用户在 USER.md（号称唯一 SoT）里改时区，对每日摘要"几点推送"和"哪段算昨天"**零影响**——真正生效的是部署侧环境变量 `OCTOAGENT_USER_TIMEZONE`。这是用户改了 SoT 却不生效的直接 UX 缺陷。修复后：用户在 USER.md 填 `user_timezone: "Asia/Shanghai"` 即生效（昨日窗口下次 cron 触发立即生效；cron 触发时刻重启后生效）。

## 根因（5-Why 浓缩）

`self._user_timezone` 在 `__init__` 一次性从 env 解析缓存，时区解析链里**根本没有 USER.md 这一环**；且 USER.md 模板缺机器可读 timezone 字段、`DailyRoutineConfig` 未解析它。两个消费点（cron 注册 + 昨日窗口）都读这个 env-only 缓存。详见 [fix-report.md](fix-report.md)。

## 实际改动 vs 计划（plan.md / tasks.md 对照）

| 任务 | 计划 | 实际 | 偏离 |
|------|------|------|------|
| T1 config 解析层 | extract 函数 + config 字段 + 导出 | ✅ 完成 | `user_timezone` 字段加 `= None` 默认（末位），使既有 4 处直接构造零改动（非计划内但更优） |
| T2 service 降级链 | `_resolve_user_timezone(user_md_tz=None)` + 移除 stale 缓存 + 3 处签名 | ✅ 完成 | 无偏离 |
| T3 USER.md 模板 | 加字段 + 字段清单 | ✅ 字段已加；**字段清单移至 config docstring**（USER.md budget 1800 仅余 97，详见下方"过程修正"）| 字段清单落点改变 |
| T4 测试 | config + service + 集成 | ✅ 完成（净增 ~21 用例）| 占位符守卫升级为读真实出厂模板（更强守卫）|
| T5 验证 + 文档 | 回归 + e2e + 漂移闸 + 报告 | ✅ 完成 | — |

## 文件改动清单

| 文件 | 净变更 | 说明 |
|------|--------|------|
| `daily_routine_config.py` | +83 | `_USER_TIMEZONE_PATTERN` + `extract_user_timezone_from_user_md`（跳过注释行 + zoneinfo 校验）+ `DailyRoutineConfig.user_timezone` 字段 + 模块 docstring 机器可读字段清单 |
| `daily_routine.py` | ~+60/-? | `_resolve_user_timezone(user_md_tz=None)` 降级链；移除 `self._user_timezone` stale 缓存；`_register_cron`/`_compute_yesterday_range_utc` 加 `user_timezone` 参数；startup / _run_daily_summary 读 config 后派生 |
| `behavior_templates/USER.md` | +4 净 | 新增 `user_timezone` 机器可读字段（占位符发布，降级 env/UTC）+ 1 行注释 |
| `test_f102_daily_routine_config.py` | +118 | TestExtractUserTimezone（12）+ TestConfigIncludesUserTimezone（3）+ AcD4 补 1 + 出厂模板守卫 |
| `test_f102_daily_routine_service.py` | +79 | TestUserTimezoneResolver 降级链（+4，原 4 保留）+ TestUserMdTimezoneAffectsYesterdayWindow（集成 1）|
| `docs/blueprint/{core-design,module-design,requirements,milestones}.md` | +13 | living-docs 漂移闸（字段 3→4 + 时区优先级 + F115 ✅）|

合计：9 文件，+328 / -29。零生产文件被删；纯增量 + 行为修复。

## 验证结果

- **focused 套件**：daily_routine config + service + budget 相关 87 passed（baseline 57 → +新增）；F115 专项 + 守卫全 PASS。
- **全量回归（PYTHONPATH 锁 worktree src，防 symlink venv 假 0）**：见下方"回归数据"。baseline = 3772 passed @ 543a93b。
- **e2e_smoke**：见下方。
- **判别式**：`import ... daily_routine` 解析到 `.claude/worktrees/F115-routine-tz/...`（已确认锁 worktree，非主仓 master src）。

### 回归数据

- baseline（clean 543a93b）：3772 passed, 0 failed
- post-fix #1：3791 passed, **1 failed**（`test_default_template_within_budget[USER.md-False]`——我初版 USER.md 注释超 1800 budget）→ 已修
- post-fix #2（修 budget 后）：见 verification-report.md

## 过程修正（自查发现并自修）

**USER.md budget 回归**：初版给 USER.md 加了详尽的"机器可读字段清单"5 行注释块 + 3 行 user_timezone 注释，净增 696 字符，超 `BEHAVIOR_FILE_BUDGETS["USER.md"]=1800`（baseline 1572，余量仅 228）→ `test_default_template_within_budget` FAIL。**修正**：USER.md 收敛到 1 注释 + 1 值行（净增控制在 ~130 字符，最终 1703 < 1800）；完整机器可读字段清单移至 `daily_routine_config.py` 模块 docstring（不占 USER.md 的 LLM 上下文 budget）。教训：USER.md 进每轮 LLM 上下文，注释也计入 budget，新增字段必须吝啬。

## AC ↔ Test 可追溯性（SDD 强化）

| AC | 绑定测试 | 状态 |
|----|----------|------|
| AC-1 USER.md 时区优先 env | `TestUserTimezoneResolver::test_user_md_overrides_env[_even_when_env_absent]` | ✅ |
| AC-2 env 兜底 | `TestUserTimezoneResolver::test_env_fallback_when_user_md_none` | ✅ |
| AC-3 均缺 → UTC | `TestUserTimezoneResolver::test_utc_when_both_absent` + 原 `test_default_fallback_to_utc` | ✅ |
| AC-4 非法时区 → None → 降级 | `TestExtractUserTimezone::test_invalid_timezone_returns_none` | ✅ |
| AC-5 extract 正确性 | `TestExtractUserTimezone`（标准/裸值/多斜杠/Etc+偏移/缺失/None/空/注释跳过/仅注释/占位符）| ✅ |
| AC-6 config 携带 | `TestConfigIncludesUserTimezone` | ✅ |
| AC-7 USER.md 时区影响昨日窗口 | `TestUserMdTimezoneAffectsYesterdayWindow::test_user_md_timezone_changes_yesterday_date` | ✅ |
| 守卫 出厂模板不硬编码时区 | `TestExtractUserTimezone::test_shipped_template_user_timezone_is_unset` | ✅ |

## living-docs 漂移闸结果

触碰模块 = DailyRoutine（config + service）+ USER.md 模板。Blueprint code↔doc 比对发现并**已同步修复** 4 处漂移（属 Blueprint 同步规则"数据模型字段增删必须同步"强制范围）：
- requirements.md §5.1.10：机器可读字段 3→4 + 时区优先级
- core-design.md §8.10.2：时区来源由"USER.md 时区字段 + env 兜底"（原描述不准，恰是 F115 修的 bug）修正为"USER.md user_timezone > env > UTC"；字段表 3→4
- module-design.md：字段 3→4
- milestones.md：F115 标 ✅

未改 `architecture-audit.md:471`（F102 的 `_user_timezone` 历史审计记录）——属历史快照不改写；F115 移除该字段的事实在本报告 + blueprint 已体现。

## 已知 limitations / 顺带发现（flag 给用户，未越界 inline 改）

1. **routine_active 注释行潜在 bug（F102 遗留，非 F115 引入）**：USER.md 模板的 `<!-- routine_active: "true"/"false" -->` 注释行会被 `extract_routine_active_from_user_md` 的正则命中并 premature 返回 "true"——若用户把 value 行改成 false，注释行的 "true" 仍先命中 → routine 关不掉。当前被"默认值 True == 模板值 True"掩盖。F115 给**自己**的 `extract_user_timezone` 加了跳过注释行的守卫（因时区示例是合法真值），但未顺手改 routine_active（scope 纪律 + 改它会变 F102 行为需独立验证）。**建议**：立独立 S 件给三个 F102 extract 统一加"跳过 `<!--` 注释行"守卫。
2. **cron 触发时刻改时区需重启**：`_register_cron` 在 startup 注册一次，USER.md 改时区后 cron **触发时刻**重启才生效（昨日窗口因每次重读 config 已即时生效）。与 daily_summary_time 同款 v0.1 限制，未扩大 scope 做"运行时重注册"。

## Codex Review

按 CLAUDE.local.md「不需要做的节点」：**单文件级 bug fix**（核心改动 2 生产文件，行为修复非架构变更）→ 跳过 Codex adversarial review。已用确定性手段打底：87 focused + 全量回归 0 regression + AC↔test 绑定 + 出厂模板守卫 + budget 守卫。

## 合入建议

**建议先 review 再合入**：改动小且回归干净，但翻转了时区优先级（env→USER.md 优先，向后兼容已论证：现有部署 USER.md 无该字段→降级 env→100% 等价）。请确认：① 优先级语义 USER.md > env > UTC 符合预期；② limitations #1（routine_active）是否同期立件。确认后我再 push。**不主动 push origin/master**。
