# F151 / F150 精确实现范围

本清单先冻结F151受权的“唯一受支持module entry在Uvicorn前把既有config/security exposure异常映射为exit 78”，再为F150桌面Web Cloudflare Access合同声明唯一可演进symbol universe。手机产品只走原生iOS App；本清单不授权mobile browser/WebView、iOS route、第二session/device体系或App内service token。无关F151 production文件由各自inventory管理，不能被本清单误拒绝。

D-03导致的`main.py` import path机械重定位由`namespace-migration.md`独立授权并以AST import等价验证，不扩大本清单的F150 semantic allowlist。

稳定基线commit为`687f20fc6246e7157957ab51ac474d46e91578b6`。该基线只有`OctoAgentConfig.front_door`与`FrontDoorConfig`；`cloudflared` mode、manifest path与owner email尚未实现。F150 T001只安装下面的path+symbol权限，不实现产品行为；后续每项行为仍须独立RED→GREEN→REFACTOR。Gate从调用方提供的base ref读取baseline，不能用旧hash覆盖当前真值。

## Allowed change manifest

| path | symbol / AST subtree | 唯一允许变化 |
|---|---|---|
| `octoagent/apps/gateway/src/octoagent/gateway/__main__.py` | 新symbol `GatewayStartOptions`、`parse_start_options`、`main` | 先唯一解析`--help/--host/--port`，unknown/重复exit64；随后在typed exception boundary内只import一次`main.app`，把既有`create_app()`传播的startup error映射exit78，并把app instance与exact-equal host/port及`proxy_headers=False`交Uvicorn，确保front-door读取真实TCP peer；不得新增preflight factory、global snapshot、第二app/host/runtime |
| `octoagent/apps/gateway/src/octoagent/gateway/main.py` | `_resolve_front_door_mode`精确control-flow + 唯一`ExceptHandler`；baseline lines207-227；baseline normalized AST hash由gate从base重算 | 在读取/apply env mode之前无条件调用完整`load_config(project_root)`恰一次；有效配置下仍为env mode > YAML mode > loopback。删除warning+`loopback`回退，按`ConfigParseError.field_path`把`front_door|security`传播为typed security config error、`runtime`/retired/unknown/root application config传播为typed runtime config error；不开放其他分支、返回值或side effect |
| `octoagent/apps/gateway/src/octoagent/gateway/main.py` | `_enforce_front_door_exposure`唯一`ExceptHandler`；baseline lines276-281；AST SHA-256 `b144c0a74fe1000342703eebaaf3702ba6001cf14938aef4e75b9bde06261ab4` | 删除校验异常warning+return的fail-open，改为传播typed security exposure error；safe/warn/reject verdict分支保持不变 |
| `octoagent/apps/gateway/src/octoagent/gateway/main.py` | 最小typed-error import | 仅为上述两个handler传播/映射所需；不得加入新的判定规则 |

`_build_runtime_alias_registry`虽也有config fallback，但module-entry canonical preflight会使非法启动配置在此前终止；它不在本allowlist，F151不得借机修改。真正runtime service composition不在此同步边界：它仍由唯一lifespan/OctoHarness composition root fail closed，不得为exit78新增第二assembly/preflight。

## F150 Web Access exact authority

