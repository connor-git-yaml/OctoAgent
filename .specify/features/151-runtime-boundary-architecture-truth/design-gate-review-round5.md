# F151 GATE_DESIGN / GATE_TASKS Round 5 Review

**Gate状态**：false；本轮仅返修Spec Driver制品与constitution草案。未进入implement，未改production/tests/repo-scripts/workflow，未stage/commit/push；既有`.gitignore`一行改动未触碰。

## A-L逐项闭环

| 项 | 旧问题 | 源码证据 | 第五版修改位置 | 机械结果 |
|---|---|---|---|---|
| A Runtime最小权限 | 把orchestrator 1141误列runtime-bearing | `orchestrator.py:1141`只为`_InlineReplyLLMService`进入通用链；Round5 seam后不再需要bundle；真实模型点为worker runtime 512与orchestrator 1268/1343 | spec FR022/SC006、data-model、contract §7、runtime-bundle、T080-T090 | source baseline48=4+44；target46=3 runtime+43 storage；runtime/pure headings=3/43，1227/1319删除点保留 |
| B D-03职责 | `channel_verifier`误标adapter | 文件仅含Pydantic result、Protocol、内存dict registry与missing builder；HTTP在`telegram_verifier` | namespace、test-owner、research、quality、T017/T024/T029 | operations表33行=13 application/5 domain/9 store/6 adapter；channel owner只验registry/result |
| C ProviderRoute YAGNI | 把不存在schema中的headers/body塑造成新DTO字段 | `ProviderEntry`只有id/name/enabled/transport/api_base/auth及v1字段；Router helpers用`getattr`探测不存在字段 | spec FR005、data-model、contract §2、research、S015 | ProviderRoute正向字段表中`extra_headers`/`extra_body`行=0；保留absolute URL与env/profile oracle；Provider内置header/per-call body边界明确 |
| D F151/F150 scope | T064允许修改而T070又笼统要求F150全无diff | `main.py::_resolve_front_door_mode`与`_enforce_front_door_exposure`各有一个warning+continue handler；FrontDoor核心符号独立 | `inventories/f150-scope.md`、FR040、contract §12、T064/T070 | 7个protected symbol完整normalized AST SHA-256冻结；2个handler baseline hash冻结；allowlist外production diff=0规则明确 |
| E CLI15 truth | 把含HTTP/filesystem/subprocess/signal/store的命令称纯presentation | 源码审计adapter import8、store import3/constructor5、HTTP1+2、subprocess2+2、signal effects3、filesystem70 | namespace exact tuple、architecture-quality R10/F10、research/plan | CLI15定义为legacy presentation/composition bucket；逐tuple只减不增；仅console_output仍为presentation-only |
| F C19 | repo-root pytest不加载pyproject，第二段无`--cov`，无trap | `uv --project`不切cwd；pyproject testpaths/coverage source位于`octoagent/` | testing matrix C19、quickstart、T009/T122 | C19 code block通过`bash -n`；同一transaction、mktemp/trap、octoagent cwd、9 testpaths、两次`--cov`、LCOV nonempty后checker |
| G PYTHONPATH | Phase0/1命令在SDK删除前漏SDK path | tests/AGENTS明确worktree lock含`packages/sdk/src` | testing C01-C22、tasks header、quickstart | 非clean-wheel Python C rows均含SDK path；C09-C11保持`PYTHONPATH=`唯一隔离例外 |
| H exact RGR | tasks只写逐node/Barrier等，Phase0也会临场选择selector | 旧制品没有slice manifest；第四版manifest又从S015开始，未覆盖T001-T013 | `inventories/rgr-slices.md`、tasks | Phase0冻结S001/S002/S003/S004/S007/S008/S011/S013 compound RGR；后续T015-016、T030-033、T040-046、T060-069、T080-089、T100-103也全部有预定义slice/nodeid/layer/oracle/path与exact command encoding；缺失task=0 |
| I Phase2/4顺序 | 先宣称全RGR，后续task才实现GREEN | 原T040-T046与T047-T048、原T083-T089语义冲突 | tasks Phase2/4、rgr manifest | Phase2每个行为在T040-T046同task闭合，T047-T048仅atomic removal；Phase4每个RED同taskGREEN/REFACTOR，T082只做characterization-preserving refactor |
| J 分层/phase gate | 新CP L3放历史顶层test，phase缺C19/C20 | tests/AGENTS规定full lifespan为L3；C18可能跑存量real_llm | testing C18/C22、tasks T029/T034/T049/T070/T090/T105 | C22只选`tests/integration/test_f151_runtime_boundary_flow.py`；六个phase gate均含C20，大production phase含C19；C18不作RGR证据 |
| K evidence/rerun | JSONL自述可伪造，要求全stdout无RERUN误伤存量 | pytest可稳定产JUnit；quarantine已有受治理条目 | contract TDD、rgr manifest、testing anti-forgery、T004-T006/T103 | runner事实=JUnit/raw stdout+stderr/exit/run metadata；负面fixture覆盖一致伪造/mismatch/reorder/selector/collection/skip/rerun；只要求F151 node rerun0+quarantine no-growth |
| L 文档一致性 | contract章节倒序与旧数字/说法残留 | 旧制品跨多文件命中 | spec/research/plan/tasks/contracts/11 inventories/quickstart/trace/checklist/analysis/constitution | contract §11后为§12；指定旧数字/旧ProviderRoute/旧rerun/F150陈述stale scan=0；前一轮review标记为superseded |

