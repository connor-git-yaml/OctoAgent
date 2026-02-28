# Gateway 改造契约

**特性**: 002-integrate-litellm-provider
**日期**: 2026-02-28
**包**: `apps/gateway` (`octoagent-gateway`)
**追踪**: FR-002-LS-1 ~ LS-3, FR-002-HC-1 ~ HC-2, FR-002-EP-1 ~ EP-2

---

## 1. 改造总览

Gateway 层的改造遵循"最小侵入"原则：新增 provider 包依赖，修改初始化逻辑和事件构建逻辑，不改变路由层和 SSE 层。

| 文件 | 改造类型 | 说明 |
|------|---------|------|
| `main.py` | 修改 | lifespan 初始化逻辑：根据 LLM_MODE 创建对应的 LLMService |
| `services/llm_service.py` | 修改 | LLMService.call() 支持 messages 格式；保留向后兼容 |
| `services/task_service.py` | 修改 | 使用 ModelCallResult 新字段构建 Event payload |
| `routes/health.py` | 修改 | /ready 支持 profile 参数 |
| `deps.py` | 新增/修改 | 依赖注入辅助（如需要） |

---

## 2. main.py lifespan 改造

### 2.1 当前实现（M0）

```python
app.state.llm_service = LLMService()  # Echo 模式
```

### 2.2 改造后

```python
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # ... 现有 Store 初始化 ...

    # LLM 服务初始化（根据配置选择模式）
    provider_config = load_provider_config()

    if provider_config.llm_mode == "litellm":
        # LiteLLM 模式：LiteLLMClient + FallbackManager
        litellm_client = LiteLLMClient(
            proxy_base_url=provider_config.proxy_base_url,
            proxy_api_key=provider_config.proxy_api_key,
            timeout_s=provider_config.timeout_s,
        )
        echo_adapter = EchoMessageAdapter()
        fallback_manager = FallbackManager(
            primary=litellm_client,
            fallback=echo_adapter,
        )
        alias_registry = AliasRegistry()  # 使用 MVP 默认配置
        llm_service = LLMService(
            fallback_manager=fallback_manager,
            alias_registry=alias_registry,
        )
        # 保存 litellm_client 引用供健康检查使用
        app.state.litellm_client = litellm_client
    else:
        # Echo 模式：与 M0 行为一致
        echo_adapter = EchoMessageAdapter()
        fallback_manager = FallbackManager(
            primary=echo_adapter,  # Echo 作为 primary（无真实 Proxy）
            fallback=None,
        )
        alias_registry = AliasRegistry()
        llm_service = LLMService(
            fallback_manager=fallback_manager,
            alias_registry=alias_registry,
        )
        app.state.litellm_client = None

    app.state.llm_service = llm_service
    app.state.alias_registry = alias_registry
    app.state.provider_config = provider_config

    yield

    # ... 现有清理逻辑 ...
```

---

## 3. LLMService 改造

### 3.1 接口变更

```python
class LLMService:
    """LLM 服务 -- Feature 002 版本

    变更:
    - 构造器接受 FallbackManager + AliasRegistry（替代直接持有 providers dict）
    - call() 支持 messages 格式和 prompt 字符串（向后兼容）
    - 返回 ModelCallResult（替代 LLMResponse）
    """

    def __init__(
        self,
        fallback_manager: FallbackManager,
        alias_registry: AliasRegistry,
    ) -> None:
        """初始化 LLM 服务

        Args:
            fallback_manager: 包含 primary + fallback 的降级管理器
            alias_registry: 语义 alias 注册表
        """

    async def call(
        self,
        prompt_or_messages: str | list[dict[str, str]],
        model_alias: str | None = None,
    ) -> ModelCallResult:
        """调用 LLM

        Args:
            prompt_or_messages:
                - str: 纯文本 prompt（M0 兼容，自动转为 messages 格式）
                - list[dict]: messages 格式（Feature 002 推荐）
            model_alias:
                - 语义 alias（如 "planner"）-> AliasRegistry 解析为运行时 group
                - 运行时 group（如 "main"）-> 直接透传
                - None -> 使用 "main" 默认

        Returns:
            ModelCallResult

        向后兼容:
            - prompt: str 自动转为 [{"role": "user", "content": prompt}]
            - model_alias=None 使用 "main"
        """
```

