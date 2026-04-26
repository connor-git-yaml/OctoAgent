# Feature 082 — Bootstrap & Profile Integrity · 实施计划

> 作者：Connor
> 日期：2026-04-26
> 上游：spec.md
> 下游：tasks.md
> 模式：spec-driver-feature

---

## 0. 总览

```
当前（Feature 081 完成后）：

新用户首次启动:
  ensure_filesystem_skeleton  → 写 BOOTSTRAP.md + bootstrap_seeded_at
        ↓
  _ensure_owner_profile      → preferred_address="你"（伪默认）
        ↓
  _ensure_bootstrap_session  → status=PENDING（创建但无完成路径）
        ↓
  ❌ _detect_legacy_onboarding_completion 误回填 onboarding_completed_at
        ↓
  is_completed()=True → BOOTSTRAP.md 跳过加载 → 引导从未真正跑
        ↓
  Profile 永远是 {preferred_address:"你", working_style:"", ...}
  USER.md 永远是占位符模板

修复后（Feature 082）：

新用户首次启动:
  ensure_filesystem_skeleton  → 写 BOOTSTRAP.md
        ↓
  _ensure_owner_profile       → preferred_address=""（空，非伪数据）
        ↓
  _ensure_bootstrap_session   → status=PENDING
        ↓
  is_completed() 严格检查（onboarding_completed_at 非空 + Profile 非空 + USER.md 已填充）
        ↓ False
  Bootstrap 真实跑通：
    Agent 引导问卷 → BootstrapSession.complete() →
      ProfileGenerator → _sync_to_owner_profile → UserMdRenderer.render() →
      mark_onboarding_completed()
        ↓
  is_completed()=True → 后续会话使用真实 OwnerProfile + USER.md
```

**核心方法**：5 Phase 渐进修复 + 老用户迁移命令兜底。每个 Phase 独立 commit + 可回滚。

---

## 1. Phase 划分

| Phase | 内容 | 估时 | 风险 |
|-------|------|------|------|
| **P0 依赖盘点 + 数据层 schema 改动** | OwnerProfile 默认值清理（`""` 替代 `"你"`） + SQLite schema 调整（含 migration） + inventory | 0.5 天 | 低 |
| **P1 Bootstrap 状态机修复** | `is_completed()` 加严 + `_detect_legacy_onboarding_completion()` 收紧 + `mark_onboarding_completed()` 路径接入 | 1 天 | 中（影响新老用户判定） |
| **P2 Bootstrap 完成路径 + Profile 回填** | `BootstrapSession.complete()` 工具 + `ProfileGeneratorService._sync_to_owner_profile()` + 字段冲突策略 | 1 天 | 中（数据写入） |
| **P3 USER.md 动态生成 + Agent system prompt 适配** | `UserMdRenderer` 服务 + `.j2` 模板 + 触发时机 + Agent system prompt 处理空值 | 1 天 | 中（system prompt 改变） |
| **P4 迁移/重置命令 + 多 root 收敛 + 文档** | `octo bootstrap reset` / `migrate-082` / `rebuild-user-md` / `octo cleanup duplicate-roots` + 文档 + 全量验收 | 1 天 | 低 |

**总计 ~4.5 天 / 5 个独立 commit**

**核心约束**：
- 每个 Phase 完成后 `python -c "from octoagent.gateway.main import app"` 必须成功
- 每个 Phase 完成后 `octo --help` / `octo config --help` 必须正常
- Feature 081 的 2078 条测试必须仍然通过

---

## 2. Phase 0 — 依赖盘点 + Schema 改动

### 2.1 全量盘点（产出 `migration-inventory.md`）

类别 A：Python import / 调用引用（待 P1-P3 修改）：

