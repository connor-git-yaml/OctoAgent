# Feature 081 — LiteLLM 完全退役 · 实施计划

> 作者：Connor
> 日期：2026-04-26
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
                ↓ Feature 081
┌─ 主路径 (Skill / Memory) ─────────┐
│ ProviderRouter 直连（与现状一致）  │
│ + 配置 schema 干净                 │
│ + DX/CLI 不再提 LiteLLM            │
│ + 前端 Settings 简洁               │
│ + 文档同步                         │
│ + LiteLLM 文件全部删除             │
└────────────────────────────────────┘
```

**核心方法**：分 4 Phase 渐进退役，每个 Phase 独立 commit + 可回滚。

---

## 1. Phase 划分

| Phase | 内容 | 估时 | 风险 |
|-------|------|------|------|
| **P1 后端核心组件删除** | 删 6 个核心文件 + 清理 main.py / compactor / memory bridge fallback | 半天 | 低（functional 已切线） |
| **P2 Schema + Migration 命令** | RuntimeConfig 字段移除（带 backward-compat） + ProviderEntry 加 transport/auth + `octo config migrate-080` | 半天 | 中（schema 变更） |
| **P3 DX/CLI + 前端清理** | setup wizard / doctor / config_commands + Settings 页字段 + Provider 表单 transport/auth 选择器 | 1 天 | 中（UX 变化） |
| **P4 测试 + 文档 + 收尾** | 删旧 integration tests + 更新文档 + docker-compose.litellm.yml 删除 + 全量验收 | 半天 | 低 |

**总计 ~2.5 天 / 4 个独立 commit / 净删 ~2500 行代码**

---

## 2. Phase 1 — 后端核心组件删除

### 2.1 删除文件（直接 `git rm`）

- `octoagent/packages/skills/src/octoagent/skills/litellm_client.py` (~600 行 LiteLLMSkillClient)
- `octoagent/packages/skills/src/octoagent/skills/providers.py` (~700 行 ChatCompletionsProvider / ResponsesApiProvider)
- `octoagent/packages/provider/src/octoagent/provider/client.py` (~200 行 LiteLLMClient)
- `octoagent/apps/gateway/src/octoagent/gateway/services/proxy_process_manager.py` (~150 行)
- `octoagent/apps/gateway/src/octoagent/gateway/services/config/litellm_generator.py` (~200 行)
- `octoagent/apps/gateway/src/octoagent/gateway/services/config/litellm_runtime.py` (~200 行)

### 2.2 清理引用

| 文件 | 改动 |
|------|------|
| `gateway/main.py` | 删除 `if provider_config.llm_mode == "litellm":` 分支启动 ProxyManager + LiteLLMClient 创建逻辑；只保留 `app.state.provider_router` 创建 + `ProviderModelClient` 用法；`echo` mode 仍保留 |
| `skills/compactor.py` | `ContextCompactor(proxy_url, master_key)` → `ContextCompactor(provider_router, alias_for_compaction)`；compact() 内部用 `router.resolve_for_alias(alias).client.call(...)` 替代直接 httpx |
| `skills/runner.py` | 删除 `from skills.providers import ...` 中已删类型的 import |
| `skills/__init__.py` | 移除已删类的 export |
| `provider/__init__.py` | 移除 `LiteLLMClient` export |
| `gateway/services/memory/builtin_memu_bridge.py` | 删除 `_fetch_embeddings` 内的 LiteLLM Proxy fallback 分支（仅保留 router 路径） |
| `gateway/services/orchestrator.py` | 清理 LiteLLM 引用 |
| `gateway/services/capability_pack.py` | 清理 LiteLLM 引用 |
| `gateway/routes/health.py` | 移除 proxy_manager 健康检查 |

### 2.3 测试

- 跑 Phase 1-5a 的 110+ 条测试 + Feature 078/079 系列（约 152 条）全部通过
- 新增 `test_compactor_provider_router.py`（5 条：compact 走 router、threshold 触发、token 估算、降级路径、router 不可用 fallback）

### 2.4 commit

`feat(provider): Feature 081 Phase 1 — 删除 LiteLLM 核心组件 + main.py 清理`

---

## 3. Phase 2 — Schema + Migration

### 3.1 ProviderEntry 升级（first-class transport/auth）

```python
# config_schema.py

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
        # 旧字段 → 新字段（如果新字段为空）
        if not self.api_base and self.base_url:
            self.api_base = self.base_url
        if self.auth is None:
            if self.auth_type == "api_key" and self.api_key_env:
                self.auth = AuthApiKey(kind="api_key", env=self.api_key_env)
            elif self.auth_type == "oauth":
                self.auth = AuthOAuth(kind="oauth", profile=f"{self.id}-default")
        return self
