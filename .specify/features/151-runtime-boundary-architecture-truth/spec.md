# Feature Specification: F151 Runtime Boundary & Architecture Truth

**Feature Branch**: `codex/f151-runtime-boundary`
**Created**: 2026-07-20
**Status**: T001-T016完成；ProviderRoute、Gateway resolver与ProviderRouter seam已闭合；T017 atomic未开始
**Input**: F151 milestone、architecture-audit §14.14、AGENTS.md、constitution、driver config

## User Scenarios & Testing

### User Story 1 - 安装事实与依赖方向一致（Priority: P1）

作为发布维护者，我可以从本地 wheels 在干净环境安装 Provider 或 Gateway；Provider 不依赖 Gateway，Gateway 明确声明自己的直接依赖并提供受支持的产品 CLI 和 application host。

**Independent Test**：仓库外 cwd、隔离 HOME/XDG/user-site/PYTHONPATH/source tree，以非 editable wheel 分别验证 Provider-only import 与 Gateway CLI/start/readiness。

**Acceptance Scenarios**：

1. **Given** Provider-only wheel 环境，**When** 导入 Provider，**Then** 成功且 Gateway 不可发现、未进入 `sys.modules`。
2. **Given** Gateway wheel 环境，**When** 执行 `octo --help`、`octo doctor --help`、`octo auth --help`，**Then** 均成功且入口由 Gateway 提供。
3. **Given** Gateway 以 non-Echo canonical provider config 启动，**When** 请求 core readiness，**Then** 只验证本地 stores/artifacts/mandatory services 与 alias resolution，不发模型网络请求。
4. **Given** source-managed service/update/install 或 benchmark 在 wheel 环境不可用，**When** 用户调用，**Then** 返回稳定 `SOURCE_CHECKOUT_REQUIRED`，不伪装成功。
5. **Given** 任一wrapper、service descriptor或update restart路径，**When** 启动Gateway，**Then** 全部只进入`python -m octoagent.gateway`；entry先解析exact argv，再只import一次`main.app`，由`main.app=create_app()`执行唯一preflight并把app instance交Uvicorn。普通read/start/restart对canonical/legacy/invalid descriptor均字节级0写，legacy/invalid typed reject。

### User Story 2 - 用户只接触一条真实模型与配置路径（Priority: P1）

作为 Octo 用户，我通过迁入 Gateway 的同一个 `octo` CLI 管理模型与凭据；系统不再读取或管理 Proxy、旧专用 env 文件或 SDK 平行 Agent loop，同时继续接受 Gateway 已支持的 Provider v1/v2 配置。

**Independent Test**：运行 config/OAuth/backup/CLI/frontend 行为测试和 retired gate，验证 ProviderRouter 唯一 turn/transport 路径及旧文件不被打开。

**Acceptance Scenarios**：

1. **Given** v1 `auth_type/api_key_env/base_url` 或 v2 `auth/api_base/transport`，**When** Gateway 加载配置，**Then** canonical resolver 生成等价、secret-safe、`api_base` 非空的 ProviderRoute。
2. **Given** ProviderRouter 创建或失效 client，**When** 同一 Task 重用 alias 或配置刷新，**Then** task-scope alias pinning 与 client invalidation 语义保持不变。
3. **Given** 旧专用 env 文件存在，**When** 应用启动，**Then** 只检测文件存在，exit 78，且不读取、复制、迁移或备份其内容。
4. **Given** Settings UI 或 behavior/config action，**When** 用户保存设置，**Then** payload 与提示不再包含旧 runtime/Proxy 输入。

### User Story 3 - 运行选择与安全错误按责任边界拒绝（Priority: P1）

作为部署者，我能区分 Worker runtime selector、历史 event decoder 与 Console/API projection；不支持的用户输入、安全配置错误和服务端依赖失效分别得到稳定、可诊断且不执行 Inline 的错误。

**Independent Test**：覆盖 422、503、exit 78 与已有 Task 内部 dispatch error；逐格断言 error code、Task/Event 创建与 Inline 调用计数。

**Acceptance Scenarios**：

