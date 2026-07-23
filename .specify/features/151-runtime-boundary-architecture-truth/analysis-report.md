# F151 T006 Closure、Formal Frontier Fix 与 T102 CI Wiring 一致性分析报告

**状态**：F151 T001-T124全部完成，76/76 tasks checked。T124首次C25因hardcoded committed mode与未stage/commit final态冲突而fail closed、0写；修正为显式mode并冻结C25=`local-working-tree`后，合同2/2、quality-smells与更新C25均PASS。Final复验进一步补齐报告读取生命周期：只有T124 complete、C25 owner、canonical index SHA、resolved base-ref与exact bytes同时成立才接受，提前/伪造报告继续fail closed；正负合同、quality-smells、`architecture all`和`verify --through-task T124`均PASS。最终报告SHA=`50521128b546ebc6d649fbfe86104756aff9684c53cbc6704d0b1f54b5cc500f`、245 bytes、无tmp，绑定canonical v2=`bd717b9d7889376d468d60480f95664dcd53dc27d116f536b4934d6c171ece9e`、269 records/head=`913082c297cf19db3c76f73891efc56faa2b8db6993d03d8d233385403cbf878`。Goal完成；未stage/commit/push。

## T100-T102 CI Wiring（已完成）

- pre-commit architecture gate在docs-only fastpath之前恒跑并显式消费base-ref；GitHub path filters覆盖octoagent、repo-scripts、docs、constitution、F151 machine artifacts与workflow自身。
- architecture与backend coverage为独立jobs、独立artifact flows；architecture使用full history和PR base/push-before merge-base。coverage先删除旧LCOV并记录fresh UTC，再生成LCOV、绑定HEAD/tree/porcelain-v2 fingerprint，以committed full contract写changed-lines report。
- benchmark job与baseline/release `benchmark-unit` lane复用C17两个exact nodes；frontend执行`npm exec vitest -- run`与`npm exec tsc -- -b`，不保留排除列表。
- wiring合同与既有lane回归39/39、architecture all、C17 2/2通过；C06-final五份gate文件153/153通过，无rerun。

## T090 Runtime Phase Gate（已完成）

- C05=203 passed；C084的45个behavior selectors全部通过；C07=9 passed；C08 execution=8 passed/1既有skip，startup=5 passed。
- C14 fresh totals为C901=167、PLR0911=66、PLR0912=89、PLR0913=259、PLR0915=74，分别不高于167/67/90/259/75 ceiling；C15 quality-smells通过。
- C19 exact transaction exit0。canonical root=`evidence/local/coverage/T090/`，五件套exact set成立、stderr空、status=PASS、fresh=true，coverage=`1404/1559=90.1%`；metadata/LCOV SHA=`39fb312ccbb1f4e21a186303c439e503ef3461185c734dfc657065773033edb0`/`b4396bf701da8289971e0f181e7542ac571c033f939f6ec601cd95a2e7da8dfc`，五件canonical aggregate=`a5d52d48789d25dd4f73438cf3769d4ed49681899a484bc6f317681fe8ff1651`。同字节诊断保留的JUnit显示main=`5396 passed, 10 skipped, 1 xfailed, 1 xpassed`、scripted E2E=`18 passed, 1 skipped`，failure/error/rerun=0。
- full-suite回归暴露的F010 resume fixture已补齐真实`task_jobs`事实；coverage tracer下的绝对CPU-time benchmark按既有CI原则在`sys.gettrace()!=None`时skip，普通无tracer执行仍通过，另外两个性能sentinel继续运行。新增AgentContext/TaskService storage回归使changed-lines跨过90%而未放宽分母或门槛。
- 四个runtime fixture regression paths归入既有`S084-test-constructors` ownership，新增session replay回归归入`S081-precomputed`；全changed-path ownership missing=0。C20普通verify与`--through-task T090`均exit0，v2未追加、未重写。

## T088-T089 Shutdown order / exactly-once（已完成）

- `S088-shutdown`正式R/G/R=`09d8b2d1a94b6e3dadb82f8638afdbb1feb6fbed26a0f2dcb95e00513b141d87`/`2bd7c94c0867b4c8fc2ef8fa653b23a299d4d305745dae26dcaaa68b5bd8967c`/`a2dc26642e31968b1ecc03b944b2a21298d0dc15e2f511a9c4bcba5714308877`。
- Harness shutdown用实例级lock与完成标记串行化；成功路径严格执行snapshot drain→producer stop→final drain→RuntimeServiceBundle `aclose`→stores close，失败不会伪装为完成。
- 重复shutdown不重复producer、Router或store close；新增exact nodes 2/2与bundle/lifespan/main相关回归44/44 PASS，未使用固定sleep。
- formal RED/GREEN归T088，REFACTOR按machine两任务range归T089；canonical v2追加records252-254，prior251不改。

## T087 Local teardown / shared Router ownership（已完成）

- `S087-local-close`正式R/G/R=`aae824a69a9957e778ab75367fc00164b75493010538444df4e5d05185fff2bd`/`cf6b93d2d800c76e89b931c2edd12f8c32d9f4f5c2b50e39409af9c2a9defc98`/`db69e934280c1c38fb7c8d40d3611ea6a7638d8940638ab80f7540f8316746b2`。
- LLMService、SkillRunner、ProviderModelClient逐层只委托`aclose`；ProviderModelClient删除旧`close`，清空history/last-access/fold metadata而不触碰Router。
- 重复关闭本地链保持幂等；RuntimeServiceBundle随后只关闭共享Router一次。composition与ProviderModelClient相关回归14/14 PASS。

## T086 Route preflight / single enqueue path（已完成）

- `S086-route-preflight`正式R/G/R=`cf43820b1994c4c9b066db14276b56c14a7b05bdf7e6f81b2796c6a182284f70`/`85b5ca2ba808c6427676922085bd25adbdcf893e48fd796df755d6e14418a45e`/`a0bc438f3e259c2e9cbf901e18529631141d0df2b7b7dfd986d993dc5ff64a9e`。
- chat/message均在TaskService构造与Task写入前调用同一preflight；TaskRunner缺失、无bundle或与`app.state.runtime_services`冲突时返回503 `RUNTIME_SERVICES_NOT_READY`，Task/work副作用为0。
- 两条route只通过TaskRunner入队，chat删除module-local background task registry与LLM/process fallback；相关chat/message回归52/52 PASS。

## T085 Composition Root / lifespan fail-closed（已完成）

- `S085-composition`正式R/G/R=`d4f93e3663e4116f3b9058672e4f406524fefcc1ca8c729673c5a7735de9c712`/`f52156ab8dd6fa06a9ba121e15895eec7e813231161dcbb3a6f3c346d5aedcea`/`99125d5b1b139c8e1857864665133bb1810c892238eebc8bf23d6a88775cefdc`。
- `app.state.runtime_services`是唯一组合根，同一对象进入TaskRunner；bundle内final LLM、ProviderRouter和background registry均保持identity，final LLM持有唯一SkillRunner，AgentSessionTurnHook保持storage-only。
- L3 characterization在真实module entry/lifespan中让TaskRunner组合构造抛出sentinel：process exit1而非static exit78，lifespan未yield，readiness/application request/backend调用0，用户Task/Work/Event为0；固定`_plugin_registry_audit`系统占位独立计数，不冒充用户workload。

## T083 RuntimeServiceBundle XOR / storage purity（已完成）

