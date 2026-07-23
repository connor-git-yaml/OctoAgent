# F151 Design/Tasks Gate Review — Round 8

**结论**：artifact-only返修已完成；`GATE_DESIGN=false`、`GATE_TASKS=false`。未执行T001，实际TDD=0，production/tests/repo-scripts/workflow实现diff=0，must-fix仍全部open。本文只证明设计制品内部闭合，不声称实现或验证通过。

## 1. Planned-diff machine closure

**旧问题**：TaskService constructor读取不存在的`projected_path`；constitution/Blueprint/codebase docs错误归S100；archive glob可吞F151自身；planned/owner/changed closure依赖purpose散文。

**源码/制品证据**：`runtime-test-constructors.v1.json`真实字段为`entries[].path`；AgentContext清单才使用`projected_path`。F151制品根在旧archive模式的匹配范围内。

**修改位置**：

- `planned-diff.v1.json`改读`entries.*.path`，8个machine sources的每个field必须解析至少一个值；missing field直接失败。
- 新增`active-artifacts.v1.json`：36 current、4 exact superseded review；禁止feature-root wildcard exclusion。
- current artifacts作为pre-T001 governance input由`S002-manifest-integrity`拥有，不参与未来behavior evidence；`S104-docs`独立拥有constitution、Blueprint与codebase docs并在最终authority同步时重验。
- `S104-docs`拥有9个exact authority/doc paths及唯一static truth node；不借S100 workflow GREEN。

**机械oracle**：machine sources=8、additional exact paths=28、current/superseded=36/4、missing fields=0、F151 self-exclusion escape=0；planned-without-owner=0、owner-without-planned=0。negative fixtures覆盖不存在字段、F151 self-match、missing current artifact与三向closure缺口。

## 2. Startup static-invalid与lifespan composition边界

**旧问题**：front-door env early return会绕过完整YAML解析；同时旧合同把真实runtime assembly失败错误地要求为Uvicorn前exit78，和`create_app`保护范围冲突。

**源码证据**：baseline `main.py::_resolve_front_door_mode`在`load_config()`前读取并返回env mode；`main.app=create_app()`位于module import；真实ProviderRouter/LLM/stores/TaskRunner composition在FastAPI lifespan/OctoHarness发生，`__main__.py` baseline不存在。

**main冻结决定与修改位置**：

- `f150-scope.md`冻结exact before/final AST shape：`_resolve_front_door_mode`首个配置动作无条件`load_config(project_root)`恰一次；typed propagation后才读取env；有效结果仍env>YAML>loopback。只允许该control-flow、既有exposure handler和必要typed imports，`create_app`其余AST不变。
- `production-startup.md`、spec/contract/tasks将exit78收窄为同步可解析的static security/runtime config；`S064-runtime-exit`用env-present malformed YAML证明`GATEWAY_RUNTIME_CONFIG_INVALID`/78、Uvicorn与workload副作用0。
- `S085-lifespan-startup`独立characterization：真实composition failure只经现有lifespan失败，readiness/request/Task/Work/Event/backend=0、process nonzero；不映射78，不新增第二preflight/factory/global snapshot。

**机械oracle**：negative fixtures拒绝env early return、load count≠1、异常吞噬、precedence改变、sibling/Host/Origin/Access改动；L3 static/lifespan node分离。

## 3. Provider test rehome universe

**旧问题**：旧Provider test map遗漏5个Gateway职责测试，并遗漏非pytest wire recorder对Gateway dotenv loader的直接import。

**源码证据**：对`packages/provider/tests`扫描得到22个直接import Gateway的collectable `test_*.py`；旧map遗漏`dx/test_config_schema.py`、`dx/test_config_wizard.py`、`dx/test_memory_console_service.py`、`dx/test_memory_retrieval_profile.py`、`test_dotenv_loader.py`。`wire_replay/record_cassettes.py`是manual utility而非pytest node。

**修改位置**：`provider-test-rehome.v1.json`重算为44 move+1 delete，另登记1个exact manual utility decouple；namespace/plan/tasks/testing/scope/planned-diff均以final path投影。