1. **Given**用户通过三个现役Control Plane action（`work.split`、`worker.spawn_from_profile`、`worker.apply` assignments）提交`target_kind=docker`、大小写/空白变体、显式空值、非string或枚举外值，**When** action执行，**Then**返回422`WORKER_RUNTIME_SELECTOR_UNSUPPORTED`；只保留本次REQUESTED+REJECTED审计，业务Task/child Task/Work/业务Event/cancel/Inline/Graph execute均为0。
2. **Given** `worker.apply` mixed assignments 或 `subagents.spawn` batch的后项无效/Graph不可用，**When**整批preflight，**Then**在descendant cancellation或首个child创建前原子失败；tool入口返回稳定tool rejection且Control Plane audit为0。
3. **Given** 同一现役action选择合法Graph但preflight缺失运行依赖或mandatory TaskRunner，**When** action执行，**Then** 返回503`WORKER_RUNTIME_UNAVAILABLE`，业务副作用为0且Control Plane audit=2；`worker.spawn_from_profile`不得fallback到`TaskService.create_task`。
4. **Given** 启动期同步可解析的static security/runtime 配置无效，**When** `main.app=create_app()`执行唯一canonical preflight，**Then** Uvicorn前exit 78并使用矩阵规定的typed code；即使front-door env override存在也必须先完整解析YAML恰一次，新Task/Work/Event/backend均为0。
5. **Given** 请求期 security 配置源失效，**When** 请求受保护资源，**Then** 返回 503 `FRONT_DOOR_CONFIG_INVALID`，新Task/Task-domain Event/audit Event/Inline 均为 0。
6. **Given**历史event backend为`docker`，**When**Console回放，**Then**仍投影兼容输出；`register_session`删除公开backend参数并固定新写inline，历史fixture直接注入raw event。
7. **Given** Worker Graph 执行，**When** Console 投影 session，**Then** backend 仍为 `inline`，metadata 保留 `runtime_kind=graph_agent`；preflight后竞态失效只使同一Task FAILED一次且Inline=0。
8. **Given** static config已通过但真实runtime service composition/assembly失败，**When** FastAPI lifespan启动，**Then** 现有OctoHarness composition root以nonzero startup failure fail closed，readiness/request/Task/Work/Event/backend均为0；不伪装为static exit78且不新增第二composition validation。

### User Story 4 - Runtime services 有显式实例所有权（Priority: P2）

作为维护者，我能沿 Gateway composition root 追踪最终 LLM、ProviderRouter 与现有 background registry；AgentContext/TaskService 不依赖 class-level setter 或隐式 fallback，关闭顺序明确且 exactly-once。

**Independent Test**：构造两个 Harness，验证 bundle identity、storage-only fail-fast、同一 background set、两阶段 drain 与 close count/order。

**Acceptance Scenarios**：

1. **Given** storage-only AgentSessionTurnHook，**When** SkillRunner 和最终 LLM 构成，**Then** composition root 随后创建唯一 bundle，不使用 mutable late binding。
2. **Given** AgentContext 或 TaskService，**When** 构造，**Then** `runtime_services` 与 `storage_only=True` 必须严格二选一；缺失或同时提供立即失败。
3. **Given**48个production TaskService构造点基线，**When**逐项检查，**Then**Orchestrator两组同方法“先append、后process”的重复实例被复用，最终为46个：3个bundle-bearing与43个pure-storage；`_dispatch_inline_decision`只使用storage-only预计算结果operation，chat/message缺TaskRunner在创建Task前503，不直跑第二条LLM路径。
4. **Given**Orchestrator已经确定的non-direct回复或Graph启动失败fallback，**When**完成Task，**Then**窄application seam直接持久化预计算`ModelCallResult`，exact content/provider与标准Task/Event/Artifact/checkpoint副作用保持，bundle/Router模型调用数为0。
5. **Given** shutdown 重复触发，**When** 完成 final drain，**Then** LLM/SkillRunner local `aclose`、ProviderRouter `aclose`、stores close 依序各执行一次。

### User Story 5 - 架构真值不会再次恶化（Priority: P2）

作为 reviewer，我可以独立运行 import direction、retired terms、complexity 和 clean-wheel gates；CI 能基于固定 snapshot 与 merge-base 低水位机械判断复杂度是否恶化，文档与实现保持一致。

**Independent Test**：对每个 gate 注入违规 fixture；对当前仓库运行独立子命令、CI path filter tests、docs/constitution scan 与 full gates。

**Acceptance Scenarios**：

1. **Given** Provider 新增静态、TYPE_CHECKING、动态或模块字符串 Gateway 引用，**When** 运行 `import-direction`，**Then** 精确定位并失败。
2. **Given** 活跃范围重新出现退役输入/路径，**When** 运行 `retired-terms`，**Then** 失败；历史 decoder/tombstone 仅按精确 path+purpose 允许。
3. **Given** 任一 complexity 指标超过 committed ceiling 或 merge-base actual，**When** 运行 `complexity --base-ref`，**Then** 失败；CI 不写 snapshot。
4. **Given** 只修改 docs 或 constitution，**When** PR workflow 评估路径，**Then** architecture gates 仍会执行。
5. **Given** 行为实现或架构迁移，**When** reviewer 检查证据，**Then** 每个切片都有稳定 RED→GREEN→REFACTOR ledger；atomic relocation 以manifest/hash/absence/import contract验收且不伪装成单元TDD。
6. **Given** 新测试或职责改动，**When** 运行质量门，**Then** 测试层、依赖层与坏味道均按冻结inventory独立报告，complexity或coverage不能替代职责审查。

## Edge Cases