| 文件 | 引用 | 处理 |
|------|------|------|
| `packages/core/.../models/agent_context.py:188` | `preferred_address: Field(default="你")` | P0 改成 `default=""` |
| `packages/core/.../store/sqlite_init.py:348` | `preferred_address ... DEFAULT '你'` | P0 schema migration（v3）|
| `packages/core/.../behavior_workspace.py:147` | `is_completed()` 简单检查 | P1 加严 |
| `packages/core/.../behavior_workspace.py:224-231` | `mark_onboarding_completed()` | P1/P2 接入完成路径 |
| `packages/core/.../behavior_workspace.py:274` | `_detect_legacy_onboarding_completion()` | P1 收紧（仅真 legacy） |
| `packages/core/.../behavior_workspace.py:571-577` | `bootstrap_seeded_at` 写入 | 不动 |
| `apps/gateway/.../services/startup_bootstrap.py:86-100` | `_ensure_owner_profile()` | P0 跟随默认值改动 |
| `apps/gateway/.../services/startup_bootstrap.py:192-282` | `_ensure_bootstrap_session()` | 不动（已是 PENDING）|
| `apps/gateway/.../services/inference/profile_generator_service.py:125-155` | `generate_profile()` | P2 加 `_sync_to_owner_profile()` |
| `apps/gateway/.../services/agent_context.py` | `_build_system_blocks` | P3 处理空值 |
| `packages/core/.../behavior_templates/USER.md` | 静态模板 | P3 改成 `.j2` 模板 |
| `provider/dx/config_commands.py` | CLI 入口 | P4 加 bootstrap 子命令 |
| `apps/gateway/.../main.py` | lifespan 启动逻辑 | P4 加多 root warn |

类别 B：测试（待 P1-P4 改写/新增）：

- 状态机测试（新增 ~6 条）
- Profile 回填测试（新增 ~5 条）
- USER.md 渲染测试（新增 ~4 条）
- CLI 命令测试（新增 ~6 条）
- 影响的现有测试（约 5-10 条改写）

### 2.2 SQLite schema migration

```sql
-- migration v3 (Feature 082 P0)
-- 修改 owner_profile 表：preferred_address 默认值 '你' → ''
-- SQLite 不支持 ALTER COLUMN DEFAULT，需要重建表

PRAGMA foreign_keys=OFF;

CREATE TABLE owner_profile_new (
    owner_profile_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL DEFAULT 'Owner',
    preferred_address TEXT NOT NULL DEFAULT '',  -- ← 改动
    timezone TEXT NOT NULL DEFAULT 'UTC',
    locale TEXT NOT NULL DEFAULT 'zh-CN',
    working_style TEXT NOT NULL DEFAULT '',
    interaction_preferences TEXT NOT NULL DEFAULT '[]',
    boundary_notes TEXT NOT NULL DEFAULT '[]',
    last_synced_from_profile_at TEXT,  -- ← P2 新增
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

INSERT INTO owner_profile_new SELECT
    owner_profile_id,
    display_name,
    -- 老数据 preferred_address='你' 视为伪默认 → 清空；其他保留
    CASE WHEN preferred_address = '你' THEN '' ELSE preferred_address END,
    timezone, locale, working_style,
    interaction_preferences, boundary_notes,
    NULL,  -- last_synced_from_profile_at
    created_at, updated_at
FROM owner_profile;

DROP TABLE owner_profile;
ALTER TABLE owner_profile_new RENAME TO owner_profile;

PRAGMA foreign_keys=ON;
```

### 2.3 P0 commit

- 新增 `.specify/features/082-bootstrap-profile-integrity/migration-inventory.md`
- `models/agent_context.py`：`Field(default="")` 替换
- `store/sqlite_init.py`：DDL `DEFAULT ''` + migration v3
- `startup_bootstrap.py:_ensure_owner_profile`：移除显式赋值"你"（如果有）
- 不改其他运行时行为

`feat(core): Feature 082 P0 — Profile 默认值清理 + SQLite schema migration`

---

## 3. Phase 1 — Bootstrap 状态机修复

### 3.1 `is_completed()` 加严

