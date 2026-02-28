# Feature 002 快速上手指南

**特性**: 002-integrate-litellm-provider
**日期**: 2026-02-28
**目标**: 从零到"看到真实 LLM 响应" < 15 分钟

---

## 前置条件

- Docker Desktop 已安装并运行
- Python 3.12+
- uv 包管理器
- 至少 1 个 LLM provider API key（OpenAI 或 Anthropic）

---

## 快速启动（3 步）

### 步骤 1: 配置 API Keys（2 分钟）

```bash
cd octoagent

# 创建 LiteLLM 专用 secrets 文件
cp .env.litellm.example .env.litellm

# 编辑 .env.litellm，填入你的 API key
# 至少需要 OPENAI_API_KEY 或 ANTHROPIC_API_KEY
```

`.env.litellm` 内容示例：
```bash
OPENAI_API_KEY=sk-your-openai-key-here
# ANTHROPIC_API_KEY=sk-ant-your-key-here  # 可选，用于 fallback
```

### 步骤 2: 启动 LiteLLM Proxy（3 分钟）

```bash
# 启动 Proxy 容器
docker compose -f docker-compose.litellm.yml up -d

# 验证 Proxy 健康
curl http://localhost:4000/health/liveliness
# 预期输出: "I'm alive!"
```

### 步骤 3: 启动 OctoAgent 并测试（5 分钟）

```bash
# 安装依赖（含 packages/provider）
uv sync

# 确保 LLM 模式为 litellm（默认值）
export OCTOAGENT_LLM_MODE=litellm
export LITELLM_PROXY_URL=http://localhost:4000
export LITELLM_PROXY_KEY=sk-octoagent-dev

# 启动 OctoAgent
uv run uvicorn octoagent.gateway.main:app --reload

# 发送测试消息
curl -X POST http://localhost:8000/api/message \
  -H "Content-Type: application/json" \
  -d '{"text": "请用一句话介绍 Python", "channel": "api", "sender_id": "test"}'

# 查看任务详情（替换 {task_id} 为返回的 ID）
curl http://localhost:8000/api/tasks/{task_id}
```

验证成功的标志：
- 响应内容是 LLM 生成的关于 Python 的介绍（非 "Echo: ..." 回声）
- EVENT 列表中 MODEL_CALL_COMPLETED 包含 `cost_usd`、`model_name`、`provider` 字段

---

## 验证清单

### 基础功能验证

```bash
# 1. 真实 LLM 响应（非 Echo）
curl -X POST http://localhost:8000/api/message \
  -H "Content-Type: application/json" \
  -d '{"text": "什么是 FastAPI？", "channel": "api", "sender_id": "test"}'
# 期望: 返回 LLM 生成的关于 FastAPI 的回答

# 2. 健康检查（LLM profile）
curl "http://localhost:8000/ready?profile=llm"
# 期望: {"status": "ready", "profile": "llm", "checks": {"litellm_proxy": "ok", ...}}

# 3. 健康检查（core profile -- M0 兼容）
curl "http://localhost:8000/ready"
# 期望: {"status": "ready", "profile": "core", "checks": {"litellm_proxy": "skipped", ...}}
```

### 降级验证

```bash
# 1. 停止 Proxy
docker compose -f docker-compose.litellm.yml down

# 2. 发送消息（应降级到 Echo）
curl -X POST http://localhost:8000/api/message \
  -H "Content-Type: application/json" \
  -d '{"text": "测试降级", "channel": "api", "sender_id": "test"}'
# 期望: 返回 "Echo: 测试降级"
# 期望: 事件中 is_fallback=true

# 3. 重启 Proxy
docker compose -f docker-compose.litellm.yml up -d

# 4. 发送消息（应自动恢复）
curl -X POST http://localhost:8000/api/message \
  -H "Content-Type: application/json" \
  -d '{"text": "测试恢复", "channel": "api", "sender_id": "test"}'
# 期望: 返回真实 LLM 响应（非 Echo）
# 期望: 事件中 is_fallback=false
```

---

## Echo 模式（无需 Proxy）

如果暂时没有 LLM provider API key，可以使用 Echo 模式：

```bash
export OCTOAGENT_LLM_MODE=echo
uv run uvicorn octoagent.gateway.main:app --reload
```

Echo 模式行为与 M0 完全一致，所有 M0 功能正常工作。

---

## 常见问题

### Proxy 启动失败

```bash
# 查看 Proxy 日志
docker compose -f docker-compose.litellm.yml logs

# 常见原因:
# 1. API key 未设置或无效 -> 检查 .env.litellm
# 2. 端口 4000 被占用 -> 修改 LITELLM_PORT
# 3. Docker 未启动 -> 启动 Docker Desktop
```

### OctoAgent 连接 Proxy 失败

```bash
# 验证 Proxy 是否运行
curl http://localhost:4000/health/liveliness

# 验证环境变量
echo $LITELLM_PROXY_URL
echo $OCTOAGENT_LLM_MODE

# 检查 OctoAgent 健康
curl "http://localhost:8000/ready?profile=llm"
```

### 成本数据显示 0.0

- 检查事件中的 `cost_unavailable` 字段
- 如果 `cost_unavailable=true`，表示 LiteLLM pricing 数据库可能未覆盖你使用的模型
- 这不影响正常功能，成本数据会在 LiteLLM 更新 pricing 后自动修复

---

## 运行测试

```bash
# 运行 M0 回归测试（确保向后兼容）
uv run pytest packages/core/tests/ apps/gateway/tests/ -v

# 运行 Feature 002 新增测试
uv run pytest packages/provider/tests/ -v

# 运行集成测试
uv run pytest tests/integration/ -v

# 覆盖率检查
uv run pytest packages/provider/tests/ --cov=octoagent.provider --cov-report=term-missing
```
