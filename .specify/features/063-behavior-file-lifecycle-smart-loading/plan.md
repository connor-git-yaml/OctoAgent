---
feature_id: "063"
title: "Behavior File Lifecycle & Smart Loading — 实施计划"
created: "2026-03-18"
updated: "2026-03-18"
---

# 实施计划

## 实施顺序

```
Phase 1 (Bootstrap 生命周期) → Phase 2 (差异化加载) → Phase 3 (Compactor)
```

Phase 1 和 Phase 2 的代码改动基本不重叠，但 Phase 2 的 BehaviorLoadProfile 需要 Phase 1 的 bootstrap 完成状态来决定是否包含 BOOTSTRAP.md，因此建议串行。Phase 3 可在 Phase 2 之后独立进行。

---

## Phase 1: Bootstrap 生命周期管理 [P0]

**预估改动量**：4 个文件修改，~150 行

**目标**：BOOTSTRAP.md 支持"已完成"状态，完成后不再注入 system prompt。

### Step 1.1: 定义 onboarding 状态模型

**文件**: `packages/core/src/octoagent/core/behavior_workspace.py`

- 新增 `OnboardingState` dataclass 或 Pydantic model：
  ```python
  class OnboardingState(BaseModel):
      bootstrap_seeded_at: datetime | None = None
      onboarding_completed_at: datetime | None = None
  ```
- 新增 `_onboarding_state_path(project_root)` → `{project_root}/behavior/.onboarding-state.json`
- 新增 `load_onboarding_state()` / `save_onboarding_state()` 读写函数
- `ensure_filesystem_skeleton()` 中创建 BOOTSTRAP.md 时，同步写入 `bootstrap_seeded_at`

### Step 1.2: resolve 时跳过已完成的 BOOTSTRAP.md

**文件**: `packages/core/src/octoagent/core/behavior_workspace.py`

- `_resolve_behavior_source()` 中（行 573-665），对 `BOOTSTRAP` file_id 增加前置检查：
  - 读取 `OnboardingState`
  - 如果 `onboarding_completed_at` 不为空 → 返回 `None`（跳过注入）
- 确保 overlay 优先级逻辑不受影响（仅在最终 resolve 阶段跳过）

### Step 1.3: Agent 标记 onboarding 完成

**文件**: `apps/gateway/src/octoagent/gateway/services/capability_pack.py`

- 扩展 `behavior.write_file` 工具：新增 `action=complete_bootstrap` 子命令
  - 或者：检测 Agent 写入 BOOTSTRAP.md 时内容包含 `<!-- COMPLETED -->` 标记
  - 写入 `onboarding_completed_at = now()`
  - 不物理删除文件（保留审计轨迹），仅标记状态

**文件**: `packages/core/src/octoagent/core/behavior_workspace.py`

- BOOTSTRAP.md 默认模板末尾追加指令：
  ```
  ## 完成引导
  当你完成上述所有引导步骤后，使用 behavior.write_file 将本文件内容替换为
  `<!-- COMPLETED -->` 来标记引导已完成。此后本文件不再注入你的上下文。
  ```

### Step 1.4: Legacy 兼容检测

**文件**: `packages/core/src/octoagent/core/behavior_workspace.py`

- `load_onboarding_state()` 增加 legacy 检测：
  - 如果 `.onboarding-state.json` 不存在，但 IDENTITY.md 内容已被用户修改（与默认模板不同）→ 视为 onboarding 已完成
  - 自动创建 state 文件并回填 `onboarding_completed_at`
- 保证已运行实例升级后行为正确

### Step 1.5: 测试

**文件**: `packages/core/tests/test_behavior_workspace.py`（新增测试用例）

- 测试 BOOTSTRAP.md 在 onboarding 未完成时正常注入
- 测试 onboarding 完成后 BOOTSTRAP.md 不注入
- 测试 Gateway 重启后状态持久化
- 测试 legacy 兼容检测（无 state 文件 + 已修改 IDENTITY.md → 自动标记完成）
- 测试 `complete_bootstrap` action 正确写入状态

---

## Phase 2: BehaviorLoadProfile 差异化加载 [P0]

**预估改动量**：5 个文件修改，~200 行

**目标**：Butler/Worker/Subagent 按角色加载不同子集的行为文件。

### Step 2.1: 定义 BehaviorLoadProfile

**文件**: `packages/core/src/octoagent/core/behavior_workspace.py`

- 新增枚举：
  ```python
  class BehaviorLoadProfile(str, Enum):
      FULL = "full"           # Butler：全部 9 个文件
      WORKER = "worker"       # Worker：AGENTS + TOOLS + IDENTITY + PROJECT + KNOWLEDGE
      MINIMAL = "minimal"     # Subagent：AGENTS + TOOLS + IDENTITY
  ```
- 新增常量：
  ```python
  _PROFILE_ALLOWLIST: dict[BehaviorLoadProfile, frozenset[str]] = {
      BehaviorLoadProfile.FULL: frozenset(ALL_BEHAVIOR_FILE_IDS),
      BehaviorLoadProfile.WORKER: frozenset({
          "AGENTS", "TOOLS", "IDENTITY", "PROJECT", "KNOWLEDGE",
      }),
      BehaviorLoadProfile.MINIMAL: frozenset({
          "AGENTS", "TOOLS", "IDENTITY",
      }),
  }
  ```

