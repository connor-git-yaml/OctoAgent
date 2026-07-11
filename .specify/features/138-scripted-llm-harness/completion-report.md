# F138 — Completion Report（脚本化 LLM harness，M9 P0 keystone）

> 2026-07-11 · 分支 `feature/138-scripted-llm-harness`（rebase onto master `a1e4ca15`）· **未 push，等用户拍板**
> 双评审：Codex spec 评审 0 HIGH（2 P2 已闭环）+ Codex 全量 review **零 finding** + Opus 式对抗自审（抓 1 真洞已修）

---

## 1. 每 Phase 实际 vs 计划

| Phase | 计划 | 实际 | 偏离 |
|-------|------|------|------|
| 收窄 | 三岔路拍板写死 | ✅ `ebc66c3c`——含新增 §3.5 hook 陷阱防御设计（计划外，实证驱动）| 增补，无删减 |
| spec 评审 | Codex 0 HIGH 后实施 | ✅ 0 HIGH + 2 P2（TestModel 文档提前宣称 / bomb 挂错点），均接受修正 `9d781fa8` | 无 |
| A harness DI | model_client+clock 两参 + elif 拦截 + clock seam | ✅ `03496df0`。diff 形状 = 纯新增 + 单行 if→elif；5 case DI 测试全空凭证 bootstrap | 无 |
| B 上提 | ScriptedModelClient + conftest re-export | ✅ `a27b6d06`。**re-export 推迟到 Phase F 尾部**（flip-at-the-end，spec §3.5 防 hook 收集炸）；过渡窗口 parity 断言看护 | 顺序偏离（防御性，spec 已先行归档）|
| C keystone | 脚本脑驱动真决策环 e2e | ✅ `16d70128`。4 case（keystone / 事件链 / 零 OAuth / 确定性双跑），2.93s | 比计划多 2 个 case（事件链 + 确定性）|
| D SchemaTestAdapter | **跳过（拍板②）** | ✅ 未实施，spec §2.2 归档 deferred 范围/理由/启动条件 | 按拍板 |
| E clock consumer | watchdog ≤5 处 | ✅ `e787fddc`。**实测 6 处**（detectors 3 + cooldown 2 + scanner 1）全接（半吊子不如全接，spec §3.3 已先行修正）；6 case AC-6 测试 | 处数 5→6（诚实计数）|
| F 文档+翻转 | e2e-testing / testing-strategy / milestones + conftest 翻转 | ✅ `079c0a46`。harness-and-context.md 实测无 DI 段无需同步 | 无 |

## 2. None 等价对账证据（AC-2）

1. **diff 形状**：`_bootstrap_executors` 唯一存量行变更 = `if _llm_mode_env != "echo":` → `elif`，块体逐行不动（git diff 可验）；构造函数/`bootstrap()` 均纯新增。
2. **对账测试**：`test_octo_harness_model_client_di.py` AC-2a（None+非 echo → ProviderModelClient 原路）/ AC-2b（None+echo → skill_runner 跳过原语义）/ AC-2c（clock seam 默认 `_utc_now` tz-aware）。
3. **additive inert seam 显式声明**：`app.state.clock` 是新增 attr（grep 实证全库此前零消费者），非字面 byte-for-byte——spec §3.2 已把承诺精确化为"控制流逐行等价 + 新增无人读 attr + watchdog 注入默认值逐值等价"（与 F087 T-P2-8 行为等价范式同款）。
4. **全量回归**：0 regression vs baseline（见 §5）。
5. **watchdog None 缺省看护**：`test_all_components_default_to_utc_now`（5 组件 `_clock is utc_now`）。

## 3. keystone 证明（AC-3/AC-8：决策环前半段，零真 HTTP）

- 驱动入口 = `llm_service.call(...)`（**非** `tool_broker.execute` 切入）；断言链：`scripted.calls == 2`（脚本脑被决策环消费到 complete）→ `TOOL_CALL_STARTED`（broker 真路径专属事件，证"LLM 决策→派发"真发生——L3 此前零覆盖的那一跳）→ USER.md 真回写 + `MEMORY_ENTRY_ADDED` → `result.content == "已记录"` 且 `is_fallback is False`（脚本输出贯穿，未落 Echo）。
- 零真 provider 三重防御：`resolve_for_alias` bomb（路径 A `router_message_adapter.py:66` 与路径 B `provider_model_client.py:548` 共同咽喉点，Codex P2-2 修正后）+ 空 tmp CredentialStore（load 空 store）+ content 断言兜底。
- **CI-runnable**：全套件不依赖 `real_codex_credential_store`（宿主无 OAuth 恒可跑）；4 case 全 11 段 bootstrap + 决策环 < 3s。
- 事件链确定性：2 对 MODEL_CALL_* + 1 对 TOOL_CALL_* 逐值断言 + 双跑逐值一致 case。

## 4. 双评审闭环

| 轮次 | 结果 | 处理 |
|------|------|------|
| Codex spec 评审（收窄后）| 0 HIGH + 2 P2 | P2-1（TestModel 勿提前宣称已落地）/ P2-2（bomb 挂 `resolve_for_alias` 非不存在的 `provider_router.call`）均接受，`9d781fa8` 闭环 |
| Codex 全量 review（实施后，--base origin/master）| **零 finding**（"未发现会破坏现有行为或新增功能的明确缺陷"）| 无待处理 |
| Codex re-review（Phase F 翻转 + 自审修复增量后，F099"修复后必 re-review"铁律）| **再次零 finding** | 无待处理 |
| Opus 式对抗自审 | 抓 1 真洞：`test_clock_di.py` module 级 import master 不存在的 `utc_now` 符号 → pre-merge 窗口 hook 收集会 ImportError（importorskip 探不到符号级缺失）| 已修：utc_now 改函数内 import（runtime-only 收集安全）|
| 自审其余挑战面 | #9 构造性不可达（生产零 import `skills.testing` + 唯一构造点 main.py:425 不传 override + 无 env/yaml 开关）/ keystone 无假通过窗口（落 Echo 则 content/calls 断言必炸）/ testing 子模块零新第三方依赖 / clock 只加不改（123 watchdog 域测试零改全绿）| 全部成立 |

