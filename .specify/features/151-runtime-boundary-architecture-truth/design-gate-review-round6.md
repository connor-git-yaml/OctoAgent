# F151 GATE_DESIGN / GATE_TASKS Round 6 Review

**结论请求**：重新提交GATE_DESIGN与GATE_TASKS复审；两Gate当前仍为`false`。本轮只验证Spec Driver artifacts，未进入T001，未执行TDD、production实现或Verify，不请求Implement。

**冻结baseline**：`9d5e1e48691c5ae5a12b33f224d64ac03d5442fc`。

## 1. Round 5 RETURN逐项闭环

| 项 | 旧问题 | 源码/基线证据 | Round 6修改位置 | 机械结果 |
|---|---|---|---|---|
| A production入口 | 新module entry会与真实wrapper/descriptor direct Uvicorn形成第二主路径 | `run-octo-home.sh`、install bootstrap/runtime descriptor与service/update生成argv仍指向`octoagent.gateway.main:app`；普通descriptor路径可读写 | `inventories/production-startup.md`、FR008/041、contract §5/§8、S064、T064 | 唯一入口冻结为`python -m octoagent.gateway`；普通legacy read/start typed reject且0写；active direct production argv目标0 |
| B F150 scope | “allowlist外全production diff=0”会拒绝F151自身合法改动 | F150-owned范围可限定为7 protected symbols、2 warning handlers与module entry；D-03只需`main.py` import relocation | `inventories/f150-scope.md`、FR040、S002/S064/T070 | 无关F151 file允许、protected drift拒绝、D-03 import-only允许/body变化拒绝的negative fixtures冻结 |
| C legacy layering | 13/5/9/6被错误宣称为physical clean layers | baseline存在application→adapter/store、store→application、adapter→application/store | `cross-role-edges.v1.json`、namespace、architecture-quality、constitution | 13/5/9/6仅role tags；41 import+147 call exact baseline只减不增；domain仍严格纯净 |
| D test constructors | 只迁production 48点会让约147处文本/144 direct test constructors集中爆炸 | AST `Call(TaskService)=144`；另有subclass定义/动态子类；44个LLM override | `runtime-test-constructors.v1.json`、runtime-bundle、S084、T084 | 144 machine identities唯一；S084收窄为44 exact owner test paths，无全测试glob；unknown/override目标0 |
| E test owner | 33/33表格含伪direct与planned冒充covered | backup audit仅间接；secret/sleep owner路径失真；六项无direct node | `test-ownership.md`、S034、T024/T034/T035 | direct/indirect/declarative/scheduled语义冻结；六项exact scheduled nodes、BackupAudit direct L4；Verify scheduled=0 |
| F evidence bootstrap | T001-T004 RED依赖T005才实现的runner，CLI语法冲突 | checker/runner在Phase0开始时不存在 | `rgr-slices.md` Phase0 bootstrap、contract evidence、T001-T006 | bootstrap仅固定pytest/JUnit/raw/exit/git metadata；T005必须重新解析；formal run/verify只有一套argv |
| G dirty required set | `base...HEAD`会在本地dirty phase vacuous PASS | 实施阶段HEAD可能不变但staged/unstaged/untracked已变 | FR039/042、contract §11、S004、C20 | local set合并committed+staged+unstaged+untracked；changed非空/required=0稳定失败 |
| H machine ownership | production paths含自由文本，无法映射changed hunk | “config schema+resolver”“all constructors”等不可解析 | `rgr-slice-scopes.v1.json`、S002 manifest tests | 80/80 slices；每项exact path/inventory/symbol或合法characterization watch；free-text ownership拒绝 |
| I future-path gates | T014/T034会引用T100/T063后才创建的文件 | stage执行时future selector会exit4/not-found | `stage-commands.md`、testing C06-early/final、C08-safety/execution/startup | 每个stage只引用producer≤stage的文件；future/retired path与selected=0均失败 |
| J C19 SDK状态 | T048后仍把已删除SDK testpath传pytest会exit4 | pyproject 9 paths；retirement前另含SDK为10，后为9 | testing C19-pre/post、stage matrix、T029/T049 | 两条single transaction；两次`--cov`、explicit report parent、pre=10/post=9 |
| K evidence accept path | 全是reject fixtures时checker可“全部拒绝”假绿 | JSONL/raw自述不能证明JUnit/exit/oracle事实 | S004、contract §11、testing matrix | pytest/Vitest完整RGR positive；oracle/failing set/extra failure/wrong assertion/JUnit mismatch negatives冻结 |
| L command一致性 | quickstart缺完整PYTHONPATH、benchmark漏第二node、final argv临场拼 | tests/AGENTS要求worktree锁；C17有bench guard+provider-error两node | quickstart、testing C17/C23/C24、stage matrix | architecture命令含完整PYTHONPATH；benchmark两exact nodes；T121/T123完整env/cwd/testpaths/markers/components冻结 |

## 2. 后续源码/manifest审计闭环

