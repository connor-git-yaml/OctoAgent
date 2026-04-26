# Feature 082 — Bootstrap & Profile Integrity

> 作者：Connor
> 日期：2026-04-26
> 上游：用户实测（Profile 显示 `preferred_address: 你` + USER.md 仍为占位符 + Bootstrap 从未真实引导）
> 模式：spec-driver-feature

## 1. 背景

OctoAgent 的"首次引导（Bootstrap）+ 用户画像（Profile）+ USER.md 注入"是一条完整的链路，**设计意图是**：
1. 系统首次启动 → 创建 BOOTSTRAP.md + BootstrapSession (PENDING)
2. 用户首次请求 → BOOTSTRAP.md 内容注入 system prompt → LLM 引导用户完成问卷
3. 用户回答 → BootstrapSession.answers 累积 → ProfileGeneratorService 生成画像
4. 完成时 → `mark_onboarding_completed()` + 画像回填 OwnerProfile + 重生成 USER.md
5. 后续会话 → 跳过 BOOTSTRAP.md，使用真实用户画像

**实测发现这条链路从未真正跑通**。

### 1.1 实测症状

用户问"你看我的 Profile 里面有没有我的居住地点？" 时 Agent 回复：

```
Profile 信息：
- preferred_address: 你
- working_style: N/A
- interaction_preferences: N/A
- boundary_notes: N/A
```

USER.md 仍是占位符模板（"待引导时填写—用户希望被称呼的名字或昵称"）。

### 1.2 实测残缺盘点

| 类别 | 文件/位置 | 问题 |
|------|-----------|------|
| 状态机自欺 | `~/.octoagent/.onboarding-state.json` | `bootstrap_seeded_at == onboarding_completed_at`（同一时间戳）→ 看起来是 `_detect_legacy_onboarding_completion()` 误标 |
| 硬编码默认值 | `packages/core/.../models/agent_context.py:188` | `preferred_address: str = Field(default="你")` |
| 硬编码默认值 | `packages/core/.../store/sqlite_init.py:348` | `preferred_address TEXT NOT NULL DEFAULT '你'` |
| 完成路径未接入 | `packages/core/.../behavior_workspace.py:224-231` | `mark_onboarding_completed()` 几乎无调用方 |
| 画像断层 | `gateway/services/inference/profile_generator_service.py:125-155` | 画像写 SoR(profile) 但**不回填 OwnerProfile 表** |
| 静态 USER.md | `packages/core/.../behavior_templates/USER.md` | 模板不被动态生成；占位符直接进 system prompt |
| 多份副本 | 3 处 `behavior/system/USER.md` | 不同 instance root 各自创建，没有 single-source |

### 1.3 根因分析

**根因 1（状态机）**：`is_completed()` 只看 `onboarding_completed_at` 是否非空，但 `_detect_legacy_onboarding_completion()` 在系统初始化时就回填了这个字段（与 `bootstrap_seeded_at` 同一时间戳），导致 bootstrap 永远跳过。

**根因 2（画像断层）**：ProfileGeneratorService 把画像写入 Memory SoR(partition=profile)，**但不更新 OwnerProfile 数据库表**——这是数据流断点。OwnerProfile 表里 `preferred_address` 永远是 SQLite 默认值 `"你"`。

**根因 3（USER.md 静态化）**：USER.md 是静态模板，无任何代码会"基于 OwnerProfile + 画像生成实际内容"。引导完成也不重生成 USER.md。

**根因 4（多 root 副作用）**：`OCTOAGENT_PROJECT_ROOT` 环境变量灵活性导致不同启动场景在不同位置初始化文件骨架，产生 3 份 USER.md。

## 2. 用户故事

- **US-1**（P0）：作为新用户，首次启动 Gateway 后第一次对话 Agent **应该真正引导我完成 bootstrap**（询问称呼/时区/工作偏好），而不是直接进入"已完成"状态
- **US-2**（P0）：作为已被误标完成的老用户，**应该有显式命令重置/迁移 bootstrap 状态**（如 `octo bootstrap reset` / `migrate`），让我重新走一遍引导
- **US-3**（P0）：完成 bootstrap 后，**OwnerProfile 字段必须反映我的真实回答**（preferred_address 不再是 "你"，working_style 不再是 ""）
- **US-4**（P1）：完成 bootstrap 后，**USER.md 必须含我的真实信息**（不再是 "待引导时填写..." 占位符）
- **US-5**（P1）：作为运维者，多 instance root 的混乱应该有**显式提示**（warn 哪个目录被使用），且系统**只在唯一规范位置**初始化文件骨架
- **US-6**（P1）：作为开发者，`preferred_address` 不应该有 `"你"` 这种伪默认值，留空（或 "Owner"）让 UI/Agent 自己处理"未填"语义