- `S083-bundle-xor`正式R/G/R aggregates=`a0da4c7585325a9031dd6dfb9dc73946602d83c02a8b9ad038385828c1bfbecd`/`5e402d0f85bb814e842f8a9b6a6b2bbd667904ed300fcabc5bf748a6bce9df9e`/`5eb3f020980eb93e0736f7953870781b2af0af6a1fa3f6cfd91ed3d17fccf4cd`；同一node覆盖TaskService与AgentContext的missing/both/valid两种mode矩阵。
- `S083-storage-purity`正式R/G/R=`d41378dc4a22a7160ec788f6a0220052692cd38dc11c620533819279bd7a7692`/`a74415e2a1b548e32a68c1a991655fcccfb3f71ce85223c5c273cc583627be6d`/`f2a197a2ff84f6ba042b065c386db9182fc55be6a6657d452df66914c55e47f2`；storage-only构造不读取class注入，不创建MemoryRuntime/reranker/background task/network，runtime方法typed fail fast。
- 新增实例级最小RuntimeServiceBundle与幂等`aclose`；关闭状态分别记录LLM与Router，避免前一步成功、后一步失败时把未关闭资源误标为整体完成。

## T081 precomputed storage-only completion / T082 inline adapter removal（已完成）

- `S081-precomputed`正式R/G/R aggregates=`5107fe116e05051238379a281ba93efe9a35c04d076df897c713db4d17f46ae6`/`ac7a6a1ac923b889dc5ca3d5c0358fcf96bebdd1934a896ef120d2203091d800`/`f35edbf2a441b94a28a90a74f1a351a2a603f7603c06ed101e9a8b337ab28a04`；storage-only API不接收LLM对象，精确持久化Task/Event/Artifact/checkpoint/SessionContext/turn/session，model/recall/compaction/extraction副作用为0。
- `AgentContext` session replay已拆出唯一storage persistence primitive；runtime wrapper独占memory extraction触发，precomputed路径只做必要存储并可在缺失时建立最小ContextFrame/RecallFrame事实。
- Orchestrator deterministic inline路径已删除fake/generic LLM adapter，改为构造确定性`ModelCallResult`并调用precomputed storage-only completion。S080三条characterization与S081 exact node同批4/4 PASS；T082是`refactor_of`，不伪造独立formal record。

## T080 deterministic inline characterization（已完成）

- 新增的三个exact node分别覆盖普通non-direct reply、Graph start抛异常与返回`Error:`；每条都使用真实Orchestrator/TaskService/store链并要求配置的外部模型对象调用次数为0。
- 合同冻结inline `MODEL_CALL_COMPLETED`的provider/model/token/cost/fallback字段、exact `llm-response`字节、`state_running→model_call_started→response_persisted→task_succeeded`四checkpoint、Task terminal pointer、SessionContext、transcript及user/assistant turns；compaction/extraction完成事件为0。
- 三node首次执行3/3 PASS，test SHA=`4e2f9be68301318a0cc58aec6c32b96d5be7d6656242ee5fae7846f7c2a2ae6d`；production字节未改，characterization不追加v2 record。

## T070 full clean-wheel 与 final dependency closure（已完成）

- `S070-clean-wheel-full`正式R/G/R aggregates=`f45677d9a91bcb6dda423f60f94ab405ce6ad5761fcb3cb704d29311e3008ab5`/`9f4e22efe1d8a242bb59eb11718215b659fef58c0fcf9ae9a6facbfed6469663`/`1af1d2aba0bde1601e7f66eaa1107ca6c54e46376b98f0194ccc894c6ef6c3d8`；`S070-direct-dependency-closure`=`875e054bf613e98f5fcb6faf4c45721a7eaf2fc37cf4af041d332536a441da8d`/`a4852d4ca7c7922d1988fc4f4d95494e3e1232a3ae1cb18f7421a1079ad2762e`/`cf15ffe176b6667dd101b683bdd89761cf4249c6b670f9b91288bb4e3f542890`。
- 同一clean-wheel checker首次真实执行Gateway full/all：外部cwd启动、host/readiness、SIGTERM、invalid startup exit78、source-managed exit69均由既有T029/T045/T064 owner事实提供；checker只消费，不转移production ownership。最终Provider direct dependency=1+6、Gateway=7+25，unknown/unowned/missing/unexpected均为0。
- phase regression全部闭合：C04=43 pass/1 warning，C08 execution=8 pass/1既有skip，C08 startup=4 pass，C22=5 pass，C11/C13/C14/C15-post均PASS。C14对HEAD中尚不存在的F151 machine artifacts打印的`fatal: path ... not in HEAD`是预期负向读取，process exit仍为0。
- fresh C19 canonical五件套位于`evidence/local/coverage/T070/`：`988/1096=90.1%`、status=PASS、fresh=true、stderr=0，metadata/LCOV SHA=`116a9f8c968d7e33c02c1d83082bfa0094fa8fc692e569d024613678f854c934`/`5d39d1e4d079515b03245e17301fafb98d029a17696b6138c6818408f0a1d9b6`，五件aggregate=`1b37f7685de194c02c7d85cadd36044070b5890d5c1112358436b2af25190030`。此前最终测试归位前的T070 coverage已可恢复隔离到`/tmp/f151-t070-coverage-stale.ynhuvq/T070`，未覆盖写。
- C20普通verify、`--through-task T070`及`architecture all --base-ref origin/master`均exit0。为闭合真实回归暴露的问题，`ExecutionConsoleService`在输入事件commit失败时保持fail closed；message fallback使用FastAPI `BackgroundTasks`而非裸`asyncio.create_task`；bench/commit-failure coverage分别归入既有测试owner；retired YAML fixture归`S040-yaml-tombstone`，A2A graph断言归`S067-graph`，没有新增slice或第二runner。

## T047-T049 atomic retirement与Phase Gate（已完成）

- T047只执行受T040-T046行为证据保护的机械删除：Proxy/config退役manifest/source/lock/docs/wiring、空`RuntimeConfig` class/root field与`.env.litellm`均已移除；没有新增CLI、Control Plane或frontend行为。
- T048删除SDK tree、workspace/lock/docs wiring与相关入口，收窄base pricing dependency；post-SDK命令固定为7个package src+Gateway的8段PYTHONPATH，SDK path引用为0。
- T049定向门全部通过：C03=8、C03-retired-behavior=4、C07=9、C09 Provider、C10 Gateway relocation、C13、C16=48 files/440 tests+TypeScript、C17=2、C19-post、C20-post；按设计未运行C11/full/all。
- fresh C19主suite=`5345 passed, 11 skipped, 1 xfailed, 1 xpassed`，scripted E2E exit0、rerun0；canonical五件套位于`evidence/local/coverage/T049/`，changed lines=`677/729=92.9%`，metadata/LCOV SHA分别为`b1b633e6e6adf69729427a1aae7718a75e231807467f14d151ca33e5e8550a0e`/`b46ce60a45b3c6b6aa5c4148b066f2eecfea3b2afd4234287a56a85e37c0b3ac`。
- C19 machine命令主体保持；当前工具策略拒绝递归删除临时目录，因此唯一执行差异是把末尾cleanup替换为no-op并保留`/private/.../f151-cov.QpM1E2`供审计。canonical output、coverage判定与随后C20-post不依赖该cleanup。
- Coverage.py下唯一超时暴露为测试辅助deadline不足：`test_attach_input_live_path_updates_session_and_job`的两次状态轮询由默认值显式设为60秒，仍使用状态驱动polling，无固定sleep、rerun或断言放宽；该target在Coverage.py下1/1 PASS。