```python
# behavior_workspace.py
def is_completed(self) -> bool:
    """Feature 082：严格检查——onboarding_completed_at 非空 仅 是必要条件，
    还要确认 OwnerProfile 至少有一个非默认值 + USER.md 已填充。
    """
    if self.onboarding_completed_at is None:
        return False
    # 加严检查：要求实质完成（非状态机自欺）
    return self._has_substantive_completion()

def _has_substantive_completion(self) -> bool:
    """检查 OwnerProfile 实质字段 + USER.md 是否已被填充。"""
    # 通过 OwnerProfile 表 + USER.md 文件双重校验
    # 注：本方法在 behavior_workspace 层，需要传入 owner_profile + user_md_content
    # 重构成 dataclass 字段或拆出来到 service 层
    ...
```

由于 `OnboardingState` 是纯数据 model，`is_completed()` 不能直接访问 OwnerProfile 表。**重构方案**：

- `OnboardingState.is_completed()` 保留简单语义（仅检查 `onboarding_completed_at`）
- 新增 `BootstrapIntegrityChecker` 服务（在 gateway 层）：
  - `check_substantive_completion(project_root) → bool`
  - 读 OwnerProfile + USER.md + onboarding_state，三者综合判定
- `resolve_behavior_workspace()` 在加载前调用 checker，决定是否注入 BOOTSTRAP.md

### 3.2 `_detect_legacy_onboarding_completion()` 收紧

```python
def _detect_legacy_onboarding_completion(...) -> bool:
    """Feature 082：仅当真实存在 legacy 完成证据时才回填。

    历史行为：只要 .onboarding-state.json 不存在就视为 legacy 完成 → 太宽泛
    新行为：要求**额外证据**：
      1. OwnerProfile.preferred_address 不为默认值（不为 "" 也不为 "你"）
      2. **或** USER.md 内容已被填充（非占位符模板）
      3. **或** 其他可靠的"用户曾完成引导"信号
    """
    if not legacy_marker_exists:
        return False
    # 加严：要求实质证据
    if owner_profile_has_real_data() or user_md_is_filled():
        return True
    log.warning(
        "legacy_onboarding_marker_without_substantive_evidence",
        recommendation="run `octo bootstrap reset` to re-onboard",
    )
    return False
```

### 3.3 `mark_onboarding_completed()` 路径接入

- 暴露为 BootstrapSession 完成时的标准调用点
- P2 会接入实际触发逻辑

### 3.4 测试

- `test_bootstrap_integrity_checker.py`（~6 条）：
  - 新用户（默认 profile）→ `check_substantive_completion()=False`
  - 老用户（真完成）→ True
  - 误标完成（profile 默认 + USER.md 占位）→ False + warning
  - legacy detection 收紧后不再误回填
  - mark_onboarding_completed 后 is_completed=True

### 3.5 commit

`feat(core): Feature 082 P1 — Bootstrap 状态机加严 + legacy detection 收紧`

---

## 4. Phase 2 — Bootstrap 完成路径 + Profile 回填

### 4.1 `BootstrapSession.complete()`

```python
# 新增 BootstrapSessionOrchestrator（gateway 层）
class BootstrapSessionOrchestrator:
    async def complete_bootstrap(self, session_id: str) -> CompleteResult:
        """Bootstrap 完成的统一入口。
        1. 触发 ProfileGenerator 重新生成画像
        2. 通过 _sync_to_owner_profile 回填 OwnerProfile
        3. 通过 UserMdRenderer 重生成 USER.md（P3 实现）
        4. 标记 onboarding_completed_at
        5. 持久化 BootstrapSession.status = COMPLETED
        """
```

### 4.2 LLM 工具：`bootstrap.complete()`

- 新增内置工具，让 Agent 在判定引导完成时显式调用
- side_effect_level=`REVERSIBLE`
- 工具描述告诉 LLM 何时调用：
  - 用户已回答"称呼"问题
  - 用户已回答"工作偏好"问题
  - 用户表达了"引导可以结束"意愿（可选）

### 4.3 `ProfileGeneratorService._sync_to_owner_profile()`

