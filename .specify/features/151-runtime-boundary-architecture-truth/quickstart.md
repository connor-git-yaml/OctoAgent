# F151 Verification Quickstart（T006 index-amendment corrective contract）

> 这是implementation完成后的验收契约。T016 route boundary已完成；T017 atomic migration尚未开始。

所有命令从repository root运行；frontend命令使用subshell。worktree禁止`uv sync`与裸pytest。精确phase命令以[`inventories/testing-matrix.md`](inventories/testing-matrix.md)为准：T048前使用含SDK source的pre-SDK lock，T048后与最终C23/C24使用不含已退役SDK的post-SDK lock；clean-wheel始终清空`PYTHONPATH`。行为slice的exact nodeids与RGR命令encoding见[`inventories/rgr-slices.md`](inventories/rgr-slices.md)。

## 定向与分层

按RGR manifest的exact node先记录实际RED，再以完全相同nodeids GREEN、REFACTOR。Cxx仅在phase review回归。L4使用tmp SQLite/path+DI fake；L3使用Echo/ScriptedModelClient/full app或真实tmp Git；L1/L2新增0。F151新增测试和自动Verify禁止network、真LLM、宿主`~/.octoagent`/凭证、外部成本、fixed sleep、blanket rerun或复制生产算法；C18/`lane.py baseline`不在本Feature命令集合中，默认HOME、凭证存在或skip都不是授权。

namespace必须独立报告：Provider exact machine map 44 rehome + 1 delete + 1 manual recorder Gateway import decouple、Gateway21 refs、root integration2、Memory1、root gate wiring1；D-03报告CLI15/config1/operations33/source0/51、legacy role tags 13/5/9/6、CLI exact side-effect no-growth、T029 snapshot/三个exception、services/application→presentation0、eager SCC0/full唯一三节点。41 import与147 direct-name call只是ceilings；changed hunks另做attribute-call与人工adversarial responsibility review。Final复验T029 snapshot against base；其后target变化必须有stable symbol scope与RGR evidence。

## Architecture gates

```bash
env PYTHONNOUSERSITE=1 PYTHONPATH="$PWD/octoagent/packages/core/src:$PWD/octoagent/packages/provider/src:$PWD/octoagent/packages/protocol/src:$PWD/octoagent/packages/tooling/src:$PWD/octoagent/packages/skills/src:$PWD/octoagent/packages/policy/src:$PWD/octoagent/packages/memory/src:$PWD/octoagent/apps/gateway/src" uv run --project octoagent --no-sync python repo-scripts/check-runtime-architecture.py import-direction
env PYTHONNOUSERSITE=1 PYTHONPATH="$PWD/octoagent/packages/core/src:$PWD/octoagent/packages/provider/src:$PWD/octoagent/packages/protocol/src:$PWD/octoagent/packages/tooling/src:$PWD/octoagent/packages/skills/src:$PWD/octoagent/packages/policy/src:$PWD/octoagent/packages/memory/src:$PWD/octoagent/apps/gateway/src" uv run --project octoagent --no-sync python repo-scripts/check-runtime-architecture.py retired-terms
env PYTHONNOUSERSITE=1 PYTHONPATH="$PWD/octoagent/packages/core/src:$PWD/octoagent/packages/provider/src:$PWD/octoagent/packages/protocol/src:$PWD/octoagent/packages/tooling/src:$PWD/octoagent/packages/skills/src:$PWD/octoagent/packages/policy/src:$PWD/octoagent/packages/memory/src:$PWD/octoagent/apps/gateway/src" uv run --project octoagent --no-sync python repo-scripts/check-runtime-architecture.py complexity --base-ref origin/master
env PYTHONNOUSERSITE=1 PYTHONPATH="$PWD/octoagent/packages/core/src:$PWD/octoagent/packages/provider/src:$PWD/octoagent/packages/protocol/src:$PWD/octoagent/packages/tooling/src:$PWD/octoagent/packages/skills/src:$PWD/octoagent/packages/policy/src:$PWD/octoagent/packages/memory/src:$PWD/octoagent/apps/gateway/src" uv run --project octoagent --no-sync python repo-scripts/check-runtime-architecture.py quality-smells
env PYTHONNOUSERSITE=1 PYTHONPATH="$PWD/octoagent/packages/core/src:$PWD/octoagent/packages/provider/src:$PWD/octoagent/packages/protocol/src:$PWD/octoagent/packages/tooling/src:$PWD/octoagent/packages/skills/src:$PWD/octoagent/packages/policy/src:$PWD/octoagent/packages/memory/src:$PWD/octoagent/apps/gateway/src" uv run --project octoagent --no-sync python repo-scripts/check-runtime-architecture.py tdd-evidence verify --mode local-working-tree --base-ref origin/master --evidence-index .specify/features/151-runtime-boundary-architecture-truth/evidence/evidence-index.v2.json
env PYTHONNOUSERSITE=1 PYTHONPATH="$PWD/octoagent/packages/core/src:$PWD/octoagent/packages/provider/src:$PWD/octoagent/packages/protocol/src:$PWD/octoagent/packages/tooling/src:$PWD/octoagent/packages/skills/src:$PWD/octoagent/packages/policy/src:$PWD/octoagent/packages/memory/src:$PWD/octoagent/apps/gateway/src" uv run --project octoagent --no-sync python repo-scripts/check-runtime-architecture.py all --base-ref origin/master
```