| path | 唯一允许变化的symbol/export | 保持不变的边界 |
|---|---|---|
| `services/config/config_schema.py` | `FrontDoorConfig.mode`、`cloudflare_manifest_path`、`cloudflare_owner_email`、两个字段normalize validator及既有`validate_mode_requirements`的`cloudflared`分支 | class内其他字段/validator/method必须与baseline AST相等 |
| `services/frontdoor_auth.py` | `FrontDoorGuard.__init__`、`authenticate`、`authenticate_cloudflare_access` | class内其他policy/status/credential分支必须与baseline AST相等 |
| `services/frontdoor_exposure.py` | `validate_front_door_exposure` | module sibling不变；禁止`0.0.0.0`、TrustedHost/CORS |
| `deps.py` | `get_front_door_guard`、`_validate_request_front_door_config`、`require_front_door_access` | cloudflared复用startup typed对象时不得request-time reparse；不增加第二guard factory/dependency |
| `services/operations/doctor.py` | `DoctorRunner.check_cloudflare_web_access` | 既有doctor checks不变 |
| `routes/control_plane.py` | `remote_access_status` | 既有route不变；禁止iOS/mobile route |
| `services/cloudflare_web_access.py` | `CloudflareWebAccessManifest`、`CloudflarePrincipal`、`load_cloudflare_web_access_manifest`、`classify_cloudflare_request`、`verify_cloudflare_access_jwt`、`validate_cloudflare_mutation` | 唯一新cohesive module；每个实施阶段的top-level public symbol必须为此集合的非空子集，最终必须精确等于此集合；imports与private helper不计public API |
| `harness/octo_harness.py` | `OctoHarness._bootstrap_paths` | 唯一lifespan composition root只加载一次canonical config/manifest并构造一个verifier与一个`FrontDoorGuard`；同一对象写入`app.state`供REST/SSE/reconnect/status共用；禁止修改sibling bootstrap、启动顺序、创建第二parser/guard/app或lazy request-time composition |
| `frontend/src/api/remote-access.ts` | `readRemoteAccessStatus` | 只提供F149可消费的唯一API seam，不复制transport/state |
| `frontend/src/domains/settings/RemoteAccessSettings.tsx` | `RemoteAccessSettings` | 只提供最小Settings入口；F149仍拥有Claude Design基线上的最终Web composition与页面状态 |

上述Frontend入口仍是桌面Web产品。390px只表示Web窄窗口健壮性，不表示手机产品；原生iOS由独立Feature以SwiftUI/Apple原生导航、手势、控件与无障碍语义实现，但保持Claude Design最初方案的视觉语言。

## `_resolve_front_door_mode` exact AST形状

### Baseline（只用于gate定位）

1. `env_mode = os.environ.get(...).strip()`；
2. `if env_mode: return env_mode`；
3. `try: cfg = load_config(project_root)`；
4. `except Exception: warning + return "loopback"`；
5. `cfg is None -> loopback`，否则返回`cfg.front_door.mode`。

### 唯一批准的final形状

1. 函数首个配置动作是`try: cfg = load_config(project_root)`，静态路径调用数恰1；
2. 唯一handler只做typed分类后`raise ... from exc`，不得log-and-continue/return/default；
3. handler之后才读取`OCTOAGENT_FRONTDOOR_MODE`；非空仍原样覆盖YAML mode；
4. env为空时`cfg is None -> loopback`，否则返回同一个`cfg.front_door.mode`；
5. 禁止再次load、缓存global snapshot、创建app/Harness/runtime、读取第二配置路径或改变mode值域。

Gate对normalized AST执行shape predicate，而不是用新hash整体放行。negative fixtures必须拒绝：保留env early-return、两次load、load后吞异常、改变env>YAML precedence、修改sibling return/Host/Origin/Access逻辑；env-present + malformed/retired runtime YAML的L3 subprocess必须仍以`GATEWAY_RUNTIME_CONFIG_INVALID`/78结束且Uvicorn/Task/Work/Event/backend调用0。

## Protected symbols（normalized AST hash必须相对baseline不变）