```

### 3.2 RuntimeConfig 字段移除

```python
class RuntimeConfig(BaseModel):
    """Feature 081：仅保留必要的 runtime 配置；LiteLLM 相关字段全部删除。"""
    
    # 全部删除：
    # - llm_mode (litellm/echo) → 不再需要 mode；echo provider 走 ProviderEntry
    # - litellm_proxy_url → 不再有 Proxy
    # - master_key_env → 不再有 master_key
```

### 3.3 Migration 命令

```python
# octoagent/packages/provider/src/octoagent/provider/dx/config_commands.py

@command("migrate-080")
def migrate_080(
    project_root: Path = Path.cwd(),
    dry_run: bool = False,
):
    """Feature 080/081：把 octoagent.yaml 从 v1 schema 升级到 v2。
    
    - 检测 config_version == 1 + runtime.llm_mode 字段存在
    - 备份原文件 → octoagent.yaml.bak.080-{timestamp}
    - 推断每个 provider 的 transport（按 id / api_base）
    - 把 auth_type + api_key_env 转成 auth: {kind, env|profile}
    - 删除 runtime.{llm_mode, litellm_proxy_url, master_key_env}
    - 写新 yaml + config_version: 2
    
    --dry-run：只打印 diff，不写文件
    """
```

### 3.4 启动时 deprecation

main.py lifespan 检测旧 schema，log warning 提示用户跑 migrate（不主动改写，修 Codex F5）：

```python
# main.py
config = load_config(project_root)
if config and config.runtime.litellm_proxy_url:  # 旧字段还在
    log.warning(
        "octoagent_yaml_legacy_schema_detected",
        recommendation="请运行 `octo config migrate-080` 升级到新 schema",
    )
