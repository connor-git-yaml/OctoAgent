# F115 问题修复报告 — daily_routine 时区接入 USER.md SoT

> 模式：spec-driver-fix（快速问题修复）｜规模：S｜baseline：origin/master @ 543a93b（= d2936e0 + docs-only commit，测试等价）

## 问题描述

用户在 `USER.md`（号称唯一 SoT）里改"时区"，对 `DailyRoutineService` 的两件事**零影响**：
1. cron 触发时刻（每日摘要在几点推送）
2. "昨日窗口"计算（哪段 UTC 区间算作"昨天"）

真正生效的是部署侧环境变量 `OCTOAGENT_USER_TIMEZONE`。这是直接的用户可感知 UX 缺陷——USER.md 自称 SoT 却对时区不起作用。来源：2026-06-07 M6 调研+审计 workflow（CLAUDE.local.md「M6 地基 sprint」F115）。

## 5-Why 根因追溯

| 层级 | 问题 | 发现 |
|------|------|------|
| Why 1 | USER.md 改时区为何不生效？ | cron 注册与昨日窗口都读 `self._user_timezone`，不读 USER.md |
| Why 2 | `self._user_timezone` 为何不含 USER.md？ | 它在 `__init__`（daily_routine.py:125）一次性由 `_resolve_user_timezone()` 算出，该方法只读 `os.environ["OCTOAGENT_USER_TIMEZONE"]` |
| Why 3 | `_resolve_user_timezone` 为何只读 env？ | F102 实现时 USER.md 没有机器可读 timezone 字段（只有人类可读"时区/地点"，daily_routine.py:123-124 注释自陈），env 是当时唯一可用的机器输入 |
| Why 4 | USER.md 为何缺机器可读 timezone 字段？ | F102 只补了 daily_summary_time / routine_active / summary_channels 三字段（F102 handoff 洞察：spec 隐性假设 USER.md 有机器可读时区，实际没有） |
| Why 5 | 为何没被现有测试捕获？ | `TestUserTimezoneResolver`（test_f102_daily_routine_service.py:633）只测了 env→UTC 降级，从未断言"USER.md 时区生效"——测试盲区与代码缺口同源 |

**Root Cause**：`DailyRoutineService._user_timezone` 是 `__init__` 阶段从 env 一次性缓存的值，时区解析链里**根本没有 USER.md 这一环**；同时 USER.md 模板缺少机器可读 timezone 字段，解析层（`DailyRoutineConfig`）也未解析它。

**Root Cause Chain**：USER.md 改时区无效 → 消费点读 `self._user_timezone` → 该字段 `__init__` 时 env-only 缓存 → `_resolve_user_timezone` 不读 USER.md → USER.md 无机器可读字段 + config 不解析 → 测试只覆盖 env 路径未发现。

## 影响范围扫描

### 同源问题（需同步修复）

| 文件 | 位置 | 模式 | 修复动作 |
|------|------|------|----------|
| daily_routine.py | L125 `__init__` | env-only 缓存 `self._user_timezone` | 移除缓存字段，改为每次从 config 派生 |
| daily_routine.py | L127-141 `_resolve_user_timezone` | 仅读 env | 改降级链：USER.md → env → UTC（加可选参数 `user_md_tz`） |
| daily_routine.py | L583-597 `_register_cron` | cron 时区读 `self._user_timezone` | 改用 `_resolve_user_timezone(config.user_timezone)` |
| daily_routine.py | L524-552 `_compute_yesterday_range_utc` | 昨日窗口读 `self._user_timezone` | 加 `user_timezone` 参数，由 caller 传入 |
| daily_routine.py | L159-171 / L204-222 | startup / _run_daily_summary 调度 | 读 config 后派生 tz 传入消费点 |
| daily_routine_config.py | `DailyRoutineConfig` / `from_user_md` | 解析 3 字段缺 timezone | 加 `user_timezone` 字段 + `extract_user_timezone_from_user_md` |
| behavior_templates/USER.md | L35-44 Daily Routine 区 | 缺机器可读 timezone | 增 `user_timezone` 字段 + 机器可读字段清单注释 |

