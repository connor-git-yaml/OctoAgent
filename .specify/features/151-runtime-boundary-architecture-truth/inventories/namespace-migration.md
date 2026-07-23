# F151 Namespace 迁移冻结清单

**D-03 已由 main 冻结**：source tree 共 51 个 Python 文件，执行 49 move + 2 delete。目标严格为 15 个 CLI/presentation、1 个既有 config service、33 个 operations backing module；不得恢复旧 namespace、增加 shim或恢复上版错误分层。

## 1. Source → target manifest

本节的人读表与机器清单[`namespace-migration.v1.json`](namespace-migration.v1.json)必须双向一致；Provider tests另由[`provider-test-rehome.v1.json`](provider-test-rehome.v1.json)冻结44 move+1 delete，并为非pytest手工录制器登记1个exact Gateway import decouple。T021/T029、RGR scope、stage selector与constructor identity都先投影到最终路径后比较；不存在字段、遗漏source或仍引用retired source path均失败。

### 15 个 legacy presentation / composition CLI

目标根：`octoagent/apps/gateway/src/octoagent/gateway/cli/`

| source basename | target basename | 角色 |
|---|---|---|
| `cli.py` | `cli.py` | Click composition / public entrypoint |
| `auth_commands.py` | `auth_commands.py` | CLI command |
| `attest_commands.py` | `attest_commands.py` | CLI command |
| `backup_commands.py` | `backup_commands.py` | CLI command |
| `behavior_commands.py` | `behavior_commands.py` | CLI command |
| `chat_import_commands.py` | `chat_import_commands.py` | CLI command |
| `cleanup_commands.py` | `cleanup_commands.py` | CLI command |
| `config_commands.py` | `config_commands.py` | CLI command |
| `project_commands.py` | `project_commands.py` | CLI command |
| `secret_commands.py` | `secret_commands.py` | CLI command |
| `service_commands.py` | `service_commands.py` | CLI command |
| `update_commands.py` | `update_commands.py` | CLI command |
| `console_output.py` | `console_output.py` | Rich presentation only |
| `install_bootstrap.py` | `install_bootstrap.py` | CLI/source-checkout bootstrap |
| `memory_commands.py` | `memory_commands.py` | F094 CLI，保留行为 |

### 1 个 existing config service

`config_bootstrap.py` → `octoagent/apps/gateway/src/octoagent/gateway/services/config/config_bootstrap.py`。它进入既有 config namespace，不进入 CLI 或 operations。核心配置构造/持久化留在config application；`click.prompt`/`click.Choice`默认实现迁入既有CLI文件，由CLI注入`prompt`/`choice_prompt` callable。目标config模块对Click/Rich/Questionary/CLI依赖为0，不新增adapter service或module。

### 33 个 backing operations

目标根：`octoagent/apps/gateway/src/octoagent/gateway/services/operations/`。`application/domain/store/adapter`只是legacy mixed operations cluster的职责标签，不是已实现的physical clean layers；它不是新deployable package/runtime。真实跨role边由[`cross-role-edges.v1.json`](cross-role-edges.v1.json)冻结，只可减少不得新增。

| source basename | layer | deterministic owner |
|---|---|---|
| `backup_audit.py` | store | backup audit/backup service L4 |
| `backup_service.py` | application | `test_backup_service.py` L4 |
| `channel_verifier.py` | application | pure registry/result L4 |
| `chat_import_service.py` | application | chat import service L4 |
| `control_plane_models.py` | domain | operations model L4 |
| `doctor.py` | application | doctor L4；presentation functions除外 |
| `doctor_remediation.py` | application | remediation L4；Rich renderer除外 |
| `import_mapping_store.py` | store | import workbench store L4 |
| `import_source_store.py` | store | import source/workbench L4 |
| `import_workbench_models.py` | domain | import workbench model L4 |
| `import_workbench_service.py` | application | import workbench service L4 |
| `models.py` | domain | DX/operations model L4 |
| `onboarding_models.py` | domain | onboarding model L4 |
| `onboarding_service.py` | application | onboarding service L4 |
| `onboarding_store.py` | store | onboarding store L4 |
| `project_migration.py` | application | F094 migration L4 |
| `project_selector.py` | application | project selection L4 |
| `recovery_status_store.py` | store | recovery status store L4 |
| `runtime_descriptor_defaults.py` | application | update safety：L4 DI fake + L3真实tmp Git |
| `secret_models.py` | domain | secret model L4 |
| `secret_refs.py` | adapter | secret reference adapter L4；runner/env/file/keyring均DI fake |
| `secret_service.py` | application | secret service L4 |
| `secret_status_store.py` | store | secret status store L4 |
| `service_manager.py` | adapter | service manager DI/subprocess L4 |
| `setup_governance_adapter.py` | adapter | setup governance adapter L4 |
| `sleep_probe.py` | adapter | controlled-clock probe L4 |
| `telegram_pairing.py` | store | TelegramStateStore direct L4 |
| `telegram_verifier.py` | adapter | Telegram verifier DI/HTTP L4 |
| `update_service.py` | application | UpdateService direct L4 |
| `update_status_store.py` | store | update status store direct L4 |
| `update_worker.py` | adapter | update worker entry L3 + application fake L4 |
| `wizard_session.py` | application | wizard core flow L4；Click prompt driver迁入既有CLI并callable注入 |
| `wizard_session_store.py` | store | wizard session store direct L4 |