## T046 retired behavior removal（已完成）

- `S046-provider-error`正式R/G/R aggregates=`8a2de14e1eed8c28c4ce81c2f71d06e11d5d7fcd178e911a2e381da65219b2eb`/`bc47fd88ceb54aeb1b56599a143a3b027c40e16c4ebea76469c45bcb34a40a81`/`ff889927989adbe7355efcb9f8604e844f96ead1c729abcfbb828b803df18713`；删除`ProxyUnreachableError`及export，fallback与benchmark改用canonical `ProviderError`。`S046-bench`保持独立characterization，不伪造formal RGR。
- `S046-config-sync-cli`=`feea122089032ac82e9684dfe0175c64f8842cbfd11b3abc47b18caaaeeef152`/`ceb93a280c20ec176b189c457dac06ac0cb8a4a29728db8f2d253508a4276ed6`/`06957b659ed26674f004e5fb4dd78c9aeeca5fbd458b1fcd9ece30015198159a`；CLI不再暴露`config sync`。`S046-activate-option`=`5dfd6698cae6e7c728e441e5ede3d8e17a3475ccd4ac4239123a530552c55016`/`9a25ede326d649d054d1e871b51545ddc9ae25222a91cd84c2ac6b72bad60f04`/`d917aea594b2e940cac1c5f5aaf65a33af6c92a8c394925e77715b62b5aadaf9`；provider add/disable不再接受`--activate`。
- `S046-config-sync-tool`=`0f5dd2248f1162b2fb8eeb6e57a18eaeae79a8fc1cecc030d8b01a2aeef50215`/`398ca564c514980a49f0a7e4406d206f22f1a84e247cae663d9c8f36081e2191`/`8e700bc5183a047f5dc9c8d3861bef29bb12b777c1725eaa01ff3158582d4328`；builtin `config.sync`、`ConfigSyncResult`与core export一并删除。
- `S046-control-plane-fields`=`cffd71b982b8a911b65419004c034a166b871c058fb4a0f232d230e641edceef`/`601bdda9c9b65c6747e4085dcf168842ce745b050d3b0f9d1f2e050705a5f5ac`/`ca984ae0e0afc9f789a5252ca6a2b4b63428342e72fcbbcd8ef083f89efd0122`；setup/OAuth只投影provider env names，不再返回activation/proxy/runtime reload字段。
- exact behavior+bench 6/6 PASS；相关回归125 passed/1 skipped/1 xpassed，18个触达文件Ruff/format与`git diff --check`均PASS。canonical v2追加15条formal records至155，`.amending` absent，`through-task T046`实际exit0。

## T045 source-checkout guards（已完成）

- `S045-service/update/install-guard`与独立`S045-bench-guard`均取得唯一oracle RED，再以同selector完成GREEN/REFACTOR；各阶段selected=1、error/skip/rerun=0。
- 统一source seam从managed descriptor解析候选根，向上验证`.git`、workspace `pyproject.toml`/`uv.lock`、installer与bench entry；不存在时输出typed `SOURCE_CHECKOUT_REQUIRED`并exit69。
- guard位于service manager、UpdateService/runtime store写路径、installer subprocess/filesystem与benchmark runner import/call之前；status/logs/help未加guard。相关三个CLI测试文件加bench exact node合计44/44 PASS。
- canonical v2追加12条formal records至140，`.amending` absent，`through-task T045`验证PASS。

## T044 structural core readiness（已完成）

- `S044-ready`正式R/G/R aggregates=`8325adc414334046660db5b64e9d2a967f48f6ba66319b0d7cd5248c963d92d2`/`02d98111e9c6ed13e588c719c649368b05ca011fee10d83db35bd04915c13dec`/`2338bda05934e90ff5d874caf010eeeb8bb5b54d5922fd043801a05ddfcced72`；三阶段均selected=1，GREEN/REFACTOR pass=1、skip/error/rerun=0。
- `/ready`删除`profile`输入/输出与`litellm_proxy`检查，不再读取或调用`litellm_client`；结构检查只消费SQLite、artifact目录、disk、alias registry与provider router的同步本地解析。canonical `main` alias解析后只报告alias/provider/model，不触发模型或网络调用。
- alias registry、provider router或路由解析缺失/失败时`provider_route=unavailable`且整体503；不存在“未解析但ready”的宽松分支。exact合同与既有US-12健康检查回归5/5 PASS，behavior watch归属`S044-ready`。
- canonical v2追加3条formal records至128，SHA=`6058cb5f9cc28b7a90d53df87068b3bac3f3fbfe2f27138fb1f22d17b6de3ba1`、head=`a596f1fc03aae892382fe441da4238f2013b6870d8d3c1c67a6bf814209aed70`；`.amending` absent，`through-task T044`验证PASS。

## T043 frontend runtime/activation retirement（已完成）

- `S043-settings`正式R/G/R aggregates=`be5679d0b8c28a6cbaf2daa29f5b4ca91d29c79be9fbe43fb3144791edb8e775`/`5aaf19c75bb5c83e5a375e51e18e3e15cfe5eb3f9bad0c75fdb613df2b157600`/`99c7a940b48c57498ac868d47fc00894d3753d83943718945a8597d8556ef902`；Settings canonical save payload不再生成proxy/master、runtime category、runtime draft、auto master secret、runtime comparison或OAuth runtime字段。
- `S043-pending`=`a0f17411d6b5f61ff64edc54c1c143e9c718e21ef9b58d5f2fa4a7d5c3995148`/`4e74fa2881c63f57224540a695cab19ab8131c01271ca643f111a722535164ad`/`167819910e099f9004de87e11f4f12b324b878942e72688501f5f9c9811ee144`；PendingChangesBar不再渲染或发送runtime activation字段。
- `S043-home`=`f1c648f3a30bf5526cb42747dafc9175e426c136a34aaf71f082c374763e0817`/`115ee07d64edfb202ac5398b3e3472699c5aa53b6972a79a489a4396a1ae1760`/`57c09f58e652d59ff949960bf23ee4c9420a96f7a6593cab033534d9621ab0b7`；Home与Workbench只按enabled provider判断可用性，不再投影`runtime.llm_mode`。完整回归暴露的Home/Workbench旧fixture已同步为provider事实，HomePage test以behavior watch纳入同slice所有权。
- `S043-app`=`0a997f883270592c9d35ab59d75cea7ec128bcb22b96c202b7bb5b653b221494`/`40323dbc735f99298552917fd9cb42914999f7718e70bcd70097b80d847e4e76`/`c7b3d169a1c6e11db81c3e7be71d48d4ae5a0abd2d5c0eb8a8e7b77147ec3d54`；setup action保留provider字段并移除runtime/activation投影，secret guidance只指向`CredentialStore`与canonical `.env`。
- 四个slice每阶段均selected=1、skip/error/rerun=0。formal runner沿用唯一parser/runner，新增frontend Vitest exact selector、离线三项env、JUnit testcase映射与RED stderr/AssertionError fail-closed验证；checker SHA=`221ea0c88da2b75f92d3ac43439dd961f5b38eafb30fd2e4382e6c681c37c035`，3805 LOC/161 top-level funcs/max48/McCabe≤10/职责8，作为新no-growth ceiling。
- 完整`npm run test -- --run`结果为48 files/440 tests PASS，`npx tsc --noEmit` PASS；既有approval conflict日志与React `act()` warning未形成失败。canonical v2追加12条formal records至125，`.amending` absent，`through-task T043`验证PASS。

