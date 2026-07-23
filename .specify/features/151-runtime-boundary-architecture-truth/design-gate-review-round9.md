# F151 Design/Tasks Gate Review — Round 9

**状态**：GATE_DESIGN=false；GATE_TASKS=false；artifact-only。TDD实际执行=0，76个tasks全unchecked，must-fix仍全部open。本轮未进入T001，未修改production/tests/repo-scripts/workflow，未stage/commit/push。

## 1. P1-01：tree/glob删除物化

**旧问题**：planned-diff不读取`resolved_globs`，SDK tree和skill目录占位漏掉真实Git paths。

**修改**：新增`tree-delete-expansion.v1.json`，绑定base `9d5e1e...`并列出SDK 6、`skills/llm-config/SKILL.md` 1、`.env.litellm.example` 1，共8个exact tracked path+object id。planned-diff读取`expansions.*.entries.*.path`，目录/glob不再充当changed path。

**负例/机械结果**：base object mismatch、漏展开、T029夹带非manifest path失败；3 patterns/8 unique paths，全部object与base一致。

## 2. P1-02/P1-08：active artifact与evidence生命周期

**旧问题**：`evidence/**`生成物既会触发unknown artifact，又可能被宽泛忽略而逃逸。

**修改**：新增`artifact-lifecycle.v1.json`与`evidence/.gitignore`。状态固定为Design→Phase0-RED→Implement→Verify→Final；6个committed exact paths逐项声明owner/first_state/first_writer/retention，4类local artifact逐项声明path template、slice/stage/name与retention。Git只忽略`evidence/local/`字节，checker仍必须遍历ignored路径。`evidence-index.v1.json`由T005在Phase0-RED首次创建，之后append-only且旧record hash不可变。

**负例/机械结果**：unknown/wrong phase/wrong writer/early file/unregistered type或name/ignored escape均失败；5 states、6 committed paths、4 local types、broad escape=0，全部committed path有first_writer。

## 3. P1-03：Phase0 main anchor机器输入

**旧问题**：main通信中的anchor没有正式CLI输入通道。

**修改**：T004后硬停`PHASE0_RED_REVIEW`；main创建唯一`evidence/bootstrap-anchor.v1.json`并在放行消息提供该文件SHA256。T005唯一入口为`C20-bootstrap`：`verify-bootstrap --bootstrap-anchor-file <exact> --bootstrap-anchor-sha256 <64-lower-hex>`。runner只能消费，不能生成、替换、重锚定或补跑。

**负例/机械结果**：missing、malformed、anchor hash mismatch、artifact replacement、mixed base/tree/argv/JUnit/stdout/stderr、second anchor全部冻结为exact S004 nodes；bootstrap slice恰6。

## 4. P1-04：SDK退役前后命令

**旧问题**：T048后多个Cxx仍含`packages/sdk/src`。

**修改**：`stage-command-matrix.v1.json`冻结pre-sdk、post-sdk、isolated三个profile和34个command IDs；C12/C15/C20拆pre/post，C20-bootstrap仅pre。T049及以后所有命令使用post profile。C19按T029/T036 pre10、T049/T070/T090/T105 post9，并用stage literal保存到lifecycle exact coverage目录。

**负例/机械结果**：post任一argv/env/cwd/selector/metadata含SDK失败；pre non-isolated漏SDK失败；34 commands、11 stages、post SDK refs=0。

## 5. P1-05/P1-09：runtime operation与生产构造点owner

**旧问题**：`AgentSessionTurnHook.__init__`没有唯一RGR owner；operation标签未证明capability reachability。

**修改**：S083-storage-purity精确拥有`agent_session_turn_hook.py::AgentSessionTurnHook.__init__`；其余46个TaskService target、2个deleted duplicates与2个Orchestrator direct AgentContext点由S084唯一拥有。gate从storage entrypoint构建interprocedural self/attribute/mixin/constructor call graph，不能只读标签。

**负例/机械结果**：42 operations；TaskService 48→46=3 runtime+43 storage；direct AgentContext3；planned constructor identities=51且unique owner=51；storage到MemoryRuntime/reranker auto-load/model/Router/background/network目标违规=0。

## 6. P1-06：C084的3个skipped构造点

**旧问题**：F033两条`@pytest.mark.skip`包含3个构造点，file-level collect无法证明行为。

