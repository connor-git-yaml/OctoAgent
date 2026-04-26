# Feature 081 — 迁移依赖盘点（P0 产出）

> 作者：Connor
> 日期：2026-04-26
> 上游：plan.md
> 用途：P1-P4 解耦 / 删除工作的 source-of-truth 清单

本清单是 **Phase 0 的核心产出**，列出仓库里所有 LiteLLM 依赖点，以及每个 Phase 的处理动作。
P1 完成时类别 A 全部解耦；P3 完成时类别 B 全部解耦；P4 完成时类别 C 全部清理 + 6 个核心文件实际 `git rm`。

每个 Phase commit 后必须满足：
- `python -c "from octoagent.gateway.main import app"` 成功
- `octo --help` / `octo config --help` 正常
- Feature 078/079/080 的 152 条测试全部通过

---

## 类别 A：Python import 引用（21 处，P1 解耦）

### A.1 Gateway 主线（5 处）

| 行号引用 | 依赖 | P1 动作 |
|----------|------|--------|
| `octoagent/apps/gateway/src/octoagent/gateway/main.py:26` | `from octoagent.provider import LiteLLMClient` | 移除 import |
| `octoagent/apps/gateway/src/octoagent/gateway/main.py:31` | `from .config.litellm_runtime import resolve_codex_backend_aliases, resolve_codex_reasoning_aliases, resolve_reasoning_supported_aliases, resolve_responses_api_direct_params` | 移除 import 块 |
| `octoagent/apps/gateway/src/octoagent/gateway/main.py:42` | `from octoagent.skills.litellm_client import LiteLLMSkillClient` | 移除 import |
| `octoagent/apps/gateway/src/octoagent/gateway/main.py:134` | `_ensure_litellm_master_key_env()` 函数 | 整函数删除 |
| `octoagent/apps/gateway/src/octoagent/gateway/main.py:164,169` | `_resolve_stream_model_aliases` / `_resolve_responses_reasoning_aliases` | 整函数删除 |
| `octoagent/apps/gateway/src/octoagent/gateway/main.py:427-487` | `if provider_config.llm_mode == "litellm": ProxyProcessManager + LiteLLMClient` 创建块 | 整块删除 |
| `octoagent/apps/gateway/src/octoagent/gateway/main.py:581` | `if provider_config.llm_mode == "litellm": SkillRunner` | 改为无条件创建 |
| `octoagent/apps/gateway/src/octoagent/gateway/main.py:838` | shutdown ProxyProcessManager 逻辑 | 删除 |

### A.2 Gateway Services（5 处）

| 行号引用 | 依赖 | P1 动作 |
|----------|------|--------|
| `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane/setup_service.py:57` | `from ..config.litellm_generator import generate_env_litellm, generate_litellm_config` | 移除 import；setup.apply 改成只写 octoagent.yaml + .env，不再生成 litellm-config.yaml |
| `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane/mcp_service.py:38` | `from ..config.litellm_generator import generate_litellm_config` | 删除 import + 调用（mcp 不需要 LiteLLM） |
| `octoagent/apps/gateway/src/octoagent/gateway/services/builtin_tools/config_tools.py:248` | `from ..config.litellm_generator import generate_litellm_config as _gen_litellm` | 删除该工具的 LiteLLM 子分支 |
| `octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py:560` | docstring `"由 SkillRunner（或 LiteLLMSkillClient）..."` | 改 docstring |
| `octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py` | 注释 `Feature 080 Phase 5：把 router 注入...替代 LiteLLM` | 改注释 |
| `octoagent/apps/gateway/src/octoagent/gateway/services/memory/builtin_memu_bridge.py` | `_fetch_embeddings` 内的 LiteLLM Proxy fallback | 删除 fallback 分支，仅保留 router 路径 |
| `octoagent/apps/gateway/src/octoagent/gateway/services/auth_refresh.py:3,126` | docstring `"将 PkceOAuthAdapter ... 接入 LiteLLMClient"` | 改 docstring |
| `octoagent/apps/gateway/src/octoagent/gateway/routes/health.py` | proxy_manager 健康检查 endpoint | 删除该分支 |

### A.3 Provider Package（4 处）

