# F151 Production / Service 启动入口冻结清单

本清单冻结 F151 实施前后的托管启动事实。`python -m octoagent.gateway` 是完成态唯一 production/service application-host 入口；`octoagent.gateway.main:app` 只保留 ASGI import、测试和 Uvicorn 内部 app target contract，不再是可由脚本、descriptor 或 service manager 直接执行的生产入口。

## 1. 当前托管链与完成态

```text
install-octo-home.sh
  -> gateway.cli.install_bootstrap
  -> ManagedRuntimeDescriptor
  -> ServiceManager (launchd/systemd/COMMAND)
  -> run-octo-home.sh
  -> python -m octoagent.gateway
  -> parse exact help/host/port
  -> import octoagent.gateway.main.app exactly once
  -> main.app=create_app() performs canonical static config/security exposure preflight exactly once
  -> Uvicorn serves that app instance with the same resolved host/port values
```

| id | 当前 path / symbol | 当前 argv /事实 | 完成态 action |
|---|---|---|---|
| `PSTART-01` | `octoagent/scripts/run-octo-home.sh` | `uv run uvicorn octoagent.gateway.main:app --host ... --port ... "$@"` | 改为 `uv run python -m octoagent.gateway ...`；不得直接执行 `main:app` |
| `PSTART-02` | `provider/dx/install_bootstrap.py::_build_runtime_descriptor`（迁移后 Gateway CLI） | source/dev descriptor 直接执行 Uvicorn；managed descriptor执行wrapper | 新生成的两类descriptor都只指向canonical module entry或其唯一wrapper |
| `PSTART-03` | `provider/dx/install_bootstrap.py::run_install_bootstrap` | existing descriptor非force时原样保留；next action宣称direct Uvicorn | 只有显式install/bootstrap/update操作可在validated transaction内atomic migrate并写回legacy argv；普通load/start绝不写；next action不再宣称direct Uvicorn |
| `PSTART-04` | `provider/dx/service_manager.py::build_spec/render`（迁移后operations） | 把descriptor argv原样写入launchd/systemd | 普通build/start只接受canonical entry/wrapper；legacy direct argv typed reject并给显式迁移指引，不在read/start路径顺手写回 |
| `PSTART-05` | `provider/dx/update_service.py::_run_restart_phase`（迁移后operations） | COMMAND可Popen persisted descriptor | 显式update可先validated atomic migration；执行时只允许canonical argv。普通restart/start遇legacy argv typed reject，绝不隐藏写回或绕过preflight |
| `PSTART-06` | `octoagent.gateway.main:app` | module import时创建ASGI app | 保留ASGI/import/test用途；不计为第二生产入口 |
| `PSTART-07` | `octoagent/apps/gateway/src/octoagent/gateway/__main__.py` | baseline不存在 | 新增唯一module entry；先解析exact argv，再在typed exception boundary内只import一次`main.app`；import触发唯一`create_app()`及一次preflight，随后把app instance和同一resolved host/port值交Uvicorn，不构造第二FastAPI/OctoHarness/runtime |

## 2. 旧 application-host 字符串 inventory

baseline exact literal `octoagent.gateway.main:app` 共18处：

| surface | count | paths / action |
|---|---:|---|
| production | 3 | `scripts/run-octo-home.sh`、`install_bootstrap.py` descriptor、`install_bootstrap.py` next action：全部迁移 |
| active tests | 8 | `apps/gateway/tests/test_main.py`、Provider DX tests 5处、root integration F023/F031 2处：除`test_service_manager`保留一个明确legacy-rejection fixture外均迁移canonical；fixture内旧字符串必须带exact negative-purpose ID |
| archived Feature docs | 7 | F001/F002/F008/F012/F129历史制品：标记`archive/no-runtime-authority`，不作为active入口；retired gate只按exact path+purpose允许 |

完成态 gate：

- active production/service argv中的 `uvicorn octoagent.gateway.main:app` = 0；
- `python -m octoagent.gateway` 是唯一 managed/source service entry；
- service descriptor、wrapper、install/update文档与测试均证明先preflight后Uvicorn；
- `import octoagent.gateway.main:app` 只在ASGI/test consumer inventory内允许，不能执行宿主。

## 3. 参数、环境与 cwd precedence

完成态只有一个参数owner：`gateway.__main__`解析、验证并把resolved host/port作为显式值传给Uvicorn。wrapper与descriptor不得再解析host/port或追加Uvicorn flags。支持的exact argv只有`--help`、`--host VALUE`/`--host=VALUE`、`--port VALUE`/`--port=VALUE`；重复同名option、缺value、非法host/port或任何unknown argv均在import `main.app`、preflight与Uvicorn副作用前以usage exit64拒绝。当前仓库没有带额外`$@` option调用wrapper的现役consumer；旧脚本的任意透传未形成公开文档契约，F151不把它继续扩成双parser。

module entry必须保持下列单值precedence。这里冻结的是值相等与单次解析，不是跨module共享对象identity：CLI解析值通过现有argv/env contract使`create_app()`看到相同host，Uvicorn再接收相等的resolved host/port；不得新增global snapshot或第二factory。

