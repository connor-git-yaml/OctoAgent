# F151 Clarifications

**状态**：T012 standard-backend checker code review被拒，当前corrective artifact-only Gate；GATE_DESIGN=false、GATE_TASKS=false。dependency resolver R/G/R与fresh unresolved=0已完成，standard-backend RED及pyproject/uv-generated lock已落地；checker存在但当前字节不具GREEN资格，shared venv未bootstrap，behavior GREEN/REFACTOR=0。冻结test/checker/pyproject/lock/evidence/v2/production，禁止执行行为命令、暂存、提交或推送。

## 已冻结决议

| 问题 | 决议 | 验收落点 |
|---|---|---|
| DX cluster 删除范围 | 49 个模块离开Provider；`dx/__init__.py`、`runtime_activation.py` delete；`memory_commands.py` 保留迁移 | source 0/51、T029 snapshot；target role tags见D-03 |
| Provider config | Gateway保留v1/v2 normalization；optional URL/transport转required，auth仅api-key env/OAuth profile；Provider删reverse loader/getattr；ProviderRoute不含真实schema不存在的headers/body | DTO contract |
| pinning/invalidation | 只evict client cache，不close共享HTTP/不清pinned Task；旧Task旧client、新Task重建 | Router regression tests |
| LiteLLM 范围 | 删除 Proxy 与 SDK runtime；pricing helper/base dependency不自动删除，只允许 pricing-only | dependency/runtime-path gate |
| 旧配置检测点 | YAML raw dict、env-after-dotenv、两个legacy files exists-only；auth/setup可不读旧文件恢复 | tombstone/recovery矩阵 |
| `OCTOAGENT_LLM_MODE` | unset/empty 与 `echo` 保留；其他值 typed reject | env behavior tests |
| execution semantics | 四个用户入口严格五值；profile capability四值；Worker backend二值；event/Console=`inline|docker`历史兼容，四域不共用enum | execution inventory + replay/projection tests |
| 历史兼容 | 内聚于现有 decoder/projection；不新建 legacy entity/service | data model 与 source gate |
| fail-closed | 用户入口为三个Control Plane action加`subagents.spawn` tool；apply/tool整批preflight，422/tool reject/exit78/503分开。同步可解析的static security/runtime config在Uvicorn前映射exit78；真正runtime composition只经既有lifespan fail closed、process nonzero | runtime contract matrix |
| bundle 构造环 | storage-only Hook → SkillRunner → final LLM → bundle；不 late-bind；AgentContext storage-only不创建MemoryRuntime/reranker/background/network | identity/mode/purity tests |
| TaskService 模式 | 基线48=4 runtime+44 storage；删除Orchestrator两点重复实例且1141改storage-only后目标46=3 runtime/43 storage | construction matrix gate |
| deterministic Inline | non-direct与Graph-start fallback保持exact precomputed result；TaskService窄seam复用现有session replay模块的唯一storage primitive，保持Task/Event/Artifact/checkpoint/SessionContext/turn/session并禁止compaction/extraction/model | characterization + L4 persistence |
| shutdown | 同一app background set、two drains、三层local `aclose`、bundle Router owner、Harness guard | order/count tests |
| complexity | 编码前真实total658/六hotspots/Ruff+config指纹ceiling + merge-base actual | frozen JSON + contract tests |
| clean-wheel | T012只给preliminary结论：真实wheel METADATA=source manifest、installed file逐import分类完整、真实child isolation观测、`final_verdict=null`；不要求当前manifest=runtime-observed imports。T023 owns manifests/lock，T070在namespace/guard/startup owners完成后首次执行full/all与final direct closure | dependency transition L4 + classification L4 + child observation L3 + T070 final L3 |
| frontend | Settings/action payload/提示移除旧输入，以 Vitest + tsc + Gateway behavior 验收 | config inventory |
| 范围保护 | F094、Echo/Mock/legacy registry、pricing、bench entry 不自动删除 | retired allowlist/scope tests |
| D-01 | 删除`JobSpec`与`ExecutionRuntimeRecord`及core exports/tests/docs；history只在raw event decoder/projection | absence + raw replay |
| D-03 | 49 move冻结为15 legacy CLI + 1 existing config + 33 operations；2 delete；13 application/5 domain/9 store/6 adapter仅是legacy mixed role tags；channel verifier归application；Doctor/wizard/config三个T029 exception | source-aware manifest/cross-role tuple/SCC/CLI ratchet |
| direct deps | T012区分runtime-required、optional-lazy、TYPE_CHECKING、test-plugin、workspace ownership并完整报告当前差异；T023写Gateway 7+25/Provider1+6 manifests，T070才做最终strict closure | distribution-owned installed AST/dynamic inventory + real METADATA + final behavior |
| evidence/coverage | Phase0标准runner artifact后硬停`PHASE0_RED_REVIEW`；main创建唯一anchor manifest并提供SHA；formal Python/Frontend只写canonical六件套；4个Final committed paths各有first-writer/producer；T122用自身stage/start tree/UTC生成fresh LCOV，拒绝复用T105 | lifecycle + producer/stage gate self-tests + main anchor |
| F150边界 | 只在F150-owned universe内允许module entry、`_resolve_front_door_mode`的load/order/typed propagation与既有exposure handler变化；7个FrontDoor protected symbols及`create_app`其余body不变；D-03 main.py import-only由namespace gate单独批准 | exact before/after AST + negative sibling fixture |
| production启动 | `python -m octoagent.gateway`是唯一production/service入口；entry先解析help/host/port，再在typed boundary只import一次`main.app`；`main.app=create_app()`执行唯一static preflight，`_resolve_front_door_mode`先完整load config恰一次、再应用env>YAML mode，Uvicorn接收app instance与相同host/port；普通load/start遇legacy direct descriptor typed reject且0写，显式install/update/bootstrap才可atomic migrate；真正composition failure留在lifespan且process nonzero | startup inventory + L3 descriptor/static-exit/lifespan tests |
| atomic迁移证据 | T029冻结base source→target normalized AST/content snapshot；后续target改动由stable symbol scope+RGR evidence授权；Final复验snapshot against base而非当前target raw hash | atomic gate + three negative fixtures |
| RGR scope | 98/98 slice machine scope；clean-wheel checker由S011 6、T012 standard 5、classification 3、child observation 2、T070 full 4与final closure 1，共21个互斥selectors。root pyproject/lock transition仍由resolver证明unresolved=0 | 四个T012 corrective slices + 两个T070 final slices + scope/stage gate |
| planned diff | 11个machine sources与35个additional exact paths覆盖source→target/delete、production/tests/config/docs/scripts/frontend/workflow/benchmark/F151 current+superseded governance artifacts；planned无owner、owner不在plan、changed无owner均失败 | `planned-diff.v1.json` + field-existence/F151-self-match/history-owner fixtures |
| operation mode | 42个TaskService/AgentContext operation与46+3个production构造点由machine allowlist分类；unknown默认deny，storage-only不得可达model/reranker/background/network/runtime | operation manifest + S081/S083/S084 |
| Provider test universe | 44个collectable tests rehome、1个retired test delete；manual wire recorder exact decouple Gateway dotenv import；旧Provider test tree对Gateway依赖目标0 | provider test map + AST/import/collect projection gate |
| active artifacts | 42个current artifacts、6个Round4-9 review标SUPERSEDED；phase lifecycle精确枚举generated-local/committed evidence；S104扫描17份exact active authority docs，不借S100 workflow证据 | active/lifecycle/authority manifests + S104 docs gate |
| descriptor read | canonical/legacy argv/invalid schema/invalid JSON的普通load/start/restart目录字节0变化；不得normalize/save或生成`.corrupted`，repair只在显式transaction | L4 fake + L3 tmp directory/service tests |
| update worktree safety | dirty/staged/untracked均在fetch/checkout/reset/merge/uv前typed `LOCAL_CHANGES_PRESENT` | fake runner L4 + real tmp Git repo L3 |
| high-risk ownership | Telegram RMW与Update active-attempt TOCTOU为must-fix；backup path mix为follow-up/no-growth | test-owner + smell inventory |

## Main 决策记录

| ID | 决定 | 约束 |
|---|---|---|
| D-01 | 选择A：删除两个死core models | 不建legacy model/service；同步exports/tests/docs |
| D-03 | 选择ownership A：15 CLI + 1 config + 33 operations + 2 delete | legacy role edge exact ratchet；services/application→presentation=0；三个有界exception；动态SCC只ratchet不谎称消失 |

`octo-bench`已冻结为保留entry、wheel中副作用前exit69`SOURCE_CHECKOUT_REQUIRED`，不再是待决项。

无新的产品决策；当前等待单一T012 corrective Design/Tasks Gate复审。Gate需一次批准后续本地批次：test rewrite/observable delta→两条新formal RED+fresh main-owned S011 direct RED→checker/scaffold修正→GREEN→REFACTOR。旧240558/294b2e/61047e证据在test改写后只作superseded history，禁止复用。只在合同矛盾、scope/authority变化、外部/破坏性操作或真实架构分叉时hard stop。
