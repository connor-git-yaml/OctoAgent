# Feature 081 — LiteLLM 完全退役 · 实施计划

> 作者：Connor
> 日期：2026-04-26
> 修订：2026-04-26（Codex 审查后 v2，5 Phase 化）
> 上游：spec.md
> 下游：tasks.md
> 模式：spec-driver-feature

---

## 0. 总览

```
当前 (Feature 080 完成后):
┌─ 主路径 (Skill / Memory) ─────────┐    ┌─ Proxy 残留 ──────────┐
│ 已切到 ProviderRouter 直连         │    │ 仍在启动但无人用        │
│ ✓ LLM 调用                        │    │ ✗ ProxyProcessManager  │
│ ✓ Memory embedding                │    │ ✗ litellm-config*.yaml │
│ ✓ OAuth refresh / 401&403         │    │ ✗ docker-compose.yml   │
└────────────────────────────────────┘    └────────────────────────┘
                ↓ Feature 081 (5 Phase)
┌─ 主路径 (Skill / Memory) ─────────┐
│ ProviderRouter 直连（与现状一致）  │
│ + 配置 schema 干净                 │
│ + DX/CLI 不再提 LiteLLM            │
│ + 前端 Settings 简洁               │
│ + 文档同步                         │
│ + 凭证 .env.litellm → .env         │
│ + 运维脚本不再依赖 docker-compose  │
│ + LiteLLM 文件全部删除             │
└────────────────────────────────────┘
```

**核心方法**：分 5 Phase 渐进退役，每个 Phase 独立 commit + 可回滚。
**核心原则**（修 Codex F1）：**先解耦所有引用方，再删文件**。任何 Phase commit 后都不允许出现 import 失败。

---

## 1. Phase 划分（5 Phase）

