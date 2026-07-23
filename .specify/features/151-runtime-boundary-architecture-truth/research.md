# F151 Phase 0 Research Decisions

本文件把[`research/tech-research.md`](research/tech-research.md)与本Feature全部machine/human inventories的代码事实转化为设计决策。D-01/D-03已由main决定。T012 standard-backend checker code review被拒，当前corrective GATE_DESIGN/GATE_TASKS=false。

## Decision 1：CLI 所有权单波迁移，业务职责不随搬迁重构

**Decision D-03**：将`provider/dx`的49个现役模块迁出Provider：15个legacy presentation/composition CLI进入既有`octoagent.gateway.cli`，`config_bootstrap`进入既有`gateway.services.config`，33个backing modules进入`gateway.services.operations`；直接删除`dx/__init__.py`与`runtime_activation.py`。13 application/5 domain/9 store/6 adapter只是legacy mixed operations cluster的role tags，不是physical clean layers；machine baseline只冻结41个import ceiling与147个可解析direct-name call ceiling，它们只减不增但不是完整interaction graph。每个changed operations hunk还必须执行attribute-call检查和人工adversarial responsibility review。`channel_verifier`是application port/registry，真实HTTP在`telegram_verifier`；`secret_refs`是adapter，`wizard_session`是application。允许三个有界职责/T029 exception：Doctor Rich renderer、wizard Click prompt driver、config-bootstrap Click default adapter；后两者进入既有CLI并callable注入，最终services/application→Click/Rich/Questionary/CLI为0。CLI15存量direct adapter/client/store/subprocess/signal/filesystem tuple只可减少，新增F151 seam必须遵守clean direction。完整清单见[`inventories/namespace-migration.md`](inventories/namespace-migration.md)。

**Rationale**：统一塞入CLI会制造下层→presentation反向边；doctor留CLI也会产生operations→CLI边。source-aware投影当前唯一RED是`doctor_remediation→console_output`。eager import图无SCC，包含deferred edge的全图仅有`doctor→secret_service→update_service→doctor`三节点SCC，F151只ratchet不谎称消失。

**Rejected**：CLI shell-only、旧namespace shim、新deployable management package、49个全部塞入CLI、doctor留presentation层、service/config隐式调用Click默认。除三个批准exception外禁止夹带职责重写。

## Decision 2：Gateway 解析 canonical config，Provider 只消费确定 DTO

**Decision**：保留 Gateway `ProviderEntry` 对 v1 `auth_type/api_key_env/base_url` 的现有归一化。Gateway resolver 在alias、默认URL和auth reference校验完成后，向ProviderRouter返回frozen `ProviderRoute`；ProviderRouter删除project-root config load、Gateway import与重复`getattr`兼容。

`ProviderRoute.api_base`与`ProviderRuntime.api_base`均为非空`str`；optional schema URL只在Gateway resolver转required。optional transport也按现有规则闭合：显式值验证，缺省`openai-codex→openai_responses`、`anthropic-claude→anthropic_messages`、其他→`openai_chat`。auth只允许api-key env或OAuth profile reference。

ProviderRoute只含alias/provider/model/required transport/absolute api_base与env-name/OAuth-profile auth reference；真实ProviderEntry没有`extra_headers`/`extra_body`，不得为未来配置新增字段。Provider内置headers由`BUILTIN_PROVIDERS`/`AuthResolver`生成，per-call `extra_body`继续留在现有调用参数。ProviderRouter pinning/invalidation冻结现状：invalidation只逐出client cache，不关闭共享HTTP、不清已pinned Task；旧Task继续旧client，新Task重建，Router shutdown才关闭共享HTTP。

**Rejected**：Provider 复制 schema、`Any` loader、Provider 内 URL fallback、raw authorization/cookie header passthrough。

## Decision 3：Proxy 与 SDK runtime 删除，pricing helper 不越权删除

