# F151 T006 Closure / Formal Frontier Fix / T102 CI Wiring Checklist

## 冻结设计

- [x] CHK001 authority baseline与写入边界未变；T001-T016完成。records33/38/39保持chain-required，active direct/selector attestation不进入v2；T017 atomic仍关闭。
- [x] CHK002 D-01删除闭包；D-03=15 CLI+1 config+33 operations+2 delete，13/5/9/6只作legacy role tags。
- [x] CHK003 cross-role只称41 import+147 direct-name ceilings；changed hunk另做attribute-call/adversarial review。
- [x] CHK004 ProviderRoute真实字段、Gateway7+25与Provider1+6 direct deps冻结。
- [x] CHK005 四个target_kind入口、四域值域、whole-batch、422/503/tool矩阵闭合。
- [x] CHK006 Runtime production 48=4+44→45=3+42；42 operation与45+3 callsite machine allowlist，unknown/default deny。
- [x] CHK007 test constructors TaskService144=123 live test/nested（含3 skipped）+20 helper+1 shadowed；AgentContext31=23 storage+8 runtime；skip rewrite/helper reverse-call/duplicate rename协议闭合。
- [x] CHK008 唯一production/service module entry；完整config load先于env mode；static security/runtime invalid→exit78，lifespan composition failure独立fail closed。
- [x] CHK009 descriptor普通四类read/start/restart字节级0写；显式transaction才migrate/repair。
- [x] CHK010 F150 scope只开放module entry、`_resolve_front_door_mode` exact load/order/typed propagation、既有exposure handler与machine-evidenced T042 exact detector call；create_app其余AST冻结。
- [x] CHK011 Provider tests=44 move+1 delete+1 manual recorder decouple；旧Provider test tree→Gateway dependency目标0。
- [x] CHK012 async lifecycle=`aclose`，唯一background registry/two-stage drain；precomputed seam复用唯一session storage primitive。

## Machine scope、TDD与命令

- [x] CHK037 T012 preliminary四类context与workspace owner正交；literal `resolved|unowned` inventory、unowned projection/count、target或lock匹配项目purelib RECORD及真实child双来源边界已冻结。T070仍唯一要求unowned=0。

