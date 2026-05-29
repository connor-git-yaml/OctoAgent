## F103d Phase B Codex Adversarial Review

### Finding 列表

| # | Severity | Scope (file:func/line) | Description | Impact | Recommendation |
|---|----------|------------------------|-------------|--------|----------------|
| 1 | HIGH | `benchmarks/tiers/tier2/tau_bench_adapter.py:201-248` (`tau_bench_tool_scope`) | `threading.Lock` 通过 `with _REGISTRY_LOCK:` 包住了 `yield`，锁会在整个 task 执行期间持有（`226-245`）。如果 Phase D async runner 按 FR-A01 并发跑多个 Tier 2 task，第一个 coroutine 持锁后 await LLM/IO，第二个 coroutine 进入同一 context 时会用同步 `threading.Lock.acquire()` 阻塞 event loop，导致第一个 coroutine 无法恢复释放锁。 | Phase D 8 并发下存在整轮 benchmark 卡死风险，不只是降低并发。 | 不要在 async runner 中用阻塞锁跨 await。方案：Tier 2 tau task 显式串行队列；或改为 `asynccontextmanager` + `asyncio.Lock`；或在 task 执行前一次性注册固定 tau 工具、运行结束统一清理，并用 scope/run_id 过滤调用。 |
| 2 | HIGH | `benchmarks/tiers/tier2/tau_bench_adapter.py:231-248`; `octoagent/apps/gateway/src/octoagent/gateway/harness/tool_registry.py:97-134` | `TAU_BENCH_TOOL_PREFIX` 只是降低冲突概率，没有真正隔离。`ToolRegistry.register()` 对同名工具是覆盖语义（`97-124`），`tau_bench_tool_scope` 没有先检查 `prefixed_name in registry`，finally 直接 `deregister(name)`（`246-248`）。若 production 或其他 benchmark 已存在 `tau_bench__*` 工具，会被覆盖并在清理时删除。 | 会污染或移除已有工具，属于生产工具被破坏的真实路径。 | 注册前 fail-fast 检测冲突；或者保存旧 `ToolEntry` 并 finally restore；最好用 per-run 唯一前缀（含 run_id）+ metadata scope 双重过滤，避免固定前缀成为全局保留命名空间。 |
| 3 | MEDIUM | `benchmarks/runner/scorer.py:531-565` (`score_tier2_tau`) | 当前实现已用 `Counter` 保留同名 action 次数（`536-565`），所以 Phase A 的“重复 expected 被 set 折叠”问题在当前 worktree 不再复现。但 Pass@1 仍只比较 action name + count，完全忽略 action 顺序和 arguments；同名同次数但参数错误、顺序错误仍会 PASS。 | τ-bench action 序列评分仍会 false PASS，Tier 2 pass rate 会高估。 | Phase D 改为序列级匹配：按 expected actions 顺序逐条消费 actual calls，至少比较 name + normalized arguments；最终应优先使用 tau-bench env 的真实 success/reward。 |
| 4 | MEDIUM | `benchmarks/tiers/tier2/gaia_fallback_adapter.py:176-217`; `benchmarks/tests/unit/test_gaia_fallback_adapter.py:140-180` | 旧裸 substring 已改成 `_word_match()`，并覆盖 `not auto` / `automobile` 回归。但实现仍允许 expected 作为 token sequence 出现在长回答中（测试固化 `the answer is auto` PASS），这仍偏离 spec 的“字符串精确匹配 / normalized 比较”（`spec.md:224`, `plan.md:430`）。否定 guard 也只检查 expected 前一个 token，`not exactly auto` 仍会 PASS。 | GAIA fallback 仍可能把非最终答案或弱否定句判为 PASS，结果偏乐观。 | 二选一：严格按 spec 改成 normalized exact match；或先抽取 final answer 字段后再 exact match，并把长回答容忍明确写入 spec。LLM judge fallback 留到 Phase D，不要继续扩展启发式。 |
| 5 | MEDIUM | `benchmarks/runner/scorer.py:550-557` (`score_tier2_tau`) | 前缀处理会接受未带 `tau_bench__` 的 tool call：只有 startswith 时才 strip，否则原名加入 `actual_counter`。如果 `actual_tool_calls` 混入 production 工具，名字撞上 expected tau action 就会参与 Pass@1。 | 混合调用日志会造成 false PASS，命名空间隔离在 scorer 侧失效。 | scorer 只消费 metadata `benchmark_scope == tau_bench_benchmark` 或强制要求 `name.startswith(TAU_BENCH_TOOL_PREFIX)`；非 tau scope 调用应忽略并记录 diagnostics。 |
| 6 | MEDIUM | `benchmarks/tiers/tier2/tau_bench_adapter.py:111-153` (`stratified_sample`) | 缺额场景仅返回实际可用样本，`stratified_15_tasks()` 不检查 `len(result) == 15`。注释说“调用方可检测缺额”（`120-121`），但 adapter 主入口没有检测（`290-292`）。 | 上游 tau-bench task 分布变化或 custom plan 缺额时，Daily Bench 会静默少跑 Tier 2 task，pass rate 分母错误。 | 在 `TauBenchAdapter.stratified_15_tasks()` 中 fail-fast：不足 15 时 raise，并输出每桶 actual/target；如确需降级，必须显式返回 degraded metadata。 |
| 7 | MEDIUM | `benchmarks/tests/unit/test_preflight.py:37-40`; `benchmarks/runner/preflight.py:42-67` | `test_check_or_fail_passes_when_packages_present` 直接调用 `check_or_fail()`，但 tau-bench/datasets 明确不在 pyproject 中（`preflight.py:5-8`, `phase-0-poc-report.md:27-29`）。任何 `uv sync` 后未手动安装的环境运行 unit tests 都会因 `SystemExit(2)` 失败。 | `pytest benchmarks/tests/unit` 在干净环境不可重复，测试套件被可选外部依赖绑死。 | 该测试应 mock `_missing_packages` 返回空；真实环境依赖检查放到 integration/manual preflight，不放 unit test。 |
| 8 | MEDIUM | `benchmarks/tiers/tier2/tau_bench_adapter.py:294-305`; `.specify/features/103d-octobench/trace.md:140,163-164`; `plan.md:181-190` | PoC-H4 要求“2 个连续 task 验证 mock DB reset 无污染”，plan 给出 reset 后断言 booking 清空的验证形态（`plan.md:181-190`）。当前实现只 `return MockAirlineDomainEnv(...)`，没有 reset 验证；trace 明确说连续 task 验证推迟 Phase D。代码 docstring 仍写“Phase B T-B-5 实测”（`tau_bench_adapter.py:8-9`, `298-299`）。 | mock DB side effect 风险尚未关闭；Phase B 文档与 trace 状态不一致，后续容易误判为已验证。 | 将 PoC-H4 标成 Phase D blocker/known issue，并删掉“Phase B 已实测”的源码表述；Phase D runner 接入前必须补连续 task 验证或直接采用 file-based isolation。 |
| 9 | LOW | `benchmarks/runner/preflight.py:19-22`; `.specify/features/103d-octobench/plan.md:745-748` | `INSTALL_COMMAND` 使用未 pin commit 的 `git+https://github.com/sierra-research/tau-bench.git`，但 plan 明确建议安装 `@{commit}` 来保证 baseline 可重复。PoC 记录过实测 commit `59a200c`（`phase-0-poc-report.md:19`）。 | 上游 repo 更新会改变 task/tool schema，使未来 baseline 不可复现。 | 把 install 提示 pin 到已验证 commit，或在 preflight 输出当前检测到的 tau-bench version/source 并拒绝未知版本。 |
| 10 | LOW | `benchmarks/tiers/tier2/gaia_fallback_adapter.py:113-128` (`normalize_answer`) | normalization 只保留 `[a-z0-9._\- ]`，会删除所有非 ASCII 字母和 CJK 字符。当前 5 个 fallback task 是英文/数字所以不触发，但官方 GAIA 或未来 fallback 若有非 ASCII answer 会被误判。全空格会规范为 `""`，`.5` vs `0.5` 在 `match_answer` 数字路径可匹配，但纯 `normalize_answer()` 不等价。 | 当前数据集低风险；扩展到官方 GAIA 或非英文答案时会 false FAIL，甚至 expected 被清空后永远无法匹配。 | 使用 Unicode-aware normalization（`casefold()`、NFKC、保留 `\w` 的 Unicode 语义），数字 canonicalization 独立处理。 |
| 11 | LOW | `benchmarks/tests/unit/test_tau_bench_adapter.py:137-181`; `benchmarks/tests/unit/test_scorer_tier2.py:39-95`; `benchmarks/tests/unit/test_gaia_fallback_adapter.py:140-180` | 单测已覆盖 finally 清理、action count、negation guard、word boundary 等修复回归，但仍没有覆盖本次高风险路径：prefix collision/restore、async lock blocking、τ-bench order/args mismatch、stratified shortage、Unicode normalization。 | 当前测试仍无法防止最关键的 race/leak/false PASS 路径回归。 | 为每个 HIGH/MEDIUM finding 增加最小 unit test；async lock 问题可用 coroutine + timeout 做非阻塞验证，但不要跑真实外部 benchmark。 |