**机械oracle**：map sources45 unique、targets44 unique；collectable direct-Gateway test22全部包含；完成态Provider旧test tree→Gateway dependency=0；retired source consumer、missing map field或把manual utility计成pytest均失败。

## 4. C084 JUnit parser

**旧问题**：命令读取JUnit root的`tests`属性；pytest 9通常输出`testsuites`根，成功运行会KeyError或假判。

**修改位置**：`testing-matrix.md`中的C084聚合single root `testsuite`或`testsuites`下全部leaf suites，要求每个suite有tests/failures/errors/skipped，汇总tests>0、failure/error/skip=0，并与`testcase`数量交叉验证。`S004-junit-parser`冻结positive/negative exact nodes。

**机械oracle**：真实pytest JUnit nested/single positive通过；missing/malformed suite、count mismatch、failure/error/skip/rerun与selected=0均失败。C084仍执行43 files+1 live-helper node，不把fixture qualname当pytest node。

## 5. Phase0 immutable anchor

**旧问题**：T005的hash验证与禁止替换anchor的措辞互相冲突，无法区分校验当前字节和改写信任锚。

**修改位置**：constitution、tasks、contract、rgr、stage commands、quickstart统一为：T001-T004只用标准pytest/Vitest transaction；T004后硬停；main在任务通信中记录不可变anchor；T005重算当前artifact字节hash并与anchor比较，但不得生成/替换/重新解释anchor，不得替换artifact或补跑。

**机械oracle**：artifact byte替换、anchor mismatch、mixed tree/base/argv/JUnit/raw、reordered RGR、selector/oracle/failing-node mismatch、collection/skip/rerun均拒绝；bootstrap exception只覆盖checker自身Phase0。

## 6. Storage-only operation machine allowlist

**旧问题**：storage-only操作和生产调用点只有散文，unknown/default-deny与capability reachability无法机械执行。

**修改位置**：新增`runtime-operation-modes.v1.json`，冻结42个TaskService/AgentContext operation，46个TaskService target构造点（3 runtime+43 storage）与3个direct AgentContext构造点；每个TaskRunner构造点也具有path+qualname+lexical ordinal，line仅报告。operation清单不是runtime registry/service。

**机械oracle**：operation identity42/42；TaskService target identity46/46；TaskRunner storage callsite14；direct AgentContext3。missing/extra/unknown method或callsite、mode不明、runtime-from-storage，以及storage-only call graph可达MemoryRuntime/reranker auto-load/model/background/network任一均失败。

## 7. 数字与active authority

**旧问题**：active文案沿用上一轮slice、Provider test与additional path口径；历史review与current assertions混扫。

**修改位置**：active artifacts明确current/superseded；Round4-7 review只作history。spec/plan/tasks/clarifications/research/trace/checklist/analysis/quickstart/constitution与inventories统一Round8事实。

**机械oracle**：FR42、tasks76 unchecked、slice90/90、declared-new120（含全部absent Provider test targets）、overlap39/shared3/partition36、cross-phase unresolved0、additional paths28、active36/4。active stale scan只读current；superseded review不能满足current assertion。

## 8. Round 8全量自检与质量状态

**机器清单**：namespace49 move+2 delete；D-03 15/1/33且role tags13/5/9/6；Provider tests44+1+1；cross-role imports41/calls147且identity unique；TaskService test constructors144=143 live+1 shadowed；AgentContext31；production operation42、TaskService46=3/43、direct AgentContext3。

**TDD/分层/架构/坏味道**：

- TDD：实际RED/GREEN/REFACTOR=0；只有selector/oracle/protocol，不能声称通过。
- 分层：L4覆盖logic/model/store/service/adapter/constructor purity；L3覆盖startup/API/Event/wheel/tmp Git；新增L1/L2=0。
- 架构：Provider→Gateway和services/routes→CLI目标0；legacy operations不是clean physical layers；41/147只是ceilings。
- 坏味道：ordinary read hidden write、storage-only hidden runtime、session persistence/extraction耦合、duplicate test、Telegram RMW、Update TOCTOU仍为must-fix；实施前均open。

## Gate请求

当前只请求main复审`GATE_DESIGN`与`GATE_TASKS`。两个Gate保持false，不请求Implement；在main明确批准前不得进入T001、stage、commit或push。
