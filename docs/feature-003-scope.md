# Feature 003: Auth Adapter + DX 工具 — Scope 冻结 v3

> **状态**: Frozen v3 — Spec-Driven 流程进行中（合并原 003.5）
> **日期**: 2026-03-01（v3 修订）
> **依赖**: Feature 002 ✅（已交付）
> **并行**: 可与 Feature 004（Tool Contract）同时开发

---

## 一句话目标

构建完整 Auth 基础设施（凭证模型 + Adapter 接口 + Credential Store + Handler Chain），支持 OpenAI/OpenRouter API Key、Anthropic Setup Token、Codex OAuth 三种认证模式；引导式配置（`octo init` / `octo doctor`）降低首次部署门槛。

## Blueprint 依据

| 章节 | 内容 |
|------|------|
| §8.9.4 | Auth Adapter：三种凭证类型 + AuthAdapter 接口 + Handler Chain |
| §12.9.1 | `octo init`：交互式引导配置 |
| §12.9.2 | `octo doctor`：环境诊断 |
| §12.9.3 | dotenv 自动加载 |
| §14 M1 | Auth 验收标准 |

## 参考实现（OpenClaw 源码）

| 文件 | 参考内容 |
|------|---------|
| `src/agents/auth-profiles/types.ts` | 三种凭证类型定义（ApiKey / Token / OAuth） |
| `src/agents/auth-profiles/store.ts` | Credential Store 文件存储 + 合并逻辑 |
| `src/agents/auth-profiles/oauth.ts` | OAuth token 刷新 + 文件锁 |
| `src/commands/auth-choice.apply.anthropic.ts` | Anthropic Setup Token 验证 + API Key 两种模式 |
| `src/commands/auth-choice.apply.openai.ts` | OpenAI API Key + Codex OAuth |
| `src/commands/auth-choice.apply.ts` | Handler Chain (Chain of Responsibility) |
| `src/commands/onboard-auth.config-core.ts` | Config/Credential 分离 + Profile 配置 |
| `src/agents/model-auth.ts` | 运行时凭证解析优先级链 |

## Scope 边界

### IN（必须交付）

1. **凭证数据模型**（Pydantic）：`ApiKeyCredential` / `TokenCredential` / `OAuthCredential`
2. **AuthAdapter 接口**：`resolve()` / `refresh()` / `is_expired()` ABC
3. **ApiKeyAdapter**：读环境变量或 credential store（支持 OpenAI、OpenRouter、Anthropic 等所有标准 API Key Provider）
4. **AnthropicSetupTokenAdapter**：`sk-ant-oat01-` 验证 + 24h TTL 过期检测
5. **CodexOAuthAdapter**：Device Flow 授权（浏览器打开 → 轮询 → token 交换 → 持久化）
6. **Credential Store**：`~/.octoagent/auth-profiles.json` + 文件锁
7. **`octo init` CLI**：交互式 Provider 选择 → 凭证输入 → .env/.env.litellm/litellm-config.yaml 生成
8. **`octo doctor` CLI**：环境检查 + 凭证诊断 + `--live` 端到端测试
9. **dotenv 自动加载**：Gateway `main.py` 启动时 `load_dotenv()`
10. **Handler Chain**：每个 Provider 一个 handler，Chain of Responsibility
11. **凭证事件类型**：在 EventType 枚举中新增 CREDENTIAL_LOADED / CREDENTIAL_EXPIRED 等（C2 合规）

### OUT（不在此 Feature 范围）

- OAuth token 自动刷新后台任务（M2）
- Azure AD / GCP Vertex AI 认证（LiteLLM Proxy 内置支持，无需应用层实现）
- GUI 配置界面（CLI only for M1）
- 多 Agent credential 继承（M2）

### 调整说明（v3 修订）

**v1 → v2**: 收敛到 OpenAI API Key，Anthropic/Codex 移至 003.5。
**v2 → v3**: 用户决策——合并 003.5 回 003，一期完成所有 Auth 工作。三种 Adapter（ApiKey + Setup Token + Codex OAuth）均在 003 交付。

## 预估

- 任务数：~15-18
- FR 条数：~10-13
- 工期：3-4 天

## 验收标准

- [ ] OpenAI/OpenRouter API Key → credential store → LiteLLM Proxy → 真实 LLM 调用成功
- [ ] Anthropic Setup Token → credential store → LiteLLM Proxy → 真实 LLM 调用成功（零成本）
- [ ] Codex OAuth Device Flow 授权 → token 持久化 → 调用成功
- [ ] `octo init` 引导新用户完成首次配置（<3 分钟）
- [ ] `octo doctor` 正确诊断：缺失 .env / 无效 Key / 过期 Token / Proxy 不可达
- [ ] `octo doctor --live` 发送 cheap 模型 ping 验证端到端连通
- [ ] Gateway 启动自动加载 .env，无需手动 source
- [ ] credential store 凭证不出现在日志/事件/LLM 上下文中（C5 合规）
- [ ] 凭证加载/过期事件记录到 Event Store（C2 合规）

## Spec-Driven 流程就绪检查

- [x] Blueprint 设计章节就位（§8.9.4 + §12.9）
- [x] 参考实现已调研（OpenClaw auth-profiles）
- [x] Scope 边界明确（IN/OUT 已列出，v3 合并 003.5）
- [x] 依赖已满足（Feature 002 ✅）
- [x] 验收标准可测试
