# 技术调研报告：F151 Runtime Boundary & Architecture Truth

**特性分支**: `codex/f151-runtime-boundary`
**调研日期**: 2026-07-20
**调研模式**: `codebase-scan`（baseline `9d5e1e48`）
**权威输入**: F151 milestone、architecture-audit §14.14、AGENTS.md、constitution、driver config

**当前T012真值**：dependency selector R/G/R与fresh unresolved=0已完成；standard-backend RED及pyproject/uv-generated lock已接受。clean-wheel checker code review因AST mapping与isolation facts假绿被拒，行为GREEN/REFACTOR=0，corrective Gates=false。

## 1. 调研结论

F151 应在既有 Gateway modular monolith 内做“所有权迁移 + 运行边界收口 + 失真路径删除”，而不是重画 package 层级。可实施设计有六个核心结果：

1. 已批准D-03把49个`provider/dx`模块迁为15 CLI + 1 existing config + 33 operations，两个文件直接删除；Doctor renderer、wizard Click、config-bootstrap Click是三个有界hash exception；Provider→Gateway production import为零。
2. Gateway 保留 canonical schema normalization 并解析确定 `ProviderRoute`；ProviderRouter 只负责 transport/auth/cache，不再读取 Gateway config。
3. Worker selector、历史 event decoder、Execution Console projection 使用各自值域，禁止合并成一个新 backend 抽象。
4. 显式 bundle 只收敛现有 class-level 三项 service，并通过 storage-only 路径解构造环；不重写 TaskService/Orchestrator。
5. import、退役词、复杂度与 clean-wheel gate 都有独立、机械可执行的报告面。
6. 每个行为切片具备RGR证据，测试层/依赖层/坏味道独立审查，coverage/complexity不得替代设计质量。

冻结 inventories 是本报告的证据附件；其中machine JSON负责路径、identity、scope与计数，Markdown只解释语义：

- [`execution-semantics.md`](../inventories/execution-semantics.md)
- [`config-retirement.md`](../inventories/config-retirement.md)
- [`runtime-bundle.md`](../inventories/runtime-bundle.md)
- [`namespace-migration.md`](../inventories/namespace-migration.md)
- [`wheel-dependencies.md`](../inventories/wheel-dependencies.md)
- [`complexity-ceiling.v1.json`](../inventories/complexity-ceiling.v1.json)
- [`testing-matrix.md`](../inventories/testing-matrix.md)
- [`architecture-quality.md`](../inventories/architecture-quality.md)
- [`test-ownership.md`](../inventories/test-ownership.md)
- [`f150-scope.md`](../inventories/f150-scope.md)
- [`rgr-slices.md`](../inventories/rgr-slices.md)
- [`namespace-migration.v1.json`](../inventories/namespace-migration.v1.json)
- [`provider-test-rehome.v1.json`](../inventories/provider-test-rehome.v1.json)
- [`runtime-test-constructors.v1.json`](../inventories/runtime-test-constructors.v1.json)
- [`agent-context-test-constructors.v1.json`](../inventories/agent-context-test-constructors.v1.json)
- [`runtime-test-behavior-owners.v1.json`](../inventories/runtime-test-behavior-owners.v1.json)
- [`cross-role-edges.v1.json`](../inventories/cross-role-edges.v1.json)
- [`rgr-slice-scopes.v1.json`](../inventories/rgr-slice-scopes.v1.json)
- [`planned-diff.v1.json`](../inventories/planned-diff.v1.json)

## 2. Codebase reality

### 2.1 Namespace、import 与 wheel