**Decision**：删除LiteLLM Proxy extra、activation/readiness/state、旧文件路径和`packages/sdk`平行调用路径；同步root manifest与`uv.lock`。保留`cost.py`/`CostTracker`/`CostCalculationError`、Gateway`LLMService._providers`/`register()`/`default_provider`/`LLMProvider`/`LLMResponse`/EchoProvider/MockProvider、F094 migration/audit/rollback与bench entry。

若`cost.py`仍需base`litellm` pricing API，manifest只保留base依赖并证明无turn/client/runtime路径。`ProxyUnreachableError`随Proxy语义删除；fallback tests改用`ProviderError`，benchmark infra分类不再点名该子类并在独立lane运行。

**Rationale**：F151 删除平行 runtime 与虚假兼容，不把“看似未使用”扩张为未授权 API 清理。

## Decision 4：退役输入只在 canonical 边界拒绝

**Decision**：YAML tombstone 在 `OctoAgentConfig.from_yaml()` 的 raw dict、Pydantic validation 之前执行；env tombstone 在 `.env` 加载后、application/Harness 组装前执行；旧文件只按精确文件名 `exists()`，不得打开。完整 key、错误与支持矩阵见 [`inventories/config-retirement.md`](inventories/config-retirement.md)。

`OCTOAGENT_LLM_MODE` 仍支持 unset/empty（Provider direct）与 `echo`（测试），不是 tombstone。仓库根 `octoagent/octoagent.yaml`、example、前端 Settings/提示与 CLI behavior 同步清理，并以 Python 行为测试、Vitest 与 tsc 验收。

## Decision 5：四个 execution 值域彻底分离

**Decision**：

- 四个用户输入面的`target_kind`严格、大小写敏感地接受`worker|subagent|acp_runtime|graph_agent|fallback`，不得trim/coerce/按worker_type重选；profile capability只有前四值；Worker瞬时backend为`graph|inline`；
- 持久化 event decoder 继续读取历史 `inline | docker`；
- Execution Console/API projection 冻结现有 `inline | docker` 输出，Graph 继续投影为 `inline`，真实类型留在 `runtime_kind=graph_agent` metadata。

不新增 `LegacyExecutionRecord`、backend registry 或持久化模型。历史兼容只在现有 decoder/projection 分支内表达。D-01已决定删除无消费者的`JobSpec`/`ExecutionRuntimeRecord`及exports/tests/docs；`container_name`只作历史JSON字段。

## Decision 6：fail-closed 矩阵按失败阶段冻结

**Decision**：三个Control Plane action加一个tool输入面；unsupported delegation值为HTTP422或stable tool reject，Graph/TaskRunner请求期不可用为503/tool reject，启动期invalid在Uvicorn前exit78。`worker.apply`全batch preflight早于cancel/write，`subagents.spawn`早于loop，profile缺TaskRunner不direct-create；CP测试排除audit singleton。Graph执行竞态只终结同一Task一次，action仍COMPLETED。

**Rationale**：客户端输入错误、进程配置错误与服务端运行依赖故障不能共享一个模糊 backend error。

## Decision 7：bundle 通过 storage-only 解构造环

**Decision**：`AgentSessionTurnHook` 先用显式 `storage_only=True` 构造，再形成 SkillRunner 与最终 LLMService，最后在现有 Harness composition root 创建唯一 `RuntimeServiceBundle`。TaskService/AgentContext 必须在 `runtime_services=<bundle>` 与 `storage_only=True` 中严格二选一；缺失或同时提供立即失败，无 `bundle=None` fallback。AgentContext storage-only构造不得创建`MemoryRuntimeService`、不得加载reranker/model、不得注册background task或触发网络。构造点基线48=4 runtime+44 storage；复用Orchestrator 1227/1319两个同方法重复实例并将1141点的deterministic completion改为storage-only operation后，目标46=3 runtime+43 storage。