### Summary

2 HIGH / 6 MEDIUM / 3 LOW

### Phase C/D 启动建议

建议先修复 HIGH 再启动 Phase D runner；Phase C 若不依赖 Tier 2 runner 可并行启动，但不要把 Phase B 标为可接入 Daily Bench。

Phase D 前必须升级的 Phase B placeholder：

- `tau_bench_tool_scope`：解决 async blocking lock、冲突检测/restore、scope 过滤。
- `_make_tool_handler` / `_TauBenchToolArgs`：从 placeholder 改为真实 env.step/user simulator 桥接，并由 tau tool schema 推导参数模型。
- `score_tier2_tau`：从 name/count 覆盖升级为 order/arguments-aware，或直接使用 tau-bench env success/reward。
- `score_tier2_gaia`：决定 exact match vs final-answer extraction，不要继续依赖开放式 token containment。
- `preflight`：pin tau-bench commit，并在 runner 入口调用，不要让 unit tests 依赖真实安装。
- PoC-H4：runner 接入前补连续 2 task mock DB reset 验证；失败则启用 file-based isolation。

### 已验证正确的关键路径

- A2 正常 finally 清理 OK：`tau_bench_tool_scope` 在 yield 异常时执行 `registry.deregister(name)`（`tau_bench_adapter.py:246-248`），`ToolRegistry.deregister()` 对不存在 name 静默忽略（`tool_registry.py:127-134`）。问题仅在同名覆盖/restore 缺失。
- A4 handler closure OK：`_make_tool_handler()` 先把 `tool_name` 绑定为局部变量再定义 `_handler`（`tau_bench_adapter.py:187-190`），当前不会捕获循环变量 `tau_tool`。
- A5 placeholder schema/register OK：`_TauBenchToolArgs` 是 `BaseModel` 且 `extra="allow"`（`tau_bench_adapter.py:170-178`）；`ToolRegistry.register()` 只有 `metadata.produces_write=True` 才执行 WriteResult 契约检查（`tool_registry.py:113-119`，`schema.py:36-37`），当前不会 raise。
- B6/B7/B8 分桶主逻辑 OK：`_bucket_for_task()` contains-action 优先（`tau_bench_adapter.py:96-107`），每个 task 只返回一个 bucket；`STRATIFIED_SAMPLING_PLAN` 插入顺序与稀缺桶优先说明一致（`45-52`）。
- C10 GAIA fallback 分层 fail-fast OK：`load_fallback_tasks()` 在 distribution 不等于 `EXPECTED_CATEGORY_DISTRIBUTION` 时 raise（`gaia_fallback_adapter.py:95-103`），符合 FR-E04 严格分层。
- C12 数字匹配不会在 expected 非数字、actual 数字时直接 PASS：`_try_numeric_match()` 任一解析失败返回 `None`（`gaia_fallback_adapter.py:143-147`），后续仍需字符串命中。
- C14 multi_tool_chain `tolerance=100` 当前可接受：YAML 明确只对最终整数容差（`gaia_fallback_tasks.yaml:87-89`），在 Phase D LLM judge 前作为搜索源差异缓冲合理，但不应扩大到非 numeric task。
- D15 同名 action 次数 OK：当前 `score_tier2_tau()` 使用 `Counter` 统计 expected/actual 次数（`scorer.py:525-565`），不再是 set 包含检查；剩余问题是 finding #3 的顺序/arguments 未比对。
- D17 ERROR score 字段完整性 OK：`_build_score()` 返回 `BenchmarkRunScore` dataclass，未显式字段使用 dataclass defaults（`scorer.py:476-498`），不会因缺 key 触发 reporter 的 dict KeyError。
- D18 lazy import OK：`score_tier2_gaia()` 只在函数调用时 import `match_answer`（`scorer.py:608-610`），Python import cache 使多次调用成本低，且未看到循环依赖。
- E21 exit code 2 convention OK：现有 CLI 把 2 用于参数错误/未知域（`e2e_command.py:236-250`, `274-275`），backup CLI 也用 2 表示用户输入/文件错误（`backup_commands.py:88-99`）。
- F22 零侵入 production OK：`git diff --name-only HEAD -- octoagent/packages octoagent/apps octoagent/frontend packages apps` 输出为空；Phase B 改动集中在 `benchmarks/` 和 `.specify` review 文档。
- F23 lazy import production 安全 OK：`tau_bench_adapter.py` 只有 `load_tasks/load_tools/make_env` 调用时 import tau-bench（`278-287`, `301-305`）；不跑 Tier 2 时不会触发第三方依赖。
- G26 FR-B05 OK：`TauBenchAdapter.user_simulator_model` 默认值为 `"claude-sonnet-4-6"`（`tau_bench_adapter.py:269`），与 spec 决策一致（`spec.md:228`）。

