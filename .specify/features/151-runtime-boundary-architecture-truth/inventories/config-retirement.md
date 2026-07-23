# F151 配置退役与支持矩阵

## 1. Canonical 路径

- YAML：现有 `OctoAgentConfig.from_yaml()` 是唯一 schema 入口。
- Provider v1 字段：`auth_type`、`api_key_env`、`base_url` 继续由 `ProviderEntry` validator 归一化成 `auth`、`api_base`；F151 不删除此 Gateway schema compatibility。
- Secret：CredentialStore/auth profile 与 `.env` 是现役路径。
- `OCTOAGENT_LLM_MODE` 仍是 Echo/测试开关，不属于 Proxy tombstone。

## 2. 精确 tombstone 表

Tombstone 不是新实体；实现为 canonical bootstrap 中的常量与 validator。

| source | 完整 key / 匹配 | 检测阶段 | action / error |
|---|---|---|---|
| YAML raw dict | `runtime.llm_mode`（exact path） | `OctoAgentConfig.from_yaml()` 在 `model_validate` 前 | `ConfigParseError(field_path=...)`；启动映射 exit 78 / `RUNTIME_CONFIG_RETIRED` |
| YAML raw dict | `runtime.litellm_proxy_url` | 同上 | 同上 |
| YAML raw dict | `runtime.master_key_env` | 同上 | 同上 |
| filesystem | project/instance root 的精确文件名 `.env.litellm` | resolve project root 后、打开任何 dotenv 前；只做 `exists()`，不得读取 | exit 78 / `LEGACY_LITELLM_ENV_FILE_FOUND` |
| filesystem | project/instance root 的精确文件名 `litellm-config.yaml` | 同一 legacy-file preflight；只做 `exists()`，不得解析 | exit 78 / `LEGACY_LITELLM_CONFIG_FOUND` |
| loaded `.env` / process env | `LITELLM_PROXY_URL` | dotenv load 后、Harness/application 组装前；case-sensitive exact key，值为空也命中 | exit 78 / `RUNTIME_CONFIG_RETIRED` |
| loaded `.env` / process env | `LITELLM_PROXY_KEY` | 同上 | 同上 |
| loaded `.env` / process env | `LITELLM_MASTER_KEY` | 同上 | 同上 |
| loaded `.env` / process env | `LITELLM_PORT` | 同上 | 同上 |
| loaded `.env` / process env | `OCTOAGENT_WORKER_DOCKER_MODE` | 同上 | exit 78 / `RUNTIME_CONFIG_RETIRED` |
| loaded `.env` / process env | `OCTOAGENT_WORKER_DOCKER_INFO_CHECK` | 同上 | 同上 |
| loaded `.env` / process env | `OCTOAGENT_LLM_MODE` | **不是 tombstone**；strip/lower 后仅 `""/unset`（Provider direct）或 `echo`（测试）合法 | 其他值含 `litellm`：exit 78 / `LLM_MODE_INVALID`；`echo` 不得误杀 |

普通 application/start command 命中任一 legacy file 都 exit 78。只有恢复命令 `octo auth` 与 `octo setup` 可在 command dispatch 后、application assembly 前绕过该退出：它们必须显示旧文件存在警告、绝不打开旧文件、只写 CredentialStore/`.env`/canonical YAML，并要求用户完成后自行删除旧文件；其他 CLI 无豁免。

## 3. Retired surface action manifest

| surface | exact action |
|---|---|
| `octoagent/.env.litellm.example` | delete |
| `repo-scripts/worktree-shared-paths.txt` 两条旧文件共享项 | delete entries |
| `octoagent/.gitignore` 的 `!.env.litellm.example` | delete；`litellm-config.yaml` ignore 可作为防误提交安全规则保留 |
| `scripts/run-octo-home.sh` / `doctor-octo-home.sh` | delete source/read block；不得用旧迁移命令提示 |
| `packages/core/.../behavior_templates/TOOLS.md` | 改为 canonical auth/setup 写入，不再指旧文件 |
| CLI `octo config sync` | delete command、help、wiring与tests；它不生成宣称的文件 |
| CLI `octo config provider add/disable --activate` | delete no-op flag、activation callback/renderer/help与tests；不保留接受后无效的compat option |
| Gateway builtin `config.sync`、`ConfigSyncResult` | delete tool/manifest/result model/wiring/tests；不得保留 `status="written"` 假成功 |
| health `litellm_client` / `litellm_proxy` check | delete，改为 structural ProviderRoute readiness |
| control-plane `proxy_url` / `compose_file` / `runtime_activated` / `litellm_sync_ok` | delete失真字段与activation wiring |
| control-plane `litellm_env_names` | 单波重命名为 `provider_env_names`，不留双字段compatibility |
| empty `RuntimeConfig` class/root field | delete；raw阶段 exact retired keys按表拒绝，空`runtime: {}`只归一化移除，其他非空runtime keys以`RUNTIME_CONFIG_UNKNOWN` exit 78 |
| `octoagent/README.md` / `skills/llm-config/SKILL.md` | README改为canonical Provider直连；仅描述旧Proxy派生配置的skill整目录删除 |
| frontend Settings/Home/Workbench/App fixtures | 删除旧runtime/activation payload与展示，保留Provider配置/保存能力 |
| `octoagent/tests/AGENTS.md` worktree PYTHONPATH | SDK删除后移除`packages/sdk/src`分量；其余PYTHONPATH锁与调用纪律保持 |

