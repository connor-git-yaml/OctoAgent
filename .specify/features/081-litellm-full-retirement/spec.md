# Feature 081 — LiteLLM 完全退役

> 作者：Connor
> 日期：2026-04-26
> 修订：2026-04-26（Codex 审查后 v2，5 Phase 化）
> 上游：Feature 080（Provider 直连，已完成 Phase 1-5a）
> 模式：spec-driver-feature

## 1. 背景

Feature 080 完成了 Provider 直连抽象 + Skill 主路径切线 + Memory embedding 路径接通。
LLM 主链路和 embedding 已完全脱离 LiteLLM Proxy。但仓库里 **LiteLLM 残留约 30 个文件**：

### 实测残留盘点（grep "litellm" 结果）

| 类别 | 文件数 | 关键文件 |
|------|-------|---------|
| 待删除（核心组件） | 6 | `litellm_client.py` / `providers.py` / `client.py` / `proxy_process_manager.py` / `litellm_generator.py` / `litellm_runtime.py` |
| 待清理（含 LiteLLM 引用） | 13 | `main.py` / `config_schema.py` / `setup_service.py` / `mcp_service.py` / `config_tools.py` / `health.py` / `builtin_memu_bridge.py`(fallback) / `orchestrator.py` / `capability_pack.py` / `runner.py` / `dx/onboarding_service.py` / `dx/doctor.py` / `dx/install_bootstrap.py` |
| DX/CLI（setup wizard） | 14 | `dx/cli.py` / `dx/onboarding_service.py` / `dx/doctor.py` / `dx/runtime_activation.py` / `dx/config_*.py` / `dx/__init__.py` |
| 脚本/运维 | 5 | `scripts/run-octo-home.sh` / `scripts/doctor-octo-home.sh` / `path_policy.py` / `runtime_activation.py` / `docker_daemon.py` |
| 凭证文件 | 1 | `~/.octoagent/.env.litellm`（运行时） |
| 前端 | 多 | Settings 页 runtime 字段 + provider 表单 |
| 测试 | ~10 | `test_f002_litellm_mode.py` / `test_f013_e2e_full.py` 等 |
| 文档 | 多 | `docs/blueprint/*` / `CLAUDE.md` / `docker-compose.litellm.yml` |

### 当前架构问题

- LiteLLM Proxy 启动逻辑仍在 `main.py` 跑（Phase 5a 没动）→ Gateway 启动慢
- `octoagent.yaml` 仍有 `runtime.llm_mode` / `litellm_proxy_url` / `master_key_env` 字段 → 配置 schema 拖累
- DX/CLI 流程（init wizard / doctor）仍假设 LiteLLM Proxy 存在 → 用户首次 setup 步骤多
- 前端 Settings 页仍有 LiteLLM 字段 → UX 噪音
- 旧测试仍假设 LiteLLM 存在 → CI 维护负担
- 部分代码（compactor / builtin_memu_bridge fallback）仍依赖 Proxy → 隐患
- **真正运行时 compaction 主线在 `gateway/services/context_compaction.py`，不在 `skills/compactor.py`**（Codex F5 发现）
- **`.env.litellm` 凭证文件仍被 `dotenv_loader.py` 启动加载，被 `setup_service.py` 写入**（Codex F3 发现）
- **运维脚本 `run-octo-home.sh` / `doctor-octo-home.sh` / `runtime_activation.py` 仍解析 `docker-compose.litellm.yml`**（Codex F4 发现）

## 2. 用户故事

- **US-1**（P0）：作为用户，**Gateway 启动 ≤ 5 秒**（无 LiteLLM Proxy 子进程等待 + 配置生成），不再需要等 LiteLLM 就绪
- **US-2**（P0）：作为用户，**首次 setup 流程不出现 LiteLLM 概念**，直接配 provider + alias 即可
- **US-3**（P0）：作为用户，**配置文件干净**：`octoagent.yaml` + `auth-profiles.json` 是仅有的两份，没有 `litellm-config*.yaml`
- **US-4**（P1）：作为用户，**前端 Settings 页只显示 provider / alias / memory 三大块**，不再有 runtime 字段
- **US-5**（P1）：作为开发者，**仓库 grep 不出 LiteLLM 引用**（除文档历史和迁移代码）
- **US-6**（P0）：作为用户，**老配置自动迁移**：跑 `octo config migrate-080` 一键升级 yaml + 凭证，不需要手动改 yaml 也不丢 API key
- **US-7**（P1）：作为运维者，**老的 home-instance 脚本（`run-octo-home.sh` / `doctor-octo-home.sh`）继续可用**，不会因 docker-compose.litellm.yml 删除而打断