- [x] CHK013 FR-001..042连续且映射task；tasks=76 unique、76 checked、0 unchecked；T001-T124全部完成。
- [x] CHK014 RGR markdown/scope=103/103；T043四个frontend slice、T044 structural readiness、T045四个source guard与T046五个retirement behavior slices的exact node/oracle/formal R/G/R闭合，`S046-bench`与`S123-task-detail-sse-stability`只作独立characterization；HomePage与US-12回归fixture分别以behavior watch归属对应slice；clean-wheel checker 21 selectors互斥且唯一；root pyproject/lock的4个`dependency:` selectors经fresh resolver验证unresolved=0；无transfer例外。
- [x] CHK015 overlap paths42、shared groups3、symbol partitions40；25 cross-phase paths/95 machine-counted members；T042 companion与S064 selectors pairwise-disjoint；三态与add/delete semantic delta已由resolver R/G/R及fresh revalidation证明。
- [x] CHK016 S084=44 exact owner paths；C084=42 files+3 exact nodes，selected>0且failure/error/skip/rerun0。
- [x] CHK017 declared-new=127且base-existing=0；T035三个T024 base-absent owner-test paths由S017 exact `test_paths`取得ownership；canonical v2与rejected v1均有exact governance owner；broad escape=0；declared-new本身不构成ownership。
- [x] CHK018 planned diff=11 machine sources+35 additional paths；base-bound tree delete=8；仅hash不变既有`.gitignore` patch可减，其他Feature/docs/design/unknown evidence及三向缺口均失败。
- [x] CHK018A atomic owner closure消费既有`owned_test_paths`/`behavior_watch_paths`与声明式machine expansions；10条S084 changed tests、24条S017 namespace consumer rewrites均有精确owner，`declared_new_paths`仍不授予ownership。
- [x] CHK019 active artifacts=42 current+6 superseded且均有S002 governance owner；artifact lifecycle=6 states/4 Final必需committed paths/5 local types；S104按索引反算扫描17份exact authority docs。
- [x] CHK020 C084支持pytest9 nested/single testsuite并拒绝malformed/missing/failure/error/skip/rerun/selected0。
- [x] CHK021 anchor与36个bootstrap raw逐字节不可变；首轮v1 index及GREEN/REFACTOR按exact SHA标为REJECTED且禁止删除、覆盖、补跑、stage或复制为valid record。
- [x] CHK022 C02 exact nodes、C05=L4+L3、C19-pre/post=10/9、T120 exact post-SDK、T122 own fresh coverage、C23/C24/C25 post-SDK；T121只含C23、T123只含C24，C18/live/host-credential/external-cost自动producer为0；stage future-path检查闭合。
- [x] CHK023 T049不追索Phase3/4、T070不追索Phase4；shared subgroup漏slice与partition空/重复/未覆盖均失败。
- [x] CHK024 T029 two-stage snapshot；迁移时夹带业务hunk、post-T029授权/未授权fixture闭合。历史same-path coverage五件套固定为`INVALID_C19_COVERAGE_ATTEMPT`；显式AFTER snapshot的source base→target final corrective已direct R/G/R。最终fresh C19以322/342（94.152047%）、rerun0通过并形成accepted canonical五件套；S029 formal R/G/R追加records63-65，C20-pre exit0，T029闭合。
- [x] CHK024A S030 formal R/G/R追加records66-68；EXEC SecretRef必须显式注入唯一runner，host subprocess、missing runner、非零退出与空输出的正负合同闭合，无第二runner、global fallback或兼容层。
- [x] CHK024B T031三个update dirty-preflight slice按unstaged→staged→untracked追加records69-77；porcelain XY前导列保持，typed `LOCAL_CHANGES_PRESENT`发生在worker/fetch/merge/uv前，真实Git HEAD/index/files不变且危险命令0。
- [x] CHK024C T032 Telegram RMW追加records78-80；两个store实例以同一file-lock覆盖read→mutate→atomic replace，delete/offset并发结果均保留，失败原bytes不变且无半写。
- [x] CHK024D T033三个slice追加records81-89；active-attempt持久化CAS只允许一个claim owner，update/release匹配owner+token，并发apply仅启动一个worker。
- [x] CHK024E T034追加records90-92；backup三类lifecycle events typed roundtrip、跨实例retry幂等且store error回滚无半写。
- [x] CHK025 T005纠正链保持有效且不可重放；T006两条slice均完成真实RED→GREEN→REFACTOR，C20-amend复用同一`run`/parser，未新增subcommand/runner/registry/support module。
- [x] CHK026 T006完成态canonical v2为26条record；20条可信前缀与12个formal run冻结，尾链严格为2 RED→2 GREEN→2 REFACTOR。正式T007-T010两条coverage slice的R→G→R随后追加第27-32条record；record/order/run=32/32与prior byte immutability均由main独立复核。
- [x] CHK027 committed mode复用fingerprint scope；clean/evidence-only与三类dirty合同全绿。index node覆盖一次采用、prior20/12runs、重入、负例与T006 frontier；actual through-task T006通过。post-subfix checker SHA=`353c2e750280163827dbebda59eda886913a13fcee40fff27364ce0cc2a7a11d`、2787 LOC/123函数/最大50行/McCabe≤10/职责簇8、single parser+runner已冻结新的no-growth ceiling。

## Formal frontier order与RED recording