| Phase | 内容 | 估时 | 风险 | 删除文件? |
|-------|------|------|------|----------|
| **P0 依赖盘点 + 兼容 shim** | 全量 grep 引用 + 写迁移清单 + 加兼容 shim 准备 | 0.5 天 | 极低（只读不删） | ❌ |
| **P1 主线切线 + 调用方迁移** | main.py / context_compaction.py / setup_service / mcp_service / dx/* / sdk/* 全部从 LiteLLM 文件解耦；compactor & memory bridge fallback 清理；**保留兼容 shim** | 1 天 | 中 | ❌ |
| **P2 Schema + 双对象 Migration** | RuntimeConfig 字段标 deprecated（保留可读）+ ProviderEntry 加 transport/auth + raw YAML legacy-key 检测 + `octo config migrate-080`（yaml + 凭证） | 1 天 | 中（schema + 凭证迁移） | ❌ |
| **P3 DX/CLI + 前端 + 脚本运维** | setup wizard / doctor / config_commands 适配；前端 Settings；run-octo-home.sh / doctor-octo-home.sh / runtime_activation.py / path_policy.py 解耦 docker-compose | 1 天 | 中（脚本 + UX） | ❌ |
| **P4 实际删除 + 文档 + 验收** | 在所有引用都干净后才 `git rm` 6 个核心文件 + docker-compose.litellm.yml + 文档更新 + 全量验收 | 0.5 天 | 低（已无引用） | ✅ |

**总计 ~4 天 / 5 个独立 commit**

**核心约束**：
- 每个 Phase 完成后 `python -c "from octoagent.gateway.main import app"` 必须成功
- 每个 Phase 完成后 `octo --help` / `octo config --help` 必须正常
- Feature 078 / 079 / 080 的 152 条测试必须仍然通过

---

## 2. Phase 0 — 依赖盘点 + 兼容 shim 准备

### 2.1 全量盘点（产出 `migration-inventory.md`）

```bash
# 在 P0 commit 中加入文件 .specify/features/081-litellm-full-retirement/migration-inventory.md
# 内容是下面三类清单 + 处理策略
```

**类别 A：Python import 引用**（13 个文件，需在 P1 解耦）：

| 文件 | LiteLLM 依赖 | P1 处理策略 |
|------|-------------|-------------|
| `gateway/main.py` | `LiteLLMClient` / `LiteLLMSkillClient` / `litellm_runtime.*` / `ProxyProcessManager` | 全部移除；保留 echo mode；router 路径已就位 |
| `gateway/services/control_plane/setup_service.py:57` | `litellm_generator` | 改成调用新 schema 写入；保留兼容 shim 至 P4 |
| `gateway/services/control_plane/mcp_service.py:38` | `generate_litellm_config` | 删除调用（mcp 不需要 LiteLLM 配置） |
| `gateway/services/builtin_tools/config_tools.py:248` | `generate_litellm_config` | 删除该工具的 LiteLLM 子分支 |
| `gateway/services/orchestrator.py` | docstring 引用 | 改 docstring |
| `gateway/services/capability_pack.py` | 注释引用 | 改注释 |
| `gateway/services/memory/builtin_memu_bridge.py` | LiteLLM Proxy fallback | 删除 fallback 分支，仅保留 router |
| `gateway/routes/health.py` | proxy_manager 健康检查 | 删除该 endpoint 分支 |
| `provider/__init__.py:37,84` | export `LiteLLMClient` | 移除 export，但**文件保留到 P4**（避免 __init__.py 期间断裂） |
| `provider/dx/__init__.py:42,50,109` | 延迟导入 stub | 改成 raise NotImplementedError 或返回空 dict |
| `provider/dx/onboarding_service.py:20` | `check_litellm_sync_status` | 删除调用 |
| `provider/dx/doctor.py:21,632` | `alias_uses_codex_backend` / `check_litellm_sync_status` | 改成 ProviderRouter 检查 |
| `provider/dx/install_bootstrap.py:16` | `generate_litellm_config` | 删除 LiteLLM 安装路径 |
| `provider/dx/config_commands.py:45,536,852` | `generate_litellm_config` 等 | 改成新 schema 写入；保留兼容到 P4 |
| `provider/dx/config_bootstrap.py:27` | `generate_litellm_config` | 同上 |
| `provider/dx/runtime_activation.py:66-120` | `docker-compose.litellm.yml` 解析 | P3 解耦（不再启动 compose） |
| `provider/dx/docker_daemon.py:3` | docstring 引用 | P3 改注释或删整文件 |
| `skills/runner.py` | import 残留 | 移除 import |
| `skills/compactor.py` | 整文件作为兼容 shim 保留至 P4 | 不动；主线在 `context_compaction.py` |
| `skills/__init__.py` | export 已删类 | 移除 export（但类文件保留到 P4） |
| `octoagent_sdk/_agent.py:287,289` | 优先使用 LiteLLMClient 分支 | 改成只走 ProviderClient |

**类别 B：脚本与运维路径**（5 项，P3 处理）：

| 文件 | 依赖 | P3 处理 |
|------|------|--------|
| `octoagent/scripts/run-octo-home.sh:18-23` | 加载 `.env.litellm` | 改成加载 `.env` |
| `octoagent/scripts/doctor-octo-home.sh:18-21` | 加载 `.env.litellm` + 检查 LiteLLM Proxy | 改成 `.env` + 检查 ProviderRouter |
| `provider/dx/runtime_activation.py:66-120` | `docker-compose.litellm.yml` | 移除 compose 启动；用 OCTOAGENT_HOME 解析 source root |
| `tooling/path_policy.py:52-60` | LiteLLM 文件列入敏感路径 | 移除条目 |
| `provider/dx/docker_daemon.py` | 整文件已无主调用方 | 整文件删除（P3 末尾） |

**类别 C：测试**（约 10 文件，P4 处理）：

| 文件 | 处理 |
|------|------|
| `tests/integration/test_f002_litellm_mode.py` | 整文件删除 |
| `tests/integration/test_f013_e2e_full.py` | 改用 ProviderRouter |
| `tests/integration/test_f002_fallback.py` | 改成 ProviderClient 测试 |
| `packages/provider/tests/test_client.py` | 删除 |
| `packages/provider/tests/test_providers_refresh_on_401.py` | 改成测 ProviderClient.call() 401/403 重试 |
| 其他散在 LiteLLM mock 的测试 | 逐个评估 |

### 2.2 兼容 shim 设计

**目标**：让 P1 解耦时，被解耦的调用方不依赖 LiteLLM 文件具体实现，但 LiteLLM 文件本身仍可被 import（避免 P4 之前 import 链断裂）。

策略：在 6 个待删文件顶部加 `# DEPRECATED Feature 081 P0 — Will be deleted in P4` 注释，函数体保留原实现；调用方改成调用新路径，但**不删除原 LiteLLM 文件的导出**。

### 2.3 P0 commit 内容

- 新增 `.specify/features/081-litellm-full-retirement/migration-inventory.md`（上述清单完整版）
- 6 个核心 LiteLLM 文件顶部加 deprecated 标记注释
- **不改变任何运行时行为**

`feat(spec): Feature 081 Phase 0 — 依赖盘点 + 迁移清单`

---

## 3. Phase 1 — 主线切线 + 调用方迁移

### 3.1 main.py 清理

```python
# 删除：
# 1. _ensure_litellm_master_key_env() 函数
# 2. _resolve_stream_model_aliases() / _resolve_responses_reasoning_aliases()
# 3. from octoagent.skills.litellm_client import LiteLLMSkillClient
# 4. from octoagent.gateway.services.config.litellm_runtime import (...)
# 5. from octoagent.provider import LiteLLMClient
# 6. if provider_config.llm_mode == "litellm": ProxyProcessManager + LiteLLMClient 创建块
# 7. if provider_config.llm_mode == "litellm": SkillRunner 包裹块（改成无条件创建）
# 8. lifespan shutdown 的 ProxyProcessManager 停止逻辑

# 保留：
# - app.state.provider_router 创建（已就位）
# - ProviderModelClient 创建（已就位）
# - echo mode 作为 fallback（不影响）
```

### 3.2 context_compaction.py 主线改造（**这是真正的运行时主线**）

`gateway/services/context_compaction.py:951-1028` 当前调用 `llm_service.call(alias, messages, ...)`。
Feature 080 后 `llm_service.call()` 底层已经走 ProviderRouter（通过 SkillRunner.run()）。

P1 改造目标：
- **不修改对外 API**（`llm_service.call()` 调用方式不变）
- 确认底层已经走 ProviderRouter（已是 Feature 080 现状）
- 保留 `compaction-alias → summarizer-alias → main-alias` 三级 fallback 语义
- 在 alias 缺失或凭证异常时降级返回空摘要 + log warning（不抛异常）
- 新增单测覆盖三级 fallback + 降级路径

### 3.3 调用方解耦（按 P0 类别 A 清单）

按 `migration-inventory.md` 类别 A 清单逐个改：

```python
# 例 1: gateway/services/control_plane/setup_service.py:57
# Before:
from octoagent.gateway.services.config.litellm_generator import (
    generate_env_litellm,
    generate_litellm_config,
)
# After:
# 删除 import；setup.apply 改成只写 octoagent.yaml + .env，不再生成 litellm-config.yaml
# 保留兼容：如果 v1 schema yaml 启动 setup，仍走老路径写出 litellm-config.yaml（让老 yaml 启动正常）

# 例 2: gateway/services/control_plane/mcp_service.py:38
# Before:
from octoagent.gateway.services.config.litellm_generator import generate_litellm_config
# After:
# 删除（mcp 不需要 LiteLLM 配置）

# 例 3: provider/dx/__init__.py:42-109 延迟导入
# Before:
def __getattr__(name: str):
    if name in {"generate_litellm_config", ...}:
        mod = importlib.import_module("octoagent.gateway.services.config.litellm_generator")
# After:
# 改成 raise AttributeError 或返回 None；P4 删除整段

# 例 4: octoagent_sdk/_agent.py:287
# Before:
# 优先使用 LiteLLMClient（如果可用）
try:
    from octoagent.provider.client import LiteLLMClient
    ...
# After:
# 直接用 ProviderClient（已是 Feature 080 主路径）
```

### 3.4 builtin_memu_bridge.py 删除 LiteLLM Proxy fallback

```python
# Before:
async def _fetch_embeddings(...):
    if self._provider_router:
        # ... router 路径
    else:
        # ... LiteLLM Proxy fallback
# After:
async def _fetch_embeddings(...):
    if not self._provider_router:
        raise RuntimeError("ProviderRouter not configured")
    # ... router 路径（不变）
```

### 3.5 health.py / orchestrator.py / capability_pack.py / runner.py / __init__.py

按 P0 清单移除 import 残留 + docstring/注释更新。

### 3.6 测试

- 跑 Feature 078/079/080 系列 152 条全部通过
- 新增 `test_context_compaction_fallback.py`（5 条：三级 fallback 命中、alias 缺失降级、凭证异常降级、threshold 触发、空摘要 fallback）

### 3.7 验证

```bash
# 必须全部成功
python -c "from octoagent.gateway.main import app"
python -c "import octoagent.provider; import octoagent.skills; import octoagent_sdk"
octo --help
octo config --help
octo config provider list
```

### 3.8 commit

`feat(provider): Feature 081 Phase 1 — 主线切线 + 调用方迁移（13 文件解耦）`

---

## 4. Phase 2 — Schema + 双对象 Migration

### 4.1 RuntimeConfig 字段保留为 deprecated（**修 Codex F2**）

```python
# config_schema.py
class RuntimeConfig(BaseModel):
    """Feature 081：LiteLLM 字段保留为 deprecated 仅供 legacy 检测；运行时被忽略。"""

    # ── deprecated 字段（保留可读，运行时不使用，下版本删除）──
    llm_mode: Literal["litellm", "echo"] | None = Field(
        default=None,
        deprecated=True,
        description="DEPRECATED: 跑 octo config migrate-080 升级",
    )
    litellm_proxy_url: str = Field(default="", deprecated=True)
    master_key_env: str = Field(default="", deprecated=True)
```

### 4.2 ProviderEntry 升级（first-class transport/auth）

```python
class AuthApiKey(BaseModel):
    kind: Literal["api_key"]
    env: str = Field(min_length=1, pattern=r"^[A-Z][A-Z0-9_]*$")

class AuthOAuth(BaseModel):
    kind: Literal["oauth"]
    profile: str = Field(min_length=1)

ProviderAuth = Annotated[Union[AuthApiKey, AuthOAuth], Field(discriminator="kind")]


class ProviderEntry(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_-]+$")
    name: str = Field(min_length=1)
    enabled: bool = True
    transport: Literal["openai_chat", "openai_responses", "anthropic_messages"] = Field(
        default="openai_chat",
        description="LLM 调用协议；不设时按 id 推断（backward-compat）",
    )
    api_base: str = Field(default="", description="provider HTTP 基础 URL")
    auth: ProviderAuth | None = Field(
        default=None,
        description="新 auth 字段；旧 auth_type + api_key_env 仍兼容",
    )
    extra_headers: dict[str, str] = Field(default_factory=dict)
    extra_body: dict[str, Any] = Field(default_factory=dict)

    # ── backward-compat 字段（标记 deprecated，下版本删除）──
    auth_type: Literal["api_key", "oauth"] | None = Field(default=None, deprecated=True)
    api_key_env: str = Field(default="", deprecated=True)
    base_url: str = Field(default="", deprecated=True)

    @model_validator(mode="after")
    def _migrate_legacy_fields(self) -> ProviderEntry:
        if not self.api_base and self.base_url:
            self.api_base = self.base_url
        if self.auth is None:
            if self.auth_type == "api_key" and self.api_key_env:
                self.auth = AuthApiKey(kind="api_key", env=self.api_key_env)
            elif self.auth_type == "oauth":
                self.auth = AuthOAuth(kind="oauth", profile=f"{self.id}-default")
        return self
```

### 4.3 raw YAML legacy-key 检测（**修 Codex F2**）

```python
# config_wizard.py（在 OctoAgentConfig.from_yaml 解析之前）
def detect_legacy_schema(raw_yaml: dict) -> list[str]:
    """检测旧 schema 字段，返回 deprecation 提示列表。"""
    legacy_keys = []
    runtime = raw_yaml.get("runtime", {}) or {}
    for key in ("llm_mode", "litellm_proxy_url", "master_key_env"):
        if key in runtime:
            legacy_keys.append(f"runtime.{key}")
    if raw_yaml.get("config_version", 1) < 2:
        legacy_keys.append("config_version<2")
    return legacy_keys


def load_config(project_root: Path) -> OctoAgentConfig | None:
    raw = yaml.safe_load(yaml_path.read_text())
    legacy_keys = detect_legacy_schema(raw)
    if legacy_keys:
        log.warning(
            "octoagent_yaml_legacy_schema_detected",
            keys=legacy_keys,
            recommendation="请运行 `octo config migrate-080` 升级到新 schema",
        )
    return OctoAgentConfig.model_validate(raw)
```

### 4.4 Migration 命令（**双对象：yaml + 凭证**，修 Codex F3）

```python
# octoagent/packages/provider/src/octoagent/provider/dx/config_commands.py

@command("migrate-080")
def migrate_080(
    project_root: Path = Path.cwd(),
    dry_run: bool = False,
):
    """Feature 080/081：把 octoagent.yaml 从 v1 schema 升级到 v2 + 迁移 .env.litellm。

    yaml 迁移：
    - 检测 config_version == 1 + runtime.llm_mode 字段存在
    - 备份原文件 → octoagent.yaml.bak.080-{timestamp}
    - 推断每个 provider 的 transport（按 id / api_base）
    - 把 auth_type + api_key_env 转成 auth: {kind, env|profile}
    - 删除 runtime.{llm_mode, litellm_proxy_url, master_key_env}
    - 写新 yaml + config_version: 2

    凭证迁移：
    - 读取 ~/.octoagent/.env.litellm
    - 把 OPENAI_API_KEY / ANTHROPIC_API_KEY 等合并到 ~/.octoagent/.env
    - 备份 .env.litellm → .env.litellm.bak.080-{timestamp}
    - 不删除 .env.litellm 原文件（兼容窗口直到 P4）

    --dry-run：只打印 diff，不写文件
    """
```

### 4.5 启动时 deprecation warning（**不主动改写**）

main.py lifespan 已经在 P2 通过 `load_config()` 内部检测；启动时如果 raw YAML 有 legacy keys，自动 log warning + 引导用户跑 migrate。

### 4.6 测试

- `test_provider_entry_v2_schema.py`：6 条（auth_kind=api_key + env 校验 / auth_kind=oauth + profile / discriminator 错误 / backward-compat 旧字段映射 / transport 默认值 / api_base 必填）
- `test_legacy_yaml_detection.py`：4 条（v1 yaml 触发 warning / v2 yaml 不触发 / 缺 config_version 触发 / 部分 legacy keys 触发）
- `test_migrate_080_command.py`：10 条（dry-run / 实际迁移 yaml / 备份 yaml / 推断 transport / 失败回滚 / 凭证迁移 / 备份凭证 / 重复执行幂等 / 缺 .env.litellm 安全降级 / 部分凭证迁移）

### 4.7 commit

`feat(config+dx): Feature 081 Phase 2 — Schema 升级 + raw YAML 检测 + octo config migrate-080（yaml + 凭证）`

---

## 5. Phase 3 — DX/CLI + 前端 + 脚本运维

### 5.1 后端 DX 适配

| 文件 | 改动 |
|------|------|
| `dx/init_wizard.py` | setup 流程跳过 LiteLLM 配置生成；只问 provider + alias |
| `dx/onboarding_service.py` | 默认 provider preset 用新 schema（`{transport, auth}`） |
| `dx/doctor.py` | 删除 LiteLLM Proxy 健康检查；改成 ProviderRouter 可用性检查 |
| `dx/runtime_activation.py` | **核心改造**：删除 `docker-compose.litellm.yml` 解析；source root 改成 `OCTOAGENT_HOME` 环境变量或固定 `~/.octoagent`；不再启动 compose |
| `dx/config_commands.py` | 加 `transport set` / `auth set` 子命令 |
| `dx/install_bootstrap.py` | 删除 LiteLLM 相关安装 |
| `dx/backup_service.py` | 备份范围不再含 litellm-config*.yaml |
| `gateway/services/control_plane/setup_service.py` | setup.review / setup.apply 适配新 schema；写 `.env`（不写 `.env.litellm`） |

### 5.2 前端

| 文件 | 改动 |
|------|------|
| `frontend/src/types/index.ts` | ProviderEntry 类型加 `transport / auth` 字段；保留旧字段做 backward-compat |
| `frontend/src/domains/settings/SettingsProviderSection.tsx` | 加 transport 选择器 + auth.kind 切换 UI（api_key / oauth） |
| `frontend/src/domains/settings/shared.tsx` | `buildProviderPreset` 默认含 transport + auth |
| `frontend/src/domains/settings/SettingsPage.tsx` | 删除 `runtime.llm_mode` / `litellm_proxy_url` / `master_key_env` 输入字段；调整 `buildManagedProviderDraft` 不再注入这些字段 |

### 5.3 脚本运维（**修 Codex F4**）

| 文件 | 改动 |
|------|------|
| `octoagent/scripts/run-octo-home.sh:18-23` | 删除 `.env.litellm` 加载行；改成 `source $HOME/.octoagent/.env` |
| `octoagent/scripts/doctor-octo-home.sh:18-21` | 同上 + 把 LiteLLM Proxy ready 检查改成 ProviderRouter ready（curl Gateway `/ready`） |
| `provider/dx/runtime_activation.py:66-120` | 移除 compose 启动逻辑；source root 解析改用 `OCTOAGENT_HOME` 或 `~/.octoagent` |
| `tooling/path_policy.py:52-60` | 从敏感路径列表移除 LiteLLM 相关条目 |
| `provider/dx/docker_daemon.py` | 整文件删除（已无调用方） |

### 5.4 测试

- `test_dx_setup_wizard_v2.py`：3 条（新 setup 不出现 LiteLLM / doctor 不查 Proxy / config_commands 加新子命令）
- `test_run_octo_home_script.py`：bash 集成测（脚本不依赖 .env.litellm）
- `test_runtime_activation_no_compose.py`：3 条（source root 解析 / 启动不调 compose / OCTOAGENT_HOME fallback）
- `frontend SettingsPage.test.tsx` 更新：移除 runtime 字段相关 assert；加 transport / auth UI assert

### 5.5 commit

`feat(dx+frontend+scripts): Feature 081 Phase 3 — DX 适配 + 前端 Settings + 脚本运维解耦`

---

## 6. Phase 4 — 实际删除 + 文档 + 验收

### 6.1 删除 LiteLLM 核心文件（前置：P1-P3 已确认所有引用解耦）

```bash
# 验证：grep 应该返回空（除测试和文档历史引用）
git grep -l "LiteLLMClient\|LiteLLMSkillClient\|ChatCompletionsProvider\|ResponsesApiProvider\|ProxyProcessManager\|litellm_generator\|litellm_runtime" \
  octoagent/ --include='*.py' | grep -v __pycache__ | grep -v test_

# 删除 6 个核心文件
git rm octoagent/packages/skills/src/octoagent/skills/litellm_client.py
git rm octoagent/packages/skills/src/octoagent/skills/providers.py
git rm octoagent/packages/skills/src/octoagent/skills/compactor.py  # 兼容 shim
git rm octoagent/packages/provider/src/octoagent/provider/client.py
git rm octoagent/apps/gateway/src/octoagent/gateway/services/proxy_process_manager.py
git rm octoagent/apps/gateway/src/octoagent/gateway/services/config/litellm_generator.py
git rm octoagent/apps/gateway/src/octoagent/gateway/services/config/litellm_runtime.py

# 删除部署相关
git rm docker-compose.litellm.yml
```

### 6.2 删除 deprecated 字段（**可选，留给下版本**）

P4 默认保留 `RuntimeConfig.llm_mode / litellm_proxy_url / master_key_env` 的 deprecated 字段（兼容窗口持续到下个 minor 版本）。

**仅在 Connor 明确同意时**才删除这些字段。

### 6.3 测试清理

- 删除：`tests/integration/test_f002_litellm_mode.py`（整文件）
- 改写：`tests/integration/test_f013_e2e_full.py` 使用 ProviderRouter 而非 LiteLLM
- 删除：`packages/provider/tests/test_client.py`
- 改写：`packages/provider/tests/test_providers_refresh_on_401.py` → 测 ProviderClient.call() 401/403
- 验证：所有保留测试通过（约 152 条 78/79/80 系列 + 本 Feature 新增）

### 6.4 文档

- `docs/blueprint.md` / `docs/blueprint/*` 更新模型调用层架构图
- `CLAUDE.md` 移除 LiteLLM Proxy 相关描述
- 新增 `docs/codebase-architecture/provider-direct-routing.md`（Feature 080+081 的最终架构）
- `README.md` 更新（如有 LiteLLM 提及）

### 6.5 全量验收

```bash
# 1. 静态导入检查
python -c "from octoagent.gateway.main import app; print('OK')"
python -c "import octoagent.provider; import octoagent.skills; import octoagent_sdk; print('OK')"

# 2. CLI 检查
octo --help
octo config --help
octo config migrate-080 --help

# 3. 测试套件
pytest tests/

# 4. 真实 Gateway 启动验证 + 三 transport 烟测
octo run --port 8000  # 启动时间 ≤ 5s
# 然后测：
# - ChatGPT Pro Codex (openai_responses + OAuth)
# - SiliconFlow / DeepSeek (openai_chat + api_key)
# - Anthropic Claude (anthropic_messages + OAuth)

# 5. grep 验证
grep -r litellm octoagent/ --include='*.py' | grep -v __pycache__ | grep -v migration
# 应返回 ≤ 3 行（仅迁移代码 / docstring 历史引用）
```

### 6.6 commit

`chore(cleanup): Feature 081 Phase 4 — 删除 LiteLLM 核心文件 + 测试清理 + 文档同步 + 全量验收`

---

## 7. Migration 兼容性矩阵

| 用户 yaml 状态 | 启动行为 | 凭证行为 | 推荐操作 |
|--------------|---------|---------|---------|
| v1 yaml + .env.litellm | 启动正常 + warning（legacy keys） | `.env.litellm` 仍被读取（P3 后） | `octo config migrate-080` |
| v1 yaml + 仅 .env | 启动正常 + warning | `.env` 读取（一直支持） | `octo config migrate-080` |
| v2 yaml（已 migrate） + .env | 启动正常 + 无 warning | `.env` 读取 | 无 |
| v2 yaml + 残留 .env.litellm | 启动正常 + 无 warning | `.env` 优先；`.env.litellm` 仍兼容读取至 P4 | 删除 `.env.litellm.bak.*` |
| 损坏 / 缺字段 | ConfigParseError + 引导用户修复 | - | 检查报错 → 手动修 / 恢复备份 |

## 8. 风险缓解

- **原子性**：每个 Phase 独立 commit，可逐个 revert；所有 Phase 都满足 I-8（commit 后 import 不挂）
- **测试覆盖**：所有 Phase 末跑全量测试（包括 Feature 078/079/080 152 条回归）
- **Migration dry-run**：默认 `--dry-run` 让用户先看 diff
- **Backward-compat**：deprecated 字段保留可读但运行时忽略（P2 实现，到下个 minor 删）
- **凭证兼容窗口**：`.env.litellm` 在 P1-P3 期间仍被读取，P4 完成时自动 fallback 到 `.env`
- **回退方案**：删除 LiteLLM 文件前 git tag + 备份；用户配置自动备份

## 9. Scope Lock（不改的东西）

- `auth-profiles.json` schema
- `PkceOAuthAdapter` / OAuth flow
- `TokenRefreshCoordinator`
- 所有 EventType 枚举
- Skill / Tool / SkillRunner / EventStore 接口
- CLI 顶层命令名
- Feature 080 的 ProviderClient / ProviderRouter / AuthResolver / ProviderModelClient
- `gateway/services/context_compaction.py` 对外 API（`llm_service.call()` 调用方式不变）

## 10. 全量验收 checklist（Phase 4 完成后）

### 功能
- [ ] Gateway 启动 ≤ 5 秒
- [ ] Memory embedding 走 ProviderRouter（不再 LiteLLM Proxy fallback）
- [ ] LiteLLM Proxy 进程不再启动
- [ ] octo config migrate-080 命令可用（yaml + 凭证双迁移）
- [ ] 老 yaml 启动给 deprecation warning + 仍能工作
- [ ] context compaction 在 alias 缺失时降级返回空摘要

### 架构
- [ ] 6 个核心 LiteLLM 文件已 git rm
- [ ] `grep -r litellm octoagent/ --include='*.py' | grep -v __pycache__ | grep -v migration` 返回 ≤ 3 行
- [ ] `RuntimeConfig` 字段保留 deprecated（不立即删）
- [ ] `ProviderEntry.transport` / `.auth` 是 first-class 字段

### 兼容性（**含 Codex 5 个 finding 的兜底**）
- [ ] 152 条 78/79/80 测试全部通过
- [ ] 现有用户跑 migrate-080 自动升级 + 备份（含凭证）
- [ ] CLI 命令照常工作
- [ ] **每个 Phase commit 后 `python -c "from octoagent.gateway.main import app"` 不抛**
- [ ] **每个 Phase commit 后 `octo config --help` 正常**
- [ ] 老 home-instance 脚本（`run-octo-home.sh` / `doctor-octo-home.sh`）在 P3 后仍可启动 Gateway
- [ ] `.env.litellm` 凭证迁移成功（API key 不丢）

### 文档
- [ ] `docs/blueprint/*` 同步
- [ ] `CLAUDE.md` 移除 LiteLLM
- [ ] 新增 provider-direct-routing.md
- [ ] `docker-compose.litellm.yml` 删除

---

## 11. 与 Codex 审查 finding 的对照

| Codex finding | 严重度 | 修订方案 |
|--------------|-------|---------|
| F1: P1 直接 git rm 后 13 个引用方炸 | High | Phase 重排为 5 个；P0 盘点 + P1 解耦 + P4 才删；I-8 不变量保证每个 commit 后可 import |
| F2: 删字段后无法做 deprecation 检测 | High | RuntimeConfig 字段保留 deprecated；raw YAML 层做 legacy-key 检测（在 Pydantic 解析之前）|
| F3: migrate-080 不处理 .env.litellm 凭证 | High | FR-9 显式凭证迁移；migrate-080 双对象（yaml + .env.litellm → .env）；兼容窗口至 P4 |
| F4: docker-compose.litellm.yml 删除有未列脚本依赖 | Medium | FR-10 显式列出 5 个运维路径；P3 解耦 |
| F5: 改了非主线 skills/compactor.py | Medium | FR-11 + 3.2 节明确以 `gateway/services/context_compaction.py` 为目标；保留三级 fallback |

---

## 12. 总结

**预计净删 ~2500 行**（删 1200 + 200 + 150 + 200 + 200 + 600 = 2550 删除；新增 ~400 schema + migration + 前端调整 + 脚本迁移）。

**收益**：
- Gateway 启动从 ~10s 降到 ~5s
- 配置 source-of-truth 仅 2 份文件（yaml + auth-profiles）
- LLM 调用栈深度稳定 2 层
- 用户首次 setup 流程从 5 步缩到 3 步
- 仓库依赖减少（不再要 docker-compose / litellm package）
- CI 测试时间减少（删除 ~10 个 integration test）

**风险**：可控，所有改动渐进式 + 可回滚 + Migration 命令 + backward-compat 兜底。Codex 审查 5 个 finding 全部纳入设计。
