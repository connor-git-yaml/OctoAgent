# F135 日常使用就绪修复 — Spec

> 类型：fix Feature。修用户 Connor 真实部署首日暴露的 2 个系统性 gap（gap-3 用户已手动解，仅补机制评估）。
> 上游背景：M6 收官 + F129 launchd 常驻服务已部署，用户开始日常用。
> 根因调查（gap-1）：`root-cause-gap1.md`（经验复现，非纯推理）。

## 问题陈述

- **Gap-1（🔴 产品级）**：Web UI 让主 Agent"帮我完成 USER.md 初始化"，Agent 回复
  "工具入口没暴露给我"。核心引导闭环（首次见面填画像）在生产走不通。
- **Gap-2（🟠 F129 引入）**：launchd 干净环境 PATH 无 node/npx，所有 `npx` 型 MCP
  （openrouter-perplexity 搜索等）启动失败 `[Errno 2] No such file or directory`。
- **Gap-3（🟡 已由用户手动解）**：USER.md 机器字段 + 时区用户已手写生效。F135 不改内容，
  仅评估 gap-1 修好后引导写 USER.md 的体验是否顺（用测试覆盖治理链）。

---

## Gap-1：behavior.write_file 发现层缺失（Core vs Deferred）

### 根因（一句话）
`behavior.write_file` 不在 `CoreToolSet.default()` → 对主 Agent 是 **Deferred** 工具
（只在 system prompt 文本里列名，无完整 schema）→ 必须先 `tool_search` 两跳激活才能调用 →
弱 model/单轮场景不可靠 → Agent 直接说"入口没暴露"。**entrypoint 过滤是死代码，非根因**
（详见 root-cause-gap1.md 三层定位）。

### FR（功能需求）
- **FR-1.1**：主 Agent（web 来源）的一次聊天中，`behavior.write_file` 必须以**完整 schema
  直接可调用**（mounted，非 deferred），无需先走 tool_search。
- **FR-1.2**：修复**不得绕过** behavior 写治理。`behavior.write_file` 写 REVIEW_REQUIRED
  文件（如 USER.md）时仍必须走 Two-Phase：首次调用（confirmed=false）返回 proposal
  （status=skipped），用户确认后 confirmed=true 再落盘。
- **FR-1.3**：修复对所有 surface（web / telegram / agent_runtime）一致——Core 工具清单是
  全局发现层，不因 surface 而异；治理执行层（review_mode）保持不变。
- **FR-1.4**：`behavior.write_file` 落盘后仍产 F107 行为版本记录（BEHAVIOR_VERSION_RECORDED）
  ——即引导写 USER.md 可追溯 + 可恢复。

### AC（验收标准）
- **AC-1.1 [@test]** `apps/gateway/tests/test_f135_behavior_tool_exposure.py::test_behavior_write_file_is_core`
  —— `CoreToolSet.default().is_core("behavior.write_file")` 为 True。
- **AC-1.2 [@test]** `apps/gateway/tests/test_f135_behavior_tool_exposure.py::test_main_web_agent_mounts_behavior_write_file`
  —— 用真实 OctoHarness 为主 Agent（system-default profile）调用
  `resolve_profile_first_tools`，`behavior.write_file` 出现在 `selected_tools`（mounted），
  **不**在 `deferred_tool_entries`。
- **AC-1.3 [@test]** `apps/gateway/tests/test_f135_behavior_tool_exposure.py::test_behavior_write_review_required_still_two_phase`
  —— 直接调 `behavior.write_file`(file_id="USER.md", confirmed=False) 返回
  status="skipped" + proposal=True（治理未被绕过）；confirmed=True 才写盘。
- **AC-1.4 [@test]** 同文件 `::test_behavior_write_records_version` —— confirmed=True 写 USER.md
  后能查到行为版本记录（F107）。