```python
async def _sync_to_owner_profile(self, profile: GeneratedProfile) -> None:
    """从画像回填 OwnerProfile 表。

    字段冲突策略：
    1. 用户显式设置（last_user_set_at > last_synced_from_profile_at）→ 不覆盖
    2. 否则 → 用画像值覆盖
    3. 更新 last_synced_from_profile_at
    """
    current = await self._owner_profile_store.get(profile.owner_profile_id)
    updates = {}

    if not current.preferred_address:  # 用户未显式设置
        if inferred := profile.basic_info.get("preferred_address"):
            updates["preferred_address"] = inferred

    if not current.working_style:
        if inferred := profile.work_style.get("description"):
            updates["working_style"] = inferred

    # ... 其他字段类似

    updates["last_synced_from_profile_at"] = datetime.now(UTC)
    await self._owner_profile_store.update(profile.owner_profile_id, **updates)
```

### 4.4 测试

- `test_bootstrap_orchestrator.py`（~5 条）：
  - complete_bootstrap 完整链路
  - ProfileGenerator 失败时的回滚
  - 用户已显式设置字段不被覆盖
  - 同步后 OwnerProfile 字段更新
  - last_synced_from_profile_at 时间戳正确

### 4.5 commit

`feat(gateway): Feature 082 P2 — Bootstrap 完成路径 + ProfileGenerator 回填 OwnerProfile`

---

## 5. Phase 3 — USER.md 动态生成 + Agent system prompt 适配

### 5.1 `UserMdRenderer` 服务

```python
class UserMdRenderer:
    def __init__(self, project_root: Path):
        self._template = self._load_template()

    def render(self, owner_profile: OwnerProfile, profile_data: dict | None = None) -> str:
        """基于 OwnerProfile + 画像渲染 USER.md。"""
        ctx = {
            "preferred_address": owner_profile.preferred_address or "Owner",
            "timezone": owner_profile.timezone,
            "locale": owner_profile.locale,
            "working_style": owner_profile.working_style or "（未设置）",
            "interaction_preferences": owner_profile.interaction_preferences,
            "boundary_notes": owner_profile.boundary_notes,
            "profile_data": profile_data or {},
        }
        return self._template.render(**ctx)

    def write(self, content: str, instance_root: Path) -> Path:
        """写入唯一规范位置（避免多 root 副本）。"""
        target = instance_root / "behavior" / "system" / "USER.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target
```

### 5.2 `.j2` 模板

```jinja2
# USER.md（由 octo bootstrap 自动生成）

## 基本信息
- **称呼**：{{ preferred_address or "Owner" }}
- **时区**：{{ timezone }}
- **语言**：{{ locale }}

## 沟通偏好
{% if interaction_preferences %}
{% for pref in interaction_preferences %}
- {{ pref }}
{% endfor %}
{% else %}
（未设置——可在引导中补充）
{% endif %}

## 工作风格
{{ working_style or "（未设置）" }}

## 边界注释
{% if boundary_notes %}
{% for note in boundary_notes %}
- {{ note }}
{% endfor %}
{% else %}
（无）
{% endif %}

---
*更新时间：{{ updated_at }}*
*同步来源：OwnerProfile {{ owner_profile_id[:8] }}*
```

### 5.3 触发时机

- **BootstrapSessionOrchestrator.complete_bootstrap()** 完成时（P2 已挖钩子）
- **OwnerProfileStore.update()** 写入时（事件订阅；可选 Phase）
- **CLI**：`octo bootstrap rebuild-user-md`（P4 提供）

### 5.4 Agent system prompt 适配

- `_build_system_blocks` 检测 USER.md 内容是否含占位符（`待引导时填写`）
- 如果含占位符 → 使用 fallback 文案（例如不注入 USER.md 块，避免污染 LLM）
- 这样新用户首次对话不会被占位符干扰

### 5.5 测试