| 事实 | 基线 | 设计影响 |
|---|---:|---|
| Provider production → Gateway static import | 15 文件 / 31 条 | 必须降为零；还要扫描 TYPE_CHECKING、dynamic/string |
| `provider/dx` | 51 Python 文件 / 17,676 LOC | 49 move，`__init__.py` 与 `runtime_activation.py` delete |
| namespace 测试引用 | Provider collectable tests=44 rehome+1 delete，另1个manual recorder exact decouple / Gateway 21 / root integration 2 / Memory 1 | 四组分别迁移/改写；Provider旧test tree→Gateway dependency目标0 |
| ProviderRouter 构造点 | 15 | resolver seam 的有界改动面 |
| TaskService production 构造点 | 基线48=4 runtime+44 storage；目标45=3+42 | T062删除worker-profile fallback；复用Orchestrator 1227/1319两个同方法重复实例；1141改storage-only预计算完成，chat/message归storage-only |
| Gateway direct deps 冻结值 | 7 internal main + 25 third-party main + 3 named extras | 包含constant dynamic `keyring`；clean-wheel同时核对AST mapping与wheel `Requires-Dist` |
| Provider direct deps 冻结值 | 1 internal main + 6 third-party main | `python-ulid`直接声明；DX过宽依赖与Provider local-embedding移除 |

Gateway 顶层生产 import 已包含 `jieba`、`pyarrow`；lancedb 是 memory 现役主路径依赖，即使部分 import lazy 也不能放在默认不会安装的 extra。迁移后 questionary/rich 同样由 Gateway manifest 直接声明。

D-03精确target为CLI15/config1/operations33；13 application/5 domain/9 store/6 adapter只是legacy mixed operations cluster的role tags，不是physical clean layers。`channel_verifier`只有registry/result/Protocol，属于application；真实HTTP在`telegram_verifier`。`secret_refs`是adapter，`wizard_session`是application。auth迁移有效节点是三文件六个，`models.py`的Credential import为dead import直接删。允许Doctor renderer、wizard Click driver、config bootstrap Click adapter三个T029 exception，最终services/application→Click/Rich/Questionary/CLI=0。CLI15是legacy presentation/composition bucket，直接side-effect tuple只可减少。machine baseline只能证明41个import ceiling与147个可解析direct-name call ceiling，不能称为完整interaction graph；changed hunks仍需attribute-call检查和人工adversarial responsibility review。AST eager图无SCC，含deferred edge的唯一已知SCC是`doctor/secret_service/update_service`。

### 2.2 Provider config 边界

Gateway `ProviderEntry` 已把 v1 `auth_type/api_key_env/base_url` 归一化到 v2 `auth/api_base`。这是 canonical loader 的受支持输入，不是应删除的 compatibility。真正的反向依赖位于 ProviderRouter：它接收 `project_root`、函数内 import Gateway loader，并通过多组 `getattr` 同时读取新旧字段。

因此 F151 只删除 Provider 侧的逆向 loader 和重复 schema compatibility。Gateway resolver 负责alias resolution、默认URL、optional schema→required runtime URL与auth reference。真实ProviderEntry不存在headers/body字段，ProviderRoute也不得增加它们；Provider静态/动态headers与per-call body保留在既有Provider/runtime边界。ProviderRouter必须保留task-scope alias pinning和`invalidate_provider_client`现有语义。

### 2.3 退役配置与 UI

`RuntimeConfig` 为空 Pydantic model，旧 `runtime.*` 字段会被静默忽略。`config_bootstrap` 也不是所有启动路径的统一入口，所以：

- YAML tombstone 只能放在 `OctoAgentConfig.from_yaml()` raw dict 阶段；
- env tombstone 必须在 dotenv load 后、application/Harness assembly 前；
- `.env.litellm` 只允许 `exists()`，禁止读取 secret。

现役仓库面仍包括 `octoagent/octoagent.yaml`、`.env.example`、`octoagent.yaml.example`、`behavior_commands.py`、前端 `SettingsPage.tsx` 的三个 runtime 字段和`frontend/src/domains/settings/shared.tsx`的旧文件提示。退役完成不能用末尾字符串扫描代替 raw dict、env、action payload、UI 行为测试。

### 2.4 Execution 四个值域与四个输入面

| 语义 | 输入/存储 | 冻结值域 |
|---|---|---|
| delegation target | 三个Control Plane action + `subagents.spawn` | case-sensitive五值；不trim/coerce/auto-reselect |
| profile capability | profile/revision | 四值，不含fallback |
| Worker transient backend | dispatch state/result | `graph|inline` |
| persisted event decoder | `ExecutionStatusChangedPayload.backend` | 新写 `inline`；历史读 `inline | docker` |
| Console/API projection | `ExecutionConsoleSession.backend` | 兼容输出 `inline | docker`；Graph 投影 `inline` |