- `OCTOAGENT_LLM_MODE` 的 unset/empty 与 `echo` 是现役测试/Provider-direct 语义，不得被 Proxy tombstone 误杀；其他非空值以 `LLM_MODE_INVALID` 拒绝。
- YAML tombstone 必须在 raw dict 阶段命中，不能依赖空 Pydantic model 的 unknown-field 行为。
- `container_name` 是历史 event JSON 字段，不是现役容器能力；保留它不应让 retired gate 失败。
- `ProviderRoute.api_base`必须是absolute `http`/`https` URL；允许path，拒绝userinfo、query、fragment和control characters。
- ProviderRoute不暴露`extra_headers`或`extra_body`：内置静态/动态headers继续由Provider的`BUILTIN_PROVIDERS`/`AuthResolver`生成，per-call `extra_body`继续是现有调用参数。
- ProviderRoute resolver 的 optional schema URL 必须在 Gateway 内默认化/验证；ProviderRuntime 不接受 `None`。
- `.env.litellm` 即使只含必须 secret 也不得读取；错误只提示重新授权命令，不回显内容。
- `JobSpec`/`ExecutionRuntimeRecord` 已由 D-01 明确删除；历史兼容只存在于raw Event decoder/projection。
- root pytest 不收集 benchmarks；相关异常删除测试必须在独立 benchmark lane 运行。
- 合法 no-op（幂等提交、测试 seam）与 F094/Echo/Mock/legacy registry 不得因宽泛关键词被删除。
- F151 新增测试与自动 Verify 不得访问网络/真 LLM/宿主 `~/.octoagent`、宿主凭证或产生外部成本；固定 sleep、blanket rerun 也不得作为通过手段。F151 不注册 `lane.py baseline`/live lane，凭证存在、默认 HOME 或 skip 均不构成授权；L1/L2 不得重复验证 L4/L3 已覆盖的业务分支。
- 纯机械 namespace move 不制造虚假 RED；其失败 oracle 是 target absence、old namespace存在、hash或entrypoint contract不满足。

## Requirements

### Functional Requirements