复用同一个`app.state.background_tasks`并删除chat module task set。普通`process_task_with_llm`只用bundle LLM。现有deterministic non-direct回复与Graph-start失败fallback改用TaskService storage-only正向operation `complete_task_with_precomputed_result`；它调用既有`agent_context_session_replay.py`中拆出的唯一storage persistence primitive，完整保持`ModelCallResult`与Task/Event/Artifact/checkpoint/SessionContext/turn/session副作用，禁止LLM对象、bundle/Router、recall planner、LLM/background compaction与SessionMemoryExtractor。runtime wrapper只在真实runtime路径额外触发extraction，不复制持久化算法。顺序冻结为final drain→LLM/SkillRunner/ProviderModelClient local-only `aclose`→bundle唯一Router `aclose`→stores close，Harness instance guard保证exactly-once。测试迁移由TaskService 144 identities（123 live test/nested含3个F033 skip、20 helper、1 shadowed）与AgentContext31 identities共同驱动；C084用独立machine owner map覆盖44 owner paths并执行42个确定性files+3个exact nodes，不能把helper/fixture qualname当pytest node。

## Decision 8：复杂度 ratchet 采用固定 snapshot + merge-base 双门

**Decision**：扫描固定生产 globs，Ruff JSON 统计 `C901/PLR0911/12/13/15`，Python tokenize 计算 logical LOC，AST 计算最大函数跨度。schema v1 记录 scanner version、rules、paths、总量和热点指标。CI 同时要求：

1. current 不超过 committed F151 ceiling snapshot；
2. PR current 不超过 merge-base 实际值。

编码前真实ceiling已冻结在[`inventories/complexity-ceiling.v1.json`](inventories/complexity-ceiling.v1.json)：total658、六hotspots、Ruff0.15.4与config fingerprint。Phase0在任何production edit前复制/验证；后续write只可收紧。CI只读，merge-base actual锁住下降后的低水位。

## Decision 9：clean-wheel 与 gates 可独立报告

**Decision**：architecture gate提供`import-direction`、`retired-terms`、`complexity --base-ref`、`quality-smells`、`tdd-evidence run|verify`、`all`；formal evidence CLI只接受已冻结的run/verify argv。clean-wheel最终提供`provider`、`gateway --level relocation|full`、`all`。T012的preliminary实现只允许Provider isolated wheel/metadata closure、当前阶段诚实的relocation事实与typed phase deferral；namespace final zero仍由T017-T029拥有，exit69由T045拥有，app-instance/exit78由T064拥有。Proxy/SDK absence在T049闭合后仍只复验C09/C10；首次full/all行为执行固定在T070，不得让checker虚构尚未实现的事实。

T012不得根据pyproject自行组装wheel或合成METADATA。backend必须保持标准Hatchling九wheel与真实METADATA，但“direct dependency AST closure”拆为preliminary inventory与final enforcement：T012只扫描distribution-owned installed files并逐occurrence分类，报告当前delta且`final_verdict=null`；T023拥有manifest/lock，T070消费所有production owners后才严格闭包。

main的`/tmp`九wheel实验仅证明standard backend可行。actual pyproject/uv-generated lock随后已接受；shared venv仍未bootstrap，当前checker字节被拒。隔离report必须直接读取同一child回传的cwd/sys.path/env/site/prefix/origins，不能由parent按命令预期重构。

Gateway clean-wheel从repo外cwd启动并隔离HOME/XDG/user-site/PYTHONPATH/source tree，验证三个CLI。direct import分类与当前Provider/Gateway逐root事实、正负controls见[`inventories/wheel-dependencies.md`](inventories/wheel-dependencies.md)；T012不得把declared-unobserved或observed-undeclared集合置空。

source-only mutating service/update/install与bench在副作用前exit69`SOURCE_CHECKOUT_REQUIRED`；status/logs/help可用。bench entry保留，不打包benchmark tree。

## Decision 10：TDD、测试分层与坏味道独立于complexity

