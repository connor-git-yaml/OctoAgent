# Feature 081 — LiteLLM 完全退役

> 作者：Connor
> 日期：2026-04-26
> 上游：Feature 080（Provider 直连，已完成 Phase 1-5a）
> 模式：spec-driver-feature

## 1. 背景

Feature 080 完成了 Provider 直连抽象 + Skill 主路径切线 + Memory embedding 路径接通。
LLM 主链路和 embedding 已完全脱离 LiteLLM Proxy。但仓库里 **LiteLLM 残留约 30 个文件**：

### 实测残留盘点（grep "litellm" 结果）

| 类别 | 文件数 | 关键文件 |
|------|-------|---------|
| 待删除（核心组件） | 6 | `litellm_client.py` / `providers.py` / `client.py` / `proxy_process_manager.py` / `litellm_generator.py` / `litellm_runtime.py` |
| 待清理（含 LiteLLM 引用） | 8 | `main.py` / `config_schema.py` / `compactor.py` / `health.py` / `builtin_memu_bridge.py`(fallback) / `orchestrator.py` / `capability_pack.py` / `runner.py` |
| DX/CLI（setup wizard） | 14 | `dx/cli.py` / `dx/onboarding_service.py` / `dx/doctor.py` / `dx/runtime_activation.py` / `dx/config_*.py` 等 |
| 前端 | 多 | Settings 页 runtime 字段 + provider 表单 |
| 测试 | ~10 | `test_f002_litellm_mode.py` / `test_f013_e2e_full.py` 等 |
| 文档 | 多 | `docs/blueprint/*` / `CLAUDE.md` / `docker-compose.litellm.yml` |

### 当前架构问题

- LiteLLM Proxy 启动逻辑仍在 main.py 跑（Phase 5a 没动）→ Gateway 启动慢
- `octoagent.yaml` 仍有 `runtime.llm_mode` / `litellm_proxy_url` / `master_key_env` 字段 → 配置 schema 拖累
- DX/CLI 流程（init wizard / doctor）仍假设 LiteLLM Proxy 存在 → 用户首次 setup 步骤多
- 前端 Settings 页仍有 LiteLLM 字段 → UX 噪音
- 旧测试仍假设 LiteLLM 存在 → CI 维护负担
- 部分代码（compactor / builtin_memu_bridge fallback）仍依赖 Proxy → 隐患

## 2. 用户故事

- **US-1**（P0）：作为用户，**Gateway 启动 ≤ 5 秒**（无 LiteLLM Proxy 子进程等待 + 配置生成），不再需要等 LiteLLM 就绪
- **US-2**（P0）：作为用户，**首次 setup 流程不出现 LiteLLM 概念**，直接配 provider + alias 即可
- **US-3**（P0）：作为用户，**配置文件干净**：`octoagent.yaml` + `auth-profiles.json` 是仅有的两份，没有 `litellm-config*.yaml`
- **US-4**（P1）：作为用户，**前端 Settings 页只显示 provider / alias / memory 三大块**，不再有 runtime 字段
- **US-5**（P1）：作为开发者，**仓库 grep 不出 LiteLLM 引用**（除文档历史和迁移代码）
- **US-6**（P1）：作为用户，**老配置自动迁移**：跑 `octo config migrate-080` 一键升级，不需要手动改 yaml

## 3. 功能需求（FR）

### FR-1：删除 LiteLLM 核心组件
- `LiteLLMSkillClient` / `LiteLLMClient` / `ChatCompletionsProvider` / `ResponsesApiProvider` 整文件删除
- `ProxyProcessManager` / `litellm_generator` / `litellm_runtime` 整文件删除
- `docker-compose.litellm.yml` 删除

### FR-2：清理引用方
- `main.py`：移除 LiteLLM Proxy 启动分支；echo mode 仍保留（fallback / 离线）
- `compactor.py`：改用 ProviderRouter 调 cheap alias 做 compaction
- `builtin_memu_bridge.py`：删除 LiteLLM Proxy fallback path
- `health.py`：移除 proxy 健康检查
- `orchestrator.py` / `capability_pack.py` / `runner.py` / `models.py`：清理 import 残留

### FR-3：Schema 升级
- `RuntimeConfig` 删除 `llm_mode` / `litellm_proxy_url` / `master_key_env` 字段
- `ProviderEntry` 新增 `transport` / `auth` 字段成为 first-class（不再依赖 Phase 1 fallback 推断）
- 提供 backward-compat 读取（旧字段 → 自动 map 到新字段）

### FR-4：Migration 命令（修 Codex F5）
- `octo config migrate-080` CLI 命令
- 显式触发，启动时**仅**检测旧 schema 并 log warning 提示用户跑 migrate
- 自动备份原 yaml → `.bak.080-{timestamp}`
- 按 `provider.api_base` / `id` 推断 transport（与 Phase 1 router fallback 推断同源）
- 写新 schema、`config_version: 2`
- 失败时不破坏原文件