- **FR-001 [必须]**：按已批准D-03 machine source→target manifest将49个`provider/dx`模块迁出Provider：15个CLI、1个config、33个operations，删除2个文件且不留shim。13/5/9/6只作legacy mixed cluster role tags；41个import/147个可解析direct-name call只是exact ceiling而非完整interaction graph，只减不增。每个changed operations hunk另做attribute-call/职责adversarial review；domain保持纯净，新F151 seam执行clean direction。
- **FR-002 [必须]**：Provider production source、manifest、TYPE_CHECKING、dynamic import、module/subprocess strings 对 Gateway 的引用必须为零且无 allowlist；活跃全仓旧 namespace 引用为零。
- **FR-003 [必须]**：Provider tests严格按`provider-test-rehome.v1.json`完成44项collectable test迁移+1项删除；manual wire recorder是唯一exact非pytest例外，必须原地移除Gateway dotenv-loader import并要求caller env，完成态Provider test tree→Gateway依赖为0。Gateway21、root integration2、Memory1、root gate1及production/scripts/benchmarks/docs/entrypoints完整迁移。T021/T029/stage/constructor identity先投影最终路径；Phase4退休Provider test路径为0。三文件六auth nodes改absolute并删除dead import。
- **FR-004 [必须]**：Gateway 保留 canonical loader 对 v1 `auth_type/api_key_env/base_url` 和 v2 schema 的既有归一化；ProviderRouter 删除 Gateway loader import、project-root fallback 与重复 `getattr` schema compatibility。
- **FR-005 [必须]**：Gateway resolver必须生成frozen、secret-safe ProviderRoute；`api_base`为absolute http/https且允许path、拒绝userinfo/query/fragment/control chars；optional transport按现有三分支映射为required；auth仅使用既有regex验证的api-key env name或canonical OAuth profile reference，不含anonymous、`SecretStr`或raw credential。DTO不得增加真实`ProviderEntry`不存在的`extra_headers`/`extra_body`；Provider内置headers与per-call body继续留在现有Provider/runtime调用边界。
- **FR-006 [必须]**：ProviderRouter保持task-scope alias pinning与invalidation现状：只逐出client cache，已pinned Task继续旧client，新Task重建，共享HTTP只在Router shutdown关闭。
- **FR-007 [必须]**：direct dependency truth分阶段执行。T012只验证source manifest与标准backend真实wheel `METADATA`一致，并从被评估distribution的installed file set逐occurrence分类四种生命周期context：`runtime-required/optional-lazy/type-checking/test-plugin`；workspace ownership由正交`workspace_owner`记录。每条literal occurrence另有`ownership_state=resolved|unowned`：resolved只接受transaction target exact RECORD，或项目解释器purelib exact RECORD且Name/Version与worktree lock一致；workspace只能来自target。unowned保留完整file/line/syntax/root/context且owner字段为null；ambiguous、损坏RECORD与nonliteral动态边失败。T012必须如实输出unowned exact projection/count、当前manifest/runtime delta、`final_verdict=null`与final owner T070，不要求pre-relocation closure。T023拥有Gateway/Provider manifest+lock目标；T070最终要求unknown/unowned/missing/unexpected=0。
- **FR-008 [必须]**：Gateway clean-wheel必须隔离HOME/XDG/user-site/PYTHONPATH/source cwd，验证三个supported CLI、唯一`python -m octoagent.gateway` host、structural readiness、SIGTERM与Uvicorn前exit78。entry先解析help/host/port，再在typed boundary只import一次`main.app`；`main.app=create_app()`是唯一app/preflight，config/exposure各恰一次，Uvicorn接收app instance及exact-equal host/port；active service argv不得直接执行`main:app`。
- **FR-009 [必须]**：wheel中`service install|uninstall`、`update|restart|stop`、install-bootstrap和bench必须在副作用前exit69`SOURCE_CHECKOUT_REQUIRED`；status/logs/help可用，bench entry保留且不打包source tree。
- **FR-010 [YAGNI-移除]**：删除LiteLLM Proxy extra、activation/state/readiness、旧文件loader/writer/backup/migration、CLI`config sync`、builtin`config.sync`/result model、setup失真字段与current docs/UI声明；`litellm_env_names`单波改为`provider_env_names`无双字段。
- **FR-011 [YAGNI-移除]**：删除 `packages/sdk` 平行 Agent/raw HTTP/LiteLLM/tool loop 及 workspace、lock、coverage、security exemption 和 current docs 引用；不提供 SDK shim。
- **FR-012 [保留边界]**：保留`cost.py`/`CostTracker`/`CostCalculationError`、`LLMService._providers`/`register()`/`default_provider`/`LLMProvider`/`LLMResponse`/EchoProvider/MockProvider、F094 migration/audit/rollback与bench entry；base LiteLLM只能pricing。`ProxyUnreachableError`明确删除，fallback/benchmark改用`ProviderError`。
- **FR-013 [必须]**：exact tombstone按source/key/stage/action执行；YAML在`from_yaml()` raw dict，env在dotenv后assembly前；两个legacy files只`exists()`。旧文件存在时仅`octo auth/setup`可不读旧文件完成canonical reauth，普通start exit78。
- **FR-014 [必须]**：清理 `octoagent/octoagent.yaml`、example、`.env.example`、CLI behavior 与前端 Settings/shared 现役旧输入；用 Python 行为测试、Vitest 与 tsc 验收。
- **FR-015 [必须]**：四个用户输入面严格、大小写敏感地接受`worker|subagent|acp_runtime|graph_agent|fallback`，仅字段缺省使用入口默认；不得trim/lower/coerce或按worker_type自动重选。profile capability独立四值且不含fallback，Worker transient backend独立`graph|inline`，Console/Event独立`inline|docker`历史值，四域不得共享一个enum。
- **FR-016 [必须]**：三个Control Plane action的unsupported selector显式映射422`WORKER_RUNTIME_SELECTOR_UNSUPPORTED`，Graph/TaskRunner不可用映射503`WORKER_RUNTIME_UNAVAILABLE`；验证在Control Plane audit envelope内发生，按request_id审计恰为REQUESTED+REJECTED，业务Task/child Task/Work/业务Event/cancel/backend均为0。`worker.apply`在cancel/merge/write前全批预检，`worker.spawn_from_profile`缺TaskRunner不得direct-create；测试预热/排除`ops-control-plane` singleton。
- **FR-017 [必须]**：`subagents.spawn`在batch loop前selector/runtime preflight并用稳定tool rejection，拒绝时Control Plane audit/spawn audit/child副作用均为0；persisted decoder继续读历史`inline|docker`，`register_session`删除公开backend参数并固定新写inline，raw Docker经GET回放且unknown history稳定失败；删除`JobSpec`/`ExecutionRuntimeRecord`及exports/tests/docs，不新增history entity/service/registry。
- **FR-018 [必须]**：受支持application-host入口只对同步可解析的static config承诺Uvicorn前exit78：security invalid→`GATEWAY_SECURITY_CONFIG_INVALID`，runtime/retired/unknown/root application config invalid→`GATEWAY_RUNTIME_CONFIG_INVALID`。`_resolve_front_door_mode`必须先无条件完整`load_config(project_root)`恰一次，再应用env>YAML mode；env override不得绕过无效YAML。真正runtime service composition失败仍只由现有lifespan/OctoHarness root以nonzero startup failure fail closed，readiness/request/workload副作用0，不映射exit78、不重复构造runtime。
- **FR-019 [必须]**：request security invalid返回503`FRONT_DOOR_CONFIG_INVALID`；Graph依赖或mandatory TaskRunner请求期preflight失败返回503`WORKER_RUNTIME_UNAVAILABLE`，在所有workload副作用前完成；Control Plane仅保留两条审计event。
- **FR-020 [必须]**：Graph preflight成功后执行竞态失效返回`WorkerBackendUnavailableError`，只给同一既有Task写一次FAILED transition，不新建Task，Inline=0；action审计保持REQUESTED+COMPLETED，Console仍投影inline+`runtime_kind=graph_agent`。
- **FR-021 [必须]**：RuntimeServiceBundle只持有final LLMService、ProviderRouter与同一个`app.state.background_tasks`；删除chat module-level LLM task set，不吸收拥有独立lifecycle的scheduler/stores/ToolDeps。
- **FR-022 [必须]**：AgentContext/TaskService在bundle-bearing与storage-only严格XOR；production目标45=3+42。`runtime-operation-modes.v1.json`必须机器枚举全部public/production-called-private operation、允许mode/capability及production constructor callsite universe；unknown默认拒绝，storage call graph触达MemoryRuntime/reranker/model/background/network失败。TaskService测试144 identity与AgentContext测试31 identity（目标23 storage-only/8 runtime）均机器唯一并投影final path；C084 machine behavior-owner map与44个constructor owner paths双向相等，运行42个确定性test files、1个live-helper替代L4 node与F033两个exact nodes，selected>0且fail/error/skip/rerun0。
- **FR-023 [必须]**：storage-only Hook解构造环；普通模型路径只用bundle identity。预计算seam复用Task/Event/Artifact/checkpoint原语，并在既有`agent_context_session_replay.py`拆出唯一storage persistence primitive保存SessionContext/turn/session；runtime wrapper才额外触发extraction。预计算路径extraction/model/Router/recall/compaction=0，删除fake/generic override且保持deterministic语义，不新增service/runtime/registry。
- **FR-024 [必须]**：复用两阶段drain；ProviderModelClient/SkillRunner/LLMService local-only幂等`aclose` chain→bundle唯一Router `aclose`→stores；Harness有instance guard/lock，各资源exactly-once。
- **FR-025 [必须]**：architecture gate提供独立`import-direction`、`retired-terms`、`complexity --base-ref`、`quality-smells`、`tdd-evidence run|verify|verify-bootstrap|recover-index`、`all`与`finalize-verification`。formal run/verify必须显式mode/base/evidence-index；Phase0唯一bootstrap anchor通道仍是`verify-bootstrap --bootstrap-anchor-file <exact-path> --bootstrap-anchor-sha256 <64-lower-hex>`，无env/default/聊天解析替代。`recover-index`只是main批准后的同runner恢复状态机，不是第二runner：除精确SHA外必须显式消费`--main-review-message-id`并与corrective aggregate绑定；runs rename、v1 rename、v2 temp/fsync/replace中断均可用完全相同argv重入，未知混合态0写失败，不声称跨路径atomic rollback。`verify --mode committed`必须在读取base→HEAD前拒绝fingerprint scope内任一staged/unstaged/untracked路径；只有exact generated evidence outputs可排除，clean tree才允许继续。T006窄纠正只扩展既有`run`的RED采用参数，不新增subcommand/runner：它一次验证main已复审的dirty与index-integrity两组RED、canonical combined aggregate和唯一review ID后原子追加两条RED record，prior 20 records与12 runs逐字节不变；错误或重入不得重复、重排或部分采用。`all`必须解析并把同一resolved `base-ref`交给base-aware subgates，missing/unresolvable失败。`finalize-verification`只以前置T120-T123与T124 input closure为条件；C25成功才形成T124 report，不自依赖，任一失败保持output不存在或原字节不变。clean-wheel最终提供`provider`、`gateway --level relocation|full`、`all`（固定full）并独立报告；T012只交付`provider`、诚实的`relocation`事实与对尚未到期`full/all`的typed phase deferral，不得提前执行T029/T045/T064拥有的行为；T070完成对应owners后才首次启用`full/all`与final direct closure。T012 dependency selector RGR已让`check_rgr_selectors(repo, scope)`用tomllib解析worktree与base-ref/merge-base的pyproject/lock，机械验证Hatchling absent→exact dev `1.29.0` add与SDK present→absent delete的非空、互斥semantic delta；fresh revalidation已从历史unresolved=2降至0。wheel事实必须来自项目声明的标准`hatchling.build` backend：root dev exact pin与标准`uv lock`结果已落地，checker须从9个真实wheel archive读取METADATA，再以transaction-local `uv pip install --offline --no-deps --target`安装。T012 preliminary只要求source manifest=wheel METADATA、distribution-owned installed imports逐occurrence分类完整、当前delta诚实输出且`final_verdict=null`；同一真实child必须输出cwd/sys.path/env/site/origins，parent不得推断。T023拥有最终manifest/lock，T070验证Provider=1+6/Gateway=7+25；手写wheel/METADATA/RECORD、target-wide扫描、availability冒充import evidence、unknown context、推断child facts、source/editable或宿主状态泄漏均失败。Hatchling不得进入Provider/Gateway runtime `Requires-Dist`。
  Final mode clarification：`finalize-verification`必须显式选择mode；C25固定`--mode local-working-tree`消费未stage/commit的最终Feature态。普通committed mode继续拒绝相关dirty worktree；这不改变T120-T123/T124 input closure、自依赖排除或失败0写合同。