## T042 legacy file no-content boundary（已完成）

- `S042-files`正式R/G/R aggregates=`bac8421443782cb18f17243ea6fadf48726caa30591d1e06d55e77e5d2b56f4d`/`b11da2b5cd4018297c4f20e6163273fdd9092a9b753ba1b207054315652eb1d4`/`ffd858fff7120f01ff697a3a4131fc6ffc5447c91a16dac5b854c4dc7d65a2d0`；detector只对两个legacy secret path做exists判定，不open/read/parse内容。
- `S042-files-early-preflight`正式R/G/R aggregates=`9eaa4bc5690a36b4851736c0aec230de3c5b5f9a28d7db89a691795b0251e881`/`d7422dc9d2f5f8cf941c8482268f10c9e66e054f278d2ba1bec3fa69109263f8`/`771d58bfec47b79861307b9290ed560b60aea274d8117ebb7e755a89b960dd90`；`create_app`在`_resolve_project_root()`后立即执行detector，并先于dotenv与app/bootstrap副作用。
- `S042-recovery`正式R/G/R aggregates=`b0bbfc3c5b6b69493941ebcdef29d53e1848dbc69e2f7fe2b46c1f7c6eff9f36`/`92a796ec255947ef51743859adeaded9c3f0e79bef9eded9c429728dfacdddc5`/`e0367d82cf6737acbcb9f307a97944b6d7020111dbd7afd1f917f2cc08c3462e`；auth/setup recovery只给typed reauth guidance，不读、复制、迁移或备份secret bytes。
- 合并回归首次暴露test自身通过python-dotenv泄漏六个退役env key；现由test作用域外层monkeypatch登记并恢复，55/55 PASS。该修正只恢复同一pytest进程的隔离，不放宽production tombstone。
- F150 gate不按临时目录或whole-file路径放行：它同时要求machine scope exact selector `function:create_app/call:detect_legacy_runtime_files`与canonical T042 RED/GREEN/REFACTOR三条record，并只从AST剥离一个detector import和一个位于project-root之后、dotenv之前、参数精确为`project_root`的调用再比较完整baseline。错误参数、顺序、重复调用或任一sibling body变化仍拒绝；两个负面gate node、C15-pre与C20-pre均PASS。

## T041 loaded environment tombstone（已完成）

- `S041-env-tombstone`正式R/G/R aggregates=`2b1d94542762ba68acfd1659a2cb7ef4407282697dc1d2fd1bf73b0efe1c63c1`/`0f54d19140f52291400589f92e3793883e1ef94186f103b6331d649a823e50c0`/`bae9b67529a83d899edf313bb732bebdfbea6cfc276e82b9c74e574353100157`；RED仅命中`F151_ENV_TOMBSTONE_MISSING`，GREEN/REFACTOR均1/1 PASS且error/skip/rerun=0。
- `validate_loaded_environment(environment)`显式消费dotenv加载后的合并mapping；`OctoHarness._bootstrap_paths`在任何update store/service/persist副作用前调用。六个exact retired key即使为空也typed fail-closed为`RUNTIME_CONFIG_RETIRED: <key>`，不新增第二loader、ambient default或compat alias。
- `OCTOAGENT_LLM_MODE=echo`与`override=False`的process-env优先级保持；dotenv 11/11、config bootstrap 9/9、echo lifespan 1/1 PASS，C15-pre exit0。

## T040 YAML runtime raw boundary（已完成）

- `S040-yaml-tombstone`正式R/G/R aggregates=`7055aea3100e70ea39fbb2ee767803296e68310c6d6d5dfb09be558ff45e6c1b`/`710492bfb3c67b1984e9891b0d43392dd1f29d64b60de770c6b2a6206c911440`/`439cdec170eb0c7a6a35459ca086c149d468582e28fa58b9d9b21dc7ae0a7e5b`；三个exact retired keys在`OctoAgentConfig.model_validate`前以`RUNTIME_CONFIG_RETIRED`和精确field path拒绝。
- `S040-yaml-supported`正式R/G/R aggregates=`72c344ff6cf275a3987ecd3d20f68c16982d00d80bddaa151bd899f6190deb2f`/`9738c55304df27f3bdf6efcb813a5722a0c7dbfdeaf37d63d8c03a24b7dcee42`/`63d3a55956b792e7d27fc476e12ddd6e82834e4d197c905df6e3a3b32317133d`；空runtime raw容器在模型校验前移除，其他非空key以`RUNTIME_CONFIG_UNKNOWN`拒绝，v1 legacy auth/base URL与v2 transport/auth/api_base均保持。
- T047已完成empty `RuntimeConfig` class/root field的机械删除；T040没有引入第二loader、compat path或Provider字段判定。config目录69/69 PASS，C15-pre exit0。

## T036 Phase review（已完成）

- `S036-relocation-post-snapshot-coverage`正式R/G/R aggregates=`9d52a793657ec12bff5361fab12b37db8be2044be8aca93303f286847abf124f`/`ac5545eac6334c3a4f197a51acb2a083713f48d60c4b3c4f177d730f00bb91d9`/`24e042da8ae4b6d3b39072e82f0a38ee6cb77bfa2746789cf5d89aa13756d1c1`；RED仅因旧checker把T029 target raw SHA当永久ceiling而命中唯一oracle，GREEN/REFACTOR均1/1 PASS。
- immutable T029 snapshot继续精确验证schema、base source bytes、snapshot内部target bytes/hash、target存在且为regular file；coverage只用base source→当前final target计算新增行。post-T029 target hunk是否合法仍由C15/C20 scope+formal evidence判定，coverage checker不复制该authority。
- 旧无效T036 C19四件套aggregate=`7ef1c5908a96b0b41bd6a208aab1b39335e8ace28f8e7f6ca3189699e4c61369`已非覆盖隔离到`/tmp/f151-rejected-c19-t036-coverage-7ef1c5908a96b0b41bd6a208aab1b39335e8ace28f8e7f6ca3189699e4c61369/T036`，逐件SHA/size保持。
- fresh C19-pre使用local safety wrapper（仅移除cleanup/trap并保留`/tmp/f151-t036-c19-final-wrapper/f151-cov.kWs0zf`）：main=`5356 passed/11 skipped/1 xfailed/1 xpassed`，e2e=`18 passed/1 skipped`，rerun=0；changed-lines=`494/526=93.9163%`、49 relocation mappings，五件aggregate=`e78378513123029be71c03211698e0111bacd083dfa55593eab2e69179cdb0e5`。C20-pre随后exit0，95/95 run↔index与through-task闭合。

## T034 Backup audit direct durability（已完成）

- `S034-backup-audit`正式R/G/R aggregates=`16a1e374902d4bc7965751a72988d6fe1d42c8ee4752e3451521b92875395212`/`177f9f2d17b355ee5eea5def69ea01397e1fe483987bb460409d99c0f59aa432`/`a191e111a83f36715ba66c90131176d3d44f72762123ce818bd0196dfeb17f7a`。
- started/completed/failed均返回持久化Event，重开StoreGroup后按task_seq 2/3/4与exact idempotency key顺序读取，payload逐项通过`BackupLifecyclePayload`验证。
- 同一retry跨两个StoreGroup返回同一event_id/task_seq且只保留一条event；SQLite store error原样抛出，事务回滚后event集合与task pointer不变。