## Gate自审补充闭环

| 问题 | 旧制品问题 | 第五版修正 | 机械结果 |
|---|---|---|---|
| Phase0 exact selectors | T001-T013只有class/行为描述，T005/T006/T009/T010声称“同selectors”但未预冻结 | 新增8个Phase0 slice；每个冻结完整nodeid集合、固定命令encoding、唯一RED oracle、production path与跨task phase关系；tasks逐项引用slice ID | Phase0要求slice 8/8存在；node count分别为4/3/4/10/3/2/5/4；T013四节点包含`test_merge_base_low_water_mark_is_enforced` |
| T017 atomic诚实性 | `RED/ATOMIC`同时占用两种证据语义且无exact node | 新增`S017-namespace-atomic`唯一gate node；T017只记录before，T029同node记录after；混合态稳定失败，不把现状称pytest RED | T017任务中`RED`命中=0；atomic gate node=1；before/after/mixed-state oracle完整 |

## 冻结事实

- **D-01=A**：删除`JobSpec`、`ExecutionRuntimeRecord`及真实closure；不建legacy entity/service/registry。
- **D-03=A**：51 source=49 move+2 delete；15 legacy CLI+1 config+33 operations；layers=13/5/9/6；三个有界hash exception。
- Runtime目标：46=3 runtime+43 storage；1141使用storage-only `complete_task_with_precomputed_result`。
- ProviderRoute：只含alias/provider/model/required transport/absolute api_base与env/profile auth reference；Provider-owned headers与per-call body不跨DTO。
- Gateway/Provider direct deps仍为7+25与1+6；四个target_kind入口、whole-batch/error matrix、D-01 closure保持前轮已通过设计。

## TDD / 测试分层 / 架构分层 / 坏味道

- **TDD**：本轮只验证artifact contract，没有伪造实际RED/GREEN。实施runner必须按exact manifest生成结构化证据；atomic relocation/absence不冒充unit TDD。
- **测试分层**：L4负责DTO/model/store/application/selector/unknown projection/benchmark；L3负责full API/audit/start/wheel/tmp Git/raw Event→REST；L1=0、L2=0。C18是可能包含存量real_llm的baseline。
- **架构分层**：Provider→Gateway=0；services/config/application/routes→CLI=0；CLI15存量composition只ratchet；runtime/storage能力XOR，precomputed operation不读bundle。
- **坏味道**：S01-S17 must-fix；R01-R13 no-growth；F01-F10 follow-up。complexity不替代职责/依赖/状态审查。

## 机械自审结果

- HEAD与origin/master均为`9d5e1e48691c5ae5a12b33f224d64ac03d5442fc`。
- production/tests/repo-scripts/workflow status diff=0；cached diff=0；`.gitignore`仍仅先前`.specify/templates/`一行。
- source `provider/dx/*.py`=51；production `TaskService(`=48。
- FR=40连续；tasks=74个ID、唯一、物理单调、全部unchecked；C01-C22连续。
- operations owner=33/33；layer=13/5/9/6；runtime headings=3/43。
- required RGR task ranges（含T001-T013）缺失=0；Phase0 slice node count=4/3/4/10/3/2/5/4，T013 merge-base low-water node=1；T017 exact atomic node=1且`RED/ATOMIC` stale=0；non-prefixed repo-root pytest selector=0。
- F150 protected symbols 7/7与allowed handler baseline 2/2的normalized AST SHA-256均和当前baseline源码匹配。
- C19 `bash -n`通过；`git diff --check`通过；本地Markdown链接在Round5 review创建后缺失=0。
- 指定stale pattern扫描=0；ProviderRoute正向extra field table rows=0。

## 剩余风险 / main复审点

1. exact command采用manifest的固定argv encoding+exact nodeids，实施runner必须逐字节展开并拒绝临场追加参数。
2. CLI filesystem70个tuple在实施gate machine JSON中需完整导出；Design文档冻结逐文件分布与其他direct tuple。
3. 预计算operation必须在真实bundle/background drain后证明所有模型派生调用0且副作用完整。
4. F150 handler-level AST gate要区分D-03机械import重定位，不得扩大semantic allowlist。
5. evidence、coverage与CI wiring仍未实现；当前只能声称artifact validation，不能声称已践行TDD或实现质量通过。

## Gate结论

GATE_DESIGN=false，GATE_TASKS=false。本版只提交main复审；所有tasks保持unchecked，未经明确批准不得进入T001、实现、stage、commit或push。
