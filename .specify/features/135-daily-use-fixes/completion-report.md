# F135 日常使用就绪修复 — Completion Report

> fix Feature。修用户 Connor 真实部署首日暴露的 2 个系统性 gap（gap-3 已由用户手动解，仅补机制验证）。
> base master b46ecc2b。**未 push origin，等用户拍板。**

## 1. Gap-1 根因结论（是 bug 还是设计？哪层？证据）

**结论：真 bug，不是"设计如此"，无需产品决策。** 但根因**不在 entrypoint 过滤**（那层是死代码），
在**工具发现层分层（Core vs Deferred）**。经真实 OctoHarness 复现确认（`scratchpad/repro_gap1.py`，
非纯推理），详见 `root-cause-gap1.md`。

三层定位：
1. **假线索层（entrypoint 死代码）**：`behavior.write_file` 声明 `entrypoints={"agent_runtime"}`
   （`misc_tools.py:46`），但 `resolve_for_entrypoint`（`toolset_resolver.py:130`）+ `toolsets.yaml`
   **在生产链路零调用者**——entrypoint 根本不参与"给 LLM 的工具集"过滤。假设"web 入口过滤掉
   agent_runtime-only 工具"是错的。
2. **真选择引擎层**：主 Agent（web）走 `orchestrator.py:1028` → `resolve_profile_first_tools`
   （`capability_pack.py:524-762`），过滤维度=tool_group ∈ default_tool_groups / tool_profile /
   availability，**不看 entrypoint**。`behavior.write_file`（tool_group=`behavior`）在 general
   `default_tool_groups`（`_UNIFIED_TOOL_GROUPS` 含 `behavior`，`capability_pack.py:919`）内 →
   通过全部过滤、进入工具宇宙。
3. **决定性层（Core vs Deferred）**：`CoreToolSet.default()`（`models.py:396-410`）**不含**
   `behavior.write_file` → 落进 deferred 桶（`capability_pack.py:655-663`）→ 只把 {name, 一行描述}
   注入 system prompt（`llm_service.py:392-403`），**无完整 schema** → LLM 须先 `tool_search`
   激活（两跳）→ 弱 model/单轮场景不可靠 → Agent 回复"入口没暴露给我"，首次见面填 USER.md 引导
   闭环在生产走不通。

**修法**：把 `behavior.write_file` 加入 `CoreToolSet.default()`——与 `graph_pipeline` / `delegate_task`
**同款先例**（commit `720d045d` "graph_pipeline 注册为 CORE——治本 F087 timeout 链路"，二者进 Core
的原因就是"不进 Core→deferred→两跳→来不及/不可靠"）。非新权限模型。

**治理不绕过**（Constitution #4/#7/#10 守住）：`behavior_write_file` handler 自身强制 Two-Phase
（`misc_tools.py:258`：`review_mode==REVIEW_REQUIRED and not confirmed → proposal`），USER.md
review_mode=REVIEW_REQUIRED（`template.py:50-58`）。提 Core 只改**发现层**（可直接调），
**执行层**（review_mode Two-Phase）不动。AC-1.3 硬验证。

## 2. Gap-2 PATH 注入方案 + plist 变化

**根因**：`build_service_path_value`（`service_manager.py:323`）刻意只拼 uv 目录 + Homebrew + 系统路径，
`del environ`（:334）丢弃真实 shell PATH，**不含 node/npx 位置**（用户 node 在 `~/.volta/bin` →
volta-shim → homebrew）。launchd 干净环境 `npx`（bare 命令）解析不到 → 所有 npx 型 MCP
（openrouter-perplexity 搜索等）`[Errno 2] No such file` 启动失败（前台跑有 shell PATH 掩盖）。

**方案**：选**注入 PATH**（非解析 npx 绝对路径）——更通用，所有依赖用户 PATH 的 MCP/子进程都受益。
新增 `_node_runtime_path_candidates()` 返回**稳定** node 目录（`~/.volta/bin` + homebrew），逐个过
`validate_stable_paths` 守卫后注入。