## T033 Update active-attempt CAS（已完成）

- `S033-claim` aggregates=`e4e500ead4c77f8b733234b4a684d6716fbf658288026ffa197f25e042ea2a31`/`d993da83e668cd3938f92cfe9b9284e67d1f6f60aa95740896c1a7685d47906d`/`94b2afc47111fce90bb25ce5127487ac5ec6a2fb5950550f42a1fe04576be86c`；两个store实例在同一持久化file lock内执行absent-check→atomic replace，只有一个owner得到compare token。
- `S033-release` aggregates=`a1d78eefa171e827696cc8c760dc369f4e59630b089d3ee8f8ed7b1542596395`/`7d9af801679f56e97610ff1f39c8924fd2fd6551bc3c90b5309ef3cc26e6c2e0`/`db2b8eda7fc5656968e2c29a419815c458c1d1e076a15d34ad0798f596571d0b`；update/release同时校验owner与当前文件字节token，错误owner/stale token均0写，replace失败不产生半写或tmp残留。
- `S033-worker` aggregates=`16da32ce1942b3e4846958ac58a363e7fda23d9c28f26d8d06217b02ea0c3d34`/`94399438bbb5802ba8dec1bafc6cc85a72060e1efb6fdf0443f8cbda12183703`/`5f87173893ba69f6149fc665148372baae078aaf372db80d19463d73f0c44737`；Barrier/Event稳定证明旧check→save双启动，GREEN后loser在launcher前以typed active conflict拒绝，精确启动一个worker。

## T032 Telegram atomic RMW（已完成）

- 正式R/G/R aggregates=`c7822deb6098d73764187b89b234b32cbfc2d21a2e8ed9828c30d6861506b0c7`/`23206231bae46d094b749a3a498ed90aa9cab501b4d1e44acabb7d2a639ea3a3`/`9aa402334af7abe3488387e14eabb1cbe6f4589a2d1a32c552c56938a013976d`。
- Barrier强制旧实现两个实例先读取同一state，Event证明delete/offset两个operation均完成；旧last-writer-wins稳定命中唯一oracle。GREEN以同一lock文件覆盖锁内read→mutate→atomic replace，最终bytes/model同时保留user delete与offset=99。
- replace失败注入保持原state bytes不变且transaction temp清零；无sleep、process-global假锁、第二store/state authority、吞错或compat path。

## T031 update dirty-preflight（已完成）

- `S031-update-unstaged` R/G/R aggregates=`e525fc925f4eb49b3fd368b2b5b2831088baaadfb70b37abd60994618fd36175`/`437c89379d337f375fe1382b95f3939481029144a7453583e6e9899ca7f94948`/`15c75f071dfb1fc97bbf45c917b69ac1f55aab148b73f97552709f331a05e3e8`。首次GREEN因command output `.strip()`破坏porcelain前导XY列而1/2失败；六件套aggregate=`cc4905b26097186873e2c2abfb537a445d41102d1016f8b6500f1e26b621ecba`已非覆盖隔离到`/tmp`，未进入index。
- `S031-update-staged` R/G/R aggregates=`26c7f7783052a9dfe373bf8639a666915959779c040bc9b51602183bd3526fd3`/`411a0d72cf2f156b0637018f6e2b2ae0ae692d9cbd29aa5c1fcb43814262a6cb`/`9f06d052e4e57795fe21d942e85c260d50d26b53a65f593090479e47a1fde9d0`。
- `S031-update-untracked` R/G/R aggregates=`5ec0588e79f47b00cb47be7216e0d47e500b331d2616d82ab1add56cb46e2d08`/`80947d7f213a0ff07eebdbc6111f7fe825346199bb711df31fc85ac0c1926e52`/`6f399162ca2f73adb7ecc14244bce3d1f26eaf042aba2709acbdc177f9d126d9`。
- 唯一preflight读取`git status --porcelain=v1 --untracked-files=all`并保留XY前导列；任一tracked/index/untracked dirty状态在worker、fetch/merge/uv之前返回typed `LOCAL_CHANGES_PRESENT`。真实tmp Git的HEAD/index/files/status逐字不变，危险命令与worker调用均为0；descriptor中的legacy/destructive sync command迁移到同一safe builder，无第二runner或Git状态机。

## T029 C19 relocation coverage corrective（已完成）

- TaskRunner治理后的历史fresh C19曾因`TestUninstallSkill::test_uninstall_user_skill`在xdist下命中宿主`~/.octoagent/skills/test-install-skill`共享目录竞态而失败。确定性corrective以显式`user_skills_dir`贯通`OctoHarness→CapabilityPackService→SkillDiscovery`，生产未注入时仍唯一回退`~/.octoagent/skills`；direct R/G/R、API/E2E/SkillDiscovery early-coverage xdist 48/48与正式S029 R/G/R均已通过。
- 2026-07-21 fresh C19两段pytest真实全绿：第一段5329 passed/11 skipped/1 xfailed/1 xpassed，第二段18 passed/1 skipped，rerun=0；local safety wrapper只移除transaction cleanup/trap并保留`f151-cov.bOayy1`。
- coverage checker按same-path空基线把49个Provider→Gateway移动目标当作全新文件，得到`6467/7905=81.8%`并exit1。该五件套固定为`INVALID_C19_COVERAGE_ATTEMPT`：LCOV SHA=`d91b9dcfcdb88d54fd6a0bd847ed13754ca9a9313392eb2ed3b8de37c7d5ec6a`，metadata SHA=`2dbaa6485e716112aa6d2c36b9cf0ae0dadfff1bd89dfb561905bd33262a5e9d`；不得覆盖、删除、移动或冒充有效C19。
- relocation corrective复用既有coverage node完成direct R/G/R；按六件SHA map的canonical JSON重算aggregate依次为RED=`2687b42d45a006bb77de1e179f870b62934874f48ec3b4e98ef3114edb5e93f0`、GREEN=`fa2e048bdf6862c72be68063639703e51beea60056ec372bd297361090b0cc0f`、REFACTOR=`236b93913f58e785ba53df9254ba713640d806c1d7311edad04a48404e6261e4`；它们不进入canonical v2。
- 对失败LCOV只读应用已验证的49组source-base→target-final映射后，真实结果为`284/343=82.7988%`。本轮只补ProviderRouter cache/auth fail-closed与Gateway route resolver default/missing事实的定向测试；其余真实分支保留为下一轮明确test owner，不得用import-only、常量访问或虚假mock命中补足90%。
- 正确合同只在full coverage显式提供T029 AFTER `path@sha256`时启用。checker必须先验证snapshot schema/base/source/target唯一性与base/target exact bytes，再对映射target使用source base bytes→final target bytes；其余path继续same-path。无参数模式保持原义，任何snapshot漂移在report写入前0写失败。
- 该纠正不把移动行视为covered、不降低90%门槛。最终有效C19 aggregate=`c80e91658f8e70080c1f8394302e056f3f7e3f7dd4639efa3582e6f4bc7ff333`，322/342（94.152047%）；S029正式RED/GREEN/REFACTOR aggregates=`a1296057f971352a9cf344be955679c00d7a02cbbb624f123ffad8c6b1377adc`/`ed444e631bc0cd421c58b5905993b7241f93ecb3b9179eecbaf849f5bcebc85b`/`d1258def3fafdb6dda3b2feda9640d73b0aeecfa955643435131e8f121e54dbc`，C20-pre exit0。