## 3. 功能需求（FR）

### FR-1：Bootstrap 状态机修复

- 移除 `_detect_legacy_onboarding_completion()` 中"未引导但写 `onboarding_completed_at`"的回填逻辑（或加严：仅当真实存在 legacy 完成证据时才回填）
- `is_completed()` 改为同时校验：
  - `onboarding_completed_at` 非空
  - **且** OwnerProfile 至少有一个非默认值字段（如 `preferred_address != "你" and != ""`）
  - **或** USER.md 内容已被填充（不再是 占位符模板）
- 保证默认行为：新用户首次启动 → `is_completed() == False` → BOOTSTRAP.md 注入 → 引导真正跑

### FR-2：Bootstrap 完成路径接入

- 提供 `BootstrapSession.complete()` 方法：
  - 标记 `onboarding_completed_at`
  - 触发 `ProfileGeneratorService.generate_profile()` 同步执行
  - 调用 FR-3 的回填路径
  - 调用 FR-4 的 USER.md 重生成
  - 持久化 BootstrapSession.status = COMPLETED
- 在 LLM 工具层提供 `bootstrap.complete()` 工具，让 Agent 在判定引导完成时显式调用
- 在 Web UI 设置页/Settings 提供"标记完成 Bootstrap"按钮（可选）

### FR-3：ProfileGenerator 回填 OwnerProfile

- `ProfileGeneratorService.generate_profile()` 完成后增加 `_sync_to_owner_profile()` 步骤：
  - 从画像中提取关键字段：preferred_address / working_style / interaction_preferences / boundary_notes / timezone / locale
  - 通过 `OwnerProfileStore.update()`（新增）写入 SQLite owner_profile 表
  - 字段冲突策略：用户显式回答 > LLM 推断 > 默认值；不覆盖用户显式设置过的字段
  - 增加 `last_synced_from_profile_at` 时间戳追踪同步源

### FR-4：USER.md 动态生成

- 引入 `UserMdRenderer` 服务：基于 OwnerProfile + 最新画像渲染 USER.md
- 使用现有模板 `packages/core/.../behavior_templates/USER.md.j2`（新增 .j2 后缀，老模板保留 fallback）
- 触发时机：
  - Bootstrap.complete 时
  - OwnerProfile 字段更新时（通过事件订阅）
  - CLI 命令 `octo bootstrap rebuild-user-md` 显式触发
- 写入唯一规范位置（FR-7 一起处理）

### FR-5：默认值清理

- `OwnerProfile.preferred_address` 默认值：`"你"` → `""`（空字符串表示未设置）
- `SQLite owner_profile.preferred_address` schema：`DEFAULT '你'` → `DEFAULT ''`
- `_ensure_owner_profile()` 创建默认 profile 时不再赋伪默认值
- Agent 渲染 system prompt 时检测 `preferred_address == ""`：使用 fallback 文案 "（用户未设置称呼，默认用'你'）" 而不是把 `"你"` 当成数据值

### FR-6：迁移与重置命令

- `octo bootstrap reset [--yes]`：
  - 清空 `~/.octoagent/.onboarding-state.json` 中的 `onboarding_completed_at`
  - 清空 `~/.octoagent/.../USER.md` 内容（恢复模板）
  - **不**清空 OwnerProfile 表（除非加 `--purge-profile`）
  - 让用户重新走一遍 bootstrap
- `octo bootstrap migrate-082`：
  - 检测：`onboarding_completed_at != null` 但 `preferred_address` 仍是 `"你"`
  - 这种情况说明用户被错误标完成 → 提示用户跑 `octo bootstrap reset` 或显式确认"我确实完成过引导"
  - 同时提供 dry-run 选项

### FR-7：多 instance root 收敛

- 规范化：以 `OCTOAGENT_PROJECT_ROOT` 为唯一 source；未设置时 fallback `~/.octoagent`
- 启动时若检测到多个并存 root（`~/.octoagent/`、`~/.octoagent/app/` 同时有 `behavior/system/USER.md`）：
  - log warning 列出所有副本路径
  - 提示用户跑 `octo cleanup duplicate-roots` 命令
- `octo cleanup duplicate-roots [--dry-run]`：
  - 检测多余的 USER.md / behavior 目录副本
  - 让用户选择保留哪个 root
  - 删除其他副本（备份为 `.bak.082-{ts}`）

### FR-8：测试 + 文档