`ExecutionBackend` 目前只有 `DOCKER/INLINE`，它服务于 event/projection，不是 Worker runtime selector。Graph 当前显式投影 `ExecutionBackend.INLINE`，F151 冻结这一 API 兼容语义。`register_session` 新写侧只接受 Inline；历史 Docker 只能由 raw event fixture 验证。`container_name` 保留为历史 JSON 字段。历史读取通过现有 decoder/projection 分支实现，不新增 history model/service/registry。

D-01已批准删除`JobSpec`与`ExecutionRuntimeRecord`及core exports/tests/docs。`register_session`删除公开backend参数并固定inline；历史Docker只从raw event回放，未知历史值稳定报projection error。

### 2.5 Runtime construction 与 teardown

真实构造链为 `AgentSessionTurnHook → SkillRunner → final LLMService`。Hook 只需 turn storage，因此 composition root 可以先构造 storage-only Hook，形成最终 LLM 后再创建 bundle，无需 mutable late binding 或第二 registry。`process_task_with_llm()` 删除独立 LLM 参数，只允许读取 bundle 的 final LLM，避免双身份。源码还表明AgentContext当前构造就创建`MemoryRuntimeService`，reranker访问会auto-load模型；storage-only目标必须显式证明这两者、background task与network均为0。

现有 `app.state.background_tasks` 是唯一 LLM/AgentContext background registry，Harness 已有 snapshot drain 和 producer 停止后的 final loop drain。chat/message 的 module-level set 随直跑 fallback 删除；拥有独立 lifecycle 的 scheduler 内部集合不纳入 bundle。共享 ProviderRouter 的关闭权只有 bundle/runtime owner；local close chain 为 `LLMService → SkillRunner → ProviderModelClient`，再由 bundle关闭Router，异步生命周期API统一为`aclose`，最后关闭stores，所有层均exactly-once。

`_InlineReplyLLMService`当前保证non-direct与Graph-start失败时使用预计算结果：provider=`inline`，model=`agent-inline`，tokens/cost=0；Graph失败真实fallback content是空字符串。直接强制bundle LLM会改变内容、延迟、成本和网络行为，还会经context compaction与SessionMemoryExtractor产生隐藏模型调用。`record_response_context`又把SessionContext/turn/session持久化与extraction绑在一起，所以只在TaskService补seam会漏状态或复制算法。F151必须先在既有`agent_context_session_replay.py`拆出唯一storage persistence primitive，runtime wrapper只额外触发extraction；TaskService precomputed operation复用该primitive并保持Task/Event/Artifact/checkpoint/SessionContext/turn/session exact side effects，同时model/Router/recall/compaction/extraction全为0。

测试侧源码共有144个TaskService AST constructor identity：123个live test/nested（其中F033两条skip含3个构造点）、20个helper、1个shadowed；另有31个AgentContext identity。删除LLM override和引入XOR不能只迁移两个gate node；constructor qualname中存在helper/fixture，不能直接拼成pytest node。C084以44 owner records展开42 files+3 exact nodes=45 selectors；F033改为持久状态/隔离oracle并取消skip，helper须reverse-call到collectable deterministic node，并要求selected>0、fail/error/skip/rerun=0。

### 2.6 Retired/runtime scope

权威范围明确授权删除：

- LiteLLM Proxy extra、activation、state/readiness 与旧文件路径；
- `.env.litellm` loader/writer/backup/migration；
- `packages/sdk` 平行 model/tool loop；
- 已确认虚假成功的 Proxy activation/config wiring；
- `docker_mode`/checker 等不存在 backend 的现役配置与声明。

以下不自动删除：F094 migration CLI/audit/rollback、EchoProvider、MockProvider、legacy provider registry、`octo-bench`、Provider pricing helper。base LiteLLM 若保留只能服务 pricing，gate 要证明它没有 runtime client/turn 路径。

## 3. 方案比较

