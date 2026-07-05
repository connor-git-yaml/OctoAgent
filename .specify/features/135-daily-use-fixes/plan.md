# F135 修复规划

> 基于 spec.md + root-cause-gap1.md。gap-1 先做（已 root-cause，结论=可直接修的 bug，非产品决策）。
> gap-1 与 gap-2 文件不重叠，可独立提交。

## Phase 顺序

`Phase A（gap-1 修复+测试）→ Phase B（gap-2 修复+测试）→ Phase F（回归）→ Phase G（双评审）→ Phase H（completion + living-docs）`

gap-1 先做因它已 root-cause 且是产品级；gap-2 独立文件次之。每 Phase 独立 commit。

---

## Phase A — Gap-1：behavior.write_file 提进 Core

### A1 — 改 CoreToolSet.default()
`packages/tooling/src/octoagent/tooling/models.py`（`CoreToolSet.default()` :396-410）
- `tool_names` 列表追加 `"behavior.write_file"`，附注释说明理由（首次引导闭环高频 + 语义关键，
  与 graph_pipeline/delegate_task 同款"不进 Core→deferred 两跳→脆弱"先例；治理在执行层不受影响）。

### A2 — 测试（AC-1.1~1.4）
新建 `apps/gateway/tests/test_f135_behavior_tool_exposure.py`：
- `test_behavior_write_file_is_core`（AC-1.1）：纯单元，`CoreToolSet.default().is_core(...)` True。
- `test_main_web_agent_mounts_behavior_write_file`（AC-1.2）：**真实 OctoHarness**（复用
  e2e_live `octo_harness_e2e` 范式或最小 harness bootstrap），为 system-default 主 Agent 调
  `resolve_profile_first_tools`，断言 `behavior.write_file ∈ selected_tools` 且 ∉ deferred。
- `test_behavior_write_review_required_still_two_phase`（AC-1.3）：调 handler，confirmed=False →
  status=skipped + proposal=True；confirmed=True → status=written。
- `test_behavior_write_records_version`（AC-1.4）：confirmed=True 写 USER.md → 查 F107 版本记录存在。

> 注：AC-1.2 若在 test 层起完整 harness 太重，退化为直接构造 CapabilityPackService（同
> repro_gap1.py 已验证路径）+ 断言 mounted，仍是真实选择引擎非 mock。

### A 回归风险
| 风险 | 缓解 |
|------|------|
| Core 多一个工具改变所有 agent mounted schema（prefix cache/token） | behavior 写是个人 AI OS 引导核心价值；仅 +1 工具；与 graph_pipeline/delegate_task 提 Core 同量级 |
| 提 Core 误绕过治理 | handler 自身 REVIEW_REQUIRED Two-Phase（misc_tools.py:258）独立于 Core/deferred，AC-1.3 硬验证 |
| CoreToolSet 变更影响 F072/F087 既有 deferred 分流测试 | 全量回归 + 检查 tool_search/deferred 相关测试（behavior.write_file 从 deferred 清单移除是预期，若有测试硬断言它在 deferred 需同步） |

---

## Phase B — Gap-2：launchd PATH 注入 node/npx

### B1 — 改 build_service_path_value
`packages/provider/src/octoagent/provider/dx/service_manager.py`（:323-353）
- 在拼接 `parts` 时，新增 node/npx 常见**稳定**位置（经 `validate_stable_paths` + 非 .venv 守卫）：
  - `~/.volta/bin`（volta，用户实际位置；`$HOME` 下稳定）
  - （`/opt/homebrew/bin` / `/usr/local/bin` / `~/.local/bin` 已在列表，不重复）
- 复用现有去重（`if candidate not in parts`）。node 目录同样是易变字段——**不**改
  `definitions_equivalent` 的 PATH 剔除逻辑（FR-2.3，幂等保持）。
- 实现方式：抽一个 `_node_runtime_path_candidates()` helper 返回稳定 node 目录列表，逐个过
  `validate_stable_paths` 后 append（与 uv 目录同款守卫），保持函数确定性 + 可测。

### B2 — 测试（AC-2.1~2.4）
`packages/provider/tests/test_service_manager.py`（既有 `TestServicePathValue` 类或新增）：
- `test_path_value_includes_node_locations`（AC-2.1）：monkeypatch `Path.home()` 到含
  `.volta/bin` 的 tmp，断言返回值含该目录。
- `test_path_value_node_locations_are_stable`（AC-2.2）：对返回的每段跑 `validate_stable_paths`，
  全部无违规。
- `test_launchd_plist_path_contains_node`（AC-2.3）：渲染 launchd plist，解析
  `EnvironmentVariables.PATH` 含 node 位置（hermetic，`command_runner` stub，不真装）。
- AC-2.4：复核既有 `test_path_only_difference_does_not_trigger_refresh` 不回归。

### B 回归风险
| 风险 | 缓解 |
|------|------|
| 注入不稳定 node 路径违 stable-working-dir 红线 | 只加 `$HOME` 下 + 系统稳定目录，全过 `validate_stable_paths`；AC-2.2 硬验证 |
| PATH 变化触发反复重装 | node 目录同 uv 目录是幂等剔除字段，不改 `definitions_equivalent`；AC-2.4 护栏 |
| `~/.volta/bin` 不存在的机器（无 volta 用户）| 无条件 append 稳定候选（存在与否不影响 PATH 合法性，缺失目录在 PATH 里无害）；或仅 append 存在的目录——择"无条件 append 稳定字面路径"（确定性 + 幂等，与现有 ~/.local/bin 无条件 append 一致） |

---

## Phase F — 联合回归

- PYTHONPATH 锁本 worktree（9 个 src）+ `uv run --no-sync python -m pytest`。
- 跑范围：gap-1 域（tooling + gateway capability/tool 相关）+ gap-2 域（provider service_manager）
  + 新增 F135 测试；最后全量 vs master b46ecc2b baseline 对账 0 regression。
- e2e_smoke（`pytest -m e2e_smoke`）过。

## Phase G — 双评审（重大架构变更节点：工具暴露策略 + 系统集成）

- **Opus 对抗自审**（主节点）：①gap-1 提 Core 没绕过 behavior 写治理（proposal→confirm 链完整）；
  ②gap-2 PATH 注入没引入 stable-working-dir 风险（全过 validate_stable_paths）；③测试 hermetic
  （不真装 launchd / 不打真 LLM）；④0 regression。
- **codex review**：`cd $WT && codex review --base master 2>&1 | tail -90`（CLI 同步；失败一次跳过注明）。
- 处理到 0 HIGH 残留。

## Phase H — completion + living-docs 漂移闸

- `completion-report.md`：改动清单 + AC 对齐 + 双评审 finding 闭环 + 用户上手验证步骤。
- living-docs：
  - `docs/codebase-architecture/harness-and-context.md`：工具暴露章补"CoreToolSet 是发现层，
    behavior.write_file 已提 Core；entrypoint 过滤为历史死代码"。
  - `docs/blueprint/deployment-and-ops.md` F129 章：补"服务 PATH 注入 node/npx 稳定位置"。

## 不主动 push —— 等用户拍板。