### 边界（Do NOT）
- ❌ 不修/不删 entrypoint 死代码（超范围；living-docs 记为已知冗余即可）。
- ❌ 不把工具裸暴露绕过审批（提 Core 只改可见性，治理执行层不动）。
- ❌ 不扩展到 user_profile.* 或其他 behavior 相关工具（gap-1 明确范围是 behavior.write_file）。
- ❌ 不改 AgentProfile.default_tool_groups 解析 / 不改 profile 层（改动面更大且非根因）。

---

## Gap-2：launchd 服务 PATH 缺 node/npx

### 根因（一句话）
`build_service_path_value`（`service_manager.py:323`）刻意只拼 uv 目录 + Homebrew + 系统路径，
`del environ`（:334）丢弃真实 shell PATH；**不含 node/npx 常见位置**（用户 node 在
`~/.volta/bin`）。launchd 干净环境下 `npx`（bare 命令）解析不到 → MCP 子进程启动失败。

### FR
- **FR-2.1**：launchd/systemd 服务定义生成的 PATH 必须包含常见 node/npx 位置，使
  `npx`/`node` 型 MCP 子进程在常驻服务下能被解析。
- **FR-2.2**：新增的 PATH 段必须满足 **stable-working-dir 红线**——只加 `$HOME` 下稳定目录
  （如 `~/.volta/bin`）+ 系统级稳定目录，**绝不**引入 worktree / `.venv` / 易删路径
  （与现有 uv 目录同款 `validate_stable_paths` 守卫一致）。
- **FR-2.3**：PATH 仍是幂等比对的**易变字段**（现有行为）——node 目录变化不触发服务重装。

### AC
- **AC-2.1 [@test]** `packages/provider/tests/test_service_manager.py::TestServicePathValue::test_path_value_includes_node_locations`
  —— `build_service_path_value()` 返回含 `~/.volta/bin`（存在时）+ Homebrew node 常见位置。
- **AC-2.2 [@test]** 同类 `::test_path_value_node_locations_are_stable` —— 注入的 node 路径均
  通过 `validate_stable_paths`（无 worktree/.venv 标记）。
- **AC-2.3 [@test]** `::test_launchd_plist_path_contains_node`（hermetic，不真装 launchd）——
  渲染的 plist `EnvironmentVariables.PATH` 含 node 位置。
- **AC-2.4** 幂等：node 目录变化仍被 `definitions_equivalent` 剔除（现有
  `test_path_only_difference_does_not_trigger_refresh` 不回归）。

### 边界（Do NOT）
- ❌ 不真装 launchd / 不写用户 `~/Library/LaunchAgents`（测试 hermetic）。
- ❌ 不复制完整 shell PATH（易变 → 幂等误判反复重装，且可能含 worktree 路径违红线）。
- ❌ 不改 MCP 子进程启动逻辑去解析 npx 绝对路径（选注入 PATH 更通用——所有依赖用户 PATH
  的 MCP/子进程都受益）。

---

## Gap-3：引导写 USER.md 体验（仅补机制）

用户已手写 USER.md（含 `user_timezone: "Asia/Shanghai"`）。F135 不改 USER.md 内容。
gap-1 修好后，引导写 USER.md 的完整链（提议→治理确认→落盘+版本记录）由 AC-1.3 + AC-1.4
覆盖，无需独立 FR。

---

## 全局约束

- PYTHONPATH 锁本 worktree 全部 packages src + `uv run --no-sync python -m pytest`，**禁 uv sync**。
- 0 regression vs master b46ecc2b。
- Constitution #4/#7/#10（工具治理收敛）+ H1（主 Agent 唯一 user-facing）。
- 命中"重大架构变更"（工具暴露策略 + 系统集成）→ Phase G 双评审（Opus 对抗 + codex review），0 HIGH。
- 产 completion-report + living-docs 漂移闸（harness-and-context 工具暴露章 / deployment-and-ops F129 PATH 章）。