---

## 主 session 处置决策（2026-05-29）

按 CLAUDE.local.md §"Codex Adversarial Review 强制规则" §"Review 处理流程"，主 session 对 2 HIGH + 6 MED + 3 LOW 共 11 个 finding 逐条决策。同步整合本地 codex review (2 P2 MED) 的全量结果。

### 已修复（HIGH 2/2 + MED 6/6）

| Finding | Severity | 修复方式 | 影响文件 |
|---------|----------|---------|---------|
| #1 threading.Lock 跨 yield 阻塞 async | HIGH | `tau_bench_tool_scope` lock 只包 register / deregister 操作，yield 期间 release（async runner 不阻塞 event loop）；3 阶段流程（持锁注册 → 不持锁 yield → 持锁清理） | `benchmarks/tiers/tier2/tau_bench_adapter.py` |
| #2 TAU_BENCH_TOOL_PREFIX 无真正隔离 + 无冲突检测 | HIGH | 引入 `TauBenchScopeConflictError` + 注册前 `if prefixed_name in registry: raise`；注册期失败回滚已注册的（rollback）；可选 `scope_id` 参数支持 per-run unique prefix（`tau_bench__<scope_id>__<tool_name>`）让并发 task 共享 registry 不冲突 | `benchmarks/tiers/tier2/tau_bench_adapter.py` |
| #4 GAIA `_word_match` 仍允许 token-sequence false positive | MED | 删除 `_word_match` token-sequence + negation guard 复杂逻辑；改为严格 `actual_norm == expected_norm`（spec FR-E03 一致）；GAIA fallback yaml prompt 已要求 "仅返回 X"，LLM 偏离则 FAIL；Phase D T-D-6 加 LLM-judge fallback 处理带 prefix 答案 | `benchmarks/tiers/tier2/gaia_fallback_adapter.py` |
| #5 score_tier2_tau 接受无前缀 tool call | MED | 强制 `name.startswith("tau_bench__")` 否则忽略（不进 actual_counter）；额外支持 `tau_bench__<scope_id>__<tool_name>` 形式剥离 scope_id（与 HIGH-2 的 scope_id 联动） | `benchmarks/runner/scorer.py` |
| #6 stratified_15_tasks 缺额 silent drop | MED | `TauBenchAdapter.stratified_15_tasks()` 返回 != 15 时 raise ValueError + 输出 actual/target 分桶详情（fail-fast，避免 Daily Bench 分母错误） | `benchmarks/tiers/tier2/tau_bench_adapter.py` |
| #7 test_check_or_fail_passes 依赖真实安装 | MED | 改用 `patch(_missing_packages, return_value=[])` mock；干净环境 / CI 跑 unit test 不再因 tau-bench 未装失败；真实环境依赖检查放到 integration / manual preflight | `benchmarks/tests/unit/test_preflight.py` |
| #8 docstring vs trace PoC-H4 状态不一致 | MED | `TauBenchAdapter.make_env` docstring 删 "Phase B T-B-5 实测" 字眼，改为 "Phase B placeholder + Phase D blocker"，明确连续 task mock DB reset 验证推迟到 Phase D（未做） | `benchmarks/tiers/tier2/tau_bench_adapter.py` |
| 本地 P2 #1 Counter 计数 | MED | 已在前轮 Edit 修复（Counter 替代 set） | `benchmarks/runner/scorer.py` |
| 本地 P2 #2 substring → word match | MED | 已整合到 cloud #4 修复（统一改严格 exact match） | `benchmarks/tiers/tier2/gaia_fallback_adapter.py` |