- **FR-026 [必须]**：complexity ratchet使用已冻结真实v1 snapshot（total658、六hotspots、Ruff0.15.4、config fingerprint）、fixed ceiling+merge-base actual双门；Phase0 production edit前落地，write只能收紧，CI永不写。
- **FR-027 [必须]**：tests-first早于wiring；先用workflow/pre-commit/lane contract RED test证明缺失，再修改wiring并用同selector GREEN。architecture gate在docs fastpath前，并有独立GitHub job按PR base SHA/push-before SHA运行，不依赖lane/backend coverage artifact；paths覆盖docs/constitution/artifacts，benchmark另设lane。
- **FR-028 [必须]**：首次完整 clean-wheel 只能在 Proxy/SDK manifest、lock 与旧文件 absence 路径清理完成且namespace、source guard、startup owners均已闭合后运行；T012较早阶段仅称 preliminary relocation gate，T049只复验Provider/relocation前置，首次`full/all`行为执行固定在T070。
- **FR-029 [必须]**：constitution、Blueprint、实现级架构、README/scripts/skills 同步最终事实；`authority-docs.v1.json`从Blueprint与实现索引反算候选并精确列17份active authority documents（含`api-and-protocol.md`、`architecture-tradeoffs.md`）并全部做semantic scan，而非只扫planned changed docs。历史陈述仅在明确标为历史/已退役时允许；当前/必选/✅表格/Mermaid运行链中的Proxy、物理kernel/worker package、Provider LiteLLM client或current Docker backend均失败。当前事实固定为ProviderRouter/direct transports、单Gateway runtime、无物理kernel/worker package、Docker仅历史Event decode/projection；SQLite为SoR，FTS/LanceDB是并列可重建索引。文档hunk由独立`S104-docs` RGR拥有，不能用`S100-workflow` GREEN冒充。
- **FR-030 [禁止]**：不得新增 management/kernel/worker package、第二 runtime/Provider/config/compat path，不得修改 F149/F150 功能代码，不得 big-bang 重写 TaskService/Orchestrator。
- **FR-031 [必须]**：每个阶段完成后运行定向测试；最终自动 Verify 的完整并行pytest与all transaction仅使用确定性C23/C24，并要求clean-wheel、import-direction、retired-terms、complexity、前端与benchmark适用lane全绿。F151不执行或登记C18/`lane.py baseline`，不得从凭证存在、默认HOME或skip推定live授权；若未来另行提出真实LLM/外部成本检查，必须由main先核对条件并取得用户当次明确授权，且不能冒充本Feature的自动证据。
- **FR-032 [必须]**：每个行为切片必须先以精确命令取得目标行为缺失导致的稳定RED，再实施到GREEN，最后REFACTOR并重跑定向与架构门。证据生命周期按Design/Phase0-RED/Implement/Verify/Final精确管理；正式Python/Frontend RGR只写`evidence/local/runs/<slice>/<phase>/`的`junit.xml/stdout.txt/stderr.txt/exit-code.txt/invocation.json/tree.json`六件套，缺件、多件、`.bin`、`run.json`或非canonical root均失败；raw/JUnit/LCOV不提交但保留可审查字节，提交anchor/index hash，未知或错误phase/type/path即使被Git忽略也失败。机械迁移必须声明atomic relocation：T029保存从冻结base重算的normalized AST/content、source→target projection、target snapshot/可验证patch、hash/absence/import与三个exception；其后target变化只由stable symbol scope+RGR evidence授权，Final复验T029 snapshot against base。不得伪装成TDD或以当前target raw hash冒充T029状态。
- **FR-033 [必须]**：测试严格按`octoagent/tests/AGENTS.md`落层：纯逻辑/model/store/service/adapter为L4，bootstrap/API/Event/storage/LLM派发为确定性L3，浏览器独有语义才用L1，真实判断力/外部事实才用release/manual L2；worktree使用PYTHONPATH锁、`--no-sync python -m pytest`，禁网络、真LLM、宿主状态、宿主凭证、外部成本、固定sleep和blanket rerun。manual L2若进入其他Feature，必须有main检查和用户单次授权，不能由环境状态隐式授权。
- **FR-034 [必须]**：新F151 seam依赖方向固定为domain/model/contracts→service/application与adapter/infrastructure→composition root/UI。D-03存量混边不冒充clean layers；machine inventory只覆盖import/direct-name ceiling，changed hunks必须追加attribute-call与manual adversarial responsibility review。不得以该ceiling声称完整interaction coverage，也不批量引入无真实多实现价值的ports。
- **FR-035 [必须]**：坏味道审计按must-fix/ratchet/follow-up三类输出并进入每阶段REFACTOR与Gate Review；must-fix未清零或ratchet恶化即失败，follow-up不得借F151做big-bang。complexity和changed-lines coverage不得替代职责、依赖和测试oracle审查。
- **FR-036 [安全-必须]**：managed update在任何fetch/checkout/reset/merge/`uv sync`前以完整porcelain状态检查tracked unstaged、staged与untracked；任一存在即typed `LOCAL_CHANGES_PRESENT`且所有上述命令调用为0。删除destructive descriptor并归一化已持久化旧/当前危险命令；L4与真实tmp Git repo L3均证明HEAD/index/文件不变。
- **FR-037 [必须]**：33个operations backing modules全部拥有AST+collect验证的direct/indirect deterministic owner；scheduled/planned不得冒充covered且Verify=0。BackupAudit durable events需direct L4；六个planned owners、setup adapter fake-only、secret/sleep owner失真全部闭合。stores覆盖roundtrip/corruption/atomic/concurrency，application使用DI fake并断言持久化结果；其余must-fix/ratchet/SCC边界不变。
- **FR-038 [必须]**：扩展既有changed-lines checker而不新增第二脚本：committed模式保持`base...HEAD`，`local-working-tree`模式合并HEAD、staged、unstaged与untracked production新增文件；EXEMPT独立报告。Verify由T122以exact `F151_COVERAGE_STAGE=T122`重新执行C19-post并绑定本次start UTC、HEAD/tree、worktree fingerprint，禁止复用T105，再检查Python changed-lines≥90%；frontend仅以完整Vitest/tsc验收。
- **FR-039 [必须]**：RGR evidence checker先有missing/fake/reordered/selector-mismatch/collection-error/skip/blanket-rerun、JSONL/raw一致伪造而JUnit不一致、canonical evidence路径/名字/六件套与producer/lifecycle集合不一致等负面fixtures；Pytest JUnit parser必须接受single/nested `testsuite`并聚合tests/failures/errors/skipped，与testcase count交叉，missing/malformed/failure/error/skip/rerun均fail closed。Phase0 main创建的唯一`evidence/bootstrap-anchor.v1.json`与36个raw保持逐字节不可变。bootstrap/corrective/formal的`tree.json`都采用与immutable raw相同的12字段exact schema，record逐字段交叉slice/phase/base-ref/base/head/tree/fingerprint scope/files/status/captured UTC。canonical index顶层、record与`invocation.json`使用exact required schema；每条record必须含唯一`record_sha256`和`previous_record_sha256`，按canonical JSON形成从确定性genesis到`chain_head_sha256`的不可断链。record identity至少为`lifecycle_type+task_id+slice_id+phase`；前20条固定为已接受的8 RED+6 GREEN+6 REFACTOR，T006纠正尾链严格为两条RED、两条GREEN、两条REFACTOR：每一阶段均先`S006-committed-worktree-clean`后`S006-index-amendment-integrity`，任一prefix可验证且prior record hash不得变化。两组RED位于同一T006 parent，以按slice id排序的artifact aggregate map计算一个canonical combined aggregate并绑定一个main review ID。index records与canonical run目录双向exact相等；删除、插入、重排、替换prior record、额外未索引run、同anchor损坏index均失败。`verify --mode`必须真实区分local-working-tree与committed，`--base-ref`必须可解析并参与merge-base，`--through-task T006`在六条尾链未完整时失败、完整后通过且不追索future；unknown/future/被忽略参数失败。fingerprint包含staged/unstaged/untracked并仅排除exact evidence outputs。F151新增/修改node rerun=0，quarantine no-growth。
- **FR-040 [边界]**：F151只可按[`inventories/f150-scope.md`](inventories/f150-scope.md)修改module entry、既有`_enforce_front_door_exposure` handler，以及最小扩大后的`_resolve_front_door_mode`精确control-flow：完整load恰一次且发生在env mode应用之前、typed分类传播、有效配置结果仍env>YAML>loopback。`create_app`其余body AST及7个protected symbols冻结；无关F151 diff由各inventory管理，D-03 import-only独立允许。不得改变mode或Host/Origin/Access语义。
- **FR-041 [必须]**：`production-startup.md`是唯一启动manifest。普通load/start/restart对canonical、legacy argv、invalid schema、invalid JSON均目录字节0写；legacy/invalid typed reject且不得写`.corrupted`。只有显式install/update/bootstrap可调用既有store上的validated atomic migrate/repair。module entry不执行第二preflight，create_app其余AST冻结。
- **FR-042 [必须]**：required-slice discovery合并committed/staged/unstaged/untracked最终态；machine selector解析非空、覆盖hunk、非shared不相交，shared subgroup all-required。所有tree/exact删除须在base SHA展开为带object id的8个exact tracked paths；planned/slice/changed三向closure只减去hash不变的既有`.gitignore`用户patch，其他Feature、`docs/design`、unknown evidence均失败。`active-artifacts.v1.json`标Round10.1 current、Round4-9 exact SUPERSEDED且二者都有S002 owner，`artifact-lifecycle.v1.json`只列4个Final必需committed paths，每个都有first_state、first_writer task与producer command。Phase0 anchor、C19 pre10/post9/T122 fresh、formal canonical六件套、C084 44 owner records/45 selectors、pre/post SDK命令与stage future-path合同均机械验证；自动stage/producer集合出现C18、live/credential/external-cost命令必须失败。