| 方案 | 方向 | 路径数量 | 迁移风险 | 结论 |
|---|---|---:|---|---|
| A. Gateway 原位收口 | Gateway → Provider | 1 | 机械移动大、设计面可控 | 采用 |
| B. 只搬 CLI shell，保留 Provider shim | 仍有反向或双 namespace | 2 | 长期漂移 | 拒绝 |
| C. 新 management/kernel/worker packages | 可重画 | 多 | big-bang、无真实部署边界 | 明确禁止 |

方案A不借17k LOC移动改业务职责，但物理target必须满足层次：services/routes→CLI为0；CLI15的存量adapter/client/store/subprocess等composition按exact tuple no-growth，不在本Feature伪装纯presentation。新增的`gateway.services.operations`只是既有Gateway distribution内的内部namespace，不是新deployable package/runtime。只批准Doctor renderer、wizard Click driver、config-bootstrap Click adapter三个有界拆分。

## 4. Error contract

调研确认至少要拆五类错误：

| 边界 | 结果 | 新 Task | Task-domain Event | Control Plane audit | Inline |
|---|---|---:|---:|---:|---:|
| 三个Control Plane action unsupported target | 422 `WORKER_RUNTIME_SELECTOR_UNSUPPORTED` | 0 | 0 | 2：REQUESTED+REJECTED | 0 |
| 三个action Graph/TaskRunner preflight unavailable | 503 `WORKER_RUNTIME_UNAVAILABLE` | 0 | 0 | 2：REQUESTED+REJECTED | 0 |
| `subagents.spawn` selector/runtime reject | stable tool error | 0 | 0 | 0 | 0 |
| 启动 security 配置无效 | exit 78 `GATEWAY_SECURITY_CONFIG_INVALID` | 0 | 0 | 0 | 0 |
| 启动同步可解析的static runtime/retired/unknown/root config无效 | exit 78 `GATEWAY_RUNTIME_CONFIG_INVALID` | 0 | 0 | 0 | 0 |
| FastAPI lifespan runtime service composition/assembly失败 | startup nonzero；readiness/request=0，不映射exit78 | 0 | 0 | 0 | 0 |
| 启动 unknown runtime key | exit 78 `RUNTIME_CONFIG_UNKNOWN` | 0 | 0 | 0 | 0 |
| 启动 retired runtime/file 输入 | exit 78，精确 tombstone code | 0 | 0 | 0 | 0 |
| 请求期 security 配置源失效 | 503 `FRONT_DOOR_CONFIG_INVALID` | 0 | 0 | 0 | 0 |

`worker.apply`整批preflight必须早于descendant cancel/write，`subagents.spawn`早于loop；profile缺TaskRunner不得direct-create。CP tests预热/排除`ops-control-plane` singleton并按request_id审计。Graph execute竞态只给同一Task写一个FAILED transition，action audit保持COMPLETED，Inline=0。

## 5. Complexity ratchet algorithm

抽象的“不得恶化”不足以让 CI 判定，故冻结以下算法：

- schema `version: 1`；记录 `scanner_version`、rules、scan paths、hotspots、total_by_rule、每热点 rule/LOC/max_function_span。
- production globs：`octoagent/apps/*/src/**/*.py` 与 `octoagent/packages/*/src/**/*.py`；排除 build、vendor、generated。
- Ruff JSON rules：`C901`, `PLR0911`, `PLR0912`, `PLR0913`, `PLR0915`。
- logical LOC：用 `tokenize`，只计含非注释、非 whitespace/newline token 的物理行。
- max function span：AST function/async function 的 `end_lineno - lineno + 1`。
- ceiling check：current 每格 `<=` committed F151 snapshot。
- low-water check：PR current 每格 `<=` merge-base 实际扫描值；base 由 `git archive <merge-base>` 解到临时目录扫描，不依赖 checkout 状态。
- snapshot首次创建必须从显式base ref的archive actual生成，不能用已修改current tree；后续`--write-snapshot`仅人工收紧，schema/path/rule变更须显式review，任何数字上调直接失败；CI永不写文件。

因此实现后指标下降无需立刻刷新 ceiling，但下一次 PR 会被 merge-base 实际低水位锁住；算法没有“自动收紧 snapshot”与“无需刷新”之间的冲突。