CI以PR base SHA或push-before SHA计算merge-base，不写死`origin/master`。formal Python/Frontend evidence只能写`evidence/local/runs/<slice>/<phase>/`的`junit.xml/stdout.txt/stderr.txt/exit-code.txt/invocation.json/tree.json`六件套；`.bin`、`run.json`、root override、缺件或多件失败。evidence还必须通过missing/fake/reordered/selector/collection/skip/rerun、JSONL/raw一致伪造但JUnit不符等负面fixtures；事实来自JUnit/raw stdout/stderr/exit/invocation/tree交叉验证，JSONL只作索引。

Phase0唯一bootstrap anchor及36个raw保持不变；rejected v1/runs继续只读quarantine。T006恢复时的20-record/12-run前缀与26-record纠正终点不得回写；T012完成态canonical v2为47 records，SHA=`64c6e18fe634a0c3dd394adaa542b74635202c290f2ffb8852597b5faf7feac1`，head=`53fa74a50d2c5b4e31208d6e2307369e958842a76f62c01c1e1217f89ac77602`，run↔index=47/47，新增formal evidence只能append。committed mode仅在fingerprint scope clean时允许；任一staged/unstaged/untracked相关路径必须在base diff前失败，exact generated evidence outputs可排除。`planned-diff.v1.json`、base-bound tree/exact-delete expansion、两份constructor inventories、Provider test rehome、operation allowlist、artifact lifecycle、evidence producer、17-doc authority set与98-slice RGR scope共同构成closure。

## Clean-wheel

```bash
env PYTHONNOUSERSITE=1 PYTHONPATH= uv run --project octoagent --no-sync python repo-scripts/check-clean-wheel.py provider
env PYTHONNOUSERSITE=1 PYTHONPATH= uv run --project octoagent --no-sync python repo-scripts/check-clean-wheel.py gateway --level relocation
# 以下两条只有T070完成后才允许执行；T012阶段必须typed phase deferral，不得伪造full PASS。
env PYTHONNOUSERSITE=1 PYTHONPATH= uv run --project octoagent --no-sync python repo-scripts/check-clean-wheel.py gateway --level full
env PYTHONNOUSERSITE=1 PYTHONPATH= uv run --project octoagent --no-sync python repo-scripts/check-clean-wheel.py all
```

T012未直接从旧replacement RED进入checker GREEN。requires-dist/isolation测试改写后，旧240558/294b2e/61047e证据只作superseded history；实际批次先完成observable-delta review、fresh S011/import RED与child selector-level predecessor binding，再进入checker G/R。

T012 preliminary只验证：当次source manifest=标准Hatchling真实wheel METADATA；被评估distribution自身installed files的每个literal import使用runtime-required/optional-lazy/type-checking/test-plugin四类context，workspace ownership正交记录，ownership_state为resolved或unowned；resolved owner只来自transaction target exact RECORD或lock匹配的exact project-purelib RECORD，unowned完整保留file/line/syntax/root/context。当前delta、unowned projection/count完整输出，`final_verdict=null`；隔离facts来自同一真实child JSON。C09 Provider与C10 Gateway relocation均PASS，后者如实报告12个unowned occurrences。Provider1+6/Gateway7+25由T023写manifest/lock，T017-T029/T045/T064各自完成production owners，T070再执行final strict closure。

下列bootstrap已由main在T012批次内执行一次并且仅作local toolchain scaffold，不是release evidence，不得重复执行，worktree仍禁止`uv sync`：

```bash
env UV_CACHE_DIR=/Users/connorlu/.cache/uv uv pip install --offline --python /Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/octoagent/.venv/bin/python 'hatchling==1.29.0'
```

CI/Final必须从committed lock经正常`uv sync --dev`取得backend，不依赖上述host cache。脚本创建repo外cwd和隔离HOME/XDG/user-site/PYTHONPATH；Gateway full覆盖`octo --help`、`octo doctor --help`、`octo auth --help`、update-worker、host/start/SIGTERM、non-Echo canonical alias structural readiness（只做本地structure+alias resolution，零DNS/HTTP/model）、exit78与source-only exit69。relocation level不含full；full必须等retired manifest/lock/absence与T045/T064 owners完成。