| value | precedence / contract |
|---|---|
| instance root | `OCTOAGENT_INSTANCE_ROOT` > isolated service HOME下`.octoagent` |
| project root | process/service `OCTOAGENT_PROJECT_ROOT` > canonical `.env` > cwd；wrapper继续把managed instance root作为默认project root |
| data dir | process/service `OCTOAGENT_DATA_DIR` > canonical `.env` > `<instance>/data` |
| host | 唯一CLI `--host` > loaded/process `OCTOAGENT_HOST` > `127.0.0.1`；`create_app()`内既有`_resolve_startup_host`与Uvicorn实际bind值必须exact equality，不能出现安全校验与bind分叉 |
| port | 唯一CLI `--port` > loaded/process `OCTOAGENT_PORT` > `8000`；readiness/descriptor使用同一resolved port，不能用固定8000或另一个port env |
| front-door mode | 完整`load_config(project_root)`在任何env mode early-return前恰一次；结果仍为既有 `OCTOAGENT_FRONTDOOR_MODE` > YAML > `loopback`，F151不得改变三种mode或判定。env mode存在也不能绕过malformed/retired/unknown runtime YAML |
| cwd | managed service为instance root；source/dev descriptor为project root；module entry不得自行切换到第二project/runtime |

没有`--config` argv：config root只按上表project/instance/cwd和canonical `.env`解析。entry不能转发unknown option给第二个Uvicorn parser；L4/L3必须分别断言exact supported集合、重复/unknown exit64、`create_app()`恰一次、config resolution恰一次、exposure validation恰一次、resolved host/port exact equality与Uvicorn app-instance调用参数。

## 4. Fail-closed 与迁移支持矩阵

| 场景 | action / oracle |
|---|---|
| 新安装/新descriptor | 只写canonical module entry；descriptor与service spec结构测试通过 |
| canonical descriptor：普通load/start/restart | 纯读取并执行canonical entry；descriptor及相邻文件字节级0写 |
| persisted legacy direct Uvicorn argv：普通load/start/restart | typed `RUNTIME_DESCRIPTOR_MIGRATION_REQUIRED`并提示运行显式install/update/bootstrap migration；descriptor及相邻文件字节级0写、0serve |
| persisted legacy direct Uvicorn argv：显式install/update/bootstrap | 先validate source/root/target，再atomic migrate并写回canonical argv；失败保留旧descriptor且0serve |
| invalid schema或invalid JSON：普通load/start/restart | typed `RUNTIME_DESCRIPTOR_INVALID`；不得normalize/save，也不得生成`.corrupted`副本；目录字节级0写、Uvicorn与service subprocess调用0 |
| explicit repair | 仅显式install/update/bootstrap可调用既有store上的`repair_runtime_descriptor`，以replacement+expected digest validated atomic replace；失败保留原字节 |
| malformed persisted argv | service/descriptor boundary typed `RUNTIME_DESCRIPTOR_INVALID`，目录字节级0写，module entry/Uvicorn/service subprocess调用0；不得冒充已进入Gateway static config preflight |
| static runtime config invalid | `OctoAgentConfig.from_yaml`完整解析得到runtime/retired/unknown/root application config错误；即使front-door env override存在也传播为`GATEWAY_RUNTIME_CONFIG_INVALID`，module entry在Uvicorn前exit78；Task/Work/Event/backend=0 |
| static security config/exposure invalid | `front_door|security` schema或既有exposure判定异常传播为`GATEWAY_SECURITY_CONFIG_INVALID`，module entry在Uvicorn前exit78；Task/Work/Event/backend=0 |
| runtime service composition/assembly失败 | 不伪装static config错误、不要求exit78；唯一FastAPI lifespan/OctoHarness composition root终止startup，readiness=0、请求0、Task/Work/Event/backend=0、process startup nonzero；不得新增第二composition validator或重复runtime构造 |
| valid source service | wrapper/launchd/systemd/COMMAND均到同一entry，启动、readiness、SIGTERM通过 |
| wheel环境source-only service/install/update/bench | 副作用前exit69 `SOURCE_CHECKOUT_REQUIRED`；不把entry absence伪装成成功 |

## 5. Gate 与测试 owner

- L4：`TestProductionStartupInventory`解析shell、Python常量、descriptor、service render；拒绝第二host字符串、legacy argv继续执行、read-time write、host/port值分叉。`test_update_status_store.py`对canonical/legacy/invalid schema/invalid JSON逐格拍摄目录字节快照，普通load后必须完全相同。
- L3：`test_f151_gateway_startup_fail_closed.py`真实subprocess：`S064-runtime-exit`证明env-present malformed runtime YAML仍为typed code/78且Uvicorn0；`S085-lifespan-startup`独立证明真实composition failure只经lifespan fail closed、readiness/request/workload=0、process nonzero。`test_f151_runtime_boundary_flow.py`验证descriptor→entry；clean-wheel在repo外cwd验证canonical entry、单次`create_app` static preflight、exit78、SIGTERM与结构readiness。
- exact negative fixtures：无关ASGI import允许；active生产旧argv拒绝；persisted legacy descriptor普通读取只能typed reject且0写；module entry若构造第二app/host、传Uvicorn import string、重复preflight或绕过`main.app=create_app()`则拒绝；`_resolve_front_door_mode`若env early-return、完整load次数≠1、吞static runtime异常或改变env>YAML precedence则拒绝。

## 6. Lifespan composition failure（非static exit78）

`create_app()`只承担同步可解析的static configuration与既有security exposure preflight。ProviderRouter、final LLM、stores、TaskRunner等真实runtime service assembly仍只在现有`lifespan -> OctoHarness.bootstrap`发生。此阶段失败时ASGI lifespan startup未完成，服务不得进入ready或接收业务请求；process以非zero startup failure结束，业务Task/Work/Event/backend调用均为0。F151不得为了把它改写成78而在module entry或`create_app()`提前构造Harness/runtime，也不得让readiness降级放行。

本清单不授权修改F150的loopback/bearer/trusted_proxy、Host/Origin/Access语义；其symbol保护见`f150-scope.md`。