- 单测：状态机 / 回填路径 / USER.md 渲染 / 迁移命令
- 集成测试：完整 bootstrap → profile 回填 → USER.md 生成 → 重启后 is_completed=True
- 文档：
  - `docs/codebase-architecture/bootstrap-profile-flow.md`（新增，描述完整数据流）
  - `CLAUDE.md` 更新（提到 Feature 082 修复了哪些设计漏洞）

## 4. 不变量

- **I-1**：现有 OwnerProfile 数据不丢失（除非用户显式 `--purge-profile`）
- **I-2**：现有 BootstrapSession schema 不变（不重构表）
- **I-3**：ProfileGeneratorService 的 SoR 写入路径不变（仅新增回填步骤）
- **I-4**：所有 EventType 枚举不变
- **I-5**：Skill / Tool / SkillRunner / AgentSession 接口不变
- **I-6**：CLI 顶层命令名（`octo run` / `octo config` 等）不变
- **I-7**：每个 Phase commit 后 `python -c "from octoagent.gateway.main import app"` 不挂、`octo --help` 正常、Feature 081 的 2078 条测试继续通过

## 5. Scope Lock

- ❌ 不重构 BootstrapSession 数据模型（改 schema 留给后续 Feature）
- ❌ 不重构 ProfileGeneratorService 内部 LLM 调用逻辑
- ❌ 不动 Memory SoR / Derived 数据流
- ❌ 不引入新的存储后端
- ❌ 不在本 Feature 内做前端 Bootstrap UI（仅暴露后端工具，前端引导跳到 follow-up Feature）
- ❌ 不动 OAuth / Provider / LLM 调用层

## 6. 风险

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| `is_completed()` 加严后老用户重复引导 | 中 | UX 抱怨 | FR-6 提供 `migrate-082` 命令明确告知 + dry-run；不主动 reset 老用户 |
| ProfileGenerator 错误推断回填脏数据 | 中 | OwnerProfile 字段被覆盖 | 字段冲突策略明确；保留 `last_synced_from_profile_at` 可回滚 |
| `_detect_legacy_onboarding_completion` 移除破坏真 legacy 用户 | 中 | 真完成的用户被强制重引导 | 加严判定（如检查 `preferred_address` 是否非默认）；保留兼容窗口 |
| USER.md 动态生成性能开销 | 低 | 启动慢 | 缓存渲染结果；仅在 OwnerProfile 变更时触发 |
| 多 root 收敛破坏多实例隔离 | 低 | 极少数高级用户 | 仅 warn，不强制；通过环境变量保留 override 能力 |
| `preferred_address` 默认 `""` 让 system prompt 缺少称呼 | 低 | 称呼变怪（"你好，！"） | Agent 层 fallback 文案 + system prompt 检测空值跳过 |
| 迁移命令把用户 OwnerProfile 误清空 | 高 | 数据丢失 | `--purge-profile` 必须显式开启；默认仅清状态机 |

## 7. 验收准则

### 功能（必须通过）

- [ ] **新用户**：首次启动 Gateway → 第一次对话被引导回答 bootstrap 问卷
- [ ] **新用户**：完成引导后 OwnerProfile.preferred_address ≠ "你" 且 ≠ ""
- [ ] **新用户**：完成引导后 USER.md 内容含真实信息（不再是占位符）
- [ ] **老用户**（被误标完成）：跑 `octo bootstrap reset` → 重新引导
- [ ] **老用户**（真完成过）：跑 `octo bootstrap migrate-082` → 检测到真完成则不动；检测到误标则提示
- [ ] `octo bootstrap rebuild-user-md` → USER.md 基于当前 OwnerProfile 重新渲染
- [ ] `octo cleanup duplicate-roots --dry-run` → 检测多 root 副本

### 架构（必须通过）

- [ ] `is_completed()` 检查 `onboarding_completed_at + OwnerProfile 非默认 + USER.md 已填充` 三者
- [ ] `BootstrapSession.complete()` 触发 ProfileGenerator + OwnerProfile 回填 + USER.md 重生成
- [ ] `ProfileGeneratorService.generate_profile()` 后调用 `_sync_to_owner_profile()`
- [ ] OwnerProfile.preferred_address 默认值 `""`
- [ ] SQLite owner_profile.preferred_address `DEFAULT ''`

### 兼容性（必须通过）

- [ ] Feature 081 的 2078 条测试继续通过
- [ ] 每个 Phase commit 后 import / CLI / lifespan 正常
- [ ] OwnerProfile schema 向前兼容（旧数据 `preferred_address: "你"` 仍可读，但视为 legacy fallback）

### 文档（应该通过）

- [ ] `docs/codebase-architecture/bootstrap-profile-flow.md` 新增
- [ ] `CLAUDE.md` 更新（Feature 082 修复列表）
- [ ] migration-inventory（如果需要）记录所有改动点