| 行号引用 | 依赖 | P1 动作 |
|----------|------|--------|
| `octoagent/packages/provider/src/octoagent/provider/__init__.py:37` | `from .client import LiteLLMClient` | 移除 import |
| `octoagent/packages/provider/src/octoagent/provider/__init__.py:84` | `"LiteLLMClient"` in `__all__` | 移除 export |
| `octoagent/packages/provider/src/octoagent/provider/__init__.py:66` | 注释 `"将取代 LiteLLMClient 成为唯一 LLM 调用层"` | 改注释 |
| `octoagent/packages/provider/src/octoagent/provider/fallback.py:19,31` | docstring `"LiteLLMClient -> EchoMessageAdapter"` | 改 docstring |
| `octoagent/packages/provider/src/octoagent/provider/provider_client.py:10,319,632,636` | docstring 历史引用 | 保留作为历史说明 |

### A.4 Provider DX（7 处）

| 行号引用 | 依赖 | P1 动作 |
|----------|------|--------|
| `octoagent/packages/provider/src/octoagent/provider/dx/__init__.py:42` | `mod = importlib.import_module("octoagent.gateway.services.config.litellm_generator")` | 改成 raise AttributeError 或返回 None |
| `octoagent/packages/provider/src/octoagent/provider/dx/__init__.py:50` | 同上对 `litellm_runtime` | 同上 |
| `octoagent/packages/provider/src/octoagent/provider/dx/__init__.py:109` | `"ProxyProcessManager": "..."` 在 _LAZY_REEXPORTS | 移除条目 |
| `octoagent/packages/provider/src/octoagent/provider/dx/onboarding_service.py:20` | `from ..gateway.services.config.litellm_generator import check_litellm_sync_status` | 删除 import + 所有调用点 |
| `octoagent/packages/provider/src/octoagent/provider/dx/doctor.py:21` | `from ..gateway.services.config.litellm_runtime import alias_uses_codex_backend` | 删除 import；alias 检查改成读 ProviderEntry.transport |
| `octoagent/packages/provider/src/octoagent/provider/dx/doctor.py:632` | `from ..gateway.services.config.litellm_generator import check_litellm_sync_status` | 删除 import + 调用 |
| `octoagent/packages/provider/src/octoagent/provider/dx/install_bootstrap.py:16` | `from ..gateway.services.config.litellm_generator import generate_litellm_config` | 删除 LiteLLM 安装路径 |
| `octoagent/packages/provider/src/octoagent/provider/dx/config_commands.py:45` | `from ..gateway.services.config.litellm_generator import build_litellm_config_dict, generate_litellm_config` | 删除 import |
| `octoagent/packages/provider/src/octoagent/provider/dx/config_commands.py:536` | `from ..gateway.services.config.litellm_generator import generate_env_litellm` | 删除 import + 调用（写 .env 而非 .env.litellm） |
| `octoagent/packages/provider/src/octoagent/provider/dx/config_commands.py:852` | `from ..gateway.services.config.litellm_generator import GENERATED_MARKER` | 改用本地常量或删除 |
| `octoagent/packages/provider/src/octoagent/provider/dx/config_bootstrap.py:27` | `from ..gateway.services.config.litellm_generator import generate_litellm_config` | 删除 import + 调用 |
| `octoagent/packages/provider/src/octoagent/provider/dx/runtime_activation.py:105,109` | docstring 提到 `ProxyProcessManager` | 改 docstring（实际逻辑 P3 处理） |

### A.5 Skills Package（4 处）

| 行号引用 | 依赖 | P1 动作 |
|----------|------|--------|
| `octoagent/packages/skills/src/octoagent/skills/runner.py` | `from .litellm_client import ...` 或类型引用 | 移除 import |
| `octoagent/packages/skills/src/octoagent/skills/__init__.py` | export 已删类 | 移除 export（类文件保留至 P4） |
| `octoagent/packages/skills/src/octoagent/skills/compactor.py:281` | docstring `"在 LiteLLMSkillClient.generate() 调用前..."` | 标记 deprecated；整文件作为兼容 shim 保留至 P4 |
| `octoagent/packages/skills/src/octoagent/skills/provider_model_client.py:3,7,11,16,57,60,98,166,407` | docstring 历史引用 | 保留作为历史说明 |

### A.6 SDK（2 处）