| 发现 | 源码/机械证据 | 修改位置 | 结果 |
|---|---|---|---|
| benchmark guard无slice | FR009要求wheel副作用前exit69，但原S046只是ProviderError characterization | `S045-bench-guard`、T045、C03/C17 | 独立exact node`test_source_checkout_required_before_side_effects`执行RGR；S046-bench只保留原行为characterization |
| six planned owners | 6个module无真实direct import/node | test-ownership exact scheduled nodes、T024/T035 | Verify planned/scheduled=0；runtime descriptor拆L4 fake+L3 real tmp Git |
| startup argv含糊 | wrapper `$@`不能证明支持集合与同一parser | production-startup PSTART-03/04/05、S064-startup-entry | 只支持help/host/port；duplicate/unknown exit64；preflight/Uvicorn共享resolved object |
| descriptor隐藏写 | 普通load/start normalize+persist会产生读路径外部写 | production-startup、contract §5、FR041 | 普通路径typed reject/0写；显式install/update/bootstrap才atomic migrate |
| duplicate test qualname | 同文件两个顶层`test_task_service_prompt_context_only_exposes_sanitized_control_metadata`，前定义被后定义覆盖 | constructor JSON、S084-shadowed-test/restored-behavior、T084 | baseline=143 live+1 dead-shadowed；RED仅可collect meta node；rename后两个behavior node作CHARACTERIZATION |
| constructor identity collision | 原`path+qualname+ordinal`对重复definition碰撞 | runtime-test-constructors identity schema | 加`definition_ordinal`；entry count=unique identity=144，line只report |
| call ratchet假精确 | 147 calls只有4 high-signal tuples会漏等量替换 | cross-role JSON | 147个完整call objects，stable lexical identity；high-signal只是派生子集；count=unique=derived=147 |
| import identity含line | 41 import数组把line放进tuple会因move/格式化漂移 | cross-role JSON | 41 import objects；identity=`projected_path+qualname+kind+target+lexical_ordinal`，line report-only；迁移前先投影target |
| shadowed RED future selector | rename后node在baseline不存在，执行会collection error | S084-shadowed-test | RED只选meta gate；baseline collect是before evidence，不冒充RED |
| scope markdown/JSON不等 | 曾为80 vs 78 | rgr-slices + scope JSON self-check | 双向80/80 exact equal |
| S084 scope过宽 | 全仓tests glob会从Phase0要求Phase4 evidence | S084 machine scope | exact 44 owner paths，无broad globs；未列测试由各slice node manifest owning |
| cross-phase overlap | 同path shared group会让T049/T070追索未来slice | 33 symbol partitions、stage negative fixtures | actual overlap35、shared groups3、cross-phase unresolved0；T049/T070 future evidence分别为0 |
| declared-new逃逸 | 宽glob或existing path可让unknown change假绿 | scope schema/self-check、S002 negative fixtures | declared-new globs=0；85 exact paths均在base absent；existing path误标失败；`octoagent/uv.lock`归S047 existing path |
| T029 raw hash矛盾 | 49 targets在Phase2–4会合法改变，Final raw hash无法同时证明T029机械move | namespace two-stage evidence、S017、T029 | T029保存base-replayed snapshot；post-T029由stable symbol+RGR evidence授权；Final重放snapshot against base |
| S083 node名称过窄 | 合同要求TaskService与AgentContext均XOR，旧node只点名TaskService | S083 exact node、T083、scope partitions | node改为`test_runtime_bundle_is_minimal_instance_holder_and_task_service_and_agent_context_require_exactly_one_mode` |

## 3. 冻结决策与数量

- D-01：删除`JobSpec`、`ExecutionRuntimeRecord`及真实closure；历史backend/container兼容只在raw Event decoder/projection。
- D-03：51 source=49 move+2 delete；target=15 legacy CLI+1 config+33 operations；role tags=13 application/5 domain/9 store/6 adapter。
- direct deps：Gateway 7 internal+25 third-party；Provider 1 internal+6 third-party。
- production constructors：48=4 runtime+44 storage→46=3 runtime+43 storage。
- test constructors：144 unique identities=143 live+1 shadowed；44 overrides=43 live+1 shadowed。
- cross-role：41 imports、147 calls，全部exact stable identities且only-decrease。
- FR=42；tasks=76 unique且全unchecked；RGR slices=80/80。

## 4. Artifact validation结果

本轮最终机械命令已通过：FR=42/42且均有task映射；tasks=76 unique/0 checked；slice=80/80；scope=35 overlap paths/3 shared groups/33 symbol partitions/cross-phase unresolved0；declared-new=85且base-existing0；cross-role=41 imports+147 calls且count/unique/direction一致；constructor=144 unique/143 live+1 shadowed/override43+1；owner rows=33/33；relative links与Markdown fences无错误；testing/quickstart bash fences通过`bash -n`；`git diff --check`通过；active stale scan=0；指定退役网络术语scan=0；production/test/scripts/workflow tracked diff=0；cached diff=0。

这些结果只代表artifact consistency，不代表TDD、实现质量、clean-wheel、coverage或runtime行为已通过。

## 5. 四项审查摘要

- **TDD**：真实RED/GREEN/REFACTOR执行数=0；当前仅冻结协议与oracle。
- **测试分层**：L4 deterministic为主，L3限bootstrap/API/Event/wheel/tmp Git；F151新增L1/L2=0。
- **架构**：D-03是legacy mixed cluster，role tags不冒充clean layers；新seam才强制clean direction。
- **坏味道**：must-fix仍open until implementation；ratchet/follow-up已冻结，禁止big-bang。

## 6. 剩余风险与Gate状态

剩余风险全部属于implementation：checker/runner尚未落地、startup/namespace/runtime bundle尚未迁移、must-fix尚未修、clean-wheel/coverage/full verify尚未执行。因此：

- `GATE_DESIGN=false`，请求main复审；
- `GATE_TASKS=false`，请求main复审；
- 不请求Implement；
- main明确批准前不得进入T001。
