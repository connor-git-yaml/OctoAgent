# F151 Gateway wheel direct-dependency manifest

本清单以baseline production AST imports与49-module Gateway layered relocation（15 CLI + 1 config + 33 operations）为输入。Gate必须比较“source import→distribution ownership”与installed wheel `Requires-Dist`，不能因transitive install成功而放行。

## Internal main dependencies

| import namespace | distribution |
|---|---|
| `octoagent.core` | `octoagent-core` |
| `octoagent.memory` | `octoagent-memory` |
| `octoagent.policy` | `octoagent-policy` |
| `octoagent.protocol` | `octoagent-protocol` |
| `octoagent.provider` | `octoagent-provider` |
| `octoagent.skills` | `octoagent-skills` |
| `octoagent.tooling` | `octoagent-tooling` |

## Gateway third-party main dependencies（25）

| imports | distribution |
|---|---|
| `aiosqlite` | `aiosqlite` |
| `apscheduler` | `apscheduler` |
| `click` | `click` |
| `cryptography` | `cryptography` |
| `fastapi` | `fastapi` |
| `filelock` | `filelock` |
| `httpx` | `httpx`（从dev移入main） |
| `jieba` | `jieba` |
| constant dynamic `import_module("keyring")` | `keyring` |
| `lancedb` | `lancedb` |
| `logfire` | `logfire` |
| `mcp` | `mcp` |
| `pyarrow` | `pyarrow` |
| `pydantic` | `pydantic` |
| `pydantic_graph` | `pydantic-graph` |
| `dotenv` | `python-dotenv` |
| `ulid` | `python-ulid` |
| `yaml` | `PyYAML` |
| `questionary` | `questionary`（legacy CLI composition ownership迁入） |
| `rich` | `rich`（legacy CLI presentation ownership迁入） |
| `sse_starlette` | `sse-starlette` |
| `starlette` | `starlette` |
| `structlog` | `structlog` |
| application command | `uvicorn` |
| `watchdog` | `watchdog` |

`jieba`、`pyarrow`在Gateway production模块顶层import，`lancedb`是现役memory主路径；三者不得放入默认不安装的extra。

`keyring`由迁入operations adapter的`secret_refs.load_keyring_module()`常量动态导入；AST gate必须扫描该字符串并要求Gateway main直接声明，不能依赖root或Provider传递依赖。

## Named optional extras

| extra | imports | distributions | contract |
|---|---|---|---|
| `voice` | `av`, `faster_whisper`, `piper` | `av`, `faster-whisper`, `piper-tts` | voice功能启用时三项都直接声明，不依赖传递安装 |
| `local-embedding` | `sentence_transformers` | `sentence-transformers` | 从Provider迁给Gateway |
| `tokenizer` | `tiktoken` | `tiktoken` | 未安装时必须走现有显式fallback；不影响core readiness |

`benchmarks`是repository source tree，不是Gateway wheel dependency；`octo-bench`在wheel环境保留entry但以exit 69 `SOURCE_CHECKOUT_REQUIRED`诚实失败。

## T012 当前源码 import 分类事实（pre-relocation inventory）

分类器只扫描被评估distribution在isolated target中的installed file set，并为每个occurrence保留`source_file/line/syntax/import_root/resolved_distribution/context/workspace_owner`。`runtime-required`指不在`TYPE_CHECKING`、test-plugin或可执行optional ImportError/availability边界内的模块级或函数内import；函数内并不自动等于optional。`optional-lazy`必须有真实guard/fallback；`type-checking`与`test-plugin`不得满足runtime Requires-Dist；`workspace-owned`是附加ownership维度，仍须保留执行context。非literal dynamic import或无法唯一分类的context为unknown并失败，禁止path/name allowlist。