### 3.2 向后兼容保证

- `LLMService.call("hello")` 仍然工作（自动转为 messages 格式）
- 返回 `ModelCallResult`（LLMResponse 的超集，包含所有旧字段）
- `EchoProvider` 和 `MockProvider` 保留在 `llm_service.py` 中但标记废弃

---

## 4. TaskService 改造

### 4.1 process_task_with_llm() 变更

主要变更点：使用 `ModelCallResult` 的新字段构建 `ModelCallCompletedPayload`。

```python
async def process_task_with_llm(
    self,
    task_id: str,
    user_text: str,
    llm_service: LLMService,
) -> None:
    # ... 现有流程不变 ...

    # 3. LLM 调用（变更：可使用 messages 格式）
    llm_result: ModelCallResult = await llm_service.call(user_text)

    # 5. MODEL_CALL_COMPLETED 事件（变更：填充新字段）
    completed_payload = ModelCallCompletedPayload(
        model_alias=llm_result.model_alias,
        model_name=llm_result.model_name,          # 新增
        provider=llm_result.provider,                # 新增
        response_summary=llm_result.content[:8192],  # 对齐 8KB 截断阈值
        duration_ms=llm_result.duration_ms,
        token_usage={
            "prompt_tokens": llm_result.token_usage.prompt_tokens,
            "completion_tokens": llm_result.token_usage.completion_tokens,
            "total_tokens": llm_result.token_usage.total_tokens,
        },
        cost_usd=llm_result.cost_usd,               # 新增
        cost_unavailable=llm_result.cost_unavailable, # 新增
        is_fallback=llm_result.is_fallback,           # 新增
        artifact_ref=artifact_id,
    )
```

### 4.2 MODEL_CALL_STARTED 事件变更

```python
# 变更：model_alias 从 ModelCallResult 动态获取而非硬编码 "echo"
started_payload = ModelCallStartedPayload(
    model_alias=model_alias or "main",  # 使用传入的 alias
    request_summary=request_summary,
)
```

### 4.3 MODEL_CALL_FAILED 事件变更

```python
# 变更：填充新字段
failed_payload = ModelCallFailedPayload(
    model_alias=model_alias or "main",
    model_name="",         # 新增（失败时可能未知）
    provider="",           # 新增（失败时可能未知）
    error_type="model",
    error_message=str(e),
    duration_ms=elapsed_ms,
    is_fallback=False,     # 新增
)
```

---

## 5. /ready 端点改造

### 5.1 接口变更

```python
@router.get("/ready")
async def ready(request: Request, profile: str | None = None):
    """Readiness 检查 -- Feature 002 扩展

    新增 profile 查询参数:
        - None / "core": 仅核心检查（M0 行为），litellm_proxy="skipped"
        - "llm": 核心检查 + LiteLLM Proxy 真实健康检查
        - "full": 等同于 "llm"（未来可包含更多检查）

    响应格式:
        {
            "status": "ready" | "not_ready",
            "profile": "core" | "llm" | "full",
            "checks": {
                "sqlite": "ok" | "error: ...",
                "artifacts_dir": "ok" | "error: ...",
                "disk_space_mb": int,
                "litellm_proxy": "ok" | "unreachable" | "skipped" | "error: ..."
            }
        }

    HTTP 状态码:
        - 200: 所有检查通过
        - 503: 至少一项检查失败

    行为规则:
        - profile=core 时，litellm_proxy 永远返回 "skipped"
        - profile=llm/full 时，通过 LiteLLMClient.health_check() 真实检查
        - Echo 模式下（无 litellm_client），litellm_proxy 返回 "skipped"
        - Proxy 不可达不影响 profile=core 的整体 ready 判定
    """
```

---

## 6. 依赖变更

### 6.1 apps/gateway/pyproject.toml

```toml
[project]
dependencies = [
    # 现有依赖...
    "octoagent-core",
    "octoagent-provider",  # 新增
]
```

### 6.2 根 pyproject.toml

```toml
[tool.uv.workspace]
members = [
    "packages/core",
    "packages/provider",  # 新增
    "apps/gateway",
]
```