| 行号引用 | 依赖 | P1 动作 |
|----------|------|--------|
| `octoagent/packages/sdk/src/octoagent_sdk/_agent.py:287` | 注释 `"# 优先使用 LiteLLMClient（如果可用）"` | 改注释 |
| `octoagent/packages/sdk/src/octoagent_sdk/_agent.py:289` | `from octoagent.provider.client import LiteLLMClient` | 改成只走 ProviderClient |

---

## 类别 B：脚本与运维路径（5 处，P3 处理）

| 文件 | 依赖 | P3 动作 |
|------|------|--------|
| `octoagent/scripts/run-octo-home.sh:18-23` | 加载 `.env.litellm` | 改成 `source $HOME/.octoagent/.env` |
| `octoagent/scripts/doctor-octo-home.sh:18-21` | 加载 `.env.litellm` + 检查 LiteLLM Proxy | 改成 `.env` + 检查 ProviderRouter（curl Gateway `/ready`） |
| `octoagent/packages/provider/src/octoagent/provider/dx/runtime_activation.py:66-120` | 解析 `docker-compose.litellm.yml`，解析 source root，启动 compose | 移除 compose 启动；source root 改用 `OCTOAGENT_HOME` 或 `~/.octoagent` |
| `octoagent/packages/tooling/src/octoagent/tooling/path_policy.py:52-60` | LiteLLM 文件列入敏感路径 | 移除 LiteLLM 相关条目 |
| `octoagent/packages/provider/src/octoagent/provider/dx/docker_daemon.py` | 整文件已无主调用方（仅被 ProxyProcessManager 用） | 整文件删除（P3 末尾） |

---

## 类别 C：测试（约 10 文件，P4 处理）

### C.1 整文件删除

- `tests/integration/test_f002_litellm_mode.py`（整文件）
- `octoagent/packages/provider/tests/test_client.py`（LiteLLMClient 单测）

### C.2 改写

- `tests/integration/test_f013_e2e_full.py` → 用 ProviderRouter 而非 LiteLLM
- `tests/integration/test_f002_fallback.py` → 改成 ProviderClient fallback 测试
- `octoagent/packages/provider/tests/test_providers_refresh_on_401.py` → 测 `ProviderClient.call()` 401/403 重试

### C.3 散在的 LiteLLM mock 测试

需要 P4 时再做一次 grep + 评估：

```bash
grep -r "LiteLLMClient\|LiteLLMSkillClient\|litellm_proxy" tests/ --include="*.py"
```

---

## 类别 D：文档（P4 处理）

- `docs/blueprint.md` / `docs/blueprint/*` — 模型调用层架构图更新
- `CLAUDE.md` — 移除 LiteLLM Proxy 描述
- `README.md` — 检查并更新（如有 LiteLLM 提及）
- 新增 `docs/codebase-architecture/provider-direct-routing.md`

---

## 类别 E：实际删除文件清单（P4 执行）

### E.1 6 个核心 Python 文件

```bash
git rm octoagent/packages/skills/src/octoagent/skills/litellm_client.py
git rm octoagent/packages/skills/src/octoagent/skills/providers.py
git rm octoagent/packages/skills/src/octoagent/skills/compactor.py  # 兼容 shim
git rm octoagent/packages/provider/src/octoagent/provider/client.py
git rm octoagent/apps/gateway/src/octoagent/gateway/services/proxy_process_manager.py
git rm octoagent/apps/gateway/src/octoagent/gateway/services/config/litellm_generator.py
git rm octoagent/apps/gateway/src/octoagent/gateway/services/config/litellm_runtime.py
```

### E.2 部署文件

```bash
git rm octoagent/docker-compose.litellm.yml
```

### E.3 P3 已删除（在 P3 commit 中）

- `octoagent/packages/provider/src/octoagent/provider/dx/docker_daemon.py`

---

## P0 验收 checklist

- [x] 仓库 grep 一遍 LiteLLM 引用，13 个 Python import 点全部纳入类别 A
- [x] 5 个脚本/运维路径全部纳入类别 B
- [x] 测试和文档分别纳入类别 C / D
- [x] 6 个核心删除目标 + 部署文件纳入类别 E
- [x] 7 个文件（6 核心 + compactor shim + docker-compose）已加 deprecated 标记
- [x] `python -c "from octoagent.gateway.main import app"` 成功
- [x] `octo --help` / `octo config --help` 正常