## Frontend与benchmark L4

```bash
(cd octoagent/frontend && npm exec vitest -- run && npm exec tsc -- -b)

env PYTHONNOUSERSITE=1 \
  PYTHONPATH="$PWD/octoagent/packages/core/src:$PWD/octoagent/packages/provider/src:$PWD/octoagent/packages/protocol/src:$PWD/octoagent/packages/tooling/src:$PWD/octoagent/packages/skills/src:$PWD/octoagent/packages/policy/src:$PWD/octoagent/packages/memory/src:$PWD/octoagent/apps/gateway/src:$PWD" \
  uv run --project octoagent --no-sync python -m pytest \
  benchmarks/tests/unit/test_octo_runner.py::test_source_checkout_required_before_side_effects \
  benchmarks/tests/unit/test_octo_runner.py::test_runner_fn_provider_error_maps_to_infra_error
```

frontend不宣称由Python changed-lines覆盖；必须完整Vitest+tsc。benchmark测试是root testpaths外的确定性L4。

## Runtime constructor行为集

T084后运行testing matrix的C084原样transaction：machine behavior-owner map必须与两份constructor inventories合并后的44 owner paths双向相等；它运行42个确定性test files、1个live-helper替代L4 node与F033两个exact nodes，并要求selected>0、fail/error/skip/rerun=0。额外的storage-only契约要证明TaskService与AgentContext XOR，以及AgentContext不会创建MemoryRuntime/reranker、不会auto-load模型、注册background task或访问网络；precomputed completion保持Task/Event/Artifact/checkpoint/SessionContext/turn/session副作用且model/Router/recall/compaction/extraction调用均为0。

## Fresh changed-lines coverage

不得读取仓库中可能stale的固定LCOV。使用testing matrix的C19-pre/C19-post单shell transaction；Verify必须以`F151_COVERAGE_STAGE=T122`写入`evidence/local/coverage/T122/`并绑定本次开始UTC、HEAD/tree与worktree fingerprint，禁止复用T105：`mktemp -d`→显式`mkdir -p` report parent→记录UTC/base→按retirement state运行10或9个exact testpaths→第二段`--cov --cov-append` scripted coverage并输出临时LCOV→同一次调用既有checker `--mode local-working-tree`。报告必须含resolved base、UTC、HEAD tree、worktree fingerprint、LCOV SHA/mtime/freshness，status必须是PASS；EXEMPT不能完成F151的Python≥90%。CI backend coverage lane改用committed mode；独立architecture job不拥有LCOV。

## Final Verify

1. 按T120 machine stage原样运行22个exact post-SDK regression IDs；T121只运行C23，T122运行fresh C19-post+C16，T123运行确定性C24。C24的architecture all必须用同一resolved base commit驱动全部base-aware subgate，missing/unresolvable ref失败。T124只以T120-T123及其input closure为前置，不依赖自身C25/report；C25成功才生成verification report并形成T124输出。
2. 不运行C18/`lane.py baseline`；显式运行确定性pytest `-n auto --dist=loadgroup`，并证明F151新增/修改node RERUN=0、quarantine相对base no-growth；既有登记rerun单列review-date报告。任何另行提出的live/manual检查都须main预检并取得用户单次授权，且不进入F151自动证据。
3. 运行clean-wheel all、architecture all、fresh C19、完整frontend与benchmark L4。
4. gate锁定8个F101 L4 normal Inline fixtures；raw historical event→REST L3恰一条，unknown projection为L4。
5. 验证RuntimeServiceBundle目标45=3/42、orchestrator 1141 storage-only、唯一session persistence primitive、`aclose`顺序、完整precomputed result副作用与bundle/Router/recall/compaction/extraction call=0；C084的44 owner records/45 selectors行为集全绿，F033三个原skip构造点有确定性行为证据。
6. 按`f150-scope.md`只检查F150-owned universe：7个protected FrontDoor symbol AST不变、两个allowed handler与module-entry exact symbol之外的F150 semantic diff=0；D-03 `main.py` import-only由namespace gate批准。module entry先解析exact help/host/port，再在typed boundary内只import一次`main.app`；`main.app=create_app()`执行唯一canonical preflight，config/exposure各一次，Uvicorn接收app instance与完全相同的host/port。不得实现Host/Origin/Access，不触F149功能。
7. 扫描`authority-docs.v1.json`全部17份文档：显式历史陈述可保留，现役/必选/✅表格/Mermaid运行链旧Proxy/kernel/worker/current Docker事实必须为0；Review报告RGR、L4/L3/L1/L2、依赖层、坏味道、owner/SCC、coverage与剩余风险。

只有全部硬门全绿后才能进入GATE_VERIFY；否则Goal保持未完成。