**Decision**：每个行为切片记录稳定RED→同selector GREEN→不改行为的REFACTOR；49-file move诚实标为two-stage atomic relocation。T029从冻结base生成normalized source AST/content、projection与target snapshot；其后target变化只由stable symbol scope+RGR evidence授权，Final复验T029 artifact against base。测试按tests/AGENTS优先L4/L3确定性，F151新增L1/L2预期0；worktree固定PYTHONPATH锁与`python -m pytest`，禁fixed sleep/rerun/宿主状态/真网络。坏味道按must-fix/ratchet/follow-up报告，services→harness 43 nodes/24 files与Harness→main 11 sites只ratchet，禁止big-bang。

**Rationale**：changed-lines≥90%与complexity ceiling只能说明覆盖和数值未恶化，不能证明依赖、职责、状态唯一或测试oracle正确。完整矩阵见testing/architecture inventories。

## Decision 11：Managed update 对用户改动 fail closed

**Decision**：`runtime_descriptor_defaults`不得执行destructive checkout/reset。tracked unstaged、staged或untracked任一存在时，在fetch/merge/uv前返回`LOCAL_CHANGES_PRESENT`，所有危险命令调用为0。普通descriptor load/start/restart对canonical、legacy argv、invalid schema与invalid JSON均为字节级零写；不得自动normalize/save，也不得生成`.corrupted`。旧pull命令和当前destructive descriptor只允许由显式install/update/bootstrap transaction做validated atomic migration；repair必须给replacement与expected digest。L4 fake runner与真实tmp Git repo L3分别证明typed code和HEAD/index/files不变。

## Decision 12：逐模块test owner与高风险并发

**Decision**：operations 33/33均有确定性owner。TelegramStateStore锁外RMW与UpdateService active-attempt TOCTOU因本次直接触达且会复活授权/并发启动，被列为must-fix并独立RGR；backup path职责混杂超出三个批准hash exception，保持follow-up并以4 helpers/13 consumers no-growth。完整表见[`inventories/test-ownership.md`](inventories/test-ownership.md)。

## Decision 13：direct dependency双向精确

**Decision**：Gateway最终manifest为7 internal+25 third-party main，动态`keyring`必须直接声明；Provider最终为1 internal+6 third-party main，新增`python-ulid`，移除DX迁出后无生产消费者的过宽依赖与Provider local-embedding。gate扫描static/TYPE_CHECKING/constant dynamic import并与两个wheel的`Requires-Dist`精确比较。

## Decision 14：证据与coverage拒绝假绿

**Decision**：TDD evidence先用missing/fake/reordered/selector mismatch/collection error/skip/rerun、JSONL/raw一致伪造但JUnit不一致、formal alias/root/缺件等负面fixtures验证checker，再以canonical `junit.xml/stdout.txt/stderr.txt/exit-code.txt/invocation.json/tree.json`六件套与index交叉校验RGR；Cxx只作phase regression。Phase0 T001-T004只用标准pytest/Vitest与冻结shell transaction生成同名六件套，完成后必须硬停`PHASE0_RED_REVIEW`；main锚定artifact SHA前不得进入T005，T005只能验证已锚定字节，不能事后替换。既有changed-lines checker增加`local-working-tree`模式覆盖tracked最终态和untracked production，CI保持committed模式；Verify由T122以自身stage/start tree/UTC新跑fresh C19-post且不能复用T105，EXEMPT不能满足F151的≥90 PASS。architecture job与backend coverage artifact流保持独立；final另跑`-n auto --dist=loadgroup`，要求F151新增/修改node rerun=0与quarantine no-growth，既有登记rerun单列报告。

## Decision 15：production/service启动入口唯一且读取无隐式写