层数冻结为application 13、domain 5、store 9、adapter 6；gate按manifest逐项核对。`channel_verifier`只有Pydantic result、Protocol、内存registry和missing-result builder，属于application port/registry；真实HTTP在`telegram_verifier`。`secret_refs`的importlib/os/stat/subprocess/Path/dotenv/keyring动态import使其只能是adapter；`wizard_session`拥有config/store流程，属于application。

### CLI15 legacy side-effect baseline

CLI15不是已经纯化的presentation层，而是本次只做atomic ownership move的legacy presentation/composition bucket。进一步把命令控制器拆成纯presentation属于后续Fix；F151只允许已批准的Doctor/wizard/config-bootstrap三个职责exception，新seam必须经operations/application。

source-aware gate以`(path, enclosing qualname, callee/import kind)`冻结并允许减少、禁止新增：adapter `ImportFrom` nodes=8（6个unique source→module pairs）、store `ImportFrom` nodes=3且constructor calls=5、dynamic HTTP client import=1/API calls=2、subprocess imports=2/calls=2、signal effect sites=3、固定filesystem matcher call nodes=70。逐文件责任如下：

| CLI file | 现役直接composition/side effect |
|---|---|
| `cli.py` | setup governance / Telegram verifier composition |
| `auth_commands.py` | Provider `CredentialStore` |
| `attest_commands.py` | `ServiceManager`、signal/kill |
| `backup_commands.py` | `BackupService`、filesystem |
| `behavior_commands.py` | project selector、HTTP client、editor subprocess、filesystem |
| `chat_import_commands.py` | import services、JSON/file read |
| `cleanup_commands.py` | `shutil`/filesystem |
| `config_commands.py` | canonical config、project migration、update service |
| `project_commands.py` | selector/wizard services |
| `secret_commands.py` | secret service |
| `service_commands.py` | `ServiceManager`、filesystem |
| `update_commands.py` | `UpdateService`/`UpdateStatusStore`、signal/kill |
| `console_output.py` | Rich presentation only |
| `install_bootstrap.py` | subprocess、config、update store |
| `memory_commands.py` | core path与F094 memory migration |

gate保存每个baseline tuple而非只比较总数；任一新增adapter/client/store/subprocess/signal/filesystem tuple失败。Doctor/wizard/config-bootstrap exception使用独立ID，不能吞掉新的side effect。

下表是direct adapter/client/store/subprocess/signal的exact tuple清单，格式为`source:line::enclosing_qualname→callee/module`。机械move后line可变，但`source→target`映射、qualname、callee/module与AST node kind必须匹配；删除允许，新增失败。

| kind | exact baseline tuples |
|---|---|
| adapter imports（8） | `cli.py:158::setup→setup_governance_adapter.LocalSetupGovernanceAdapter`；`cli.py:328::init._run→setup_governance_adapter.LocalSetupGovernanceAdapter`；`cli.py:400::onboard→telegram_verifier.build_builtin_verifier_registry`；`cli.py:445::onboard._run→setup_governance_adapter.LocalSetupGovernanceAdapter`；`attest_commands.py:29::module→service_manager`；`behavior_commands.py:770::_compact_gateway_settings→service_manager.resolve_instance_root`；`project_commands.py:142::edit_project._run→setup_governance_adapter.LocalSetupGovernanceAdapter`；`service_commands.py:20::module→service_manager` |
| store imports（3） | `auth_commands.py:15::module→auth.store.CredentialStore`；`update_commands.py:17::module→update_status_store.UpdateStatusStore`；`install_bootstrap.py:20::module→update_status_store.UpdateStatusStore` |
| store constructors（5） | `auth_commands.py:78::paste_token→CredentialStore`；`update_commands.py:24::_has_managed_descriptor→UpdateStatusStore`；`update_commands.py:125::stop→UpdateStatusStore`；`install_bootstrap.py:226::run_install_bootstrap→UpdateStatusStore`；`install_bootstrap.py:277::run_install_bootstrap→UpdateStatusStore` |
| HTTP client | import `behavior_commands.py:827::_compact_request→httpx`；calls `:831→httpx.Client`、`:834→client.request` |
| subprocess | imports `behavior_commands.py:8::module→subprocess`、`install_bootstrap.py:6::module→subprocess`；calls `behavior_commands.py:352::edit_behavior._run→subprocess.run`、`install_bootstrap.py:26::_run_command→subprocess.run` |
| signal/kill effects | `attest_commands.py:177::run_service_probe→do_kill(SIGKILL)`；`update_commands.py:111::_pid_alive→os.kill`；`update_commands.py:144::stop→os.kill`（signal selection at line140） |