| path | protected qualname | baseline normalized AST sha256 prefix | 保护事实 |
|---|---|---|---|
| `services/config/config_schema.py` | `FrontDoorConfig` | `81dc4a513df0caa1cf0a7121154d25da33b5452a82008d9d62cd6746ba04df95` | mode值域与配置模型 |
| `services/frontdoor_auth.py` | `FrontDoorGuard` | `4ace0cd136e638300a7316889b343d7752d81b093f6d99a0f94b864786da3c32` | loopback/bearer/trusted_proxy认证判定 |
| `services/frontdoor_exposure.py` | `validate_front_door_exposure` | `58e3b230b62dfd7663f0b3daf3077a46ed5ff32711251a4ce641ee86442a10b0` | 现有host↔mode safe/warn/reject矩阵 |
| `main.py` | `_resolve_startup_host` | `51fded27f59638064da456fefeb57c79b47453b15e9858bdaca58c2ad53ad8b8` | argv/env host precedence |
| `main.py` | `create_app` | `d63f5c9f1b68c2511faab39c7791c11ae613b495fb9c4a4a403b29a5cecd1443` | 单Gateway application host；仅其他F151 manifest精确授权的非F150机械import可另行登记 |
| `deps.py` | `get_front_door_guard` | `09c5611f4cea1af53f8ad9273a09bc54a12bfbb5cc456ca1eae768bd1a9368d1` | request guard composition |
| `deps.py` | `require_front_door_access` | `d0c877a9b651718138962be98771396b3590311be495776bf0b12e233c35d093` | request access dependency |

normalized AST定义为`ast.dump(symbol_node, annotate_fields=True, include_attributes=False)`的UTF-8 SHA-256。实施时gate从merge-base重新计算完整hash；任何baseline drift先回Gate，不以更新本表静默放行。

## Semantic forbidden additions

- 不删除或改名`loopback|bearer|trusted_proxy`；只允许在`FrontDoorConfig.mode`增加`cloudflared`。
- 只能在上表owned symbol内实现F150；同class/module sibling必须与baseline AST相等。
- 不新增第二manifest parser、guard、state registry、device/session表或F149页面状态机。
- 不新增mobile/iOS route、手机浏览器/WebView入口、Access Bypass或App service token。
- 不新增TrustedHost/CORS、Access-Control或`0.0.0.0`公网bind；Host/Origin只属于批准的mutation validator。
- 不把startup exit78映射复用为request 503，反之亦然。
- 不把现有Web UI当视觉基线；Web/iOS都以Claude Design最初方案为视觉/交互基线，F150不得为实现方便改变F149排版。

## Gate algorithm

1. 从显式base ref抽取protected qualname的normalized AST并比较完整hash。
2. 对`_resolve_front_door_mode`比较上述before/after AST shape，只允许无条件完整load的order、typed classification/propagation、env precedence保持所需的精确control-flow；对`_enforce_front_door_exposure`仍只允许handler subtree与必要import变化。任何sibling change失败。
3. 对F150-owned universe分类diff：既有7个protected symbols、2个handler、module-entry，以及上表10个Web Access path。owned class/module的非owned sibling、new module额外top-level symbol、Frontend额外export均失败。
4. `main.py`的D-03 import-only relocation由namespace manifest验证“import target变、函数body AST不变”；它不是F150 semantic allowlist。body变化失败。
5. 对F150-owned当前字节做semantic token scan；出现Bypass、service token、iOS/mobile route、device/session registry、`0.0.0.0`、TrustedHost/CORS/Access-Control即失败并要求scope review。
6. negative/positive fixtures必须证明：无关F151 file允许；approved Web Access symbol集合接受且validator零写；同文件config/guard sibling、第二parser/guard/state registry、public bind、mobile route、Bypass、service token、device/session表和F149页面状态机逐项拒绝且输入有observable delta。
7. D-03 import-only与F151 T042/T064 exact形状继续由既有分支验证，不因F150 Web Access权限放宽。
8. T064的L3 subprocess nodes证明`main.app=create_app()`仍是唯一构造与canonical static preflight、完整config resolution/exposure validation各恰一次；static security/runtime typed error在Uvicorn前exit78，resolved host/port与Uvicorn exact equality，并要求`proxy_headers=False`防止转发头改写ASGI peer。T085另证lifespan composition failure，不把它伪装成exit78。T070只断言“F150-owned scope内未授权semantic diff=0”，不得写全仓production allowlist外diff=0。