### FR-5：DX/CLI 适配
- `init_wizard` / `setup_service` 走新 schema（providers[].transport + auth）
- `doctor` 不再检查 LiteLLM Proxy 状态
- `onboarding_service` 默认配置不含 LiteLLM 字段
- `config_commands` 提供 `transport set / auth set` 子命令

### FR-6：前端清理
- Settings 页移除 `runtime.llm_mode` / `litellm_proxy_url` / `master_key_env` 输入字段
- ProviderSection 加 `transport` 选择器（openai_chat / openai_responses / anthropic_messages）+ `auth.kind` 切换（api_key + env / oauth + profile）
- 默认 provider preset 模板更新（`buildProviderPreset`）

### FR-7：测试更新
- 删除：`test_f002_litellm_mode.py` / `test_f013_e2e_full.py` 中纯 LiteLLM 测试
- 改写：依赖 LiteLLMClient 的单测改为依赖 ProviderClient
- 新增：migration 命令单测 / e2e 三 transport 直连烟测

### FR-8：文档更新
- `docs/blueprint/*` 模型调用层章节
- `CLAUDE.md` 移除 LiteLLM Proxy 描述
- 新增 `docs/codebase-architecture/provider-direct-routing.md`
- 删除 `docker-compose.litellm.yml`

## 4. 不变量

- **I-1**：`auth-profiles.json` schema 不变
- **I-2**：`PkceOAuthAdapter` / OAuth flow 不变
- **I-3**：所有 EventType 枚举（OAUTH_*, MODEL_CALL_*）不变
- **I-4**：Skill / Tool / SkillRunner 接口不变
- **I-5**：CLI 顶层命令名（`octo run` / `octo config provider list`）不变
- **I-6**：用户的现有 octoagent.yaml 在跑 migrate-080 之前**仍能工作**（旧字段 backward-compat 读取）

## 5. Scope Lock

- ❌ 不引入新的 transport（bedrock / vertex / google_gemini 留给后续 Feature）
- ❌ 不重构 SkillRunner / EventStore / Tool Broker
- ❌ 不动 OAuth refresh / TokenRefreshCoordinator 任何逻辑
- ❌ 不在本 Feature 内做 cost calculator 重构
- ❌ 不引入凭证池轮换（Hermes 风格，等真有需要再加）

## 6. 风险

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| Migration 把用户 yaml 弄坏 | 中 | Gateway 启动失败 | 必须备份；migrate 命令支持 --dry-run；失败回滚到 .bak |
| 删除 LiteLLMClient 后某遗漏调用方挂掉 | 中 | 部分功能失效 | grep 全代码库 + integration 测试覆盖 |
| 前端 Settings 字段变化破坏现有 setup 流程 | 中 | 用户首次 setup 失败 | 前端配套更新；提供"恢复推荐配置"按钮 |
| 老用户 yaml 还有 `runtime.llm_mode` 字段，新读者抛错 | 高 | 启动失败 | RuntimeConfig 改为 deprecated（保留可读但忽略） |
| compactor 切到新链路后 compaction 行为差异 | 低 | summarization 质量变化 | 当前 compaction threshold 默认 1.0 不触发，改造时同步更新单测 |

## 7. 验收准则

### 功能（必须通过）

- [ ] Gateway 启动 ≤ 5 秒（无 LiteLLM 子进程）
- [ ] `~/.octoagent/litellm-config*.yaml` 不再被代码读取
- [ ] `~/.octoagent/.env.litellm` 不再被代码读取
- [ ] 前端 Settings 页无 `runtime.llm_mode` / `litellm_proxy_url` 输入
- [ ] `octo config migrate-080` 命令能把旧 yaml 升级到新 schema
- [ ] 老 yaml（含 runtime 字段）启动时给 deprecation 提示，不直接挂

### 架构（必须通过）

- [ ] LiteLLM Proxy 进程不再启动
- [ ] `ProxyProcessManager` 类不存在
- [ ] `litellm_generator.py` / `litellm_runtime.py` / `proxy_process_manager.py` 文件不存在
- [ ] `LiteLLMClient` / `LiteLLMSkillClient` / `ChatCompletionsProvider` / `ResponsesApiProvider` 类不存在
- [ ] LLM 调用栈深度 ≤ 2 层（Skill → ProviderClient）
- [ ] `grep -r litellm octoagent/ --include='*.py' | grep -v __pycache__ | grep -v migration` 返回 ≤ 3 行（仅迁移代码 / docstring 历史引用）

### 兼容性（必须通过）

- [ ] 现有用户跑 `octo config migrate-080` → 自动升级 + 备份原 yaml
- [ ] 升级前的 yaml 启动 Gateway → 看到 deprecation warning，但能正常工作
- [ ] CLI 命令 `octo config provider list` / `octo config alias set` 等照常工作
- [ ] Feature 078 / 079 / 080 现有测试（约 110 + 42 = 152 条）继续通过

### 文档（应该通过）

- [ ] `docs/blueprint/*` 更新
- [ ] `CLAUDE.md` 不再提 LiteLLM Proxy
- [ ] 新增 `docs/codebase-architecture/provider-direct-routing.md`
- [ ] `docker-compose.litellm.yml` 删除
