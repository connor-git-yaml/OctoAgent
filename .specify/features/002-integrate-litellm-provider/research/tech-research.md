# Feature 002 技术调研报告：LiteLLM Proxy 集成 + 成本治理

> **文档类型**: 技术调研报告（Tech Research）
> **Feature**: 002 - LiteLLM Proxy 集成 + 成本治理
> **日期**: 2026-02-28
> **模式**: [独立模式] 本次技术调研未参考产品调研结论，直接基于需求描述和代码上下文执行
> **前序制品**: constitution.md, blueprint.md (SS8.9), m1-feature-split.md

---

## 1. 执行摘要

本报告针对 Feature 002（LiteLLM Proxy 集成 + 成本治理）执行技术调研，覆盖以下核心问题：

1. **LiteLLM SDK/Proxy 技术分析** -- SDK v1.81.16 完全兼容 Python 3.12 + FastAPI async，支持 `acompletion` 异步调用、流式输出、公开 API 的成本计算、Router fallback/retry
2. **现有代码分析** -- M0 已预留良好的抽象层（`LLMProvider` ABC + `LLMService` alias 路由），改造成本低
3. **架构方案选型** -- 评估 3 个方案，**推荐方案 A：SDK 直连 Proxy（OpenAI-compatible 模式）**
4. **依赖库评估** -- 核心依赖 `litellm` + 可选 `httpx`（健康检查），与现有依赖无冲突
5. **技术风险** -- 识别 5 个技术风险，均有缓解策略

**关键结论**：LiteLLM SDK 通过 Proxy 的 OpenAI-compatible endpoint 调用是最优方案，兼顾简洁性、可维护性和 Constitution 合规性（C6 可降级、C8 可观测）。

---

## 2. LiteLLM SDK/Proxy 技术分析

### 2.1 LiteLLM SDK 概览

| 属性 | 值 |
|------|-----|
| 最新版本 | v1.81.16（2026-02） |
| Python 兼容 | >=3.9, <4.0（Python 3.12 完全兼容） |
| 许可证 | MIT |
| 维护活跃度 | 极活跃（GitHub 20k+ stars，日更新频率） |
| 核心能力 | 统一 100+ LLM provider 的 OpenAI 兼容接口 |

### 2.2 核心 API

#### 2.2.1 同步/异步调用

```python
# 同步
from litellm import completion
response = completion(model="gpt-4o", messages=[...])

# 异步（FastAPI 推荐）
from litellm import acompletion
response = await acompletion(model="gpt-4o", messages=[...])
```

- `acompletion()` 原生 async，与 FastAPI event loop 兼容，无需额外线程池
- 返回 `ModelResponse` 对象，包含 `choices`, `usage`，并可通过公开 API 计算成本（私有字段仅作兼容兜底）

#### 2.2.2 流式调用

```python
response = await acompletion(
    model="gpt-4o",
    messages=[...],
    stream=True
)
async for chunk in response:
    content = chunk.choices[0].delta.content or ""
```

- 异步生成器模式，支持 SSE 转发
- `litellm.stream_chunk_builder` 可重建完整响应
- 内置 `REPEATED_STREAMING_CHUNK_LIMIT` 防止无限循环

#### 2.2.3 成本计算

```python
from litellm import completion_cost

# 方式 1（推荐）：使用公开 API 计算
cost = completion_cost(completion_response=response)

# 方式 2（兼容兜底）：必要时读取私有字段
cost = response._hidden_params.get("response_cost", 0.0)

# 方式 3：手动计算
from litellm import cost_per_token
prompt_cost, completion_cost_val = cost_per_token(
    model="gpt-4o",
    prompt_tokens=100,
    completion_tokens=50
)
```

- `response.usage` 包含 `prompt_tokens`, `completion_tokens`, `total_tokens`
- `completion_cost()` 内置 pricing 数据库，覆盖主流 provider（主路径）
- 私有字段 `_hidden_params` 仅作为 SDK 兼容兜底，不作为主契约

#### 2.2.4 Model Alias（SDK 层）

```python
import litellm
litellm.model_alias_map = {
    "cheap": "gpt-4o-mini",
    "main": "gpt-4o",
}
response = await acompletion(model="cheap", messages=[...])
```

> **注意**：SDK 层 alias 适用于直连模式。通过 Proxy 时，alias 在 Proxy 配置中管理。

### 2.3 LiteLLM Proxy 技术分析

#### 2.3.1 部署方式

**Docker 部署（推荐）**：

```yaml
# docker-compose.litellm.yml
services:
  litellm:
    image: ghcr.io/berriai/litellm:main-latest
    ports:
      - "4000:4000"
    volumes:
      - ./litellm-config.yaml:/app/config.yaml
    command: ["--config", "/app/config.yaml", "--port", "4000"]
    environment:
      - LITELLM_MASTER_KEY=${LITELLM_MASTER_KEY:-sk-octoagent-dev}
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
```

