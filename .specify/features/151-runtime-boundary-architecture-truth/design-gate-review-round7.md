# F151 GATE_DESIGN / GATE_TASKS Round 7 Review

**结论**：`GATE_DESIGN=false`，`GATE_TASKS=false`。本轮只返修constitution与F151 Spec Driver artifacts；未进入T001，未修改production/tests/repo-scripts/workflow，未stage/commit/push。以下“通过”只表示artifact的静态/机器一致性，不表示实现、TDD、coverage或clean-wheel已通过。

## 写入制品清单

- constitution：`.specify/memory/constitution.md`。
- 核心Feature制品：`spec.md`、`research.md`、`research/tech-research.md`、`clarifications.md`、`data-model.md`、`contracts/runtime-boundary-contract.md`、`plan.md`、`tasks.md`、`quickstart.md`、`trace.md`、`analysis-report.md`、`checklists/requirements.md`与本Review。
- Round 7 machine inventories：`namespace-migration.v1.json`、`provider-test-rehome.v1.json`、`agent-context-test-constructors.v1.json`、`runtime-test-constructors.v1.json`、`runtime-test-behavior-owners.v1.json`、`cross-role-edges.v1.json`、`rgr-slice-scopes.v1.json`、`planned-diff.v1.json`。
- 同步的人读inventories：`production-startup.md`、`f150-scope.md`、`runtime-bundle.md`、`namespace-migration.md`、`config-retirement.md`、`architecture-quality.md`、`rgr-slices.md`、`testing-matrix.md`、`stage-commands.md`。其余F151 inventories与Round 4–6历史Review保留为上下文，未改production/test实现。

## Round 6 RETURN逐项闭环

| # | 旧问题 | 源码/基线证据 | Round 7修改位置 | 机械oracle/当前结果 |
|---:|---|---|---|---|
| 1 | module entry若先独立preflight再import `main.app`会重复构造/校验；真实wrapper仍直启Uvicorn | `gateway/main.py:496 create_app`、`:587 app=create_app()`；`scripts/run-octo-home.sh:33`仍执行module string | production-startup、f150-scope、spec FR-008/041、contract §4、S064、T064、constitution | entry只解析help/host/port；typed boundary内只import一次`main.app`；config/exposure各一次；Uvicorn只收app instance与exact-equal host/port；第二factory/global snapshot/module string均为negative |
| 2 | ordinary descriptor read会normalize/save，invalid JSON写`.corrupted` | `provider/dx/update_status_store.py:69/77/162/188` | production-startup、architecture-quality S19、spec FR-041、contract §5、S064-descriptor-read、T064 | canonical/legacy/invalid schema/invalid JSON普通load/start/restart目录字节0变化；显式install/update/bootstrap才可validated atomic migrate/repair |
| 3 | 39个Provider tests没有exact rehome map，Phase4仍引用退休路径 | Provider现有40组引用事实=39 rehome+`test_runtime_activation.py` delete | `provider-test-rehome.v1.json`、namespace、runtime constructor projection、planned-diff、T021/T029/T084 | machine map=39 move+1 delete，source/target unique；constructor/stage先投影final path；退休test owner path consumer=0 |
| 4 | precomputed completion只改TaskService会漏SessionContext/turn/session或复制`record_response_context`算法 | `agent_context_session_replay.py:56 record_response_context`把持久化与extraction绑在一起 | runtime-bundle、spec FR-023、plan Phase4、contract §8、S081/S082、T080-T082 | 既有session replay模块拆唯一storage primitive；Task/Event/Artifact/checkpoint/SessionContext/turn/session exact；model/Router/recall/compaction/extraction=0；不新增service/runtime/registry |
| 5 | AgentContext `storage_only`仍可能构造MemoryRuntime/reranker或auto-load | `agent_context.py:280`无条件构造MemoryRuntime；`agent_context_memory_services.py:227`暴露reranker getter | runtime-bundle、data-model、contract §8、S083-storage-purity、T083、constitution | storage-only构造对MemoryRuntime/reranker/model-load/background/network全部0；runtime方法typed fail fast |
| 6 | 41/147被写成完整interaction graph；异步close命名漂移 | baseline只能静态枚举41 imports与147 direct-name calls，无法覆盖全部attribute interactions | cross-role、architecture-quality、spec FR-001/034、contract、plan、constitution；runtime-bundle统一`aclose` | 统一称ceilings；stable identity只减不增；changed hunk额外attribute-call+manual adversarial review；bundle/LLM/SkillRunner/client/Router异步API统一`aclose` |
| 7 | 80/80 slice ID相等不能证明planned file都有owner | config/UI/root docs/scripts/workflow/benchmark与source→target/delete来自多套清单 | `planned-diff.v1.json`、S002 manifest integrity、spec FR-042、plan、contract、T002/T120 | 6个machine sources+28 additional exact paths闭合；planned无owner、owner不在plan、changed无owner均失败；真实shared path修正为`frontend/src/domains/settings/shared.tsx` |
| 8 | frontend selector从frontend cwd仍带`octoagent/frontend/`前缀，会0 selected | testing命令在`(cd octoagent/frontend && ...)`运行 | rgr-slices四类frontend RGR、testing matrix、quickstart | selector统一`src/...`；每slice冻结实际argv、unique test name、selected=1、no collection error |
| 9 | overlap字段存在但未保证selector可执行；update_service同symbol三种dirty行为可漏slice | staged/unstaged/untracked都改同一UpdateService guard symbol | scope JSON selector grammar、shared subgroup、stage negatives、S002 | selector解析非空、覆盖changed hunk、非subgroup不相交；duplicate/nonexistent失败；update_service三slice all-required；T049/T070真实diff不追索未来phase |
| 10 | S082独立REFACTOR-only不可验证；T047混入用户行为删除 | deterministic behavior先由S080 characterization与S081 GREEN建立；config sync/activate/CP/UI是独立用户行为 | rgr-slices、scope、tasks T043/T046/T047/T080-T082 | S082含`refactor_of`；T047只做mechanical deletion/manifest/lock/absence；六个用户行为有独立RGR/characterization selector |
| 11 | S084只跑两个AST gate，未覆盖44个owner paths；constructor helper/fixture qualname又不能当pytest node | tests目录TaskService calls=144，AgentContext calls=31；其中20个TaskService与1个AgentContext qualname不是collectable test node；duplicate顶层test另遮蔽一个constructor | 两份constructor JSON、`runtime-test-behavior-owners.v1.json`、C084、S084、T084 | TaskService identity144 unique=143 live+1 shadowed；AgentContext31 unique=23 storage+8 runtime；C084 owner set=44，执行43 files+1 live-helper L4 node，selected>0/fail0/skip0/rerun0 |
| 12 | T001-T004需要evidence但formal checker到T005才存在，trust anchor可被事后替换 | Phase0 checker不能自证自己的首轮RED | rgr-slices、testing/stage matrix、tasks T001-T005、plan、contract、constitution | 只用标准pytest/Vitest shell transaction；T004后硬停`PHASE0_RED_REVIEW`；main通信锚定artifact SHA；T005只消费同一字节，不得替换/补跑/重哈希 |
| 13 | C02总体`-k`可漏整组；C05层级失真；SDK lock文案冲突 | C05含integration selectors；T048后SDK path已删除 | testing-matrix、stage-commands、quickstart、tasks T120 | C02为10个exact nodes；C05=L4+L3；pre-SDK命令含SDK，post-SDK/C23/C24不含；C19 pre/post=10/9 paths |

