# Bootstrap & Profile 数据流（Feature 082）

> 作者：Connor
> 引入版本：Feature 082（5 Phase，2026-04-27）
> 状态：✅ 完成

## 1. 历史问题

OctoAgent 的"首次引导（Bootstrap）+ 用户画像（Profile）+ USER.md 注入"链路从未真实跑通：

| # | 症状 | 根因 |
|---|------|------|
| 1 | `Profile` 输出永远显示 `preferred_address: 你` | 三处硬编码默认值 `"你"`（model + DDL + 创建代码）|
| 2 | USER.md 永远是占位符（"待引导时填写..."）| 静态模板，无动态生成 |
| 3 | Bootstrap 状态机被自动标记完成，引导从未跑过 | `_detect_legacy_onboarding_completion()` 把 `data/` 非空当成完成证据，Gateway 跑过一次后**永远**触发 |
| 4 | `~/.octoagent/` 下有 3 份 USER.md 副本 | `OCTOAGENT_PROJECT_ROOT` 灵活性副作用 |

## 2. 修复后架构

```
新用户首次启动 Gateway
        ↓
ensure_filesystem_skeleton  → 写 BOOTSTRAP.md
        ↓
_ensure_owner_profile       → preferred_address=""（P0 修复，非伪默认）
        ↓
_ensure_bootstrap_session   → status=PENDING（创建但等待引导）
        ↓
load_onboarding_state
  ├─ 文件不存在 → _detect_legacy_onboarding_completion()
  │  ├─ P1 加严：要求 (IDENTITY.md 改) AND (USER.md 已填充) 双证据
  │  └─ 默认场景两个证据都没有 → 不回填 onboarding_completed_at
  └─ 返回 OnboardingState(completed=False)
        ↓
BootstrapIntegrityChecker.check_substantive_completion()  ← P1 新增
  ├─ marker（onboarding_completed_at 非空）+
  ├─ owner_profile_filled（OwnerProfile 至少一个非默认字段）+
  └─ user_md_filled（USER.md 不含占位符）
        ↓ 三者综合判定
  is_substantively_completed = False
        ↓
resolve_behavior_workspace 注入 BOOTSTRAP.md → system prompt
        ↓
Agent 引导对话（提问 称呼/工作风格/时区...）
        ↓
Agent 调 bootstrap.complete(preferred_address=..., working_style=..., ...)  ← P2 新增工具
        ↓
BootstrapSessionOrchestrator.complete_bootstrap()  ← P2 新增编排器
  ├─ 1. 加载 BootstrapSession，验证 status=PENDING
  ├─ 2. _apply_field_conflict_strategy
  │     ├─ 用户在 sync 后改过 → 严格保留
  │     ├─ 当前是伪默认 → 覆盖
  │     └─ 当前是用户显式 → 保留
  ├─ 3. save_owner_profile（含 last_synced_from_profile_at = now）
  ├─ 4. UserMdRenderer.render_and_write()  ← P3 新增
  │     └─ 基于 OwnerProfile 渲染 USER.md（不再是占位符）
  ├─ 5. mark_onboarding_completed() → .onboarding-state.json
  └─ 6. BootstrapSession.status = COMPLETED + completed_at=now
        ↓
后续会话
        ↓
BootstrapIntegrityChecker.check_substantive_completion() = True
        ↓
BOOTSTRAP.md 跳过；USER.md 真实数据进 system prompt
```

## 3. 核心组件

### 3.1 OwnerProfile 字段（packages/core/.../models/agent_context.py）

| 字段 | 默认值 | 引导完成后 |
|------|--------|-----------|
| `display_name` | `"Owner"` | 用户全名（可选）|
| `preferred_address` | `""`（P0 之前是 `"你"`）| 用户选择的称呼（如 `"Connor"`）|
| `timezone` | `"UTC"` | 用户时区（如 `"Asia/Shanghai"`）|
| `locale` | `"zh-CN"` | 主要语言 |
| `working_style` | `""` | 工作风格描述 |
| `interaction_preferences` | `[]` | 沟通偏好列表 |
| `boundary_notes` | `[]` | 边界与禁忌列表 |
| `last_synced_from_profile_at` | `None`（P2 新增）| ProfileGenerator 上次同步时间，作字段冲突锚点 |

### 3.2 字段冲突策略（services/bootstrap_orchestrator.py）

```
优先级（高到低）：
1. 用户在 last_synced_from_profile_at 之后改过 updated_at
   → 严格保留所有字段（不被 LLM 推断覆盖）
2. 当前是"伪默认"（"" / "你" / "UTC" / "Owner" / []）
   → 覆盖为新值
3. 当前是用户显式值
   → 保留（除非属于伪默认）
```