编码前真实快照已从 `9d5e1e48` 以 Ruff 0.15.4 计算并冻结：config fingerprint `262e9b7fc5e626f42a08aa9dc6bd48cb4c1180cdac7f012e589699fe8cfedd56`，总计 658（C901=167、PLR0911=67、PLR0912=90、PLR0913=259、PLR0915=75）；六个 hotspot 及其 rule/LOC/max-span 数值见 inventory。Phase 0 必须在任何 production edit 前 byte-for-byte 复制并验证该快照，不能从改后树首次生成。

### 5.1 TDD、分层与坏味道不是复杂度附属项

`octoagent/tests/AGENTS.md`要求不需真判断力的测试降到确定性层。F151因此以L4契约/服务测试和L3 Echo/DI stub/ScriptedModelClient/subprocess为主，L1/L2新增预期均为0。每个行为改动留下稳定RED→同selector GREEN→REFACTOR；namespace move用atomic manifest证据，不制造虚假RED。

源码层次扫描还发现两项存量债务：services→harness为43 AST nodes/24 files，Harness→main module为11 sites。F151不big-bang清零，只ratchet且禁止ProviderRoute/bundle新seam继续使用。`quality-smells`单独审查must-fix/ratchet/follow-up，`tdd-evidence`核对RGR ledger；coverage与complexity均不能替代它们。

## 6. Clean-wheel contract

T012 dependency resolver现已从base-ref/merge-base和worktree的TOML/lock结构解析semantic transition；历史两path unresolved=2基线经R/G/R与fresh revalidation降为0。三态、nonempty/absent、互斥与fail-closed负例合同不变。

归零后，T012必须使用项目声明的标准`hatchling.build` backend，而不是checker自行从pyproject合成wheel或METADATA。root dev dependency精确锁定`hatchling==1.29.0`，lock只能由标准`uv lock`生成；Hatchling不得进入Provider/Gateway runtime `Requires-Dist`。checker以`hatchling.build.build_wheel`生成当前core/provider/protocol/tooling/skills/policy/memory/sdk/gateway九个`py3-none-any` wheel，从真实wheel archive读取METADATA，再用transaction-local `uv pip install --offline --no-deps --target <tmp/site> <local wheels...>`安装所需workspace closure。Provider-only target不得发现Gateway，Gateway target的workspace module origins必须全部位于target。

main已在独立`/tmp`验证上述九wheel构建、offline/no-deps target安装和外部cwd import origin；该结果只证明设计可行，不是release evidence。actual pyproject/lock随后已变更并接受，但shared venv仍未bootstrap；当前checker实现被拒。CI/Final仍只能从committed lock经正常`uv sync --dev`取得backend。

对当前真实源码的机械复算证明“manifest=全部无语境AST imports”不可作为T012 invariant。Provider manifest15/observed17，declared-unobserved=`jieba,lancedb`，observed-undeclared=`aiosqlite,octoagent-gateway,pytest,python-ulid`；Gateway manifest11/observed34，declared-unobserved=`uvicorn`，并有runtime、optional、workspace与source-managed多类observed roots。正确模型必须按distribution-owned installed files逐occurrence分类`runtime-required/optional-lazy/type-checking/test-plugin/workspace-owned`；T012报告delta且final verdict为空，T070才严格执行最终closure。

隔离facts不能由parent重建expected env/search paths。执行imports的同一child必须返回cwd、ordered sys.path、exact env、site/user-site、prefix/base-prefix和workspace origins；host path、source/editable origin、ambient PYTHONPATH、字段漂移或stdout污染均fail closed。

Gateway/Provider wheels 从本次transaction的本地 wheelhouse 非 editable 安装到隔离target。所有进程：

- cwd 在仓库外；
- 临时 HOME、XDG_CONFIG_HOME、XDG_DATA_HOME、XDG_CACHE_HOME、XDG_STATE_HOME；
- `PYTHONNOUSERSITE=1`、清空 `PYTHONPATH`，不把 source root 写入任何 path；
- Provider-only 环境验证 Gateway 不可发现；
- 每个workspace module的`__file__` origin都在isolated target，source/editable origin失败；
- Gateway 环境验证三个 supported CLI help、main import、真实进程启动、structural core readiness 与 SIGTERM；
- invalid security/runtime config 的 subprocess 验证 exit 78；
- source-managed 命令在任何副作用前稳定 exit 69 `SOURCE_CHECKOUT_REQUIRED`：`service install|uninstall`、`update|restart|stop`、install-bootstrap module 与 bench；`service status`、logs、help 继续可用。