## T012 checker rejection：5-Why、事实表与阶段闭包

1. checker为什么会假绿：扫描target全部`.py`，把静态、TYPE_CHECKING、lazy、optional与test-plugin imports混成一个集合，并以distribution“已安装”替代实际import evidence。
2. 为什么当前测试永远无法诚实GREEN：它要求该混合集合永久等于current manifest；真实Provider为manifest15/observed17、Gateway为manifest11/observed34，差异不是checker bug能在三文件预算内消除。
3. 为什么不能忽略unexpected：这会同时掩盖待迁出DX、test-plugin、optional extra与真正缺失direct dependency，破坏FR-007。
4. 为什么T012当时不应下最终结论：namespace/rehome、manifest、source guard、startup分别属于T017-T029、T023、T045、T064；当前前三项已经完成，startup仍待T064，因此最终判定仍归T070。
5. 为什么isolation facts不可信：parent按预期命令重构env/sys.path并硬编码空source leak，未读取实际child观察。

闭环：T012新增distribution-owned逐occurrence分类与真实child observation两个slice，只接受四类context、正交workspace owner、literal `resolved|unowned`完整inventory、manifest=real METADATA与`final_verdict=null`；resolved third-party仅来自target或lock匹配的项目purelib RECORD，unowned exact projection/count双向相等。T023拥有manifest/lock，T070新增final strict closure。S011 v4及更早direct证据只作superseded history；v5合同必须fresh main-owned direct RED/binding。

## Formal frontier Fix：5-Why与影响面

1. **为什么T007没有取得RED**：formal runner在启动pytest前拒绝`S007-local-coverage/RED`，稳定报`EVIDENCE_RGR_ORDER_INVALID`。
2. **为什么合法的S007被拒绝**：`validate_record_order()`与`next_record_identity()`把可接受序列硬编码为Phase0/T005后续与`T006_AMENDMENT_TAIL`；record 26后候选直接变为`None`。
3. **为什么状态机只知道T006**：实现没有消费`rgr-slices.md`的machine RGR task/phase关系，T006临时纠正尾链被误当成永久全序末端。
4. **为什么现有manifest解析也不能接管**：`parse_rgr()`只匹配显式`Tnnn RED|GREEN|REFACTOR`，没有覆盖active表中的三类任务语法：`/ T015 | RGR`隐式单任务、`/ T088-T089 | RGR`两任务范围、`/ T100→T101→T102 | compound RGR`三任务箭头；也不能据protocol排除CHARACTERIZATION/ATOMIC。
5. **为什么T006验收未发现**：既有合同只验证固定26条前缀与`through-task T006/T007`边界，没有post-T006合法下一项的正向测试。

**影响面与闭环**：缺口只在单一evidence runner的formal frontier。Fix已一次解析完整machine表的active RGR语法，并以“已完成集合 + 每slice phase前缀 + remaining最小numeric task；同task候选可交错”的partial order执行，同时保留现有26条前缀与T006 exact-tail特例，未新增runner/parser/manifest/entity。扩展合同的有效RED aggregate=`7374b3908c7231813faee73ff734292d8af5dfc1694013f703f2f730074a98f8`，direct GREEN aggregate=`93a592c76e6aa49536c196a5406f6f653d9d2ea033b87e78dd5e6ed35389fee5`，同字节no-op direct REFACTOR aggregate=`b0fb20020ec0de97480393868ce7e92cd6a0df21740064033b36ddeac98fbc4b`。三阶段test SHA均为`a3b9b0f95527707e8fe9057ea6034bfaf992cc83c693b208af502cda8009b9fa`；旧RED aggregate=`6a926da66dd903aeee16d50b0f973a34b3bd0ab5970a850a8155789f89a343aa`仅为`superseded-by-test-contract-expansion`历史，不计有效证据。修复后正式T007-T010两条coverage slice的R→G→R均已进入canonical链。

## Formal RED recording subfix：P1与合同

- main在subfix前确认：`validate_formal_invocation()`把所有phase的expected `exit_code`固定为0；`formal_record_from_run()`随后无条件要求failures/errors/skips/reruns全0，并以`oracle=None`、`failing_nodeids=[]`生成record。底层`validate_red_outcome()`与`make_v2_record()`虽已有RED语义，但formal runner尚未接入。
- main在subfix前确认：formal pytest collection会在导入期触发LiteLLM cost map远程获取；只有显式`LITELLM_LOCAL_MODEL_COST_MAP=True`才强制使用package内local backup。当时`execute_formal_run()`、`formal_metadata()`与`validate_formal_invocation()`只执行/记录`PYTHONNOUSERSITE`、`PYTHONPATH`，依赖父进程ambient env会破坏正式evidence的审计与逐字重放。
- 已用exact node`TestTddEvidence::test_formal_runner_records_exact_red_oracle_before_green`冻结phase RED exit1、exact failing nodeids、contract oracle与formal-rgr record字段，并以唯一oracle `F151_FORMAL_RED_RECORDING_MISSING`完成direct RED；GREEN与同字节no-op REFACTOR均通过。
- 新formal run的metadata/invocation必须显式记录`LITELLM_LOCAL_MODEL_COST_MAP=True`，`exact_command`由同一三项env map确定性构造；缺失、False/错误值或command/env不一致均拒绝且六件套0改写。父进程变量不得作为fallback。既有26条record是不可变历史prefix：identity、末端record hash、record chain与逐条`validate_v2_record`均保持，原两项env字节格式继续可读。合法第27条及后续formal tail必须exact三项env并逐条验证；非formal lifecycle继续遵守既有合同，不能把“当前总数26”固化为永久schema，也不能放宽任意tail env。
- 合同拒绝RED exit0/2、partial/extra failure、oracle缺失/错误、error、skip、rerun、非空stderr及GREEN带failure；连同三项offline invocation负例共14项，每个独立tmp Git case先有accept control，失败不得生成合法record。GREEN/REFACTOR既有exit0路径保持，新run同样自描述离线env。main接受的direct roots/aggregates为：RED `/tmp/f151-formal-red-recording-offline-red-main.bcLvA9` / `0187697982a67b839081caea16c73b3711e8d53ddcef3a9bdd64d973c08c13f6`，GREEN `/tmp/f151-formal-red-recording-offline-green-main.wuvcfT` / `7e585c12b097148bfa455c463cc89f48379f557ab897c09b4f8fe0cc9894426e`，REFACTOR `/tmp/f151-formal-red-recording-offline-refactor-main.1TddLp` / `b926a9cb6c7758835b26c1e8902cfff6e46c7a3dc0a963e01c8a87a5ad53703d`。三阶段冻结test SHA=`8b9d8790615ff115fcdc1fb2c9ed47a175fd614900e2a531fda189f75d371fbe`。
- clean-wheel纠正不新增numeric task、module、runner、parser或registry；新增`S070-clean-wheel-full`只把既有T070的真实full行为与T012 preliminary分开。record33保留在append-only chain但不具release资格；phase-correct direct replacement RED已由main接受且不进入canonical v2，当前只完成artifact truth sync，不修改test/checker/evidence或production。

## T006 evidence boundary闭环