| distribution | 当前root/distribution | 当前分类 | T012必须如实报告 | 最终owner |
|---|---|---|---|---|
| Provider | `aiosqlite` | `runtime-required`，来自待迁出的DX operations | observed且当前manifest未声明；不得隐藏 | T017-T029迁出，Gateway/T023承接 |
| Provider | `octoagent.gateway` | `workspace-owned + runtime-required`，当前反向DX边 | observed且Provider非法反向依赖 | T017-T029 namespace closure |
| Provider | `pytest` | `test-plugin` | observed但不进入runtime Requires-Dist | T021 test/plugin rehome |
| Provider | `ulid`→`python-ulid` | `runtime-required` | observed且当前manifest未声明 | T023 Provider manifest |
| Provider | `jieba`,`lancedb` | 当前manifest声明、Provider自身未观察到runtime import | declared-unobserved；T012不得伪造AST evidence | T023在DX迁出后移除 |
| Gateway | `aiosqlite`,`click`,`filelock`,`httpx`,`jieba`,`lancedb`,`mcp`,`pyarrow`,`pydantic`,`pydantic_graph`,`dotenv`,`ulid`,`yaml`,`starlette` | `runtime-required`（含迁入DX与现有Gateway模块） | 每个occurrence与distribution映射完整；当前manifest delta仅inventory | T017-T029 + T023 |
| Gateway | `octoagent.memory`,`octoagent.protocol`,`octoagent.skills`,`octoagent.tooling` | `workspace-owned + runtime-required` | origin/owner来自same-transaction wheel与isolated target | T023 manifest + T070 final gate |
| Gateway | `av`,`faster_whisper`,`piper`,`sentence_transformers` | `optional-lazy` | 必须显示真实lazy/optional guard并映射到named extra | T023 extras + T070 final gate |
| Gateway | `tiktoken` | `optional-lazy` with executable fallback | 未安装fallback与安装路径都须可观测 | T070 final gate |
| Gateway | `benchmarks` | source-managed repository capability，不是wheel dependency | wheel路径只能typed source-required，不进入Requires-Dist | T045 guard |
| Gateway | `uvicorn` | 当前manifest声明、Gateway自身未观察到import | declared-unobserved；T012不得用installed availability代替evidence | T064 application-host + T070 final gate |

当前机械差异基线必须原样进入T012 report：Provider manifest=15、observed distributions=17，declared-unobserved=`jieba,lancedb`，observed-undeclared=`aiosqlite,octoagent-gateway,pytest,python-ulid`；Gateway manifest=11、observed distributions=34，declared-unobserved=`uvicorn`，其余observed-undeclared至少包含main复审列出的24个roots。该基线是test-code review的observable truth，不是永久数量oracle；源码变化后从installed files重算。

T012的PASS只表示：source manifest=真实wheel METADATA、所有occurrence完成上述分类、差异集合未被隐藏、`final_verdict=null`且`final_owner=T070`。它不表示当前manifest等于runtime imports。T023只拥有Provider/Gateway manifest与lock目标；namespace/rehome由T017-T029、source guard由T045、startup import/runtime protocol由T064拥有。T070在这些owner全部完成后才要求Provider `1+6`、Gateway `7+25`与最终runtime/optional/workspace evidence闭合。

### 分类与隔离 observable controls

- positive：module-level与function-local unconditional均归`runtime-required`；`if TYPE_CHECKING`归`type-checking`；`try/except ImportError|ModuleNotFoundError`或真实availability fallback归`optional-lazy`；installed test/plugin module中的`pytest`归`test-plugin`；workspace root同时记录owner与执行context；literal`import_module`/`__import__`按所在context分类。
- negative：target全目录扫描污染distribution、自定义path/name allowlist、installed availability冒充import、nonliteral dynamic import、unknown distribution/context、manifest差异被置空、T012输出final PASS均失败。
- child positive：执行import的同一child回传cwd、ordered `sys.path`、exact env、user-site、prefix/base-prefix与workspace origins；parent只验证这些观测值。
- child negative：host HOME/XDG/TMP/cache、ambient PYTHONPATH、repo/source/editable path、user-site enabled、workspace origin不在target、字段缺/多或stdout额外文本均失败；parent重构的“预期facts”不能替代child输出。

## Gate rules

1. main-scope非stdlib绝对import必须由通用context classifier映射到runtime distribution、workspace owner或可执行optional fallback；禁止path/name allowlist。
2. extra-scope import必须由对应extra直接声明；传递依赖不算direct。
3. T012只验证current source manifest与真实wheel METADATA一致并输出完整分类inventory；最终Requires-Dist与runtime evidence精确闭包延至T070。
4. Provider-only metadata不得出现Gateway/Memory/CLI distribution；base LiteLLM仅允许pricing module消费。
5. 新增未映射/unknown import立即失败；T070时删除仍被runtime-required import的distribution、把main dependency下沉到extra、optional没有真实guard或test/type-only污染runtime依赖均失败。

## Provider最终direct inventory

DX迁出与Proxy删除后，Provider main只允许：

| 类别 | import/distribution |
|---|---|
| internal 1 | `octoagent.core` → `octoagent-core` |
| third-party 6 | `litellm`（仅`cost.py` pricing）、`httpx`、`pydantic`、`structlog`、`filelock`、`python-ulid`（`auth/events.py`直接`from ulid import ULID`） |

Provider manifest必须移除无非DX production消费者的`octoagent-memory`、`click`、`rich`、`questionary`、`python-dotenv`、`keyring`、`PyYAML`、`lancedb`、`jieba`以及`local-embedding` extra；`litellm[proxy]`收窄为base `litellm`。pytest插件/TYPE_CHECKING/dev依赖按host/dev分类，不计main。gate扫描静态import、TYPE_CHECKING与constant dynamic import，并比较Provider wheel `Requires-Dist`精确集合。