- 单容器足够（无需 PostgreSQL/Redis，MVP 阶段不需要 key management）
- 内存占用约 200-400MB
- 启动时间 < 5s

#### 2.3.2 配置文件格式（litellm_config.yaml）

```yaml
model_list:
  # cheap alias -- 小模型，用于 router/summarizer
  - model_name: "cheap"
    litellm_params:
      model: "gpt-4o-mini"
      api_key: "os.environ/OPENAI_API_KEY"

  # main alias -- 大模型，用于 planner/executor
  - model_name: "main"
    litellm_params:
      model: "gpt-4o"
      api_key: "os.environ/OPENAI_API_KEY"

  # fallback -- 备选 provider
  - model_name: "fallback"
    litellm_params:
      model: "claude-3-5-haiku-20241022"
      api_key: "os.environ/ANTHROPIC_API_KEY"

litellm_settings:
  drop_params: true        # 忽略 provider 不支持的参数
  num_retries: 2           # 每个模型重试次数
  request_timeout: 60      # 请求超时（秒）

router_settings:
  routing_strategy: "simple-shuffle"  # MVP 不需要复杂路由
  fallbacks:
    - {"cheap": ["fallback"]}
    - {"main": ["fallback"]}

general_settings:
  master_key: "os.environ/LITELLM_MASTER_KEY"
```

**关键配置维度**：

| 配置块 | 职责 | MVP 需求 |
|--------|------|---------|
| `model_list` | 模型注册 + alias 定义 | cheap + main + fallback |
| `litellm_settings` | SDK 行为调优 | drop_params, retries, timeout |
| `router_settings` | 路由策略 + fallback | simple-shuffle + fallback chain |
| `general_settings` | Proxy 管理 | master_key |

#### 2.3.3 Model Group 与 Alias 机制

- 相同 `model_name` 的多个条目自动组成 model group（负载均衡）
- Proxy 对外暴露 `model_name` 作为 alias，客户端无需知道实际 provider
- 支持 `model_group_alias` 在 `router_settings` 中定义别名映射

**与 Blueprint SS8.9.1 / SS8.9.2 的映射**：

| 语义 alias（业务侧） | category | 运行时 group（Proxy `model_name`） | 实际模型（示例） |
|---------------------|----------|------------------------------------|-----------------|
| router | cheap | cheap | gpt-4o-mini |
| extractor | cheap | cheap | gpt-4o-mini |
| summarizer | cheap | cheap | gpt-4o-mini |
| planner | main | main | gpt-4o |
| executor | main | main | gpt-4o |
| fallback | fallback | fallback | claude-3.5-haiku |

> **M1 默认策略**：保留 Blueprint 定义的 6 个语义 alias，由 AliasRegistry 映射到 3 个运行时 group（cheap/main/fallback）；不是语义层的缩减。

#### 2.3.4 健康检查 API

| Endpoint | 方法 | 说明 | 用于 |
|----------|------|------|------|
| `/health/liveliness` | GET | 纯活性检查，永远返回 200 | liveness probe |
| `/health/readiness` | GET | 检查 DB/缓存连通性 | readiness probe |
| `/health` | GET | 检查所有配置模型的可达性 | `/ready?profile=llm` |
| `/v1/model/info` | GET | 返回配置的模型列表和详情 | 调试/可观测 |

**健康检查响应示例**：
```json
{
  "healthy_endpoints": [
    {"model": "gpt-4o-mini", "api_base": "https://api.openai.com"}
  ],
  "unhealthy_endpoints": []
}
```

#### 2.3.5 Fallback 与 Retry 机制

LiteLLM Router 内置三种 fallback 类型：

| Fallback 类型 | 触发条件 | 配置 |
|--------------|---------|------|
| General fallback | 任意错误（429/500/超时）重试耗尽后 | `router_settings.fallbacks` |
| Context window fallback | 输入超过模型 token 上限 | `router_settings.context_window_fallbacks` |
| Content policy fallback | 安全过滤器拒绝 | `router_settings.content_policy_fallbacks` |

**执行流程**：
1. 首次调用 primary model
2. 失败则在 primary model 上重试（`num_retries` 次）
3. 重试全部失败 -> 切换到 fallback model
4. fallback 也失败 -> 抛出异常

---

## 3. 现有代码分析（M0 LLM 服务架构）

### 3.1 当前架构总览

```
message.py (路由)
  └── TaskService.process_task_with_llm()
        └── LLMService.call(prompt, model_alias)
              └── LLMProvider.call(prompt)  # EchoProvider / MockProvider
                    └── LLMResponse(content, model_alias, duration_ms, token_usage)
```

### 3.2 关键抽象分析

#### LLMProvider（ABC）

```python
class LLMProvider(ABC):
    @abstractmethod
    async def call(self, prompt: str) -> LLMResponse:
        ...
```