### 类似模式（已评估，无需改）

| 文件 | 位置 | 评估结果 |
|------|------|----------|
| agent_context.py | L269-273 `ZoneInfo(timezone)` fallback | `[安全]` 独立的 prompt 时间渲染路径，时区来源不同（非 routine 范畴），不在本 Feature 范围 |
| USER.md L10 `时区/地点` | 人类可读字段 | `[安全]` 保留，供 LLM 对话引用；本 Feature 新增的是**并存的机器可读字段**，不替换人类可读字段 |
| BOOTSTRAP.md L12 时区引导 | 引导对话脚本 | `[安全]` 人类可读，与机器解析无关 |

### 同步更新清单

- 调用方：`_register_cron` / `_compute_yesterday_range_utc` 的唯一 caller 均在 daily_routine.py 内部，已覆盖
- 测试：现有 4 个 `_resolve_user_timezone()` 无参用例**保持兼容**（无参=env→UTC）；新增 USER.md 优先 / env 兜底 / 缺失回退用例 + config 解析用例 + service 级集成用例
- 文档：USER.md 模板机器可读字段清单；daily_routine_config.py 模块 docstring；blueprint 漂移检查（completion gate）

## 修复策略

### 方案 A（推荐）— config 聚合 + 移除 stale 实例缓存

时区与现有 3 字段同级纳入 `DailyRoutineConfig`，消除 `self._user_timezone` 这个"`__init__` env-only 缓存"坏味道（它正是 F102 自修 bug 的同一字段），改为每次读 config 时从 USER.md 派生：

1. `daily_routine_config.py`：新增 `extract_user_timezone_from_user_md(content) -> str | None`（正则解析 + zoneinfo 校验，缺失/非法返回 None）；`DailyRoutineConfig` 加 `user_timezone: str | None`；`from_user_md` 接入。
2. `daily_routine.py`：`_resolve_user_timezone(user_md_tz: str | None = None)` 实现降级链 **USER.md → env → UTC**（无参=原 env→UTC，向后兼容现有 4 测试）；移除 `self._user_timezone` 字段；`_register_cron` / `_compute_yesterday_range_utc` 改由 caller 传入派生时区；startup 与 _run_daily_summary 读 config 后派生。
3. `USER.md` 模板：Daily Routine 区增 `user_timezone` 机器可读字段 + 字段清单注释。

**优先级语义**：USER.md `user_timezone`（有效）> env `OCTOAGENT_USER_TIMEZONE`（有效）> `UTC`。

**向后兼容性**：现有部署的 USER.md 都没有 `user_timezone` 字段（新字段）→ 解析为 None → 降级到 env → 行为与现状 100% 等价。只有用户主动在 USER.md 写 `user_timezone` 才覆盖 env——这正是"USER.md is SoT"的预期。

**正确性增益**：`_compute_yesterday_range_utc` 每次触发从重读的 config（L212）派生时区 → USER.md 改时区，**下次 cron 触发的昨日窗口立即生效**（无需重启）。cron 触发时刻在 startup 注册时从 config 派生（改时区需重启生效，与 daily_summary_time 同款 v0.1 限制，可接受）。

### 方案 B（备选）— 仅在 _read_config 内刷新 self._user_timezone

保留 `self._user_timezone` 字段，在 `_read_config()` 内 `self._user_timezone = _resolve_user_timezone(config.user_timezone)` 刷新。改动更小但保留可变缓存字段（state 坏味道）+ `_read_config` 带副作用。**不推荐**：与项目"架构整洁优先、不把最小改动当默认目标"冲突。

## Spec 影响

- 无独立 spec.md（F102 spec 覆盖 routine 主体）。本 Feature 为 fix，在 F115 feature 目录产出 fix-report / plan / tasks / verification / completion-report。
- 需检查 blueprint 是否描述时区解析（completion gate living-docs 漂移闸处理）。

## 范围检测

受影响文件 3 个生产文件（daily_routine.py / daily_routine_config.py / USER.md）+ 1 测试文件，1 个模块。**适合 fix 模式**（< 10 文件 / < 3 模块）。