```

### 3.5 测试

- `test_provider_entry_v2_schema.py`：6 条（auth_kind=api_key + env 校验 / auth_kind=oauth + profile / discriminator 错误 / backward-compat 旧字段映射 / transport 默认值 / api_base 必填）
- `test_migrate_080_command.py`：5 条（dry-run / 实际迁移 / 备份创建 / 推断 transport 准确性 / 失败回滚）

### 3.6 commit

`feat(config+dx): Feature 081 Phase 2 — Schema 升级 + octo config migrate-080`

---

## 4. Phase 3 — DX/CLI + 前端清理

### 4.1 后端 DX 适配

| 文件 | 改动 |
|------|------|
| `dx/init_wizard.py` | setup 流程跳过 LiteLLM 配置生成；只问 provider + alias |
| `dx/onboarding_service.py` | 默认 provider preset 用新 schema（`{transport, auth}`） |
| `dx/doctor.py` | 删除 LiteLLM Proxy 健康检查；改成 ProviderRouter 可用性检查 |
| `dx/runtime_activation.py` | 删除 / 简化（不再启动 LiteLLM Proxy） |
| `dx/config_commands.py` | 加 `transport set` / `auth set` 子命令 |
| `dx/install_bootstrap.py` | 删除 LiteLLM 相关安装 |
| `dx/backup_service.py` | 备份范围不再含 litellm-config*.yaml |
| `gateway/services/control_plane/setup_service.py` | setup.review / setup.apply 适配新 schema |

### 4.2 前端

| 文件 | 改动 |
|------|------|
| `frontend/src/types/index.ts` | ProviderEntry 类型加 `transport / auth` 字段；保留旧字段做 backward-compat |
| `frontend/src/domains/settings/SettingsProviderSection.tsx` | 加 transport 选择器 + auth.kind 切换 UI（api_key / oauth） |
| `frontend/src/domains/settings/shared.tsx` | `buildProviderPreset` 默认含 transport + auth |
| `frontend/src/domains/settings/SettingsPage.tsx` | 删除 `runtime.llm_mode` / `litellm_proxy_url` / `master_key_env` 输入字段；调整 `buildManagedProviderDraft` 不再注入这些字段 |

### 4.3 测试

- `test_dx_setup_wizard_v2.py`：3 条（新 setup 不出现 LiteLLM / doctor 不查 Proxy / config_commands 加新子命令）
- `frontend SettingsPage.test.tsx` 更新：移除 runtime 字段相关 assert；加 transport / auth UI assert

### 4.4 commit

`feat(dx+frontend): Feature 081 Phase 3 — DX/CLI 适配 + 前端 Settings 重构`

---

## 5. Phase 4 — 测试 + 文档 + 收尾

### 5.1 测试清理

- 删除：`tests/integration/test_f002_litellm_mode.py`（整文件）
- 改写：`tests/integration/test_f013_e2e_full.py` 使用 ProviderRouter 而非 LiteLLM
- 删除：`packages/provider/tests/test_*` 中纯 LiteLLM 测试
- 验证：所有保留测试通过（约 152 条 78/79/80 系列）

### 5.2 文档

- `docs/blueprint.md` / `docs/blueprint/*` 更新模型调用层架构图
- `CLAUDE.md` 移除 LiteLLM Proxy 相关描述
- 新增 `docs/codebase-architecture/provider-direct-routing.md`（Feature 080+081 的最终架构）
- `README.md` 更新（如有 LiteLLM 提及）

### 5.3 删除残留文件

- `docker-compose.litellm.yml`
- `litellm-config-template.yaml`（如果有）
- 任何遗留 `.env.litellm.example` 文件

### 5.4 全量验收

跑完整测试套件 + 真实 Gateway 启动验证 + 三 transport 烟测：
- ChatGPT Pro Codex (openai_responses + OAuth)
- SiliconFlow / DeepSeek (openai_chat + api_key)
- Anthropic Claude (anthropic_messages + OAuth)

### 5.5 commit

`chore(cleanup): Feature 081 Phase 4 — 删除测试残留 + 文档同步 + 验收`

---

## 6. Migration 兼容性矩阵

| 用户 yaml 状态 | 启动行为 | 推荐操作 |
|--------------|---------|---------|
| v1（含 `runtime.llm_mode`） | 启动正常 + warning 提示运行 migrate | `octo config migrate-080` |
| v1（含 LiteLLM 字段但 router 切线后用不到） | 启动正常 + warning | 同上 |
| v2（已 migrate） | 启动正常 + 无 warning | 无 |
| 损坏 / 缺字段 | ConfigParseError + 引导用户修复 | 检查报错 → 手动修 / 恢复备份 |

## 7. 风险缓解

- **原子性**：每个 Phase 独立 commit，可逐个 revert
- **测试覆盖**：所有 Phase 末跑全量测试（包括 Feature 078/079/080 152 条回归）
- **Migration dry-run**：默认 `--dry-run` 让用户先看 diff
- **Backward-compat**：旧 schema 字段保留可读但忽略（Phase 2 实现）
- **回退方案**：删除 LiteLLM 文件前 git stash + 备份；用户配置自动备份

## 8. Scope Lock（不改的东西）

- `auth-profiles.json` schema
- `PkceOAuthAdapter` / OAuth flow
- `TokenRefreshCoordinator`
- 所有 EventType 枚举
- Skill / Tool / SkillRunner / EventStore 接口
- CLI 顶层命令名
- Feature 080 的 ProviderClient / ProviderRouter / AuthResolver / ProviderModelClient

## 9. 全量验收 checklist（Phase 4 完成后）

### 功能
- [ ] Gateway 启动 ≤ 5 秒
- [ ] Memory embedding 走 ProviderRouter（不再 LiteLLM Proxy fallback）
- [ ] LiteLLM Proxy 进程不再启动
- [ ] octo config migrate-080 命令可用
- [ ] 老 yaml 启动给 deprecation warning + 仍能工作

### 架构
- [ ] 6 个核心 LiteLLM 文件已 git rm
- [ ] `grep -r litellm octoagent/ --include='*.py' | grep -v __pycache__ | grep -v migration` 返回 ≤ 3 行
- [ ] `RuntimeConfig` 不含 LiteLLM 字段
- [ ] `ProviderEntry.transport` / `.auth` 是 first-class 字段

### 兼容性
- [ ] 152 条 78/79/80 测试全部通过
- [ ] 现有用户跑 migrate-080 自动升级 + 备份
- [ ] CLI 命令照常工作

### 文档
- [ ] `docs/blueprint/*` 同步
- [ ] `CLAUDE.md` 移除 LiteLLM
- [ ] 新增 provider-direct-routing.md
- [ ] `docker-compose.litellm.yml` 删除

---

## 10. 总结

**预计净删 ~2500 行**（删 1200 + 200 + 150 + 200 + 200 + 600 = 2550 删除；新增 ~300 schema + migration + 前端调整）。

**收益**：
- Gateway 启动从 ~10s 降到 ~5s
- 配置 source-of-truth 仅 2 份文件
- LLM 调用栈深度稳定 2 层
- 用户首次 setup 流程从 5 步缩到 3 步
- 仓库依赖减少（不再要 docker-compose / litellm package）
- CI 测试时间减少（删除 ~10 个 integration test）

**风险**：可控，所有改动渐进式 + 可回滚 + Migration 命令 + backward-compat 兜底。