## 3. 功能需求（FR）

### FR-1：删除 LiteLLM 核心组件（**最后一步执行**）

- `LiteLLMSkillClient` / `LiteLLMClient` / `ChatCompletionsProvider` / `ResponsesApiProvider` 整文件删除
- `ProxyProcessManager` / `litellm_generator` / `litellm_runtime` 整文件删除
- `docker-compose.litellm.yml` 删除
- **前置条件**：所有引用方都已经从这些文件解耦（FR-2 + FR-5 + FR-10 完成）

### FR-2：清理引用方（**P1 完成所有引用解耦，文件保留到 FR-1**）

- `main.py`：移除 LiteLLM Proxy 启动分支；echo mode 仍保留（fallback / 离线）
- `gateway/services/context_compaction.py`（**真正的运行时主线**）：用 `llm_service.call(alias, ...)` 链路替代 LiteLLM 直连，保留 compaction → summarizer → main 三级 fallback 语义；改造完成前 `skills/compactor.py` 仍保留作为兼容 shim
- `builtin_memu_bridge.py`：删除 LiteLLM Proxy fallback path
- `health.py`：移除 proxy 健康检查
- `orchestrator.py` / `capability_pack.py` / `runner.py` / `models.py`：清理 import 残留
- `dx/onboarding_service.py` / `dx/doctor.py` / `dx/install_bootstrap.py` / `dx/config_commands.py` / `dx/config_bootstrap.py` / `dx/__init__.py`：从 `litellm_generator` / `litellm_runtime` / `proxy_process_manager` import 解耦（改为新 schema 路径或删除调用）
- `gateway/services/control_plane/setup_service.py` / `mcp_service.py`：从 `litellm_generator` 解耦
- `gateway/services/builtin_tools/config_tools.py`：从 `litellm_generator` 解耦
- `provider/__init__.py`：移除 `LiteLLMClient` export
- `octoagent_sdk/_agent.py`：移除 `LiteLLMClient` 引用，统一走 ProviderClient

### FR-3：Schema 升级（**legacy-key 检测在 Pydantic 解析之前**）

- `RuntimeConfig.llm_mode` / `litellm_proxy_url` / `master_key_env` 标记 `deprecated=True` 但**保留可读**（运行时被忽略，仅供检测使用）
- `ProviderEntry` 新增 `transport` / `auth` 字段成为 first-class（不再依赖 Phase 1 fallback 推断）
- 提供 backward-compat 读取（旧字段 → 自动 map 到新字段）
- **legacy-key 检测在 raw YAML 层做**（在 `OctoAgentConfig.from_yaml` 解析之前），不依赖 Pydantic 字段访问；检测命中即抛 deprecation warning + 引导用户跑 migrate-080

### FR-4：Migration 命令（**双对象：yaml + 凭证**）

- `octo config migrate-080` CLI 命令
- 显式触发，启动时**仅**检测旧 schema 并 log warning 提示用户跑 migrate
- **yaml 迁移**：
  - 自动备份原 yaml → `.bak.080-{timestamp}`
  - 按 `provider.api_base` / `id` 推断 transport（与 Phase 1 router fallback 推断同源）
  - 写新 schema、`config_version: 2`
  - 失败时不破坏原文件
- **凭证迁移**（新增）：
  - 读取 `~/.octoagent/.env.litellm` 内的 provider API key（如 `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` 等）
  - 写到 `~/.octoagent/.env`（统一文件）+ 备份 `.env.litellm` → `.env.litellm.bak.080-{timestamp}`
  - 同步更新 `secret_store` / `auth-profiles.json`（如适用）
  - 迁移成功后保留 `.env.litellm.bak.*` 一份兜底
- `--dry-run`：只打印 diff，不写文件
- 命令幂等：重复跑不会再次触发迁移（检测 `config_version: 2` 即 skip）

### FR-5：DX/CLI 适配

- `init_wizard` / `setup_service` 走新 schema（providers[].transport + auth）
- `doctor` 不再检查 LiteLLM Proxy 状态，改成 ProviderRouter 可用性 + provider 三 transport 烟测
- `onboarding_service` 默认配置不含 LiteLLM 字段
- `config_commands` 提供 `transport set` / `auth set` 子命令
- `dx/__init__.py` 的延迟导入 stub（`importlib.import_module("octoagent.gateway.services.config.litellm_generator")` 等）改为 raise NotImplementedError 或返回空

### FR-6：前端清理