- `test_user_md_renderer.py`（~4 条）：
  - 默认 profile 渲染（preferred_address="" → "Owner"）
  - 含画像数据的渲染
  - 列表字段为空 → 显示"未设置"
  - 写入路径正确（唯一规范位置）

### 5.6 commit

`feat(gateway+core): Feature 082 P3 — USER.md 动态生成 + Agent system prompt 空值处理`

---

## 6. Phase 4 — 迁移/重置命令 + 多 root 收敛 + 文档

### 6.1 CLI 命令

```python
# packages/provider/.../dx/config_commands.py

@cli.group()
def bootstrap_group() -> None:
    """Bootstrap 状态管理"""

@bootstrap_group.command("reset")
@click.option("--yes", is_flag=True)
@click.option("--purge-profile", is_flag=True)
def bootstrap_reset(yes: bool, purge_profile: bool) -> None:
    """重置 bootstrap 状态，让用户重新走引导。"""

@bootstrap_group.command("migrate-082")
@click.option("--dry-run", is_flag=True)
def bootstrap_migrate_082(dry_run: bool) -> None:
    """检测 Feature 082 之前的 legacy 误标完成；提示用户处理。"""

@bootstrap_group.command("rebuild-user-md")
def bootstrap_rebuild_user_md() -> None:
    """基于当前 OwnerProfile 重新生成 USER.md。"""

@cli.group()
def cleanup_group() -> None:
    """清理工具"""

@cleanup_group.command("duplicate-roots")
@click.option("--dry-run", is_flag=True)
def cleanup_duplicate_roots(dry_run: bool) -> None:
    """检测多个 instance root 副本，让用户选择保留哪个。"""
```

### 6.2 多 root warn（main.py lifespan）

```python
# main.py 启动时
def _warn_duplicate_roots(project_root: Path) -> None:
    """检测多个 instance root；仅 warn 不阻断启动。"""
    candidates = [
        Path.home() / ".octoagent" / "behavior" / "system" / "USER.md",
        Path.home() / ".octoagent" / "app" / "behavior" / "system" / "USER.md",
        Path.home() / ".octoagent" / "app" / "octoagent" / "behavior" / "system" / "USER.md",
    ]
    existing = [p for p in candidates if p.exists()]
    if len(existing) > 1:
        log.warning(
            "multiple_instance_roots_detected",
            count=len(existing),
            paths=[str(p) for p in existing],
            recommendation="run `octo cleanup duplicate-roots` to consolidate",
        )
```

### 6.3 文档

- 新增 `docs/codebase-architecture/bootstrap-profile-flow.md`：
  - 完整数据流图
  - Bootstrap session 生命周期
  - Profile 字段映射规则
  - USER.md 模板与渲染机制
  - 多 root 处理策略
- 更新 `CLAUDE.md`：
  - Feature 082 修复列表
  - 新增 CLI 命令说明
  - 设计原则（Profile 层级：用户显式 > LLM 推断 > 默认值）

### 6.4 全量验收

```bash
# 1. 静态导入
python -c "from octoagent.gateway.main import app; print('OK')"

# 2. CLI
octo --help
octo bootstrap --help
octo bootstrap reset --help
octo bootstrap migrate-082 --dry-run
octo cleanup duplicate-roots --dry-run

# 3. 测试
pytest tests/

# 4. 真实 Gateway 启动 + 新用户 / 老用户场景
# (a) 新用户：rm ~/.octoagent → octo run → 第一次对话被引导
# (b) 老用户：保留 .onboarding-state.json → octo run → 警告 multiple roots
# (c) 老用户：octo bootstrap migrate-082 → 检测出误标 → 提示 reset
# (d) octo bootstrap reset → 状态机清空，下次启动重新引导

# 5. 验收 grep
grep -r 'preferred_address.*"你"' octoagent/ --include='*.py' | grep -v test_ | wc -l
# 应返回 0（除了 backward-compat 处理）
```

### 6.5 commit

`chore(cleanup): Feature 082 P4 — Bootstrap CLI 命令 + 多 root 收敛 + 文档同步 + 全量验收`