### Key Runtime Value Objects

- **ProviderRoute**：Provider包内的immutable DTO，只含alias/provider/model/transport/required api_base与嵌套env/profile auth reference；不含Gateway schema/path、credential原值、headers或body开放字段。
- **RuntimeServiceBundle**：Gateway 单 runtime graph 的实例级 holder，含最终 LLMService、ProviderRouter 和既有 background registry，并拥有明确关闭协议。

Tombstone 是 canonical bootstrap 常量/validator；complexity snapshot 是版本控制的 gate 输入；历史 decode 是现有 projection 分支。三者都不是新 runtime entity/service。

## Success Criteria

- **SC-001**：Provider → Gateway与旧namespace均为0；D-03 15/1/33+2删除通过；role tags诚实，import41/direct-name147 ceiling不增长，changed-hunk attribute review完整，domain→other=0。
- **SC-002**：Provider-only 与 Gateway clean-wheel 全绿；所有当前workspace distributions都由标准Hatchling backend生成transaction-local wheel，Provider/Gateway及其workspace closure的import origin全部位于isolated target，Provider-only环境不可发现Gateway；Gateway 三个 supported CLI、structural readiness、负向 exit 78、source-only typed unsupported 均通过且不接触源码路径。
- **SC-003**：Proxy/旧文件/SDK/伪 activation 与旧 Docker 配置的 active-scope 计数为 0；pricing、history/tombstone 精确例外通过。
- **SC-004**：错误矩阵每格的HTTP/exit/code、业务Task/child Task/Work/业务Event/cancel/audit/backend由行为测试证明；三个Control Plane actions与一个tool入口均覆盖422/503或tool reject，不存在虚构REST或4xx/503冲突。
- **SC-005**：delegation五值、profile capability四值、Worker backend二值、Event/Console历史二值分域测试全绿；`worker.apply`/tool batch原子，Graph输出兼容与raw历史`docker`回放不变，被删模型引用为0。
- **SC-006**：production收敛45=3/42；TaskService144与AgentContext31 identity唯一，所有production constructor path+symbol恰一RGR owner；C084 44 owner records展开45 selectors，F033三个原skip构造点具备确定性行为证据，selected>0且fail/error/skip/rerun0；duplicate/override/unknown0，storage call graph零runtime capability，precomputed完整session副作用与zero-model/teardown通过。
- **SC-007**：任一 architecture 子 gate 可独立执行；任一 complexity 指标超过 ceiling 或 merge-base actual 都会失败，snapshot 无数字上调。
- **SC-008**：Settings action payload/UI 文案、YAML/env tombstone、重新授权/降级支持矩阵通过 Python/Vitest/tsc 行为验证。
- **SC-009**：17份exact active authority docs对package direction、Provider config、execution、readiness与存储索引事实一致；未处置现役Proxy/kernel/worker/current Docker陈述为0，显式历史陈述通过语义fixture。
- **SC-010**：所有定向tests、确定性C23/C24、clean-wheel、import-direction、retired-terms、complexity、frontend与benchmark lane全绿，自动stage/producer中C18/live/credential/external-cost命令为0；在此之前Goal保持未完成。
- **SC-011**：machine-readable RGR证据对全部98个slice完整且负面fixtures全绿；canonical v2 index/chain/run closure与mode/base/through-task语义全部通过。scope markdown/JSON集合98/98、最终cross-phase unresolved=0；clean-wheel checker的21个stable selectors两两互斥。T012四个corrective slices与新main-owned S011 replacement证据均完成R/G/R或direct RED，旧240558 binding只作superseded history；T012 report只下`final_verdict=null`。T070两条slice最终证明Provider 1+6/Gateway 7+25、runtime/optional/type-test/workspace ownership闭包以及真实child隔离，unknown/unowned/missing/unexpected=0。其余append-only、relocation、coverage与分层约束保持。
- **SC-012**：`quality-smells`报告must-fix=0、ratchet无上升、follow-up仅为冻结ID；分层/forbidden-import gate为0违规，且Review明确证明complexity未替代职责审查。
- **SC-013**：dirty/staged/untracked三类真实tmp Git repo均返回`LOCAL_CHANGES_PRESENT`且无fetch/checkout/reset/merge/uv；Telegram与Update并发must-fix契约全绿，33/33 test-owner与SCC/no-growth ratchet通过。
- **SC-014**：C23 post-SDK 9 paths通过，F151 node rerun0；F150 protected AST不变；active direct Uvicorn argv0，create_app/static config/exposure各一次，env-present invalid runtime YAML exit78；lifespan composition failure readiness/request/workload=0且nonzero；四类descriptor普通load/start/restart目录写入0。
- **SC-015**：dirty HEAD未变不vacuous pass；pytest/Vitest与single/nested JUnit positive/negative通过；planned-diff real-field/source-target/tree-delete8/Provider44+1+exception/operation-mode/artifact-lifecycle/authority17/selector/stage self-check、C19 10→9、C03 exact、C084 44 owners/45 selectors、T120/C24/C25均机械通过。

## Complexity Assessment

**HIGH / XL**：跨 Gateway、Provider、SDK、root manifest/lock、scripts、frontend、tests、CI 与架构文档。控制策略是机械 manifest、分阶段 tests-first 与硬 Gate；禁止借搬迁做应用宿主重写。