必须清理的仓库现役输入面还包括：

- `octoagent/octoagent.yaml`
- `octoagent/octoagent.yaml.example`
- `octoagent/.env.example`
- `gateway/services/config/config_schema.py` 的 header/runtime 空块说明
- 迁移后的 `gateway/cli/behavior_commands.py`
- 前端 `domains/settings/SettingsPage.tsx`、`domains/settings/shared.tsx`、Home/Workbench runtime mode 展示及对应 tests
- `PendingChangesBar.tsx` 的保存payload路径、`HomePage.test.tsx`、`WorkbenchLayout.test.tsx` 与 `App.test.tsx` activation fixture

精确保留的安全 denylist（不是运行兼容）：

- `packages/tooling/.../path_policy.py` 对 `litellm-config.yaml` 的不可读规则；
- `gateway/services/workspace_git.py` 与 `octoagent/.gitignore` 对该旧生成物的不可提交规则；
- 对 `.env*` 的通用 secret path保护及其 tests。

retired-term gate 必须以 exact path + symbol + `security-denylist` purpose 允许这些规则，并有行为测试证明旧文件仍不可读/不可提交；不得为了词扫描删除安全保护。

## 4. `.env.litellm` 支持矩阵

| 场景 | 行为 | 验收 |
|---|---|---|
| 文件不存在 | 只加载 `.env`/CredentialStore，正常启动 | loader 行为测试 |
| 文件存在 | 只检测文件名，不打开、不 source、不 backup、不复制；exit 78 | spy 断言 `open/read_text/dotenv_values` 未调用 |
| 重新授权 | 旧文件存在时，`octo auth` / `octo setup` 仍可作为精确恢复命令启动；显示警告、绝不打开旧文件，只写 canonical store，随后用户自行删除旧文件 | command-dispatch + no-open spy + canonical write behavior |
| downgrade | F151 不生成旧文件、不回写旧 secret；降级只能使用用户/operator在F151外维护的filesystem snapshot或旧版本环境，不是Octo backup bundle | 文档断言 + Octo backup manifest 不含旧 secret |
| 仅旧文件里有必需 secret | 启动仍失败，不读取 secret；错误给出重新授权命令 | exit 78 + 日志不含文件内容 |

## 5. YAML schema 支持矩阵

| 输入 | 结果 |
|---|---|
| v1 Provider `auth_type/api_key_env/base_url` | Gateway validator 归一化并生成 canonical ProviderRoute |
| v2 Provider `auth/api_base/transport` | 直接归一化并生成同值 ProviderRoute |
| 新旧 Provider 字段冲突 | 继续沿用现有 Gateway precedence/validation；F151 不在 ProviderRouter 复制判断 |
| 任一退役 `runtime.*` key | raw dict 阶段拒绝，不能被空 RuntimeConfig 静默忽略 |
| 空 `runtime: {}` | raw阶段归一化移除；不保留RuntimeConfig runtime model |
| 其他非空 `runtime.*` | raw阶段拒绝，exit 78 / `RUNTIME_CONFIG_UNKNOWN`；不得由Pydantic静默忽略 |

## 6. 前端验收

- Tests first 更新 `SettingsPage.test.tsx`、`SettingsPage.phase079.test.tsx`、`HomePage.test.tsx`、`WorkbenchLayout.test.tsx`、`App.test.tsx`，覆盖 PendingChangesBar/draft/action/activation payload 不再包含三个 `runtime.*` key或Proxy字段。
- `frontend/src/domains/settings/shared.tsx` 不再提示旧文件，现役 secret 文案只指向 CredentialStore/`.env`。
- 运行定向 Vitest、全量 `npx vitest run` 与 `npm run typecheck`（若现有 script 名不同，以 `package.json` 现役 tsc script 为准）。
- 对 Gateway action body 做行为断言，不能以字符串扫描替代 raw-dict/tombstone 测试。
