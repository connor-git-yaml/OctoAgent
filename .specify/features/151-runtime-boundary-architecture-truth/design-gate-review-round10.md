# F151 Round 10.1 + T005/T006 Corrective Design/Tasks Gate 报告

**当前结论**：`GATE_DESIGN=false`，`GATE_TASKS=false`。本轮仅完成F151 corrective artifact validation；未执行corrective RED，未修改test/checker/evidence。65-node bootstrap RED仍由main锚定；首轮GREEN/REFACTOR已拒绝，可信GREEN=0。所有production must-fix仍待实施。baseline为`9d5e1e48691c5ae5a12b33f224d64ac03d5442fc`。

## 0. T005/T006 Corrective Review Round 1 闭环

| Round 1 blocker | 当前冻结结果 | machine oracle |
|---|---|---|
| tree schema与immutable raw冲突 | bootstrap/corrective/formal统一为真实12字段；record逐字段交叉slice/phase/base/head/tree/fingerprint scope/files/status/captured UTC | 6个bootstrap tree field set与machine schema相等，field count=12 |
| recovery缺main授权输入 | C20-recover唯一exact argv新增`--main-review-message-id`；不得从env/default/聊天推断，并与corrective aggregate形成approval binding | 缺失/空/unknown/anchor-rejected-T006复用失败；argv flag count=1 |
| 跨多路径atomic rollback不可实现 | 改为R0 source、R1 runs quarantine、R2 all source quarantine、R2 partial temp、R3 durable temp、R4 complete的hash-identified单向可重入FSM；runs直接rename到existing `evidence/local`下exact quarantine root，无mkdir中间态 | runs/v1 rename及temp write/fsync/replace各中断可重入；未知混合态0写失败；不声称rollback |
| finalize自依赖 | 前置只含T120-T123与T124 input closure；C25/report/T124完成明确排除 | self dependency count=0；C25成功才形成T124 output |
| architecture all的base-ref无行为冻结 | 复用现有S004 compound node加入valid ref正向和missing/unresolvable负向，要求同一resolved commit传给base-aware subgates | 不新增nodeid；ignored/invalid base不得PASS |
| v2 frontier含糊 | identity=`lifecycle_type+task_id+slice_id+phase`；前缀固定为anchor顺序6 bootstrap RED + S004/S002 2 corrective RED；rejected v1=0，之后才GREEN/REFACTOR | prefix 8/8；bootstrap 6、corrective 2、rejected 0；顺序machine exact |

## 1. Round 10.1窄修正

| 问题 | 选择与制品修改 | 机械结果 |
|---|---|---|
| C18可能隐式使用宿主真实凭证、外部网络与成本 | 不保留C18。`testing-matrix`删除`lane.py baseline` argv，T121与stage matrix只登记C23，T123继续只登记确定性C24；spec/plan/tasks/contract/constitution/quickstart/lifecycle/producer同步禁止从默认HOME、凭证存在或skip推定授权 | 当前testing logical IDs=39、profiles=39、集合相等（含corrective C20-recover）；T121=`[C23]`、T123=`[C24]`；自动stage/producer中C18/`lane.py baseline`=0，live/host-credential/external-cost producer=0 |
| C084命令形式尚未本机预检 | 从`testing-matrix.md`提取C084 exact backtick argv，不运行pytest或写evidence；对原样inner Bash做`bash -n -c`，再仅执行`for selector`之前的manifest/selector提取片段 | 本机GNU Bash 5.3.9；`bash -n` exit0；selector-only exit0、stderr空、`owners=44 selectors=45` |

F151自动Verify不包含manual live gate。若其他Feature未来提出真实LLM/外部成本检查，必须由main先核对条件并取得用户当次明确授权；它不能替代F151确定性自动证据。

## 2. Round 10 RETURN逐项闭环

| 问题 | 源码/机器事实 | 制品修改 | 负例与机械结果 |
|---|---|---|---|
| formal RGR path/name冲突 | 旧RGR写`.bin/run.json`及`evidence/<slice>`，lifecycle写`evidence/local/runs`六件套，二者无法同时成立 | `artifact-lifecycle.v1.json`、`evidence-producers.v1.json`、RGR/testing/stage/contract统一为`evidence/local/runs/<slice>/<phase>/`与`junit.xml/stdout.txt/stderr.txt/exit-code.txt/invocation.json/tree.json` | `.bin`、`run.json`、非canonical root、缺`invocation/tree`、子集/超集均fail；2类formal producer exact-set mismatch=0 |
| superseded review无owner | planned source会物化5个旧review，但S002只展开current | active改为42 current+6 Round4-9 history，S002 machine expansion读取`current[]`和`superseded.*.path`；retired scan exception仍不授权changed path | 独立重算48/48 active/history均有S002 owner；planned/owner总集合422/422，missing=0、extra=0 |
| authority docs漏项 | Blueprint index直接链接`api-and-protocol.md`和`architecture-tradeoffs.md`，前者有Gateway↔Kernel与`/kernel/*`，后者把Docker写成当前最后防线 | authority增至17；S104/planned/spec/tasks/contract同步；`index_derivation`反算18个root links并逐项included/excluded+reason | root links实际18、candidate18、集合相等；unclassified=0；未列runtime-truth candidate、现役/✅/Mermaid旧事实失败 |
| LiteLLM dotfile matcher不可复算 | `git ls-tree -- octoagent/**/*litellm*`与`PurePath.match`均不能稳定表达zero-directory hidden file | `.env.litellm.example`改为`tracked-exact-delete`；S047以exact resolved path拥有；SDK/skill保留literal tree-prefix | base `git ls-tree <sha> -- <exact path>`复算8/8 object id；glob matcher count=0；用`**`表示根dotfile失败 |
| optional committed exact path | `execution-state`既在required equality又写“if used”；无可达producer | 从lifecycle/planned/declared-new完全移除；committed set只保留4个Final必需路径 | optional exact equality失败；当前declared-new实际124（含canonical v2）且base-existing=0 |
| completion report无producer | lifecycle要求Final completion report，tasks只到verification report | 移除completion artifact；T124以C25生成唯一`verification-report.md`，不新增虚构Final stage/task | committed path→first_state→first_writer task→producer command→Final required set四向均为4/4且集合相等 |
| T120含糊且可能复用pre-SDK | stage matrix没有T120，旧任务写“全部适用” | T120冻结22个post-SDK command IDs；新增C01/C02/C08-safety/C21的post variants | T120含pre-SDK ID、SDK path、临场增删均fail；39 command profiles与testing matrix逻辑IDs集合相等 |
| T122 coverage stage缺失 | lifecycle只允许T029/T036/T049/T070/T090/T105，T122使用C19必被拒绝或复用旧LCOV | lifecycle新增T122；stage matrix冻结`F151_COVERAGE_STAGE=T122`与C19-post+C16；C19 metadata绑定本次start UTC/HEAD/tree/worktree fingerprint并精确写五件套 | lifecycle coverage stages与stage matrix C19 stages均为7且集合相等；复用T105或stage/start binding不符失败 |
| producer/lifecycle未双向验证 | 旧self-check只计数，不能证明producer实际输出集合 | 新增`evidence-producers.v1.json`；formal、bootstrap、corrective、coverage、C084和committed producer均登记path template与exact-set；C084补`selected-nodes.json` | 8 local producers全部与5 lifecycle types的模板/名字集合相等；4 committed refs与lifecycle path/command完全相等 |