**评估**：
- 接口过于简单：仅接受 `str` prompt，不支持 messages 格式、temperature 等参数
- M1 需要扩展为 messages-based 接口以对接 LiteLLM
- 但抽象层方向正确，M1 可在此基础上演进

#### LLMResponse（dataclass）

```python
@dataclass
class LLMResponse:
    content: str
    model_alias: str
    duration_ms: int
    token_usage: dict[str, int]
```

**评估**：
- 缺少 `cost` 字段（M1 关键需求）
- 缺少 `provider` 字段（可观测需求）
- 缺少 `model_name` 字段（区分 alias vs 实际模型）
- 建议升级为 Pydantic BaseModel 并扩展字段

#### LLMService（路由层）

```python
class LLMService:
    def __init__(self, default_provider=None):
        self._providers: dict[str, LLMProvider] = {}
        self._default = default_provider or EchoProvider()

    def register(self, alias: str, provider: LLMProvider) -> None: ...
    async def call(self, prompt: str, model_alias: str | None = None) -> LLMResponse: ...
```

**评估**：
- alias 路由逻辑简单但有效
- 可以直接注册 `LiteLLMProvider` 替换 `EchoProvider`
- `register()` 方法为 M1 集成提供了良好的扩展点

#### ModelCall 事件 Payload

```python
class ModelCallStartedPayload(BaseModel):
    model_alias: str
    request_summary: str
    artifact_ref: str | None = None

class ModelCallCompletedPayload(BaseModel):
    model_alias: str
    response_summary: str
    duration_ms: int
    token_usage: dict[str, int]
    artifact_ref: str | None = None
```

**评估**：
- `ModelCallCompletedPayload` 缺少 `cost`, `provider`, `model_name` 字段
- 需要扩展以满足 Blueprint SS8.9.2 的成本治理要求
- 扩展属于向后兼容变更（新增 optional 字段），不破坏现有事件

#### 健康检查（/ready）

```python
# 4. LiteLLM Proxy（M0 固定 skipped）
checks["litellm_proxy"] = "skipped"
```

**评估**：
- M0 已预留 `litellm_proxy` 检查项，仅需替换为真实检查逻辑
- 需要增加 `profile` 参数支持（`?profile=llm`）

### 3.3 初始化与依赖注入

```python
# main.py lifespan
app.state.llm_service = LLMService()  # Echo 模式

# message.py 路由
if hasattr(request.app.state, "llm_service"):
    asyncio.create_task(service.process_task_with_llm(...))
```

**评估**：
- 通过 `app.state` 管理 LLM 服务实例，初始化在 lifespan 中
- M1 需要在 lifespan 中根据配置创建 `LiteLLMProvider` 并注册
- 现有模式支持平滑切换，无需修改路由层代码

### 3.4 改造影响面评估

| 文件 | 改造类型 | 影响 |
|------|---------|------|
| `llm_service.py` | 新增 LiteLLMProvider | 低 -- 新增类，不改已有代码 |
| `main.py` | 修改 lifespan 初始化 | 低 -- 仅改 LLMService 创建逻辑 |
| `payloads.py` | 扩展 ModelCall payload | 低 -- 新增 optional 字段 |
| `config.py` | 新增 LiteLLM 配置项 | 低 -- 新增配置函数 |
| `health.py` | 扩展 profile 分级检查 | 低 -- `llm/full` 做真实检查，`core` 维持 skipped |
| `task_service.py` | 传递更多字段到事件 | 低 -- 使用 LLMResponse 扩展字段 |

**总结**：M0 预留了良好的扩展点，改造影响面小，主要是新增代码而非重写。

---

## 4. 架构方案选型

### 4.1 方案 A：SDK 直连 Proxy（OpenAI-compatible 模式） -- **推荐**

```
OctoAgent Gateway
  └── packages/provider
        └── LiteLLMClient
              └── litellm.acompletion(
                      model="cheap",           # alias
                      api_base="http://localhost:4000",  # Proxy endpoint
                      api_key="sk-..."          # Proxy master key
                  )
```

**实现方式**：使用 LiteLLM SDK 的 `acompletion()`，通过 `api_base` 指向本地 Proxy。Proxy 负责 alias 路由、fallback、成本统计。客户端只需知道 alias 名称。

**优点**：
- SDK 提供 `completion_cost()`、`response.usage` 等开箱即用的成本/usage 解析
- 天然支持 streaming（async generator）
- Proxy 层配置 alias/fallback/retry，业务代码无感
- SDK 自动处理 OpenAI 兼容格式转换
- 社区文档和示例丰富

**缺点**：
- 引入 `litellm` 作为直接依赖（包体较大，~50MB+依赖链）
- SDK 版本更新频繁，需要锁定版本

### 4.2 方案 B：纯 HTTP 直连 Proxy（httpx 模式）

```
OctoAgent Gateway
  └── packages/provider
        └── LiteLLMClient
              └── httpx.AsyncClient.post(
                      "http://localhost:4000/v1/chat/completions",
                      json={"model": "cheap", "messages": [...]}
                  )
```