filesystem 70-call baseline由同一gate按固定callee set`read_text|write_text|mkdir|open|unlink|rename|chmod|exists|is_file|is_dir|iterdir|glob|rglob|resolve|expanduser`与`shutil.*`导出完整machine JSON；Design制品冻结总数和逐文件分布`backup3/behavior22/chat_import4/cleanup7/config8/service7/install18/memory1`。任何新增tuple失败，减少允许。

### 2 个直接删除

- `dx/__init__.py`：compatibility 聚合；不迁移、不留 shim。
- `runtime_activation.py`：已确认 no-op activation；连同 wiring/tests 直接删除，不先 move 再 delete。

## 2. 三个批准的机械迁移 hash exception

49-file transaction 在T029 snapshot时默认保持normalized content/AST与内容hash。允许的exception只有以下三类，manifest逐文件记录symbol/line/hash变化：

- `DoctorRunner`、`DoctorRemediationPlanner`、`DoctorGuidance`及其 domain/application model 留在 `gateway.services.operations`；
- `format_report`、`format_guidance`、`format_guidance_panel`与 Rich `Table/Panel/RenderableType` presentation 移到 `gateway.cli.console_output`；
- `onboarding_service`、`update_service`继续只依赖 operations doctor API；`services/**`、`routes/**` → `gateway.cli` 最终必须为 0。

2. `wizard_session.py`：`WizardSessionService`的会话状态、schema draft与store协调留operations application；`_prompt_field/_prompt_choice/_prompt_confirm`的Click驱动移入既有`gateway.cli`文件，通过单个prompt-driver callable注入。operations不得import Click。
3. `config_bootstrap.py`：`_default_prompt/_default_choice_prompt`的Click默认实现移入既有`gateway.cli`文件，config application只接受显式callable；CLI interactive路径必须注入，非interactive/echo不需要prompt。config不得反向import CLI。

T029时除此之外的内容变化一律失败，不得借迁移拆垂直域、重命名public behavior或改变store语义。T029之后，已迁移target可由Phase2–4的行为slice合法修改；这些变化不回写或放宽T029 hash，而由machine RGR scope的稳定symbol/qualname与evidence独立授权。

### 两阶段原子证据

- **T029 relocation snapshot**：从冻结base的51个source重新计算normalized content/AST，应用source→target path/import projection及三个批准exception，保存49个target snapshot（或等价可验证patch/AST dump）、2个delete absence、逐文件source/target hash、exception明细、entrypoint/import报告与worktree fingerprint。此时任何未授权业务hunk失败。
- **Final relocation proof**：验证source=0、49个target完整、2个retired file absent、shim=0；重新以冻结base复验T029 artifact，而不是把当前target raw hash当成机械迁移结果。T029之后的target symbol变化必须全部命中`rgr-slice-scopes.v1.json`的stable symbol partition并有对应RGR evidence，未映射变化失败。
- **负面夹具**：T029夹带业务改动失败；T029后有完整slice evidence的target修改通过；同样的后续修改缺slice或evidence时失败。

## 3. Auth import 精确修复

源码审计为 **三文件、6 个有效节点**；`models.py` dead import不计入：

| target file | effective nodes | target import |
|---|---:|---|
| `cli/auth_commands.py` | 4 | `credentials.OAuthCredential`、`profile.ProviderProfile`、`store.CredentialStore`、`validators.validate_claude_setup_token`均改为`octoagent.provider.auth.*` absolute import |
| `services/operations/doctor.py` | 1 | `octoagent.provider.auth.store.CredentialStore` |
| `services/operations/secret_service.py` | 1 | `octoagent.provider.auth.store.CredentialStore` |

`models.py` 的 `Credential` import 未使用，直接删除，不计为迁移后的 auth edge。实现前由 AST gate 输出 6 个 source node 的 path/lineno/symbol；如源码漂移，先回 Gate，不能靠文字计数放行。

## 4. 公开入口与字符串输入面

必须在同一个 atomic transaction 内更新：