- `S006-committed-worktree-clean`与`S006-index-amendment-integrity`均完成真实RED→GREEN→REFACTOR；两组selector、oracle与canonical六件套保持不变。
- T006在既有20条可信前缀后严格追加2 RED→2 GREEN→2 REFACTOR；prior20 object/hash与12个formal run不变，26条previous/hash/head链与artifact SHA/size全部闭合。
- T006 closure时，`verify --through-task T006`实际exit0；T007尚无自身证据时`--through-task T007`实际exit1并返回`EVIDENCE_THROUGH_TASK_INVALID`。随后接受的正式T007 RED已推进canonical frontier，该结果只保留为T006时点历史。
- checker保持单文件、单parser、单runner。formal recording时点SHA=`353c2e…a11d`、2787/123、dependency-resolver时点`657b0785…3a7b`、3124/133与T042 scope-aware SHA=`7fb421a0…6c9e`、3643/156均为历史证据；T043 frontend-aware active SHA=`221ea0c88da2b75f92d3ac43439dd961f5b38eafb30fd2e4382e6c681c37c035`，no-growth ceiling=3805 LOC/161 funcs/max48/McCabe≤10/职责8/single parser+runner。后续增长重新review，不机械压缩。

## 0. T005 evidence-integrity纠正历史（已闭环，保持只读）

- main在独立tmp Git fixtures中证明：删除record hash、篡改chain/anchor、增加未索引S999、使用minimal invocation/tree、same-anchor损坏index、无效mode/base/task均可exit0；`finalize-verification`命令不存在。
- 这批问题位于L4 evidence boundary；上层E2E或后续production不能补偿。现有v1 index SHA `1ad740db7bb515c633e48c42c75da92dd310ad7bc1e1993cdd973c9c52023adb`与首轮12组GREEN/REFACTOR只读标记REJECTED，不计release evidence。
- 该纠正已按S004/S002真实RED、main review、同一checker `recover-index`、六组GREEN与六组REFACTOR闭环；其20条有效record随后成为T006不可变前缀。rejected v1与旧runs继续在exact quarantine只读保留，不进入release chain。
- anchor、36 raw、前20个record object/hash和12个canonical formal run均保持不变；T006只追加获批六条尾链，未重放T005 recovery或既有GREEN/REFACTOR。
- T006闭环不自动开放T007行为RED或任何production实现。

## 1. 设计真值

| 面 | Round 10.1结论 | 机器/人读制品 |
|---|---|---|
| production startup | `python -m octoagent.gateway`唯一service entry；entry解析exact help/host/port后只import一次`main.app`。`main.app=create_app()`仍是唯一static preflight；`_resolve_front_door_mode`先完整load config恰一次，再应用env>YAML mode。static security/runtime invalid在Uvicorn前typed exit78；真正composition failure只经lifespan fail closed、process nonzero | production-startup、f150-scope、S064/S085 |
| descriptor I/O | canonical/legacy argv/invalid schema/invalid JSON普通load/start/restart字节级0写；显式transaction才migrate/repair | production-startup、contract |
| D-03角色 | 15 CLI+1 config+33 operations+2 delete；13/5/9/6只是legacy mixed role tags | namespace manifests |
| Provider tests | 44 collectable tests rehome+1 retired delete；1个manual wire recorder exact decouple；旧Provider test tree→Gateway dependency目标0 | provider-test-rehome |
| Runtime | production 48=4+44→45=3+42；42个TaskService/AgentContext operation与45+3构造点machine allowlist；unknown/default deny；storage-only无runtime派生 | runtime-operation-modes、runtime-bundle |
| test constructors | TaskService 144 identities=123 live test/nested（含3 skipped）+20 helper+1 shadowed；AgentContext31；目标C084=44 owner records/45 selectors，helper reverse-call与F033行为证据必需 | constructor/behavior-owner manifests |
| planned diff | 11 machine sources、35 additional paths、base-bound delete8、42 current+6 superseded、4 Final必需committed lifecycle paths；仅exact user patch可减，其他Feature/docs/design/unknown evidence fail closed | planned-diff、active/lifecycle/tree inventories |
| authority docs | Blueprint索引反算17个exact active authority documents，新增API/protocol与architecture tradeoffs；历史可显式保留，现役/必选/✅/Mermaid退役事实目标0 | authority-docs、S104 |
| evidence/commands | formal Python/Frontend统一canonical六件套；4个Final committed paths都有reachable producer；C084聚合pytest9；C19 pre/post=10/9且T122 fresh；T120-T124均有exact stage | lifecycle、evidence-producers、testing/stage manifests |
| external access | 自动Verify完整suite/all只登记确定性C23/C24；C18/live/host-credential/external-cost producer为0，HOME/凭证存在/skip不构成授权；C084 exact Bash语法与selector提取已做只读预检 | testing/stage/lifecycle/producers |

41个exact import与147个direct-name call只称machine ceilings，不冒充完整interaction graph；changed hunk仍需attribute-call/static职责检查与人工adversarial review。

## 2. Machine scope重算

- FR：42/42连续；tasks：76 unique、76 checked、0 unchecked。
- RGR：markdown 102个unique slice；scope JSON 102；ID集合双向相等。
- overlap：42 exact paths；3个all-required shared groups；40个stable symbol partitions；25个cross-phase paths/95 machine-counted members。clean-wheel不引入transfer例外：S011 6个preliminary/defer、S012 standard-backend 5个、import inventory 3个、child observation 2个、S070 full/all 4个与final closure 1个，共21/21 selectors唯一；root pyproject/lock由S012 Hatchling add与S047 SDK delete共4个exact `dependency:` selectors按三态transition分区。历史prefix-only/unresolved=2已由resolver R→G→R与fresh revalidation降至0。
- S084：44 exact owner paths；42 deterministic files+3 exact nodes=45 selectors；F033 baseline skip constructors3、target0；broad test glob=0。
- declared-new：127 exact paths，base-existing=0；T035新增3个T024 base-absent owner-test paths并由S017 exact `test_paths`取得ownership；新增canonical v2 index并保留rejected v1的exact governance owner，declared-new本身不取得ownership。
- namespace：49 move+2 delete；Provider tests 44 move+1 delete+1 manual exception。
- cross-role：41 import identities与147 call identities，count=unique；line只报告，source path先投影target。
- constructors：TaskService tests144=123 live test/nested（原3 skipped已移除）+20 helper+1 restored shadow；AgentContext31 unique；production TaskService target45=3/42、direct AgentContext3、operation42、planned callsite owner51/51。
- planned diff：machine sources11、additional exact paths35、active artifacts42 current/6 superseded；missing field=0、F151 self-exclusion escape=0。
- commands：testing/stage logical IDs=40，stage=17（pre6/post11）且集合相等；canonical v2当前251 records；record33/38/39仍chain-required，release资格由direct replacement/selector attestation补足。

## 3. TDD / 测试分层 / 架构 / 坏味道

- **TDD真实状态**：canonical v2现有254条record。T070两条slice、T081、T083两条slice、T084三条formal slice、T085 composition、T086 route preflight、T087 local close与S088 shutdown均完成正式R/G/R；T084恢复行为节点及T085 lifespan failure作真实characterization。T047-T049为机械retirement/phase gate，T066/T068/T080为真实characterization，T082为`refactor_of`直接复验，均不伪造新增formal records。首次dependency formal attempt与T031首次无效GREEN继续作INVALID历史，不具release资格。
- **测试分层**：L4负责DTO/model/store/service/adapter/selector/unknown projection/constructor purity；L3负责bootstrap/full-lifespan API/audit/wheel/tmp Git/raw Event→REST/static exit/lifespan assembly。F151新增L1=0、L2=0。
- **架构真值**：Provider→Gateway与services/routes→CLI目标0；domain保持纯净；operations application/store/adapter仍有legacy混边。新seam遵循contracts/domain←application/adapters←composition。
- **坏味道**：Update destructive dirty handling、Telegram RMW与Update active-attempt TOCTOU已分别由T031-T033关闭；ordinary read hidden write、storage-only hidden runtime与duplicate test qualname均已关闭。session persistence/extraction职责继续由后续composition/route gates审查；complexity与coverage不能替代职责/状态/oracle审查。