**实现方式**：不依赖 LiteLLM SDK，直接通过 httpx 向 Proxy 发送 OpenAI-compatible HTTP 请求。

**优点**：
- 零额外依赖（httpx 已是 FastAPI 间接依赖）
- 完全解耦 SDK 版本
- 包体极小

**缺点**：
- 需手动实现成本计算（Proxy 返回 usage，但 cost 需要自维护 pricing 表或依赖 Proxy 的 spend tracking）
- 需手动处理 streaming（SSE 解析）
- 需手动处理错误分类和重试逻辑
- 需手动构建 OpenAI-compatible request/response 解析
- 开发量显著增加

### 4.3 方案 C：SDK 直连模式（无 Proxy）

```
OctoAgent Gateway
  └── packages/provider
        └── LiteLLMClient
              └── litellm.acompletion(model="gpt-4o", ...)
                  # 直接调用 provider API，无 Proxy 中间层
```

**实现方式**：使用 LiteLLM SDK 直接调用 provider API，在应用层配置 Router 实现 fallback。

**优点**：
- 减少一跳网络延迟（~1-5ms）
- 无需部署/维护 Proxy 容器
- 配置更集中

**缺点**：
- 违反 Blueprint 设计（SS8.9 明确要求 LiteLLM Proxy）
- alias/fallback 配置散落在应用代码中，不符合关注点分离
- 多应用场景下需要在每个进程中重复配置
- 密钥管理分散，违反 Constitution C5（Least Privilege）
- 无法利用 Proxy 的统一 cost dashboard、key management 等治理能力

### 4.4 方案对比矩阵

| 维度 | 方案 A（SDK + Proxy） | 方案 B（HTTP + Proxy） | 方案 C（SDK 直连） |
|------|---------------------|---------------------|--------------------|
| **与 Blueprint 对齐** | 完全对齐 | 对齐（Proxy 层） | 偏离（无 Proxy） |
| **开发效率** | 高 -- SDK 开箱即用 | 低 -- 手动实现多项 | 中 |
| **成本追踪** | 内置 completion_cost() | 需自实现 pricing | 内置 |
| **流式支持** | 原生 async generator | 需手动 SSE 解析 | 原生 |
| **依赖包体** | ~50MB+ | ~0（httpx 已有） | ~50MB+ |
| **可维护性** | 高 -- 社区维护 | 中 -- 自维护 | 中 |
| **密钥管理** | Proxy 统一管理 | Proxy 统一管理 | 应用层分散 |
| **网络延迟** | +1-5ms（本地 Proxy） | +1-5ms | 无额外 |
| **降级能力** | Proxy fallback + 应用层 Echo | Proxy fallback | 应用层 Router |
| **Constitution C6** | 符合 -- Proxy 不可达降 Echo | 符合 | 部分符合 |
| **M2+ 扩展性** | 多 Worker 共享 Proxy | 多 Worker 共享 Proxy | 每个 Worker 独立配置 |

### 4.5 推荐方案

**推荐方案 A：SDK 直连 Proxy（OpenAI-compatible 模式）**

理由：
1. **完全对齐 Blueprint SS8.9**：LiteLLM Proxy 作为统一模型出口，业务代码不写死厂商模型名
2. **开发效率最高**：`acompletion()` + `completion_cost()` 开箱即用，省去大量样板代码
3. **Constitution 合规**：
   - C2（Everything is an Event）-- SDK 返回完整的 usage/cost 数据，直接写入事件
   - C5（Least Privilege）-- API keys 仅在 Proxy 配置中，不进应用层
   - C6（Degrade Gracefully）-- Proxy 不可达时降级到 EchoProvider
   - C8（Observability）-- 完整的 model_alias/provider/cost/tokens 可观测数据
4. **M2+ 友好**：多 Worker 共享同一个 Proxy，配置统一

---

## 5. 接口设计草案

### 5.1 packages/provider 包结构

```
packages/provider/
  pyproject.toml
  src/octoagent/provider/
    __init__.py            # 公开接口导出
    client.py              # LiteLLMClient -- Proxy 调用封装
    alias.py               # AliasRegistry -- alias 配置管理
    cost.py                # CostTracker -- 成本解析/聚合
    fallback.py            # FallbackManager -- 应用层降级策略
    models.py              # ModelCallResult -- 调用结果数据模型
    config.py              # Provider 配置（环境变量/YAML）
    exceptions.py          # Provider 异常体系
```

### 5.2 核心接口草案

#### 5.2.1 ModelCallResult（扩展 LLMResponse）