- [x] CHK027A 首次formal T007调用在pytest前以`EVIDENCE_RGR_ORDER_INVALID`失败，run/record均不存在并保持INVALID历史；修复后正式T007-T010两条coverage slice的R→G→R均获main接受。REFACTOR aggregates=`84ebc62e68413f6c79e1ec22cdc697615dbd164d892ce88fcc8ffcb843c664f7`/`5dc9b6857dc277f60a7c30ada0b021ed8b48714f540321e7788607d96e5d65d3`；T010 checkpoint v2=`8d4ca127e3f1d91704fc675a600ca7425ae8fefd4f66c4999f5660875d545603`、records=32、head=`04355c75d348f42a5762646acc1b457acd79293dc1857265d814deded55b3e25`、run↔index=32/32。
- [x] CHK027B 5-Why定位为T006硬编码末端、未消费machine RGR，以及隐式单任务、两任务范围、三任务箭头均未统一映射；影响面限单一evidence runner frontier。
- [x] CHK027C test-code合同直接读取真实`rgr-slices.md`与canonical 26-record prefix；冻结S007/S008/T009 partial order、S013/S015单任务、S088的T088 RED→GREEN/T089 REFACTOR、S100的T100→T101→T102、CHARACTERIZATION/ATOMIC排除、T006 exact tail及非RGR T014 through-task。
- [x] CHK027D expanded RED/GREEN/REFACTOR aggregates=`7374b390…a98f8`/`93a592c7…9fee5`/`b0fb2002…fbc4b`，同一test SHA=`a3b9b0f9…9b9fa`，均获main接受；旧RED `6a926d…43aa`仅为superseded history。Fix的direct证据未修改canonical v2；其后正式T007 RED合法追加第27条record。
- [x] CHK027E formal RED recording exact node已完成direct R→G→R；新run显式`LITELLM_LOCAL_MODEL_COST_MAP=True`并由同一三项env构造exact command。既有26 records作为不可变prefix继续校验fixed identity、末端hash、chain、两项env与逐条record；合成合法第27条formal tail可接受，tail env missing/False拒绝，非formal tail沿用既有合同。14个fail-closed负例含env missing/False/command mismatch。main接受的RED/GREEN/REFACTOR roots为`/tmp/f151-formal-red-recording-offline-red-main.bcLvA9`、`/tmp/f151-formal-red-recording-offline-green-main.wuvcfT`、`/tmp/f151-formal-red-recording-offline-refactor-main.1TddLp`，aggregates=`0187697982a67b839081caea16c73b3711e8d53ddcef3a9bdd64d973c08c13f6`/`7e585c12b097148bfa455c463cc89f48379f557ab897c09b4f8fe0cc9894426e`/`b926a9cb6c7758835b26c1e8902cfff6e46c7a3dc0a963e01c8a87a5ad53703d`，test/checker SHA=`8b9d8790615ff115fcdc1fb2c9ed47a175fd614900e2a531fda189f75d371fbe`/`353c2e750280163827dbebda59eda886913a13fcee40fff27364ce0cc2a7a11d`。direct证据不写canonical v2。
- [x] CHK040 T047-T048 Proxy/config/SDK机械retirement已完成；T049 C03/C03-retired/C07/C09/C10/C13/C16/C17/C19-post/C20-post全部PASS，未运行C11/full/all。fresh C19主suite=5345 passed/11 skipped/1 xfailed/1 xpassed、scripted E2E exit0、rerun0、coverage=677/729（92.9%）；canonical T049 coverage五件套与C20-post闭合，下一frontier为T060。
- [x] CHK041 T060五个selector slice均完成正式R/G/R；四入口exact target、domain `graph|inline` backend与真实模型owner scope闭合。相关回归11/11、complexity合同8/8、真实Ruff totals=653≤658及`architecture all` PASS；canonical v2=170/head=`51f02c6c…0ec`，下一frontier为T061。
- [x] CHK042 T061两个batch slice均完成正式R/G/R；Control Plane apply与tool spawn在任何work读取、cancel、apply或首个child前完成完整batch selector与TaskRunner可用性预检，失败副作用0。整文件回归13/13、Ruff与`architecture all` PASS；canonical v2=176/head=`0f35dcea…0cf`，下一frontier为T062。
- [x] CHK043 T062两个spawn preflight slice均完成正式R/G/R；缺TaskRunner不再fallback `TaskService.create_task`，Graph target在child创建前调用真实backend构造预检。相关回归18/18、Ruff与`architecture all` PASS；canonical v2=182/head=`35df065c…44c3`，下一frontier为T063。
- [x] CHK044 T063两个真实HTTP mapping slice完成正式R/G/R；unsupported selector精确返回422，runtime unavailable保持503，三个集成合同均证明accept control、REQUESTED→REJECTED与拒绝前workload副作用0。spawn HTTP因T062后已绿而改作characterization；canonical v2=188/head=`f71ef244…a1d`，下一frontier为T064。
- [x] CHK045 T064五个startup/descriptor slices完成正式R/G/R；唯一module entry在应用导入前完成参数解析，runtime/security invalid在Uvicorn前exit78，Uvicorn只接收app instance与exact host/port；descriptor普通四类read/start/restart目录0写，显式install才迁移。exact合同9/9、相关回归127/127；canonical v2=203/head=`61a6fe03…1899`，下一frontier为T065。
- [x] CHK046 T065请求期security配置源损坏完成正式R/G/R；统一dependency在workload前返回503 `FRONT_DOOR_CONFIG_INVALID`，valid accept control保持且invalid execute/child/apply=0。同文件4/4、architecture all与through-task PASS；canonical v2=206/head=`cb5a55d7…fffff`，下一frontier为T066。
- [x] CHK047 T066真实Orchestrator characterization直接PASS：preflight后Graph消失返回typed error、Graph1、Inline0、同Task FAILED transition恰1。machine protocol/owner已诚实改为characterization/0，architecture all与through-task T066 PASS；v2不追加伪造record，下一frontier为T067。
- [x] CHK048 T067三slice正式R/G/R完成：新session无backend参数且固定Inline，历史Docker读取保留，未知历史值稳定`EXECUTION_BACKEND_UNKNOWN`，Graph runtime_kind投影不丢失；旧ask-back 22/22与architecture all PASS；v2=215/head=`54d20352…85b0`，下一frontier为T068。
- [x] CHK049 T068 raw Docker L3 exact node经真实EventStore与REST router首次即PASS；machine诚实改为CHARACTERIZATION/production owner0，保留三个behavior watch，不制造production churn或伪造record；v2保持215，下一frontier为T069。
- [x] CHK050 T069 model absence正式R/G/R=`4b576900…9b81`/`446f469c…0485`/`7c40f22e…58fd`；`JobSpec`/`ExecutionRuntimeRecord`定义、export和遗留单测删除，负向合同不以ImportError冒充RED。core+raw-history 39/39、architecture all与through-task PASS；v2=218/head=`ee22b045…beb`，下一frontier为T070。
- [x] CHK051 T080三个characterization node首次执行3/3 PASS；exact inline response、ModelCallResult持久字段、Task/Event/Artifact/checkpoint/SessionContext/turn/session均被锁定，配置的外部模型调用0，production diff=0且未伪造formal record；下一frontier为T081。
- [x] CHK052 T081 `S081-precomputed`正式R/G/R=`5107fe11…ae6`/`ac7a6a1a…d800`/`f35edbf2…28a04`，storage-only API复用唯一session storage primitive且model/recall/compaction/extraction=0；T082删除inline fake/generic LLM adapter，同四nodes 4/4 PASS并按`refactor_of`协议不制造独立formal record。canonical v2=227/head=`748d44b9…dc23`，下一frontier为T083。
- [x] CHK053 T083 bundle-XOR正式R/G/R=`a0da4c75…becd`/`5e402d0f…df9e`/`5eb3f020…f4cd`，storage-purity=`d41378dc…7692`/`a74415e2…be6d`/`f2a197a2…47f2`；TaskService/AgentContext missing/both失败，storage-only构造MemoryRuntime/reranker/background/network=0。canonical v2=233/head=`fb6f49a9…73a1`，下一frontier为T084。
- [x] CHK054 T084 shadowed-test与两条constructor slice完成正式R/G/R；恢复behavior nodes 2/2 PASS，production 45=3/42，TaskService144/AgentContext31显式mode，F033 restart/project隔离通过。C084 576 passed、4 deselected、1 xpassed且failure/error/skip/rerun0；canonical v2=242/head=`107693d3…4360`，下一frontier为T085。
- [x] CHK055 T085 composition正式R/G/R=`d4f93e36…c712`/`f52156ab…dcea`/`99125d5b…efdc`；单一bundle贯穿storage Hook/SkillRunner/final LLM/TaskRunner且Router/background identity一致，Harness不写旧class locator。真实lifespan assembly failure process1/non78、serving与用户Task/Work/Event/backend0；canonical v2=245/head=`a42d0b93…4c21`，下一frontier为T086。
- [x] CHK056 T086 route preflight正式R/G/R=`cf43820b…84f70`/`85b5ca2b…8a45e`/`a0bc438f…64a9e`；chat/message在Task创建前要求TaskRunner，missing/mismatched runtime services稳定503且副作用0，统一runner入队并删除局部fallback。相关回归52/52；canonical v2=248/head=`eb428bac…830c`，下一frontier为T087。
- [x] CHK057 T087 local close正式R/G/R=`aae824a6…f2bd`/`cf6b93d2…fc98`/`db69e934…46b2`；本地三层统一`aclose`且只清客户端状态，共享Router仅由bundle关闭一次。相关回归14/14；canonical v2=251/head=`6603918d…f587`，下一frontier为T088。
- [x] CHK058 T088-T089 shutdown正式R/G/R=`09d8b2d1…1d87`/`2bd7c94c…967c`/`a2dc2664…8877`；两阶段drain、producer stop、bundle close、stores close顺序固定，重复shutdown exactly-once。相关回归44/44；canonical v2=254/head=`b73a5cbb…0bc3`，下一frontier为T090。
- [x] CHK059 T090 Phase Gate全绿：C05=203、C084 45 selectors、C07=9、C08 execution=8+1既有skip/startup=5；C14 totals=167/66/89/259/74不高于ceiling，C15 PASS。fresh C19=`1404/1559=90.1%`、五件aggregate=`a5d52d48789d25dd4f73438cf3769d4ed49681899a484bc6f317681fe8ff1651`、stderr0；C20与`through-task T090` PASS，v2保持254，下一frontier为T100。
- [x] CHK060 T100-T102 CI wiring正式R/G/R=`8435504a…6aab`/`02aee3cd…5463`/`58e113fe…288a`；pre-commit architecture先于docs fastpath，architecture/backend coverage/benchmark jobs独立，fresh committed coverage report与完整Vitest/tsc闭合。C06-final=153/153，C20与`through-task T102` PASS；v2=257/head=`f0778669…8276`，下一frontier为T103。
- [x] CHK061 T103三slice正式R/G/R闭合：rerun独立解析JUnit且F151 node fail-closed，quarantine相对merge-base只减不增，既有登记rerun按review date单列并复验record33/replacement binding release exclusion。C20与`through-task T103` PASS；v2=266/head=`92db4411…6ac0`，下一frontier为T104。
- [x] CHK062 T104文档权威正式R/G/R=`3a17fd5a…e51e`/`fa73f8e3…1218`/`489c7f6b…0187`；17份authority docs、根索引与实现索引闭合，显式历史可留，现役旧Proxy/物理kernel-worker/Docker sandbox、✅表格、Mermaid及未登记authority link均失败。v2=269/head=`913082c2…f878`，下一frontier为T105。
- [x] CHK063 T105 Phase Gate全绿：六architecture subcommands与C12-post/C15-post/C20-post独立PASS，C06-final=157/157；fresh C19-post主suite=5405 passed、scripted E2E exit0/rerun0、changed-lines=`1404/1559=90.1%`、五件aggregate=`0817c247…ff47`、stderr0；`through-task T105` PASS，下一frontier为T120。
- [x] CHK064 T120 22个exact post-SDK command IDs全部exit0；C084首轮remote cost-map fallback未被误验收，C23/C24/C084显式offline env、现有复合node单缺陷负例和C084无网重跑全部PASS，stderr0；下一frontier为T121。
- [x] CHK065 T121仅运行冻结C23；finalize fixture显式设置T120-T123目标状态后定向PASS，同一C23完整复跑=5510 passed/11 skipped/1 xfailed/1 xpassed、0 failure；未运行C18/实LLM/外部成本，下一frontier为T122。
- [x] CHK066 T122 fresh C19-post五件套stage/start UTC/HEAD/tree/fingerprint绑定正确，LCOV fresh=true、`1404/1559=90.1%`、stderr0、aggregate=`9f271774…5dba4`；C16=48 files/440 tests与tsc PASS，未复用T105。