## 5. 改动清单 + 回归数

- **20 文件，+1639 / −61**（vs origin/master a1e4ca15，含本报告）。生产 src：`octo_harness.py`（DI+拦截+seam；存量行改动恰 5 行 = 1 行 if→elif + 4 行 watchdog 构造加 clock 参数，其余纯新增）、watchdog 4 文件（clock 构造注入）、`skills/testing/` 新模块（2 文件）；测试：4 新文件（18 case）+ conftest 翻转；文档 3 + spec 制品 4 + pyproject markers 1 行。
- **Baseline**（rebase 后未改动 worktree）：4919 passed / 14 skipped / 1 xfailed / 1 xpassed（379s）。
- **最终门（双确认）**：全量 **4937 passed / 14 skipped / 1 xfailed / 1 xpassed**——两次独立全量（380.16s + HEAD `5ca81170` 收口跑 345.04s）逐项一致；+18 = 新增测试净数恰等于 5 DI + 3 multistep + 4 keystone + 6 clock，skipped/xfailed/xpassed 与 baseline 逐项相同，**0 regression**。HEAD 上 e2e_smoke 8/8（PYTHONPATH 锁定跑法，2.38s）；e2e_scripted 全绿；ruff 逐文件对账零新增（octo_harness 20=20，conftest 2=2 且错误码逐条一致）。
- **首跑全量插曲**（非代码回归）：与并行 F137 会话竞态——其 provider 包 pytest11 entry-point（`octoagent_model_request_gate`）被 sync 进共享 venv 的窗口内，任何 pytest 启动都会 import `octoagent.provider.testing`，被本 worktree PYTHONPATH 劫持到无该子模块的 provider → 启动即炸。重跑加免疫 flag `-p no:octoagent_model_request_gate`（插件缺席时零副作用）。见 §7。

## 6. Deferred 清单

1. **SchemaTestAdapter（TestModel 等价，Phase 2）**：范围 = `tool_broker.discover()` + pydantic-ai 私有 `_JsonSchemaTestData(schema, seed)` 填参组装 tool_calls + 私有 API 签名锁测试（F110 范式）。价值 = 扫 63 工具 schema 广度批量降层。启动条件：F138 合入后任意时点独立 followup（DI 缝已就位，纯增量）。归档：spec §2.2。
2. **keystone 是否升 e2e_smoke / 归入 F141 pr lane**：合入后主 session 决定（marker `e2e_scripted` 已登记）。
3. **/api/message 全链路广度样例**（含 TaskService 编排 + selected_tools 上游填充）：不在本 Feature（spec §6）。
4. **合入后顺手（一行级）**：新测试文件顶部的 `pytest.importorskip("octoagent.skills.testing")` 在合入 master 后变永久 no-op guard，可留（自文档化）可删。

## 7. 与 F137 的合并交点（主 session 合并时按序处理）

1. **`octoagent/pyproject.toml` markers**：F138 加 1 行 `e2e_scripted`；F137 也动 markers → 文本级冲突可能，逐行并集即可。
2. **共享 venv 争用（过程性，非代码冲突）**：①F137 的 pytest11 entry-point 使"venv 装了 F137 provider + PYTHONPATH 指向无该模块的树"组合在 pytest 启动即炸——两分支**合并后此矛盾自动消失**（master 同时含两者）；未合并期间跨树跑测试需 `-p no:octoagent_model_request_gate`。②实测共享 venv 的 editable pth 在本次并行期间被多次改写（F138 worktree ↔ 主仓来回）——**pre-commit hook 的 `uv run`（无 --no-sync）本身就是改写源**；修正 memory `project_precommit_hook_execution_model` 的认知：hook 跑的不是恒定 master src，而是"最近一次 sync 的树"（移动靶）。**建议两分支都合入后在主仓 `octoagent/` 跑一次 `uv sync` 收敛 venv**（同时消除 worktree 删除后 pth 悬垂风险，即 magical-bardeen 前科）。
3. **`docs/blueprint/testing-strategy.md`**：F138 改了 §13.1/§13.3（ALLOW_MODEL_REQUESTS 标"F137 落地中"）——若 F137 也改此文档，合并时以后合者对齐前者措辞。
4. **文件不冲突面（复核成立）**：F138 未触碰 provider_client.py / fallback.py / llm_service.py / .githooks / .github / 前端。

## 8. 已知 limitations

1. `e2e_scripted` 套件当前只覆盖 `user_profile.update` 单工具决策链 + system.echo/file_read 多步（skills 层）——63 工具广度属 SchemaTestAdapter Phase 2。
2. keystone 走 direct 入口（`llm_service.call`），未覆盖 TaskService 编排层（deferred §6.3）。
3. conftest 翻转后，本 worktree **合并前**的后续 commit 若遇 venv 指向非本树，hook 需 `SKIP_E2E=1` + 以锁定 `pytest -m e2e_smoke` 作补偿 gate（spec §3.5 defense #2；合入 master 后 hook 恒绿、零残留）。
4. 共享 venv 被并行会话活体改写期间，任何"裸跑"（无 PYTHONPATH 锁）结果不可信——本 Feature 全部验证均带锁跑。