**plist 变化**（AC-2.3 hermetic 验证）：`EnvironmentVariables.PATH` 从
`<uv>:~/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:...`
变为 `<uv>:~/.volta/bin:/opt/homebrew/bin:/usr/local/bin:~/.local/bin:/usr/bin:...`
（实测：`/opt/homebrew/bin:/Users/connorlu/.volta/bin:/usr/local/bin:/Users/connorlu/.local/bin:...`）。

**stable-working-dir 红线**（FR-2.2）：只列 `$HOME` 下 + 系统稳定目录字面值（不列 nvm 版本化子目录，
随版本变+which 解析属易变，违红线）；PATH 仍是幂等比对**剔除的易变字段**（node/uv 位置变化不触发
服务重装，FR-2.3，AC-2.4 护栏不回归）。

## 3. 各 gap 改动 + commit

| commit | 内容 |
|--------|------|
| `12bde0b7` | docs：spec + plan + gap-1 根因结论（经验复现） |
| `01cd53eb` | **gap-1 fix**：behavior.write_file 提进 CoreToolSet + test_f135（4 test）+ 2 处 Core 集大小校准测试修复 |
| `0ab9067c` | **gap-2 fix**：build_service_path_value 注入 node/npx 稳定位置 + 3 个 hermetic test |
| `8ff542db` | docs：living-docs 漂移闸（工具暴露真实门 + 服务 PATH 注入） |

**改动文件（净增减）**：
- 生产：`packages/tooling/.../models.py`（+7 注释+1 工具）；`packages/provider/.../service_manager.py`（+37/-4）。
- 测试：`apps/gateway/tests/test_f135_behavior_tool_exposure.py`（新建 +219）；
  `packages/provider/tests/test_service_manager.py`（+59）；`apps/gateway/tests/test_deferred_tools_e2e.py`（+7 校准）；
  `packages/tooling/tests/test_models.py`（+7/-3 校准）。
- 文档：`root-cause-gap1.md` / `spec.md` / `plan.md` / `completion-report.md`；`harness-and-context.md` / `deployment-and-ops.md`。

## 4. AC ↔ test 绑定（[@test] 显式校验）

| AC | test | 状态 |
|----|------|------|
| AC-1.1 | `test_f135_behavior_tool_exposure.py::test_behavior_write_file_is_core` | PASS |
| AC-1.2 | `::test_main_web_agent_mounts_behavior_write_file`（真实选择引擎） | PASS |
| AC-1.3 | `::test_behavior_write_review_required_still_two_phase`（治理未绕过） | PASS |
| AC-1.4 | `::test_behavior_write_confirmed_records_version`（F107 版本记录） | PASS |
| AC-2.1 | `test_service_manager.py::...::test_path_value_includes_node_locations` | PASS |
| AC-2.2 | `::test_path_value_node_locations_are_stable` | PASS |
| AC-2.3 | `::test_launchd_plist_path_contains_node`（hermetic） | PASS |
| AC-2.4 | 既有 `::test_path_only_difference_does_not_trigger_refresh` 不回归 | PASS |

## 5. 回归 + 双评审 finding 闭环

**回归（PYTHONPATH 锁 worktree + `uv run --no-sync python -m pytest`，禁 uv sync）**：
- tooling + provider：**1087 passed / 0 failed** / 1 skipped。
- gateway + core（排 e2e）：**2852 passed / 1 failed** / 2 skipped。
- **唯一 failure = pre-existing 环境依赖假失败，非 F135 回归**：
  `test_plugin_watcher.py::test_start_degrades_without_watchdog` 断言"watchdog 未装→start()=False"，
  但 worktree 共享 `.venv`（symlink 主仓）**已装 watchdog** → start()=True。该测试文件 + plugin_watcher.py
  **与 master 字节相同**（`git diff master` 空），失败纯由 watchdog 存在与否决定 → master 同环境同样失败。
  该测试缺 `skipif(watchdog installed)` 守卫，属 F106 测试 hygiene，**已 spawn follow-up task**（不进 F135 范围）。
- e2e_smoke：每次 commit pre-commit hook **8/8 PASS**（4 次 commit 均绿）。
- **F135 归因回归 = 0。**

