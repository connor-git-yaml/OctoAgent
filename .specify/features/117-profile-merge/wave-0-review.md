# F117 Wave 0 — 评审记录（review log）

> Wave 0 = 加性地基：AgentProfile 吸收 9 个 worker 字段 + store save/hydrate + sqlite_init schema。
> 评审：Opus 对抗评审（agent ac5074e）。Codex+Opus 双评审面板留给高风险 Wave 1-2（Wave 0 纯加性、低风险，右尺寸）。
> 回归：baseline 4135 passed → Wave 0（含 resource_limits）4135 passed → 撤 resource_limits 后复验中。

## 评审结论：结构健全，2 MEDIUM（均已处理），0 HIGH

### 验证通过（评审实测确认）
- **save_agent_profile 列/参数对齐**：精确（撤 resource_limits 后 26 列 / 26 占位 / 26 参数）。
- **防御性 hydrate**：`row.keys()` 对 aiosqlite.Row 有效（row_factory 在 store/__init__.py:106 设）；enum parse `try/except ValueError` 对未知/空值安全回退，匹配模型默认。
- **DDL/ALTER/model/save/hydrate 一致性**：9 列名/类型/默认值四处完全一致；`WorkerProfileStatus.ACTIVE.value=='active'` 等无 enum-vs-string 漂移。
- **fresh-install ordering**：`init_db` CREATE（含 9 列）→ `_migrate_legacy_tables`（resource_limits ALTER + 9 ALTER 跳过已存在）安全幂等，实测 27 列齐全。
- **无 test 断言 model_dump 全键集** → +9 字段不破测试；30 store 测试通过。
- **无运行时行为变更**：逐一 trace 9 字段 + resource_limits 的消费方，确认无任何现有运行时路径从 AgentProfile 读这些字段（worker_service/session_service 的 `.status/.summary` 等都读的是 **WorkerProfile** 对象，本波未动）。

### MEDIUM-1：resource_limits 由死列变活持久化 —— **已撤回（disposition: REVERT）**
- 原 Wave 0 顺手把 resource_limits 加进 save/hydrate。评审确认：运行时 limits 不受影响（来自 dispatch envelope metadata，非 reloaded AgentProfile），但**管理面 `agent_profile.update_resource_limits` 动作由"持久化丢失"变成"真持久化"**——一处管理面行为变更。
- **决策：撤回**。理由：resource_limits 是 F117 范围外的既有死列（agent+worker 两侧 store 对称地都不持久化），与 WorkerProfile 合并无关；按"严格执行要求范围 / 不加未请求优化（避免意外副作用）"硬规则，不在合并波夹带无关行为变更（即使是 bug fix）。已撤 save+hydrate 两处，回到 baseline（恒 {}）。模型字段 + DDL + ALTER 均为既有，未动。
- **deferred 跟进**：resource_limits 死列 latent bug（`update_resource_limits` 持久化丢失）记入 handoff，作 F117 落地后独立 fix 候选（worker 侧随 F117 删除消失，焦点收敛到 agent_profiles 单侧）。

### MEDIUM-2：镜像 builder 未填充 9 字段 —— **转为 Wave 1 强制 gate（disposition: DEFER→W1）**
- `worker_profile_ops.py:130 _build_agent_profile_from_worker_profile` + `agent_context_entity_ensure.py:971 _ensure_agent_profile_from_worker_profile` 构造 agent_profiles(kind=worker) 镜像行时，9 个新字段留模型默认（status=ACTIVE 即使 worker 是 DRAFT/ARCHIVED、tool 列空）。Wave 0 无运行时消费方读它们 → **非当前回归**，但 Wave 4 切读路径时会读到默认值 → 静默数据丢失。
- **决策：Wave 1 强制 gate**。这正是 Wave 1 的 **populate-then-switch 不变量**：先让两个镜像 builder 复制全部 9 字段进统一行（+ 已写镜像行 backfill），**再**切运行时读路径（capability_pack/chat）。验收：切读路径前，镜像行的 tool 字段必须与 worker_profiles 源一致。

### LOW / 跨文件 note（记录，不阻塞）
- **混合 idiom**：新代码用 `"col" in row.keys()`，相邻既有 kind hydrate 用 `try/except (KeyError,IndexError)`。均对 aiosqlite.Row 有效，纯风格；不改既有 kind idiom（避免无关 churn）。
- **migration_117 列序与 save_agent_profile 不同**：`migration_117:441` INSERT 列序（version 在 resource_limits 前）与 save 不同。二者均用具名列（位置 VALUES）→ 均不 buggy，但维护隐患。**Wave 4 迁移定稿时对齐列序**。

## 状态
- 2 MEDIUM 处理：M1 撤回 / M2 转 W1 gate；0 HIGH 残留。
- 撤 resource_limits 后全量复验：`/tmp/f117_wave0_final.log`（目标 4135 = baseline）。
- 复验绿 → commit Wave 0。