```python
from pydantic import BaseModel, Field

class ModelCallResult(BaseModel):
    """LLM 调用结果 -- 扩展 M0 的 LLMResponse，增加成本/provider 字段"""

    content: str = Field(description="响应文本内容")
    model_alias: str = Field(description="请求时使用的 alias")
    model_name: str = Field(description="实际调用的模型名称")
    provider: str = Field(default="", description="实际 provider（openai/anthropic/...）")
    duration_ms: int = Field(description="端到端耗时（毫秒）")
    token_usage: TokenUsage = Field(description="Token 使用详情")
    cost_usd: float = Field(default=0.0, description="本次调用的 USD 成本")
    is_fallback: bool = Field(default=False, description="是否使用了 fallback")

class TokenUsage(BaseModel):
    """Token 使用统计"""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
```

#### 5.2.2 LiteLLMClient

```python
from typing import Protocol

class LLMClientProtocol(Protocol):
    """LLM 客户端协议 -- 支持多种实现（LiteLLM, Echo, Mock）"""

    async def complete(
        self,
        messages: list[dict[str, str]],
        model_alias: str = "main",
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs,
    ) -> ModelCallResult:
        """发送 chat completion 请求"""
        ...

    async def health_check(self) -> bool:
        """检查 LLM 服务可达性"""
        ...

class LiteLLMClient:
    """LiteLLM Proxy 客户端 -- 通过 SDK acompletion 调用 Proxy"""

    def __init__(
        self,
        proxy_base_url: str = "http://localhost:4000",
        proxy_api_key: str = "",
        default_alias: str = "main",
        timeout_s: int = 60,
    ) -> None: ...

    async def complete(
        self,
        messages: list[dict[str, str]],
        model_alias: str = "main",
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs,
    ) -> ModelCallResult:
        """
        调用 LiteLLM Proxy：
        1. litellm.acompletion(model=alias, api_base=proxy_url, ...)
        2. 解析 response.usage -> TokenUsage
        3. 优先通过公开 API 计算 cost，必要时走兼容兜底
        4. 构建 ModelCallResult（含 cost_unavailable）
        """
        ...

    async def health_check(self) -> bool:
        """GET {proxy_base_url}/health/liveliness"""
        ...
```

#### 5.2.3 AliasRegistry

```python
class AliasConfig(BaseModel):
    """单个 alias 配置"""
    name: str
    description: str = ""
    category: str = "main"  # cheap / main / fallback
    runtime_group: str = "main"  # cheap / main / fallback

class AliasRegistry:
    """Alias 注册表 -- 管理语义 alias 到 category/runtime group 的映射

    M1 MVP：从环境变量或 YAML 加载静态配置。
    M2+：可扩展为从 Proxy /model/info 动态获取。
    """

    def __init__(self, aliases: list[AliasConfig] | None = None) -> None: ...

    def get_alias(self, alias: str) -> AliasConfig | None: ...
    def get_aliases_by_category(self, category: str) -> list[AliasConfig]: ...
    def get_aliases_by_runtime_group(self, runtime_group: str) -> list[AliasConfig]: ...
    def list_all(self) -> list[AliasConfig]: ...
```

#### 5.2.4 CostTracker

```python
class CostTracker:
    """成本追踪器 -- 实时计算 + 事后查询

    实时：每次调用后计算 cost，写入 ModelCallResult。
    事后：通过 EventStore 聚合 MODEL_CALL_COMPLETED 事件的 cost 字段。

    M1 MVP：仅实现实时计算。事后查询依赖 EventStore 已有的聚合能力。
    """

    @staticmethod
    def calculate_cost(response) -> tuple[float, bool]:
        """从 LiteLLM response 计算 USD 成本

        优先使用公开 API（如 completion_cost）；
        必要时读取私有字段 `_hidden_params["response_cost"]` 作为兼容兜底。
        返回值：(cost_usd, cost_unavailable)。
        """
        ...

    @staticmethod
    def parse_usage(response) -> TokenUsage:
        """从 LiteLLM response 解析 token usage"""
        ...
```

#### 5.2.5 FallbackManager

```python
class FallbackStrategy(StrEnum):
    """降级策略"""
    ECHO = "echo"        # 降级到 EchoProvider（测试/演示）
    RETRY = "retry"      # 重试当前 alias
    SWITCH = "switch"    # 切换到备选 alias

class FallbackManager:
    """应用层降级管理器

    注意：LiteLLM Proxy 已内置 model-level fallback。
    本组件处理的是 Proxy 本身不可达时的降级策略。

    降级链：LiteLLM Proxy（内置 model fallback）-> Proxy 不可达 -> EchoProvider
    """

    def __init__(
        self,
        primary_client: LiteLLMClient,
        fallback_client: LLMClientProtocol | None = None,  # 默认 EchoMessageAdapter
    ) -> None: ...

    async def call_with_fallback(
        self,
        messages: list[dict[str, str]],
        model_alias: str = "main",
        **kwargs,
    ) -> ModelCallResult:
        """
        尝试 primary -> 失败时降级到 fallback。
        降级时 ModelCallResult.is_fallback = True。
        """
        ...
```

### 5.3 改造后的 LLMService