**修改**：behavior owner把F033从file selector改为两个exact nodes；T084要求移除skip，并把漂移的prompt-text断言改为restart持久状态与跨project/session隔离oracle。C084由44 owner records展开42 files+3 nodes=45 selectors。144 identities分类为123 live test/nested（含3 skipped）+20 helper+1 shadowed；20 helper须reverse-call到collectable deterministic node。

**负例/机械结果**：任一F033 identity仍skip、helper无reverse-call、path存在但无行为node、selected=0/failure/error/rerun均失败；target evidence=144，target skipped=0。

## 7. P1-07：changed-path宽泛逃逸

**旧问题**：other feature roots与`docs/design/**`被整体排除，可隐藏F149/F150或设计文档误改。

**修改**：planned-diff删除所有目录/glob changed-path exclusion，只允许现有`.gitignore`用户patch在SHA256 `c4cca5...`完全不变时从changed set减去。retired-term历史例外与changed-path scope分离；F149/F150/其他Feature、`docs/design`及evidence变化默认都必须有exact F151 owner，否则失败。

**负例/机械结果**：任意其他Feature/docs/design变化、用户patch byte drift、F151 evidence/archive自匹配逃逸均失败；broad exclusion=0，exact preexisting baseline path=1。

## 8. planned/scope/lifecycle三向机器闭包

**修改**：planned-diff现在有11 machine sources+33 additional exact paths；active artifacts=41 current+5 superseded；scope=90/90 slices、declared-new125（base existing=0）。planned/slice/changed集合以真实field/path重算，missing field稳定失败。Round9 review是Design current；Gate批准后其exact bytes冻结为design snapshot，Verify/Final authority由各自report承担。

**机械结果**：missing field=0；planned-without-owner=0；owner-without-planned=0；F151 self-exclusion=0。真实implementation前这些是artifact schema validation，不是已执行gate pass。

## 9. P1-10：active authority docs完整性

**旧问题**：S104原9路径遗漏`docs/blueprint.md`等仍把Proxy/kernel/worker/current Docker写成现役的authority docs。

**修改**：新增`authority-docs.v1.json`，从9扩到15个exact paths，新增：`docs/blueprint.md`、`architecture-overview.md`、`requirements.md`、`appendix.md`、`testing-strategy.md`、`modules/02-gateway-runtime-and-control-plane.md`。S104/planned-diff/tasks/contract/trace同步。当前真值固定为ProviderRouter/direct transports、单Gateway runtime、无物理kernel/worker package、Docker仅历史Event decode/projection。

**语义fixture/处置**：明确历史/已退役段落可保留；当前/必选声明、✅旧完成表、current Mermaid旧运行链失败；authority index链接但machine set遗漏失败。15/15均标action=modify/disposition，未处置现役陈述目标0。F151合入后F150必须rebase并重核authority diff。

## 10. F150 future config seam事实更正

当前baseline只有`OctoAgentConfig.front_door`/`FrontDoorConfig`，`manifest_path`与`owner_email`尚不存在。F151只冻结现有canonical schema/loader/setup IO作为未来F150字段唯一落点，本轮不添加、不实现、不迁移且static tests不期待字段存在；现有FrontDoorConfig hash保护当前语义。F150实施时显式更新hash/allowlist/tests。

## 11. 保持已通过方向

- startup A：module entry解析exact argv后只import一次`main.app`；`create_app()`唯一static preflight；完整load一次后env>YAML。
- startup B：exit78只覆盖同步static config；真实composition仍由lifespan fail closed/nonzero。
- Provider tests：44 move+1 delete+1 manual decouple。
- production TaskService：48→46=3/43；ProviderRoute、precomputed persistence、descriptor ordinary zero-write等既定方向未重写。

## 12. 四项审查与剩余风险

- **TDD**：实际RED/GREEN/REFACTOR=0；Phase0还未开始。
- **分层**：L4覆盖machine gate/model/store/service/constructor purity；L3覆盖startup/API/Event/wheel/tmp Git；新增L1/L2=0。
- **架构**：operations仍是诚实legacy mixed cluster；41 imports/147 direct-name calls只称ceilings；新seam单向且storage capability默认拒绝。
- **坏味道**：F033 skip/shadow、descriptor hidden write、Telegram RMW、Update TOCTOU、storage hidden runtime等must-fix仍open until implementation。

## 13. Gate结论

本轮只完成artifact一致性返修。GATE_DESIGN=false、GATE_TASKS=false；不请求T001/Implement。等待main复审。