`/ready?profile=core` 定义为结构检查：SQLite/stores、artifacts、mandatory runtime services 已组装，canonical alias 可解析为 secret-safe ProviderRoute；不调用真实 provider 网络或模型。fixture 使用非 Echo、fake endpoint/profile reference 的 canonical config。

首次完整 clean-wheel 要等 Proxy/SDK manifest、lock、`.env.litellm` absence、namespace final、source guards与startup owners全部闭合；CLI relocation阶段只做build/import/static preliminary gate。T049只复验C09/C10，T070才首次执行C11/full/all。

两个T012 slices的RED均已由main接受；dependency slice随后完成R/G/R，standard-backend仍为RED-only。旧T011 replacement RED不能替代它们。standard-backend node继续覆盖pin/lock/backend/manual builder/host/source泄漏矩阵；手写wheel不是降级路径。

### 6.1 Managed update 与高风险状态

`runtime_descriptor_defaults.py`当前只用`git diff --quiet`检查unstaged tracked change，dirty分支还执行`git checkout -- .`；staged/untracked会被漏过。调用链经descriptor→UpdateService/update-worker真实可达，因此是P0用户数据安全缺陷。`UpdateStatusStore.load_runtime_descriptor`又会在普通读取时normalize/save，invalid JSON还写`.corrupted`。F151必须在任何fetch/merge/uv前检查完整porcelain状态并返回`LOCAL_CHANGES_PRESENT`；普通load/start/restart对canonical、legacy argv、invalid schema和invalid JSON都保持目录字节0变化。旧pull/destructive命令只能在显式install/update/bootstrap transaction中validated atomic migrate；显式repair必须带replacement与expected digest。

`TelegramStateStore`的9个mutator均为锁外load→mutate→另锁save，可能复活已删除授权；`UpdateService`的active attempt为check→save/无owner clear，可能双启动或旧worker清除新attempt。两者纳入must-fix并用controlled barrier/event测试；`backup_service`四个path helpers和13个production consumers属于后续职责拆分，只做no-growth。

## 7. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 机械迁移漏掉 relative auth/string entry | source-aware49-file manifest、三文件六有效auth nodes、Provider test exact 44 rehome+1 delete+1 manual decouple map、Gateway21/root2/Memory1与root gate1、全输入面scan |
| Gateway v1 config 被误删 | canonical DTO contract tests固定 v1/v2 normalization |
| old runtime fields 被 Pydantic 静默忽略 | raw-dict tombstone 行为测试 |
| Echo 把 readiness 假装通过 | non-Echo canonical alias structural readiness fixture |
| Graph 被错塞进 ExecutionBackend | 三值域 contract + projection replay tests |
| bundle 捕获 bootstrap LLM或 double-close | storage-only purity、唯一session persistence primitive、`aclose` identity/order/count、C084 exact44 owner records/45 executable selectors |
| complexity 改善后再次回升 | merge-base actual low-water gate |
| root pytest 漏 benchmark | Proxy exception benchmark 独立 lane |
| 未授权删除 public API | 只执行已批准D-01两模型删除；F094/Echo/Mock/registry/pricing/bench明确保留 |
| update丢失用户修改或普通read隐藏写 | 三类dirty真实tmp Git repo；`LOCAL_CHANGES_PRESENT`且危险命令0；descriptor四类输入目录字节0变化 |
| 高风险RMW/claim竞态 | Telegram/Update独立RGR；33/33 test owner；backup path只no-growth |

## 8. 结论与 Gate 状态

设计无须新增 runtime service、tombstone entity、history entity 或 budget service。允许的新运行时持有者仅为 `ProviderRoute`（含必要 auth reference）和实例级 `RuntimeServiceBundle`。

D-01/D-03已决定；`octo-bench`保留entry、不打包benchmark tree，并在wheel环境副作用前exit69。产品选项均已冻结；整体Design/Tasks Gate仍未批准，禁止实现。