```python
class LLMService:
    """LLM 服务 -- M1 版本

    变更：
    - 默认 provider 从 EchoProvider 切换到 FallbackManager（LiteLLM + Echo 降级）
    - call() 接口从 prompt: str 扩展为 messages: list[dict]
    - 保留向后兼容的 prompt 模式（自动转为 messages 格式）
    """

    def __init__(
        self,
        fallback_manager: FallbackManager,
        alias_registry: AliasRegistry,
    ) -> None: ...

    async def call(
        self,
        messages: list[dict[str, str]] | str,
        model_alias: str | None = None,
    ) -> ModelCallResult: ...
```

### 5.4 扩展 ModelCallCompletedPayload

```python
class ModelCallCompletedPayload(BaseModel):
    """MODEL_CALL_COMPLETED 事件 payload -- M1 扩展"""

    model_alias: str
    model_name: str = Field(default="", description="实际调用的模型名称")
    provider: str = Field(default="", description="实际 provider")
    response_summary: str
    duration_ms: int
    token_usage: dict[str, int]
    cost_usd: float = Field(default=0.0, description="USD 成本")
    cost_unavailable: bool = Field(default=False, description="成本是否不可用")
    is_fallback: bool = Field(default=False, description="是否降级调用")
    artifact_ref: str | None = None
```

> **向后兼容**：所有新增字段均有默认值，M0 的旧事件在反序列化时不会报错。

### 5.5 扩展 /ready 检查

```python
@router.get("/ready")
async def ready(request: Request, profile: str | None = None):
    """Readiness 检查

    profile 参数：
    - None / "core"：仅检查 SQLite + artifacts_dir + disk
    - "llm"：额外检查 LiteLLM Proxy 可达性
    """
    checks = {}
    all_ok = True

    # ... 现有检查 ...

    resolved_profile = profile or "core"

    # LiteLLM Proxy 检查（仅 llm/full 时执行）
    if resolved_profile in ("llm", "full"):
        try:
            litellm_client = request.app.state.litellm_client
            is_healthy = await litellm_client.health_check()
            checks["litellm_proxy"] = "ok" if is_healthy else "unreachable"
            if not is_healthy:
                all_ok = False
        except Exception as e:
            checks["litellm_proxy"] = f"error: {str(e)}"
            all_ok = False
    else:
        checks["litellm_proxy"] = "skipped"
```

---

## 6. 依赖库评估

### 6.1 核心依赖

| 库 | 版本 | 许可证 | 用途 | 兼容性 |
|----|------|-------|------|--------|
| `litellm` | >=1.80,<2.0 | MIT | LLM SDK：acompletion + completion_cost | Python 3.12 兼容，FastAPI async 兼容 |
| `httpx` | >=0.27 | BSD-3 | 健康检查 HTTP 客户端 | 已是 dev 依赖，无冲突 |

### 6.2 litellm 包评估

| 维度 | 评估 |
|------|------|
| **版本稳定性** | v1.x 已稳定，主版本号未变更；更新频率高但向后兼容性好 |
| **维护活跃度** | 极活跃 -- GitHub 20k+ stars，核心团队持续维护，日更新 |
| **许可证兼容性** | MIT -- 与项目 MIT 许可证完全兼容 |
| **下载量** | PyPI 月下载量 > 1M |
| **依赖链** | 较重（openai, tiktoken, tokenizers, httpx 等），~50MB+ 安装体积 |
| **Python 3.12** | 完全兼容（requires-python >=3.9,<4.0） |
| **FastAPI 兼容** | 官方支持，提供 `litellm[proxy]` extras 含 FastAPI |
| **Pydantic 2.x** | 兼容 Pydantic v2（与项目 core 包 pydantic>=2.10 无冲突） |

### 6.3 与现有依赖的兼容性

| 现有依赖 | litellm 依赖 | 冲突风险 |
|----------|-------------|---------|
| pydantic>=2.10 | pydantic（兼容 v2） | 无 |
| httpx>=0.27 | httpx | 无 |
| structlog>=25.1 | 无直接依赖 | 无 |
| aiosqlite>=0.21 | 无直接依赖 | 无 |
| fastapi>=0.115 | fastapi（proxy extras） | 无冲突（仅 SDK 使用不需要 proxy extras） |

**结论**：`litellm` 是唯一需要新增的核心依赖，与现有依赖无冲突。

### 6.4 依赖安装策略

```toml
# packages/provider/pyproject.toml
[project]
name = "octoagent-provider"
dependencies = [
    "litellm>=1.80,<2.0",      # LLM SDK
    "httpx>=0.27,<1.0",         # 健康检查
    "pydantic>=2.10,<3.0",      # 数据模型
    "structlog>=25.1,<26.0",    # 结构化日志
    "octoagent-core",           # 共享模型
]
```

---

## 7. 设计模式调研

### 7.1 Strategy 模式（Provider 切换）

**应用场景**：`LLMProvider` 抽象 + 多种实现（LiteLLM, Echo, Mock）