- `octo` console script → `octoagent.gateway.cli.cli:main`；
- `octo doctor --help`、`octo auth --help`保持公开入口；
- update worker module string → `octoagent.gateway.services.operations.update_worker`；
- install/bootstrap、`python -m`、subprocess argv、dynamic import map、monkeypatch string；
- production、tests、scripts、benchmarks、docs中的旧 namespace；
- Provider collectable tests按machine map拆为44迁移/rehome + `test_runtime_activation.py`删除；非pytest `wire_replay/record_cassettes.py`单独清除Gateway dotenv-loader import并改为caller-provided env。另有Gateway 21 / root integration 2 / Memory 1 四组引用责任与 root gate wiring 1。完成态Provider旧测试树→Gateway dependency=0，任何Phase4命令或constructor inventory引用Provider退休测试路径均失败。

Gate不得只扫最终路径。它先读取本 manifest 建立 source→target→layer 映射，再扫描：静态 `Import/ImportFrom`、`TYPE_CHECKING`、常量 `import_module`/module map、subprocess/`python -m`字符串、monkeypatch target、pyproject entrypoint/dependency与 update-worker 字符串。

## 5. Role truth、跨role ratchet与 SCC

新F151 seam冻结方向：

```text
domain/contracts ← application/adapters ← composition root/UI
gateway.cli → gateway.services.operations application/config（新F151 seam）
```

这不是对现存operations物理图的描述。baseline有62个relative ImportFrom nodes，其中41个跨role：application→domain11/store11/adapter6、store→domain4/application2、adapter→domain2/store2/application3；可解析direct-name calls为147。`update_status_store→backup_service/runtime_descriptor_defaults`、`setup_governance_adapter→wizard_session`等反向/混层边真实存在。Gate逐tuple读取`cross-role-edges.v1.json`，允许删除/收窄，禁止新增path/qualname/kind/target或数量扩大。domain→其他role=0并继续严格纯净。

composition/routes当前也不是纯application consumer：到application import nodes=6，routes另直连store=2；作为exact no-growth debt冻结。F151新seam不得以legacy edge为许可；clean physical layering另立follow-up，不在本Feature批量引入无多实现价值的ports。

禁止：

- `gateway.services/**` 或 `gateway.routes/**` import `gateway.cli`；
- operations domain modules import Click、Rich、Questionary、routes、CLI、filesystem、network client或subprocess；
- application通过CLI调用 backing service；
- old/new namespace并存或 source/target双 entrypoint。

CLI15存量direct adapter/client/store/subprocess只受上述exact no-growth ratchet，不在本Feature内伪称已经满足纯presentation依赖；`services/**`/`routes/**`→CLI仍必须为0。

Atomic relocation不伪装成pytest RED。`S017-namespace-atomic`的唯一gate node在before state将现有source edge按本manifest虚拟映射并稳定报告三个批准的presentation边：doctor renderer、wizard Click prompt、config bootstrap Click defaults；T017记录该before artifact。T029用同一node验完整snapshot state，此时services/config/application/routes→Click/Rich/Questionary/CLI=0；任何source/target混合态以稳定atomic-state错误失败。Final再次运行该node时复验T029 snapshot against base，再检查T029后target变化均由machine RGR slice/evidence授权。

Gate同时输出 operations import graph SCC与cross-role edge diff。基线 `doctor → secret → update → doctor` 的动态环登记为 follow-up/ratchet：F151不借49-file move大改，但任何新增节点/边或更大SCC失败；不得声称 operations 无循环或application/store/adapter已物理clean。

edge算法冻结为AST `ImportFrom`节点计数（同一source的两个节点分别计数），不是unique module pair：CLI→operations 30、CLI→config 7、operations→config 13、operations→CLI 1（当前RED）；诊断同时可报告unique pair 25/7/12/1但不得拿来与node ceiling混比。full AST图SCC为`doctor --deferred--> secret_service --eager--> update_service --eager--> doctor`；eager-only图SCC=0。

## 6. Atomic transaction 验收

1. target：CLI 15/15、config 1/1、operations 33/33；source 0/51。
2. delete：`__init__.py`、`runtime_activation.py`均 absence，无 shim。
3. Provider production→Gateway=0；活跃旧 namespace=0。
4. services/routes→CLI=0；domain forbidden imports=0；SCC不扩大。
5. 六个有效 auth relative nodes迁为absolute，unused `Credential` import删除。
6. 四组引用、entrypoint、update-worker/dynamic/subprocess/monkeypatch strings全部闭合。
7. T029 snapshot的内容hash仅允许doctor presentation、wizard Click driver、config-bootstrap Click adapter三个exception和机械import/path changes；T029后变化由stable symbol scope + RGR evidence判定，不与T029 current-target raw hash直接比较。
8. 每个33 backing module拥有`test-ownership.md`中的verified direct/indirect owner；scheduled/planned在Verify必须为0，高风险store与durable audit必须direct L4。
9. `cross-role-edges.v1.json`实际集合差为unknown=0、expanded=0；domain跨role=0；composition/routes与CLI存量边不增长。

T017-T025是单个不可提交中间态的 atomic transaction；rollback只能整体反向move，不得加compatibility layer。