### 3.3 BootstrapIntegrityChecker（services/bootstrap_integrity.py）

实质完成 = onboarding_completed_at 非空 **AND**
（owner_profile 至少一字段非默认 **OR** USER.md 不含占位符）

仅 marker 不算实质完成——这是 Feature 082 修复历史误标问题的核心。

### 3.4 UserMdRenderer（services/user_md_renderer.py）

基于 OwnerProfile 渲染 markdown：
- 用纯 Python 字符串拼接（不引入 jinja2 依赖）
- OwnerProfile 实质未填充时不写文件（避免覆盖用户手工 USER.md）
- 写入 `<project_root>/behavior/system/USER.md` 唯一规范位置

## 4. CLI 命令（Feature 082 P4 新增）

### 4.1 `octo bootstrap reset [--yes] [--purge-profile]`

重置 bootstrap 状态让用户重新走引导：
- 删除 `behavior/.onboarding-state.json`
- 删除 `behavior/system/USER.md`（让模板重新生成）
- 默认**不**清空 OwnerProfile（除非 `--purge-profile`）

### 4.2 `octo bootstrap migrate-082 [--dry-run]`

检测 Feature 082 之前的"data/ 非空误标完成"场景：
- 输出 IntegrityReport（marker / owner_profile / USER.md 三源）
- 命中误标 → 建议跑 `octo bootstrap reset`

### 4.3 `octo bootstrap rebuild-user-md`

基于当前 OwnerProfile 重新渲染 USER.md（不动状态机）。

### 4.4 `octo cleanup duplicate-roots [--dry-run] [--keep <path>]`

检测多 instance root 副本（`~/.octoagent/` / `~/.octoagent/app/` / `~/.octoagent/app/octoagent/`），让用户选择保留哪个；其他副本备份为 `.bak.082-{ts}`。

## 5. LLM 工具（Feature 082 P2 新增）

### `bootstrap.complete(preferred_address?, working_style?, ...)`

side_effect_level=`REVERSIBLE`，让 Agent 在引导对话完成时显式调用。

参数：
- `preferred_address` / `working_style` / `timezone` / `locale` / `display_name`：字符串
- `interaction_preferences` / `boundary_notes`：列表
- `bootstrap_id`：可选（默认从当前 project context 推断）

宪法原则 #9：**字段抽取由 Agent（LLM）负责**，工具只接收结构化字段并落盘。

## 6. SQLite Schema 变更（Feature 082 P0）

```sql
-- DDL（新建库）
CREATE TABLE owner_profiles (
    ...
    preferred_address TEXT NOT NULL DEFAULT '',  -- 改自 '你'
    ...
    last_synced_from_profile_at TEXT,  -- 新增
    ...
);

-- ALTER（升级老库；_migrate_legacy_tables 自动跑）
ALTER TABLE owner_profiles
  ADD COLUMN last_synced_from_profile_at TEXT;
```

历史 `preferred_address='你'` 数据**不**在启动时静默清洗——避免误改用户真实输入；P4 `octo bootstrap migrate-082` 提供显式迁移路径。

## 7. 老用户兼容矩阵

| 用户状态 | 升级 Feature 082 后行为 | 推荐操作 |
|---------|------------------------|----------|
| 全新用户 | Bootstrap 真实跑通；OwnerProfile 字段被填充；USER.md 含真实数据 | 走引导即可 |
| 误标完成（preferred_address='你' + USER.md 占位）| `is_substantively_completed=False` → BOOTSTRAP.md 仍注入 → 重新引导 | `octo bootstrap migrate-082` 看诊断 |
| 真完成（OwnerProfile 有真实数据）| `is_substantively_completed=True` → 正常 | 无 |
| `preferred_address='你'` 是真用户输入 | schema migration 不影响已有数据；保留 | 用户手动改 |
| 多 root 并存 | warn + 不阻断启动 | `octo cleanup duplicate-roots` |

## 8. 性能 + 安全

- USER.md 渲染：纯 Python 字符串拼接，无外部依赖；< 1ms
- 字段冲突策略：纯函数，O(n) where n = profile 字段数
- 多 root 检测：启动时 1 次，仅文件存在性检查；< 1ms
- BootstrapIntegrityChecker：每次 `resolve_behavior_workspace` 调用 1 次；含 1 次 USER.md 读取

## 9. 相关 Feature 文档

- `.specify/features/082-bootstrap-profile-integrity/spec.md`
- `.specify/features/082-bootstrap-profile-integrity/plan.md`
- `.specify/features/082-bootstrap-profile-integrity/migration-inventory.md`