## 3. 唯一证据集合与命令闭包

独立从machine JSON重算，而非读取`self_check`结论：

| producer type | canonical root | exact artifact set |
|---|---|---|
| Phase0 pytest/Vitest bootstrap | `evidence/local/bootstrap/<slice>/RED/` | `junit.xml`, `stdout.txt`, `stderr.txt`, `exit-code.txt`, `invocation.json`, `tree.json` |
| formal Python/Frontend RGR | `evidence/local/runs/<slice>/<phase>/` | 同一六件套 |
| C19 pre/post coverage | `evidence/local/coverage/<stage>/` | `coverage.lcov`, `coverage-metadata.json`, `stdout.txt`, `stderr.txt`, `exit-code.txt` |
| C084 constructor coverage | `evidence/local/special/C084/` | `junit.xml`, `stdout.txt`, `stderr.txt`, `exit-code.txt`, `selected-nodes.json` |
| committed governance | exact paths only | `.gitignore`, `bootstrap-anchor.v1.json`, `evidence-index.v2.json`, `verification-report.md` |

Cxx逻辑command IDs=39、profile IDs=39、集合相等；stage=15（pre=4、post=11）；coverage stage=7；T120=22 exact commands；T121只含C23；T123只含C24；T122与T124各有唯一producer。formal runner只有带`--evidence-index`的`run`/`verify`语法，不接受output override。

## 4. 机器重算摘要

- JSON inventories：16份全部可解析。
- FR：`FR-001..042`连续，共42；tasks=76 unique/76 unchecked。
- RGR：Markdown=90、scope JSON=90，ID集合相等；declared-new=124，base-existing=0。
- planned/owner：materialized unique paths=422，machine owners=422，missing=0，extra=0；其中active/history=48，S002 owner missing=0。多owner路径仍由既有symbol/shared subgroup规则治理，不以路径计数替代hunk分区。
- authority：documents=17/unique17；Blueprint root link derivation=18/unique18/unclassified0。
- tree/exact delete：8 entries/8 unique，base mode/type/object id mismatch=0。
- lifecycle：committed=4、producer refs=4、Final required=4，三个集合双向相等；local producers=8且与5类lifecycle template/name exact-set mismatch=0；tree exact fields=12、v2 prefix=8、recovery states=6。
- stage：profiles=39、testing logical IDs=39；coverage lifecycle/stage集合均为`T029,T036,T049,T070,T090,T105,T122`；自动C18/live/host-credential/external-cost producer=0。
- production/tests/repo-scripts/workflow tracked diff=0；cached diff=0；既有根`.gitignore`一行改动未触碰。

## 5. 四项真实状态与剩余风险

- **TDD**：65-node bootstrap RED已由main锚定，anchor SHA=`77ca1bb3ffb26d69e85fb06e830c2b6cb1a3756080336831ab36259d41037878`且36个raw仍匹配；首轮GREEN/REFACTOR被拒绝，可信GREEN=0；corrective RED尚未执行。
- **测试分层**：L4仍负责gate/DTO/model/store/service/adapter/constructor；L3负责startup/API/Event/wheel/tmp Git；F151新增L1/L2=0。T122 coverage与T120 regression只是Verify contract，不冒充RGR。
- **架构真值**：D-01、D-03、startup A/B、Provider44+1+1、Runtime46=3+43均未改；operations继续诚实标为legacy mixed cluster。新增清单全是non-runtime gate input，没有新增service/registry/config/runtime。
- **坏味道**：destructive update、Telegram RMW、Update TOCTOU、storage-only purity、duplicate test、session persistence seam等must-fix仍open；complexity/coverage不替代职责审查。

当前没有新的产品/架构决策请求。请main复审T005/T006 corrective `GATE_DESIGN`与`GATE_TASKS`；复审前不得执行corrective RED、修改test/checker/evidence、进入T007或production Implement。