## Gate

- [x] CHK028 main批准T006 index-amendment corrective GATE_DESIGN。
- [x] CHK029 main批准T006 index-amendment corrective GATE_TASKS。
- [x] CHK030 main接受真实`S006-index-amendment-integrity` RED并签发两RED combined aggregate/review ID。
- [x] CHK031 T006 Implementation Review通过，26条可信chain、分层与坏味道无阻断。
- [ ] CHK032 clean-wheel、import-direction、retired-terms、complexity、fresh coverage、xdist与全部验证全绿。
- [ ] CHK033 GATE_VERIFY通过后方可完成Goal。
- [x] CHK034 main批准T011/T012 preliminary parser/error defer与S070 full/all command/parser hunk两两互斥、record33 release-exclusion的corrective GATE_DESIGN。
- [x] CHK035 T012 Round2 Gate与两个exact nodes通过；首次dependency attempt作为pre-pytest INVALID历史保留。随后两个RED已接受，dependency slice完成G/R，standard-backend仍为RED-only。
- [x] CHK036 `S012-dependency-selector-semantics`已完成真实R/G/R（`cee3de98…23f5`/`8acbfcf6…4d0e`/`df4cdc63…9ada`）；fresh scope=`d84d2544…b49`验证PASS，两path=`pre_T012→pre_T012`、unresolved=0。
- [x] CHK037 standard-backend、import-classification、child-observation三个T012 behavior slices完成GREEN/REFACTOR：只扫描distribution-owned files，分类/当前delta完整且final verdict为空，child facts真实；标准Hatchling九wheel/真实METADATA/offline target install与反旁路负例全绿。
- [x] CHK038 S011 requires-dist/isolation合同改写后，旧240558/294b2e/61047e证据标superseded history；S011 v5/import v5六件map与child v4 selector attestation由fresh batch binding管理，review ID=`main-f151-t012-final-unowned-v5-review-20260721`。
- [x] CHK039 T070同时完成full与final direct-dependency closure；T023 manifest/lock、T017-T029 namespace/test、T045 guard、T064 startup owner均可追溯，Provider1+6/Gateway7+25的unknown/unowned/missing/unexpected=0。两slice R/G/R、C11、fresh C19=`988/1096`、C20/through-task与architecture all均PASS；canonical v2=224 records。
- [x] CHK035 main批准并接受cwd/env/argv/invocation/tree/result/六件hash-size/aggregate/attestation全部machine-bound的direct replacement RED；`main_review_state=ACCEPTED`、`canonical_index_adoption=false`。

当前提交`F151_GOAL_COMPLETE`：T001-T124共76项全部checked。T122 Python fresh coverage=90.1%；T123 C24 clean-wheel all、architecture all、benchmark 2/2、frontend 440/440+tsc均PASS；T124 C25显式local-working-tree并原子生成最终报告（SHA=`50521128…500f`、无tmp）。Final lifecycle正负合同及ordinary/through-task验证确认该报告是合法C25输出、提前/伪造输出仍fail closed。canonical v2=`bd717b9d…ce9e`、269/head=`913082c2…f878`。全部自动Gate闭合；stage/commit/push仍未执行。