- Settings 页移除 `runtime.llm_mode` / `litellm_proxy_url` / `master_key_env` 输入字段
- ProviderSection 加 `transport` 选择器（openai_chat / openai_responses / anthropic_messages）+ `auth.kind` 切换（api_key + env / oauth + profile）
- 默认 provider preset 模板更新（`buildProviderPreset`）

### FR-7：测试更新

- 删除：`test_f002_litellm_mode.py` / `test_f013_e2e_full.py` 中纯 LiteLLM 测试
- 改写：依赖 LiteLLMClient 的单测改为依赖 ProviderClient
- 新增：migration 命令单测（含 yaml + 凭证两类） / e2e 三 transport 直连烟测 / context_compaction 主线 fallback 链测试

### FR-8：文档更新

- `docs/blueprint/*` 模型调用层章节
- `CLAUDE.md` 移除 LiteLLM Proxy 描述
- 新增 `docs/codebase-architecture/provider-direct-routing.md`
- 删除 `docker-compose.litellm.yml`

### FR-9：凭证文件迁移（新增，修 Codex F3）

- `dotenv_loader.py`：保留 `.env.litellm` 读取直到 P3 完成；P4 删除文件后自动 fallback 到 `.env`
- `setup_service.py:1482` 等写入路径：改写到 `.env`（不再产新 `.env.litellm`）
- 兼容窗口：从 P1 commit 到 P4 commit 之间，老 `.env.litellm` 仍被读取；P4 完成后只剩 `.env`
- 迁移工具：`migrate-080` 命令负责把 `.env.litellm` 内容合并到 `.env`

### FR-10：脚本与运维路径迁移（新增，修 Codex F4）

- `octoagent/scripts/run-octo-home.sh`：去除 `.env.litellm` 加载，改用 `.env` 或 `~/.octoagent/.env`
- `octoagent/scripts/doctor-octo-home.sh`：同上 + 改成检查 ProviderRouter ready 而非 LiteLLM Proxy
- `provider/dx/runtime_activation.py:66-120`：去除 `docker-compose.litellm.yml` 依赖；source root 通过其他方式解析（如新增 `OCTOAGENT_HOME` 环境变量或固定路径），不再启动 compose
- `tooling/path_policy.py:52-60`：从敏感路径列表里移除 LiteLLM 相关文件
- `provider/dx/docker_daemon.py`：删除（不再有需要 docker daemon 的场景），或精简为 docstring 标记 deprecated

### FR-11：context_compaction 主线改造（新增，修 Codex F5）

- 当前真正生效的 compaction 路径在 `gateway/services/context_compaction.py:951-1028`，通过 `llm_service.call()` 调用
- 改造目标：保留 `llm_service.call(alias, ...)` 入口，但确保底层走 ProviderRouter（Feature 080 已切线）
- 保留三级 fallback：compaction-alias → summarizer-alias → main-alias，缺失任一时降级返回空摘要
- `skills/compactor.py` 在 P1-P3 期间作为兼容 shim 保留；P4 删除（此时已无引用）
- 验收：context compaction 在 alias 缺失或凭证异常时**不抛出异常**，仅降级返回空摘要 + log warning

## 4. 不变量

- **I-1**：`auth-profiles.json` schema 不变
- **I-2**：`PkceOAuthAdapter` / OAuth flow 不变
- **I-3**：所有 EventType 枚举（OAUTH_*, MODEL_CALL_*）不变
- **I-4**：Skill / Tool / SkillRunner 接口不变
- **I-5**：CLI 顶层命令名（`octo run` / `octo config provider list`）不变
- **I-6**：用户的现有 octoagent.yaml 在跑 migrate-080 之前**仍能工作**（旧字段 backward-compat 读取）
- **I-7**（新）：用户的现有 `.env.litellm` 在跑 migrate-080 之前**仍能被读取**（凭证不丢）
- **I-8**（新）：每个 Phase commit 后 CLI / Gateway / SDK 都**可 import 可启动**（不允许中间不可用版本）

## 5. Scope Lock

- ❌ 不引入新的 transport（bedrock / vertex / google_gemini 留给后续 Feature）
- ❌ 不重构 SkillRunner / EventStore / Tool Broker
- ❌ 不动 OAuth refresh / TokenRefreshCoordinator 任何逻辑
- ❌ 不在本 Feature 内做 cost calculator 重构
- ❌ 不引入凭证池轮换（Hermes 风格，等真有需要再加）
- ❌ 不修改 `gateway/services/context_compaction.py` 的对外 API（`llm_service.call()` 调用方式不变，只确保底层走 ProviderRouter）