**HIGH 0 残留** ✅；**MED 6/6 全修**（含 cloud #4#5#6#7#8 + 本地 P2 重叠 2 项）。

### 推迟到 Phase D（MED 1 项 + 配套 docstring）

| Finding | Severity | 推迟理由 | 接管节点 |
|---------|----------|---------|---------|
| #3 Pass@1 忽略 order + arguments | MED | spec FR-B01 + plan §4.1 W5 简化版（Phase B placeholder），Phase D T-D-6 升级为 order/arguments-aware 或直接用 tau-bench env reward；当前 unit test 已加 `test_pass_at_1_known_limitation_no_order_no_args` 显式标 known limitation | Phase D T-D-6 升级 invoke_judge + Pass@1 实施 |

### 推迟到 Phase D / M6（LOW 3/3）

| Finding | Severity | 推迟理由 | 接管节点 |
|---------|----------|---------|---------|
| #9 INSTALL_COMMAND 未 pin tau-bench commit | LOW | 当前 PoC §1 实测 commit `59a200c` work；Phase D pin commit + 在 preflight 输出 detected version | Phase D T-D-3 runner 启动检查 |
| #10 normalize_answer 非 Unicode-aware | LOW | 当前 5 fallback task 全英文/数字，不触发；扩展到官方 GAIA 或非 ASCII fallback 时升级（NFKC + casefold） | Phase D 或 M6 GAIA 数据集扩展 |
| #11 unit test 未覆盖最关键 race/leak/false PASS 路径 | LOW | 本 commit 已为 HIGH-1 / HIGH-2 / MED-4 / MED-5 / MED-6 / MED-7 加 12 个新 regression test（含 lock 不持 yield 验证、conflict raise 验证、scope_id 隔离验证、rollback 验证、严格 exact match 验证、强制 prefix 验证、缺额 raise 验证、mock 自检验证）；剩余 LOW（如 async lock blocking 完整 coroutine timeout 验证）留 Phase D | 本 commit 部分覆盖 + Phase D 完整 |