```
LLMClientProtocol (Protocol)
    ├── LiteLLMClient       # 生产环境
    ├── EchoProvider         # 测试/降级
    └── MockProvider         # 单元测试
```

**适用性**：M0 已采用此模式（`LLMProvider` ABC），M1 沿用并增加 `LiteLLMClient` 实现。
**风险**：无 -- 已验证的模式。

### 7.2 Chain of Responsibility 模式（Fallback 链）

**应用场景**：FallbackManager 的降级策略

```
Request -> LiteLLMClient (via Proxy)
              ├── 成功 -> 返回结果
              └── 失败 -> EchoProvider
                            └── 返回降级结果（is_fallback=True）
```

**适用性**：两级 fallback 足够（Proxy 内置 model fallback + 应用层 Proxy fallback），不需要复杂链。
**风险**：低 -- 链路短，逻辑清晰。

### 7.3 Registry 模式（Alias 管理）

**应用场景**：AliasRegistry 管理 alias 配置

**适用性**：MVP 使用静态配置（环境变量/YAML），后续可扩展为动态注册。
**风险**：低 -- 配置变更频率低。

### 7.4 Adapter 模式（LLMResponse 适配）

**应用场景**：将 LiteLLM SDK 的 `ModelResponse` 适配为项目内部的 `ModelCallResult`

```
litellm.ModelResponse -> CostTracker.parse() -> ModelCallResult
```

**适用性**：隔离 SDK 内部类型，避免 SDK 升级时影响业务层。
**风险**：低 -- 适配逻辑集中在 `LiteLLMClient` 内部。

### 7.5 模式选型总结

| 模式 | 用于 | 推荐度 |
|------|------|--------|
| Strategy | Provider 切换 | 必须 -- 已有基础 |
| Chain of Responsibility | Fallback 链 | 推荐 -- 简化为两级 |
| Registry | Alias 管理 | 推荐 -- MVP 静态，M2+ 动态 |
| Adapter | SDK 响应适配 | 推荐 -- 隔离第三方类型 |

---

## 8. 技术风险与缓解

### 8.1 风险清单

| # | 风险 | 概率 | 影响 | 缓解策略 |
|---|------|------|------|---------|
| R1 | LiteLLM SDK 版本更新导致 breaking change | 中 | 高 | 锁定版本范围 `>=1.80,<2.0`；Adapter 模式隔离 SDK 类型；CI 定期运行兼容性测试 |
| R2 | LiteLLM Proxy 不可达（容器崩溃/网络问题） | 中 | 高 | FallbackManager 自动降级到 EchoProvider；`/ready` 检查暴露状态；Blueprint SS8.9 要求的冷却机制 |
| R3 | `completion_cost()` 返回不准确（新模型未及时更新 pricing） | 低 | 中 | 公开 API 为主路径；私有字段 `_hidden_params["response_cost"]` 仅兼容兜底；记录 raw usage 以便事后修正 |
| R4 | litellm 依赖包体过大影响部署 | 低 | 低 | 仅安装 litellm 核心（不装 proxy extras）；Docker 镜像层缓存；考虑 `litellm[proxy]` 与 `litellm` 的区分 |
| R5 | async event loop 阻塞（litellm SDK 内部同步调用） | 低 | 高 | 使用 `acompletion()`（原生 async）；监控 event loop lag；设置 `request_timeout` |

### 8.2 Proxy 模式 vs SDK 直连模式 Tradeoff 分析

| 维度 | Proxy 模式 | SDK 直连模式 |
|------|-----------|-------------|
| 延迟 | +1-5ms（本地环回） | 无额外延迟 |
| 部署复杂度 | 需额外容器 | 无额外容器 |
| 密钥管理 | 集中在 Proxy | 分散在应用 |
| alias 配置 | Proxy YAML（独立于应用） | 应用代码/配置 |
| fallback | Proxy + 应用双层 | 仅应用层 |
| 多 Worker 共享 | 天然共享 | 需每个 Worker 独立配置 |
| 可观测 | Proxy dashboard + 应用事件 | 仅应用事件 |

**结论**：对于 OctoAgent 的单用户场景，Proxy 模式的额外 1-5ms 延迟可忽略，但带来的治理收益（密钥集中、配置独立、多 Worker 共享）是显著的。

### 8.3 测试策略分析

| 策略 | 适用场景 | 优点 | 缺点 |
|------|---------|------|------|
| **Mock Provider** | 单元测试 | 快速、确定性、无网络依赖 | 不验证真实 API 行为 |
| **EchoProvider** | 集成测试（无 LLM） | 验证完整事件链，无外部依赖 | 不验证真实 LLM 响应 |
| **VCR 录制回放** | 集成测试（模拟真实 LLM） | 接近真实、可重放、无网络依赖 | 录制需真实 API key；响应固化 |
| **真实 Proxy** | 端到端测试 | 完全真实 | 需要 API key、成本、不确定性 |

**推荐分层策略**：