## 4. 关键negative oracles

1. env已设置但malformed/retired/unknown runtime YAML仍必须static typed exit78且Uvicorn0；`_resolve_front_door_mode`任一sibling semantic change失败。
2. lifespan composition failure不得映射static exit78；必须readiness/request/workload副作用0、process nonzero。
3. provider-test map遗漏Gateway-dependent collectable test、错误字段或retired source consumer失败。
4. operation/callsite missing、extra、unknown、mode不明，或storage-only可达model/reranker/background/network/runtime失败。
5. pytest9 nested/single suite聚合错误、missing/malformed suite、failure/error/skip/rerun、selected=0均失败。
6. main anchor mismatch、artifact byte替换、mixed tree/base/argv/JUnit/raw均失败。
7. tree delete未exact展开、planned/owner/changed三向缺口、missing field、其他Feature/docs/design变化、unknown ignored evidence或F151 active artifacts逃逸均失败。
8. T049追索Phase3/4、T070追索Phase4、shared group漏slice、partition空/重复/未覆盖hunk均失败。
9. authority历史段落合规；现役/必选/✅表格/Mermaid链旧事实及未列authority file失败。
10. formal `.bin`/`run.json`/noncanonical root/缺invocation或tree、superseded review无S002 owner、root dotfile用不可复算glob、T122复用T105、committed path无producer均失败。
11. tree非12字段或record逐字段不一致、v2前缀非6+2、review ID缺失/空/未知/复用、任一恢复中断不能重入或出现未知混合态、`architecture all`忽略/无法解析base-ref、finalize把自身列为前置均失败。

## 5. Gate结论

T006 evidence boundary、post-T006 frontier order Fix与formal RED recording + deterministic offline invocation subfix均已完成并获main接受。正式T007-T010、T012四slice/S011 release、T013 snapshot、T015-T016 route四slice、T017-T029 atomic transaction、T030-T034、T036 corrective、T040-T049、T060-T070、T081、T083-T090、T100-T105、T120-T124均已闭合；T066/T068/T080与T123 test stability因既有行为直接满足而诚实作characterization，T082按`refactor_of`同四nodes复验，均未伪造formal record。T122 fresh C19-post=`1404/1559=90.1%`，T123 C24全绿，T124 C25 final report已生成。canonical v2保持269 records/head=`913082c297cf19db3c76f73891efc56faa2b8db6993d03d8d233385403cbf878`。76个task全部完成。record33/38/39继续append-only chain-required；canonical链与run/index闭包成立。Goal完成。

## T011 clean-wheel 5-Why 与 owner closure

1. **症状**：T012若让五node GREEN，必须报告当前不存在的namespace zero、exit69、exit78与full/all事实。
2. **直接原因**：S011 scope只有checker，却把T017-T029 namespace transaction、T045、T064与S070/T070 production结果写进T012验收。
3. **流程原因**：旧计划把首次C11放在startup/source owners之前，与FR-028矛盾。
4. **证据原因**：formal runner忠实记录了“checker absent”RED，但无法判断test contract是否越权；因此record33字节有效不等于release语义有效。
5. **纠正**：T012只实现Provider/preliminary relocation与typed deferral；T049复验C09/C10；T070用独立S070 RGR首次启用full/all；record33由machine lifecycle排除release并由main-owned direct replacement RED补足TDD，T103最终核对binding。

机械所有权为：namespace final=`T017-T029`；source-managed exit69=`S045/T045`；app-instance/static exit78=`S064/T064`；first full/all=`S070/T070`；final automatic all=`C24/T123`。T012 combined implementation diff恰为runtime architecture checker、root dev scaffold `octoagent/pyproject.toml`、标准`uv lock`结果`octoagent/uv.lock`与唯一`repo-scripts/check-clean-wheel.py`四项；backend子预算仍三项，Provider/Gateway manifests及runtime产品代码diff=0。pyproject/lock中的S012 Hatchling add与S047 SDK delete必须用exact `dependency:` selectors按三态semantic delta互斥分区。

## T012 standard-backend scaffold 5-Why 与可行性

1. **症状**：若checker直接按pyproject dependencies手写wheel/METADATA，manifest=wheel的检查由同一算法生成并验证，可稳定false-green。
2. **直接原因**：最初shared venv与lock没有Hatchling，原T012 scope又只允许checker，诱发了绕开声明backend的第二打包算法；现在lock已落地，shared venv仍未bootstrap。
3. **架构原因**：wheel metadata的权威生产者应是项目声明的`hatchling.build`；checker只能消费标准backend产物，不能复制build backend职责。
4. **所有权闭环**：T012共四个corrective RGR。resolver slice独占runtime architecture checker四个stable functions；backend、import inventory与child observation分别独占single clean-wheel checker的5/3/2个stable functions；T070 final closure独占1个function。S047仍独占SDK retirement hunk，不引入transfer例外；三态required_absent/nonempty避免把不存在坐标伪装为已解析。
5. **负例闭环**：pin/lock/backend漂移、runtime泄漏、manual wheel/METADATA/RECORD、host cache/HOME、source/editable origin、未安装locked scaffold假PASS均必须fail closed；标准backend不可用时停Gate，不能降级。

main的独立`/tmp`实验只作Design可行性；grouped batch内又按授权执行了一次shared-venv scaffold，仍不是release evidence且不得重放。实际C09/C10以Hatchling 1.29.0标准backend构建当前9个workspace wheels，offline/no-deps target安装并从external cwd验证workspace origins来自target。CI/Final只能从committed lock经正常`uv sync --dev`取得backend。

dependency resolver已完成R/G/R与fresh unresolved=0。standard-backend/import/child/S011的active direct RED治理和formal G/R已全部闭合；S011/import后置helper修正以RED命中点未到达与AST delta管理，child v4以selector-level AST attestation保留资格。

replacement RED的机器协议不再使用“standard pytest/JUnit”散文代替事实：lifecycle冻结repo-root cwd、有序三项env（含pre-SDK PYTHONPATH与`LITELLM_LOCAL_MODEL_COST_MAP=True`）、16项ordered argv/absolute JUnit、11字段invocation、12字段tree/fingerprint、5个exact node、process exit=1、selected/failures=5/5、errors/skips/reruns=0与空stderr；main acceptance必须写回六件SHA/size map、canonical aggregate、test SHA、review ID与attestation binding。原始/tmp六件套保留到T012 REFACTOR复审，T103验证machine attestation，不依赖/tmp永久存在。

旧S011 acceptance `240558…/294b2e…/61047e…`仅作superseded history。active batch的S011 v5/import v5/child v4 aggregates为`7eae68d7…28521`/`83ecf652…36489`/`92fa08d7…f2e0`，review ID=`main-f151-t012-final-unowned-v5-review-20260721`，batch binding=`fca3d946f3c245737cbf8bd0a153766e5922afa17566f85b2ad1815ffc8abaec`，`canonical_index_adoption=false`。