## 后续机械发现闭环

| 发现 | 源码/证据 | 闭环位置 | 当前机器结果 |
|---|---|---|---|
| duplicate test qualname | `test_task_service_context_integration.py:1782`与`:2043`同名，前一定义不可collect | runtime constructor inventory、S084-shadowed-test、T084、quality-smells | baseline 144 AST identities=143 live+1 shadowed；meta RED只选可collect node，rename后两个真实behavior nodes仅作characterization |
| direct-name calls必须存全部tuples | 仅存4 high-signal不能防等量替换 | cross-role JSON | calls=147、stable identities=147；high-signal只作派生子集 |
| import identity不得含line | D-03移动/格式化会漂line | cross-role JSON | imports=41、stable identities=41；line report-only；source path先按namespace map投影target |
| scope markdown/JSON与future selector | 旧80/78、future renamed node会collection error | rgr-slices/scope/stage negatives | 当前86/86双向exact；四个frontend slices精确覆盖Settings/Pending、Home/Workbench、App/shared；future/nonexistent selector失败；characterization恢复node不冒充RED |
| cross-phase partition | 同path Phase2/3/4若all-required会让早期C20不可运行 | 36个symbol partitions、3个same-hunk groups | overlap paths=39；cross-phase paths=20/members=74，unresolved=0 |
| T029迁移后target继续合法修改 | final raw hash不能证明迁移时点纯机械 | namespace、scope、atomic fixtures | T029保存base replay snapshot；post-T029变更需owning RGR；迁移夹带/后续授权/后续未授权三类fixture冻结 |
| declared-new误标existing path | 旧清单曾把`octoagent/uv.lock`标new | scope self-check | declared-new=87且base-existing=0；existing path误标与broad escape都失败 |
| planned owner与exact path闭包 | ID/count一致仍可漏docs/config/frontend | planned-diff JSON | machine source count=6、additional exact paths=28、planned_without_owner=0、owner_outside_plan=0 |

## 数量与映射自审

- FR：`FR-001..042`连续，42/42有task映射。
- tasks：76个unique ID，全部`[ ]`，物理顺序与DAG一致。
- RGR：Markdown 86 IDs，scope JSON 86 keys，双向差集为空；frontend恰为四个slices。
- scope：39 overlap paths、3 overlap groups、36 partition paths、20 cross-phase paths/74 members、unresolved=0；declared-new 87且base-existing=0。
- namespace/Provider tests：production 49 move+2 delete；Provider tests 39 move+1 delete。
- cross-role：41 import objects、147 direct-name call objects，count=unique，line不参与identity。
- constructors：TaskService144 unique=143 live+1 shadowed；AgentContext31 unique；C084 behavior-owner set=44且selectors=44（43 files+1 node）。
- startup/F150：7 protected symbols、两个allowed handler与3个module-entry symbols；`create_app`其余AST冻结。

## 四项真实状态与剩余风险

- **TDD**：实际RED/GREEN/REFACTOR=0。Phase0锚定协议尚未执行，任何checkbox均未完成。
- **测试分层**：设计以L4 deterministic contract/store/service/constructor purity和L3 startup/API/Event/wheel/tmp Git为主；F151新增L1/L2=0。C05明确混合L4/L3。
- **架构分层**：operations是legacy mixed cluster；41/147只是ceiling，完整interaction未被虚构。新seam仍必须遵循clean direction，domain纯净。
- **坏味道**：ordinary read hidden write、storage-only hidden runtime、session persistence/extraction耦合、duplicate test等均是must-fix且仍open；现有SCC/CLI15/backup path等按ratchet/follow-up管理。

剩余风险是所有实现与实际gate尚未开始：clean-wheel、import-direction、retired-term、complexity、coverage、xdist与真实RGR均没有运行结果。Round 7仅请求main复审`GATE_DESIGN`与`GATE_TASKS`；不请求Implement。