---

## 7. 兼容性矩阵

| 用户状态 | Feature 082 启动行为 | 推荐操作 |
|---------|---------------------|---------|
| 全新用户（无 .onboarding-state.json）| 正常引导流程跑通 | 无 |
| 老用户（已被误标完成 + profile 仍是默认）| `is_completed()=False` → 重新引导 OR warn 让用户决定 | `octo bootstrap migrate-082` 检测 + reset |
| 老用户（真完成过 + profile 有真实数据）| `is_completed()=True` → 正常使用 | 无 |
| 老用户（preferred_address="你" 是真用户输入）| migration 不会清空（schema 改 default 不影响已有数据） | 用户手动改成空或留 |
| 多 root 并存 | warn + 不阻断启动 | `octo cleanup duplicate-roots` |

## 8. 风险缓解

- **状态机变更影响范围广**：每个 Phase 加 fallback；P1 收紧 legacy detection 时保留 warn 不直接拒绝
- **OwnerProfile 数据回填风险**：字段冲突策略（用户显式 > LLM 推断）+ `last_synced_from_profile_at` 可追溯回滚
- **USER.md 渲染失败**：默认 fallback 到静态模板（保证 system prompt 不空）
- **多 root warn 噪音**：仅 warn 不阻断；用户可通过环境变量关闭
- **CLI 命令误操作**：`reset` / `migrate-082` 必须 `--yes` 或交互确认

## 9. Scope Lock（不改的东西）

- BootstrapSession 数据模型（schema 不变，仅新增 `complete_bootstrap` 编排器）
- ProfileGeneratorService 内部 LLM 调用逻辑
- Memory SoR / Derived 数据流
- Skill / Tool / SkillRunner / AgentSession 接口
- CLI 顶层命令名（`octo run` / `octo config` 等）
- ProviderRouter / Provider 直连层（Feature 080+081）
- OAuth / TokenRefreshCoordinator

## 10. 全量验收 checklist（Phase 4 完成后）

### 功能
- [ ] 新用户：首次启动 → 第一次对话被 Agent 引导回答 bootstrap 问卷
- [ ] 引导完成后 OwnerProfile 字段反映真实回答（preferred_address ≠ ""，working_style ≠ ""）
- [ ] 引导完成后 USER.md 含真实信息（不再是占位符）
- [ ] 老用户：`octo bootstrap migrate-082` 检测到误标完成
- [ ] 老用户：`octo bootstrap reset` → 重新引导
- [ ] `octo cleanup duplicate-roots --dry-run` → 列出多余副本

### 架构
- [ ] `is_completed()` 严格三重校验
- [ ] `BootstrapSessionOrchestrator.complete_bootstrap()` 触发完整链路
- [ ] `ProfileGeneratorService._sync_to_owner_profile()` 实现
- [ ] `UserMdRenderer` 服务存在并可调用
- [ ] OwnerProfile.preferred_address 默认 ""

### 兼容性
- [ ] Feature 081 的 2078 条测试继续通过
- [ ] 每个 Phase commit 后 import / CLI / lifespan 正常
- [ ] OwnerProfile schema 向前兼容（旧数据可读）

### 文档
- [ ] `docs/codebase-architecture/bootstrap-profile-flow.md` 新增
- [ ] `CLAUDE.md` 更新

---

## 11. 总结

**预计净增 ~1500 行**（新增 ~2000 + 删除/简化 ~500）。

**收益**：
- Bootstrap 真实跑通：新用户首次对话即被引导（修复多年的设计漏洞）
- OwnerProfile 字段反映真实数据，不再是 `"你"` 占位
- USER.md 动态生成，system prompt 注入真实用户信息
- Agent 真正"认识"用户（提升对话质量）
- 老用户有清晰迁移路径（不强制破坏）
- 多 root 副本有诊断 + 收敛机制

**风险**：可控，所有改动渐进式 + 老用户有 migrate 命令 + Schema 向前兼容兜底。