## 6. 风险

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| Migration 把用户 yaml 弄坏 | 中 | Gateway 启动失败 | 必须备份；migrate 命令支持 --dry-run；失败回滚到 .bak |
| 删除 LiteLLMClient 后某遗漏调用方挂掉 | 中 | 部分功能失效 | grep 全代码库 + integration 测试覆盖；P0 Phase 做完整盘点；P1 完成所有引用解耦才进 P4 删除 |
| 前端 Settings 字段变化破坏现有 setup 流程 | 中 | 用户首次 setup 失败 | 前端配套更新；提供"恢复推荐配置"按钮 |
| 老用户 yaml 还有 `runtime.llm_mode` 字段，新读者抛错 | 高 | 启动失败 | RuntimeConfig 改为 deprecated（保留可读但忽略） |
| compactor 切到新链路后 compaction 行为差异 | 低 | summarization 质量变化 | 当前 compaction threshold 默认 1.0 不触发，改造时同步更新单测 |
| **凭证文件 .env.litellm 删除后老用户 API key 丢失** | 高 | 老用户 API 调用失败 | FR-9 显式迁移；保留兼容读取窗口直到 P4 |
| **运维脚本 run-octo-home.sh 引用被删的 docker-compose.litellm.yml** | 高 | 老 home-instance 脚本启动失败 | FR-10 提前迁移脚本；P3 完成所有运维路径解耦 |
| **改了非主线 skills/compactor.py，主线 context_compaction.py 没动** | 中 | 主线 compaction 仍走旧路径 | FR-11 显式以 context_compaction.py 为目标 |
| **legacy schema 检测发生在 Pydantic 之后，旧字段被吞** | 高 | 用户既无 warning 也不知要 migrate | FR-3 检测前置到 raw YAML 层 |
| **P1 commit 后 setup/doctor/CLI/SDK import 失败** | 高 | 中间版本不可用 | I-8 + Phase 重排：P1 只做引用解耦不删文件 |

## 7. 验收准则

### 功能（必须通过）

- [ ] Gateway 启动 ≤ 5 秒（无 LiteLLM 子进程）
- [ ] `~/.octoagent/litellm-config*.yaml` 不再被代码读取
- [ ] `~/.octoagent/.env.litellm` 不再被代码读取（P4 后）
- [ ] 前端 Settings 页无 `runtime.llm_mode` / `litellm_proxy_url` 输入
- [ ] `octo config migrate-080` 命令能把旧 yaml + `.env.litellm` 升级到新 schema + `.env`
- [ ] 老 yaml（含 runtime 字段）启动时给 deprecation 提示，不直接挂
- [ ] 老 `.env.litellm` 在迁移完成前仍被读取（不丢凭证）
- [ ] `gateway/services/context_compaction.py` 主线在 alias 缺失时降级返回空摘要

### 架构（必须通过）

- [ ] LiteLLM Proxy 进程不再启动
- [ ] `ProxyProcessManager` 类不存在（P4 后）
- [ ] `litellm_generator.py` / `litellm_runtime.py` / `proxy_process_manager.py` 文件不存在（P4 后）
- [ ] `LiteLLMClient` / `LiteLLMSkillClient` / `ChatCompletionsProvider` / `ResponsesApiProvider` 类不存在（P4 后）
- [ ] LLM 调用栈深度 ≤ 2 层（Skill → ProviderClient）
- [ ] `grep -r litellm octoagent/ --include='*.py' | grep -v __pycache__ | grep -v migration` 返回 ≤ 3 行（仅迁移代码 / docstring 历史引用）

### 兼容性（必须通过）

- [ ] 现有用户跑 `octo config migrate-080` → 自动升级 + 备份原 yaml + 凭证迁移
- [ ] 升级前的 yaml 启动 Gateway → 看到 deprecation warning，但能正常工作
- [ ] CLI 命令 `octo config provider list` / `octo config alias set` 等照常工作
- [ ] Feature 078 / 079 / 080 现有测试（约 110 + 42 = 152 条）继续通过
- [ ] **每个 Phase commit 完之后，`python -c "from octoagent.gateway.main import app"` 不抛 import 错误**
- [ ] **每个 Phase commit 完之后，`octo --help` / `octo config --help` 正常输出**
- [ ] 老 home-instance 脚本（`run-octo-home.sh` / `doctor-octo-home.sh`）在 P3 后仍可启动 Gateway

### 文档（应该通过）

- [ ] `docs/blueprint/*` 更新
- [ ] `CLAUDE.md` 不再提 LiteLLM Proxy
- [ ] 新增 `docs/codebase-architecture/provider-direct-routing.md`
- [ ] `docker-compose.litellm.yml` 删除