### Step 2.2: resolve_behavior_workspace() 接受 load_profile

**文件**: `packages/core/src/octoagent/core/behavior_workspace.py`

- `resolve_behavior_workspace()` 新增 `load_profile: BehaviorLoadProfile = BehaviorLoadProfile.FULL` 参数
- 在遍历 file_id 列表时，跳过不在 `_PROFILE_ALLOWLIST[load_profile]` 中的文件
- 向后兼容：不传参数时默认 FULL，现有调用方零改动

### Step 2.3: 消费方适配

**文件**: `apps/gateway/src/octoagent/gateway/services/butler_behavior.py`

- `resolve_behavior_pack()` 新增 `load_profile` 参数，透传给 `resolve_behavior_workspace()`
- `build_behavior_slice_envelope()` 改为使用 `BehaviorLoadProfile.WORKER` 而非当前的 ad-hoc 子集逻辑

**文件**: `apps/gateway/src/octoagent/gateway/services/agent_context.py`

- `_build_system_blocks()` 中根据 `AgentRuntime.role`（butler/worker）或 `AgentSessionKind` 决定使用哪个 load_profile
- Butler → FULL
- Worker → WORKER
- Subagent → MINIMAL

**文件**: `apps/gateway/src/octoagent/gateway/services/llm_service.py`

- 如果有直接调用 behavior 相关函数的地方，确保 load_profile 正确传递

### Step 2.4: 测试

**文件**: `packages/core/tests/test_behavior_workspace.py`（新增测试用例）

- 测试 FULL profile 返回全部 9 个文件
- 测试 WORKER profile 只返回 5 个文件（AGENTS/TOOLS/IDENTITY/PROJECT/KNOWLEDGE）
- 测试 MINIMAL profile 只返回 3 个文件（AGENTS/TOOLS/IDENTITY）
- 测试 WORKER profile 不含 USER/SOUL/HEARTBEAT/BOOTSTRAP
- 测试向后兼容（不传 load_profile 等同 FULL）
- 测试 BOOTSTRAP.md 在 FULL profile + onboarding 已完成时也被跳过（与 Phase 1 联动）

---

## Phase 3: Behavior Compactor [P1]

**预估改动量**：3 个新文件，~400 行

**目标**：行为文件总大小监控 + 手动压缩 + 压缩保护标记。

### Step 3.1: 总大小监控

**文件**: `packages/core/src/octoagent/core/behavior_workspace.py`

- 新增 `measure_behavior_total_size(project_root, agent_slug)` → 返回各文件大小和总量
- 新增阈值常量 `_BEHAVIOR_SIZE_WARNING_THRESHOLD = 15000`（字符）

**文件**: `apps/gateway/src/octoagent/gateway/services/butler_behavior.py`（或新文件）

- `resolve_behavior_pack()` 完成后检查总大小，超过阈值时：
  - structlog 记录 warning
  - 写入 Event Store（事件类型 `behavior.size_warning`）
  - 向 Agent 注入一条提示："行为文件总大小已超过 {threshold}，建议执行压缩"

### Step 3.2: 压缩保护标记

**文件**: `packages/core/src/octoagent/core/behavior_workspace.py`

- 新增 `extract_protected_sections(content: str) -> list[str]`
  - 解析 `<!-- 🔒 PROTECTED -->` ... `<!-- /🔒 PROTECTED -->` 之间的内容
- 新增 `merge_after_compaction(compacted: str, protected_sections: list[str]) -> str`
  - 压缩后将保护区段原样插回

### Step 3.3: Compactor 核心逻辑

**文件**: `apps/gateway/src/octoagent/gateway/services/behavior_compactor.py`（新文件）

- `compact_behavior_file(file_path, llm_client)`:
  1. 读取原文件内容
  2. 提取 protected sections
  3. 调用 LLM 压缩非保护区内容（system prompt 指导：保留关键规则、合并重复、删除过时条目、保持结构）
  4. 合并保护区段
  5. 如果压缩后更大 → 跳过（OpenClaw 的经验）
  6. 备份原文件到 `behavior/.compactor-backup/{date}/`
  7. 写入压缩结果

- `compact_all_behavior_files(project_root, agent_slug, llm_client)`:
  - 遍历所有行为文件，逐个压缩
  - 返回压缩报告（各文件压缩前后大小）

### Step 3.4: Compactor 工具暴露

**文件**: `apps/gateway/src/octoagent/gateway/services/capability_pack.py`

- 扩展 behavior 工具集，新增 `action=compact` 子命令
  - Agent 可主动触发压缩
  - 需要用户确认（review_required）
  - 返回压缩报告

### Step 3.5: CLI 支持

**文件**: `packages/provider/src/octoagent/provider/dx/behavior_commands.py`

- 新增 `octo behavior compact` 命令
  - 展示当前总大小
  - 交互确认后执行压缩
  - 输出压缩报告

### Step 3.6: 测试

**文件**: `apps/gateway/tests/test_behavior_compactor.py`（新文件）

- 测试 protected section 提取与还原
- 测试压缩后大小确实下降（mock LLM 返回更短内容）
- 测试压缩后变大时自动跳过
- 测试备份文件正确创建
- 测试阈值警告事件写入 Event Store