1. **单元测试**（`packages/provider/tests/`）：MockProvider + 纯函数测试（CostTracker, AliasRegistry）
2. **集成测试**（`tests/integration/`）：EchoProvider 验证事件链完整性
3. **Contract 测试**：验证 `ModelCallResult` 字段完整性、Payload 扩展向后兼容性
4. **VCR 测试**（可选）：pytest-recording + vcrpy 录制真实 Proxy 请求，CI 回放
5. **端到端测试**（手动/CI optional）：真实 Proxy + 真实 API key

---

## 9. 需求-技术对齐度评估

### 9.1 功能覆盖检查

| 需求交付项 | 技术方案覆盖 | 说明 |
|-----------|-------------|------|
| 接入 LiteLLM Proxy + 语义 alias 映射 | 方案 A：SDK + Proxy + 6 个语义 alias 映射到 cheap/main/fallback 运行时 group | 完全覆盖 |
| packages/provider 包（4 个组件） | LiteLLMClient + AliasRegistry + CostTracker + FallbackManager | 完全覆盖 |
| EchoProvider -> LiteLLMProvider 改造 | LiteLLMClient 实现 LLMClientProtocol + FallbackManager 包装 | 完全覆盖 |
| MODEL_CALL 事件写入真实数据 | ModelCallCompletedPayload 扩展 cost/tokens/provider/cost_unavailable 字段 | 完全覆盖 |
| `/ready` 增加 llm profile | health.py 扩展 profile 参数 + LiteLLMClient.health_check() | 完全覆盖 |

### 9.2 Constitution 合规检查

| 宪法原则 | 合规状态 | 说明 |
|----------|---------|------|
| C1: Durability First | 符合 | MODEL_CALL_STARTED/COMPLETED/FAILED 事件持久化到 EventStore |
| C2: Everything is an Event | 符合 | 每次调用生成 STARTED/COMPLETED/FAILED 事件 |
| C5: Least Privilege | 符合 | API keys 仅在 Proxy，不进应用层 |
| C6: Degrade Gracefully | 符合 | Proxy 不可达自动降级到 EchoProvider |
| C8: Observability | 符合 | model_alias/provider/cost/tokens/latency 完整记录 |

### 9.3 扩展性评估

| 扩展方向 | 技术方案支持度 | 风险 |
|---------|-------------|------|
| M1 Feature 003（ToolBroker） | cheap alias 可用于 summarizer | 无 |
| M1 Feature 004（Skill Runner） | LiteLLMClient 支持 messages + temperature | 无 |
| M2 多 Worker | 多 Worker 共享同一 Proxy | 无 |
| 新增 provider | Proxy YAML 新增 model_list 条目 | 无 |
| 细分 alias（6 个） | AliasRegistry 支持动态扩展 | 低 |
| 预算控制 | CostTracker 可扩展 per-task budget | 中 -- 需 EventStore 聚合查询 |

---

## 10. 附录

### 10.1 LiteLLM Proxy Docker Compose 参考

```yaml
# docker-compose.litellm.yml
version: "3.8"
services:
  litellm:
    image: ghcr.io/berriai/litellm:main-latest
    container_name: octoagent-litellm
    restart: unless-stopped
    ports:
      - "4000:4000"
    volumes:
      - ./litellm-config.yaml:/app/config.yaml
    command: ["--config", "/app/config.yaml", "--port", "4000"]
    environment:
      - LITELLM_MASTER_KEY=${LITELLM_MASTER_KEY:-sk-octoagent-dev}
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:4000/health/liveliness"]
      interval: 30s
      timeout: 10s
      retries: 3
```

### 10.2 环境变量清单

| 变量 | 说明 | 默认值 | 必须 |
|------|------|--------|------|
| `LITELLM_PROXY_URL` | Proxy 地址 | `http://localhost:4000` | 否 |
| `LITELLM_PROXY_KEY` | Proxy Master Key | `sk-octoagent-dev` | 否 |
| `LITELLM_MASTER_KEY` | Proxy 管理密钥（Proxy 侧） | - | 是（Proxy） |
| `OPENAI_API_KEY` | OpenAI API Key（Proxy 侧） | - | 是（Proxy） |
| `ANTHROPIC_API_KEY` | Anthropic API Key（Proxy 侧） | - | 否（fallback） |
| `OCTOAGENT_LLM_MODE` | LLM 模式：litellm / echo / mock | `litellm` | 否 |

### 10.3 关键文献与参考

- LiteLLM 官方文档：https://docs.litellm.ai
- LiteLLM GitHub：https://github.com/BerriAI/litellm
- LiteLLM Proxy 部署指南：https://docs.litellm.ai/docs/proxy/deploy
- LiteLLM Router/Fallback：https://docs.litellm.ai/docs/routing-load-balancing
- LiteLLM 成本追踪：https://docs.litellm.ai/docs/completion/token_usage
- Blueprint SS8.9：Provider Plane 设计
- Blueprint SS9.10：packages/provider 职责定义