**Codex adversarial review（`codex review --base master`）**：〔见 §6，Codex 完成后回填〕。

**Opus 对抗自审（主节点）**：
1. **gap-1 未绕过治理**：唯一生产改动=CoreToolSet 加一个字符串，只改发现层；handler Two-Phase 逻辑
   独立于 Core/Deferred，AC-1.3 实证 proposal 门仍 fire。✓
2. **gap-2 未引入 stable-working-dir 风险**：候选是字面 `$HOME`/系统目录（非 which 派生，无 worktree
   解析风险），每段再过 validate_stable_paths，AC-2.2 断言每 PATH 段合规。✓
3. **hermetic**：Phase B 用 FakeCommandRunner + plistlib.loads（不真装 launchd）；Phase A 用 tmp store +
   进程内 pack（不打 LLM/网络）。✓
4. **边界守住**：不动 entrypoint 死代码（记 living-docs）；不扩到 user_profile.*；不改 profile 层。✓
5. **token/prefix-cache**：+1 Core 工具 schema 小（file_id/content/confirmed），负担可忽略，与
   graph_pipeline/delegate_task 同量级，价值明确。✓

## 6. Codex review finding 闭环

〔Codex review 完成后回填。若 Codex CLI 失败一次则跳过并注明，以 Opus 自审 + 确定性测试打底。〕

## 7. 用户上手：修复合入后真机验证步骤

**前置**：合入 master 后重装/重启托管实例（`octo update` 重启 或 `octo service install --force` 刷新
服务定义，让新 CoreToolSet + 新服务 PATH 生效）。

**验证 gap-1（agent 能真的帮写 behavior 文件）**：
1. Web UI 对主 Agent 说"帮我完成 USER.md 初始化"（或"把我时区改成 Asia/Shanghai 写进 USER.md"）。
2. **预期**：Agent **不再**回"工具入口没暴露给我"——它直接用 `behavior.write_file` 提出**修改提议**
   （proposal，因 USER.md 是 REVIEW_REQUIRED），展示将写入的内容摘要，请你确认。
3. 你确认后，Agent 再次调用（confirmed=true）真正落盘 + 产 F107 行为版本记录（可在文件工作台看版本历史）。
4. **治理仍在**：未经你确认它不会静默改 USER.md（Two-Phase 未被绕过）。

**验证 gap-2（MCP 搜索恢复）**：
1. 确认服务定义已刷新（`octo service install --force` 后 `octo service status` running）。
2. 让 Agent 做一次联网搜索（走 openrouter-perplexity MCP，如"搜一下今天的 X 新闻"）。
3. **预期**：不再 `mcp_session_open_failed [Errno 2] No such file`——npx 型 MCP 正常启动、搜索返回结果。
4. 排查用：`octo service status` 看 running；服务日志（`~/.octoagent/logs/`）不再有 openrouter-perplexity
   的 `[Errno 2]`；plist（`~/Library/LaunchAgents/com.octoagent.gateway.plist`）的 `PATH` 含 `~/.volta/bin`。

> **安全提醒**：实例 `~/.octoagent/data/ops/mcp-servers.json` 里 openrouter-perplexity 的
> `OPENROUTER_API_KEY` 在本次调查中被读到过（明文）。建议在 OpenRouter 控制台轮换一次该 key。

## 8. 已知 limitations（living-docs 漂移闸）

- **entrypoint 三件套是历史冗余**：`ToolEntry.entrypoints` / `resolve_for_entrypoint` / `toolsets.yaml`
  对 LLM 工具集零作用（无生产调用者）。F135 未清理（超范围），已在 harness-and-context.md 记录为冗余。
  未来可独立 Feature 删除或重新赋义。
- **`discovery_entrypoints` 命名误导**：`resolve_profile_first_tools` 返回的 `discovery_entrypoints`
  是**工具名**列表（推荐 discovery），与 web/agent_runtime entrypoint 概念同名冲突（F122 已记 worker_type
  命名收敛）。F135 未动。
- **plugin_watcher 环境依赖假失败**：见 §5，已 spawn follow-up。