**Decision**：`python -m octoagent.gateway`是唯一production/service启动入口。wrapper、install bootstrap/runtime descriptor、service install/update生成argv与active docs/tests全部迁到该入口；`import octoagent.gateway.main:app`只保留ASGI/import/test contract。module entry先解析并验证唯一支持的`--help/--host/--port`（duplicate/unknown exit64），然后在typed exception boundary内只import一次`main.app`；`main.app=create_app()`仍是唯一app构造与canonical preflight，config/exposure各执行恰一次。Uvicorn只能接收该app instance与同一解析出的host/port值，禁止module string。普通descriptor load/start遇legacy direct Uvicorn argv只typed reject并给迁移指引，0写、0serve；只有显式install/update/bootstrap可validated atomic migration。invalid config/security在Uvicorn前exit78。

## Decision 16：machine scope不追索未来阶段

**Decision**：RGR/scope=98/98；S011/S012/S070的21个clean-wheel selectors唯一且互斥。root pyproject/lock用三态`dependency:` transition分区；resolver R/G/R后的fresh semantic revalidation已PASS、unresolved=0。新增import inventory、child observation与T070 final closure各有独立owner；重复/别名hunk、跨阶段提前追索或无owning RGR evidence均失败，其他ownership与planned-diff边界不变。

## Decision 18：Provider test rehome universe与operation mode machine allowlist

**Decision**：Provider test rehome由“所有collectable Provider tests中直接import Gateway或其production owner迁入Gateway”重算为44 move+1 retired delete；非pytest wire recorder另以exact exception移除Gateway dotenv-loader import，不能作为collectable test计数或保留反向依赖。TaskService/AgentContextService的42个public/production-called-private operation、45个TaskService target构造点与3个direct AgentContext构造点由`runtime-operation-modes.v1.json`全量分类；unknown operation/callsite默认拒绝，storage-only call graph可达model/reranker auto-load/background/network/runtime任一即失败。清单只是gate artifact，不是runtime registry/service。

## Decision 19：活动制品与documentation evidence

**Decision**：`active-artifacts.v1.json`精确区分42个current artifacts与6个Round4-9 superseded review；两者都是S002拥有的planned governance paths，仅current可满足Round10 authority。`artifact-lifecycle.v1.json`管理Design→Phase0-RED→Implement→Verify→Final转移；4个Final必需committed paths各有唯一first-state/first-writer task/producer command，`evidence-producers.v1.json`使每类producer output与lifecycle exact set双向相等。F151 active/local evidence必须进入phase closure，不得被archive或`.gitignore`吞掉；changed-path只减去hash不变的既有`.gitignore`用户patch，其他Feature与`docs/design`变化均失败。constitution、Blueprint与codebase architecture的实施后同步由独立`S104-docs` RGR拥有，从索引反算并扫描`authority-docs.v1.json`的17个exact documents；不得用更早的S100 workflow GREEN覆盖后续文档hunk。

## Decision 17：测试构造点与duplicate qualname如实建模

**Decision**：144个`TaskService(...)` identities为123 live test/nested（含F033三处skipped constructors）+20 helper+1 dead-shadowed；44个LLM override为43 collected-live+1 shadowed。identity含`definition_ordinal`。T084先rename shadow，再把F033两条skip重写为确定性行为节点，并用reverse-call证明helper coverage。完成态144/144 behavior join、skip constructor0、duplicate0、unknown mode0、override0。

## 数据与回滚

- 不新增表、event schema 或 migration。
- 不读取、复制或自动迁移 `.env.litellm` secret；用户通过迁移后的 `octo auth`/`octo setup` 重新授权后自行删除旧文件。
- 降级只支持operator在F151外维护的filesystem snapshot/旧版本环境，不指Octo backup bundle；F151不生成旧secret文件。
- DX 与 SDK 是无 shim 的 breaking removal；旧 Provider v1 config 则继续由 Gateway canonical schema 归一化。

## Gate 状态

D-01/D-03均已决定，不再有产品选项待main选择。Design/Tasks Gate仍未批准；源码数量、三个exception、SCC或must-fix scope发生漂移必须先重新过Gate。