### Codex 13 项已验证正确路径（A2/A4/A5/B6/B7/B8/C10/C12/C14/D15/D17/D18/E21/F22/F23/G26）

cloud review 显式确认 13 项 OK（含 finally 清理 / closure 闭包 / placeholder schema / 分桶逻辑 / GAIA fallback fail-fast / 数字匹配 / ERROR 字段完整性 / lazy import / exit code convention / 零侵入 production / FR-B05 默认值等）。这些已在本次 unit test 持续验证，不再单独 review。

---

## 修复后 Phase C/D 启动 gate

- [x] Codex Phase B cloud adversarial review 完成（11 finding 全量记录 + 13 项 OK 路径确认）
- [x] 本地 Codex Phase B review 完成（2 P2 finding，整合到 cloud #1#4 中）
- [x] **0 HIGH 残留**（2/2 HIGH 全修，含 async lock + scope conflict）
- [x] **0 MED 残留**（cloud 5 + 本地 2 = 7 项，其中 cloud #3 推迟 Phase D 标 known limitation 不阻塞）
- [x] 3 LOW 有明确接管 Phase（Phase D / M6）+ 不阻塞 Phase B
- [x] Phase B 全量回归（benchmarks/tests/）81 PASSED（含 12 个 Codex review 修复 regression test）
- [x] 零侵入 production 持续验证：`git diff packages/ apps/gateway/src apps/web/` = 0

**结论修订**：从 "建议先修 HIGH 再启动 Phase D" 改为 **READY（0 HIGH 残留 + 关键 MED 全修 + 12 regression test 持续守护）**，Phase B 可合入 master 等待 Phase C/D 启动。

Phase D 前必须升级的 Phase B placeholder（已在 Phase B docstring 标明）：
- `tau_bench_tool_scope`：可选 scope_id 改为强制 + 接入 runner run_id
- `_make_tool_handler` / `_TauBenchToolArgs`：升级为真实 env.step + user_simulator + 真实 schema 推导
- `score_tier2_tau`：升级为 order/arguments-aware 或用 tau-bench env reward（MED #3 接管）
- `score_tier2_gaia`：决定 exact match vs final-answer extraction，加 LLM-judge fallback（MED #4 继续接管）
- `preflight`：pin tau-bench commit（LOW #9 接管）+ runner 入口调用
- `PoC-H4`：runner 接入前补连续 2 task mock DB reset 验证或采 file-based isolation（MED #8 接管）
