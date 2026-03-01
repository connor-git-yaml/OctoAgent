# M1 Feature 拆分方案（v2）

> **文档类型**: 里程碑拆分方案（Implementation Planning）
> **依据**: Blueprint §14（M1 定义）+ Feature 001/002 交付经验 + 参考项目调研
> **状态**: v2 — 已对齐最新 Blueprint（含 Auth Adapter §8.9.4 + DX §12.9）
> **日期**: 2026-03-01
> **变更记录**: v1(2026-02-28) → v2(2026-03-01) Feature 002 已交付，新增 Feature 003 Auth+DX，调研 AgentZero/OpenClaw/AgentStudio 后更新设计

---

## 1. 背景与动机

### 1.1 已交付里程碑度量

| 里程碑 | 测试数 | 交付日期 | 关键产出 |
|--------|--------|---------|---------|
| M0 Feature 001 | 105 | 2026-02-28 | Task/Event/Artifact + SSE + Web UI |
| M1 Feature 002 ✅ | 205（+98） | 2026-03-01 | LiteLLM Proxy + AliasRegistry + FallbackManager + 成本双通道 |

**关键经验**：
- M0 scope 清晰（单一技术域）、依赖链单一，质量极高
- Feature 002 通过 Spec-Driver 10 阶段流程验证，4 个 MEDIUM 质量问题全部修复
- OpenRouter 多 Provider 配置已跑通端到端真实 LLM 调用（Claude Opus 4.6）

### 1.2 M1 剩余工作量分析

Blueprint §14 对 M1 的完整定义：

- [x] 接入 LiteLLM Proxy + 运行时 alias group + 语义 alias 映射 — **Feature 002 已交付**
- [x] 语义 alias → 运行时 group 映射 + FallbackManager + 成本双通道 — **Feature 002 已交付**
- [ ] Auth Adapter：API Key / Setup Token / OAuth 三种凭证 + DX 工具 — **新增，§8.9.4 + §12.9**
- [ ] 实现 Pydantic Skill Runner（结构化输出）
- [ ] 工具 schema 反射 + ToolBroker 执行
- [ ] Policy Engine（allow/ask/deny）+ Approvals UI
- [ ] 工具输出压缩（summarizer）

剩余 **5 个技术域**，需拆分为独立 Feature。

### 1.3 参考项目调研洞见（v2 新增）

对 AgentZero、OpenClaw、AgentStudio 源码的调研结论：

| 特性 | AgentZero | OpenClaw | AgentStudio | OctoAgent 采纳 |
|------|-----------|----------|-------------|---------------|
| Tool Schema | prompt 驱动，无 manifest | `AgentTool<I,O>` 强类型 | MCP `inputSchema` | **Pydantic 强类型** |
| Tool 大输出 | >500 字符存文件 | Content Block 多 Part | Multi-Part | **阈值存 Artifact + 路径引用**（M1 简化） |
| Policy 模型 | 通知+暂停 | **多层 Pipeline** | Pre-Send Guard | **多层 Pipeline**（M1 先 2 层） |
| 审批流 | 无门禁 | **Two-Phase** register→wait | 4 种决策 | **Two-Phase**（防并发竞态） |
| Tool Profiles | 无 | minimal/coding/messaging/full | 按 Agent 配 | **引入 Profile**（M1 先 2 级） |
| Auth | 环境变量 + round-robin | 3 凭证类型 + Handler Chain + 刷新 | 环境变量 | **三层架构**（Config/Credential/Adapter） |

---

## 2. 拆分方案：6 个 Feature（002+003 已完成 + 4 个待实现）

### 2.1 依赖关系

```
Feature 002 ✅ 已完成（LiteLLM + 成本治理）
    │
    ├──→ Feature 003 ✅ ──→ Feature 003-b: OAuth PKCE ─┐
    │    （Auth + DX）       （对接真实 Provider）       │
    │                                                   │
    ├──→ Feature 004: Tool Contract + ToolBroker ───────┤──→ Feature 006: Policy + Approvals
    │    （依赖 002 的 cheap alias）                     │    （依赖 004 ToolMeta + 005 Skill）
    │                                                   │
    └──→ Feature 005: Skill Runner ────────────────────┘
         （依赖 002 LLM + 004 ToolBroker）
```

**关键优化**：Feature 003-b / 004 / 005 三者无互相依赖，**可并行开发**。

### 2.2 与 Blueprint FR/需求 的映射

| Feature | Blueprint FR | Blueprint 设计章节 | M1 验收标准覆盖 |
|---------|-------------|-------------------|----------------|
| 002 ✅ | FR-LLM-1, FR-LLM-2 | §8.9 Provider Plane | ④⑤ cost/tokens + alias 路由 |
| 003 | FR-AUTH-1（新增） | §8.9.4 Auth Adapter + §12.9 DX | ⑥⑦ Auth 零成本调用 + DX 工具 |
| 004 | FR-TOOL-1, FR-TOOL-2 | §8.5 Tooling | ③ schema 反射 contract test |
| 005 | FR-SKILL-1 | §8.4 Skill | ① LLM→结构化输出→工具执行 |
| 006 | FR-TOOL-3, FR-CH-1[M1] | §8.6 Policy Engine | ② irreversible 审批流 |

---

## 3. Feature 详细定义

### Feature 002: LiteLLM Proxy 集成 + 成本治理 ✅ 已完成

> **交付日期**: 2026-03-01 | **测试**: 205 passed（+98 新增）| **Spec**: `.specify/features/002-integrate-litellm-provider/`

| 指标 | 数值 |
|------|------|
| 任务数 | 52 |
| 新增 package | 1（`packages/provider`） |
| 代码变动 | +10,685 / -318 行 |
| 质量审计 | 4 MEDIUM issues → 全部修复 |

交付物清单：
- `packages/provider`：LiteLLMClient + AliasRegistry（6 语义 alias → 3 运行时 group）+ CostTracker（EventStore + ModelCallResult 双通道）+ FallbackManager（lazy probe + Echo 降级）
- `apps/gateway`：双模式 lifespan（litellm/echo）+ LLMService 重构 + profile-based health check
- `docker-compose.litellm.yml` + `litellm-config.yaml`（OpenRouter 配置）
- `.env.example` + `.env.litellm.example`

验收标准（4/4 通过）：
- [x] cheap/main alias 路由到不同模型（OpenRouter: Qwen / Claude Opus 4.6）
- [x] 每次模型调用事件包含真实 cost/tokens/latency/provider
- [x] LiteLLM Proxy 不可达时降级到 Echo 模式（is_fallback: true）
- [x] `/ready?profile=llm` 正确检测 LiteLLM Proxy 状态

---

### Feature 003: Auth Adapter + DX 工具（新增）

**一句话目标**：构建完整 Auth 基础设施（凭证模型 + Adapter 接口 + Credential Store + Handler Chain），支持 OpenAI/OpenRouter API Key、Anthropic Setup Token、Codex OAuth 三种认证模式；引导式配置（`octo init` / `octo doctor`）降低首次部署门槛。

#### 范围

| 维度 | 内容 |
|------|------|
| Blueprint FR | FR-AUTH-1（新增）|
| Blueprint 设计 | §8.9.4（Auth Adapter M1）+ §12.9（DX） |
| 参考实现 | OpenClaw `auth-profiles/`、`onboard-auth.config-core.ts` |
| 新增模块 | `packages/provider/auth/`（Adapter + Credentials + Store） |
| CLI 命令 | `octo init`、`octo doctor` |
| 改造模块 | `apps/gateway/main.py`（dotenv 自动加载） |

#### 关键交付

1. **凭证数据模型**（对齐 §8.9.4 + OpenClaw `AuthProfileCredential`）：
   ```python
   # packages/provider/auth/credentials.py
   class ApiKeyCredential(BaseModel):
       type: Literal["api_key"] = "api_key"
       provider: str
       key: SecretStr

   class TokenCredential(BaseModel):
       type: Literal["token"] = "token"
       provider: str
       token: SecretStr
       expires_at: datetime | None = None

   class OAuthCredential(BaseModel):
       type: Literal["oauth"] = "oauth"
       provider: str
       access_token: SecretStr
       refresh_token: SecretStr
       expires_at: datetime
   ```

2. **AuthAdapter 接口 + 三种实现**：
   - `AuthAdapter` ABC：`resolve()` / `refresh()` / `is_expired()`
   - `ApiKeyAdapter`：读环境变量或 credential store（支持 OpenAI、OpenRouter、Anthropic 等标准 API Key Provider）
   - `AnthropicSetupTokenAdapter`：`sk-ant-oat01-` 前缀验证 + 24h TTL 过期检测
   - `CodexOAuthAdapter`：RFC 8628 Device Flow 协议框架（端点可配置，003-b 对接真实 Provider）

3. **Credential Store**（参考 OpenClaw `auth-profiles.json`）：
   - 文件存储：`~/.octoagent/auth-profiles.json`（`.gitignore`）
   - Config/Credential 分离：config 声明 profile 元数据，credential 存储实际凭证
   - 文件锁保护原子更新

4. **Handler Chain**（参考 OpenClaw `applyAuthChoice`）：
   - 每个 Provider 一个 handler，Chain of Responsibility 匹配
   - 解析优先级：显式 profile → credential store → 环境变量 → 默认值

5. **`octo init` CLI**（§12.9.1）：
   - 交互式：选择 LLM 模式 → 选择 Provider → 输入凭证 → 生成 .env
   - 自动生成随机 LITELLM_MASTER_KEY
   - 检测 Docker daemon 可用性

6. **`octo doctor` CLI**（§12.9.2）：
   - 环境检查：Python 版本、uv、.env、Docker、Proxy 健康
   - 凭证诊断：检测缺失/过期/无效
   - `--live` 标志：发送 ping 验证端到端连通

7. **dotenv 自动加载**（§12.9.3）：
   - Gateway `main.py` 启动时 `load_dotenv()`
   - 不覆盖已设置的环境变量

#### 验收标准

- [x] OpenAI/OpenRouter API Key → credential store → LiteLLM Proxy → 真实 LLM 调用成功
- [x] Anthropic Setup Token → credential store → 格式校验 + TTL 过期检测
- [x] OAuth Device Flow 协议框架（RFC 8628）实现完成，端点可配置（真实 Provider 对接见 003-b）
- [x] `octo init` 引导新用户 3 分钟内完成首次配置
- [x] `octo doctor` 正确诊断缺失/无效/过期凭证
- [x] `octo doctor --live` 端到端验证 LLM 连通性
- [x] Gateway 启动时自动加载 .env，无需手动 source
- [x] credential store 凭证不出现在日志/事件/LLM 上下文中（C5 合规）
- [x] 凭证加载/过期事件记录到 Event Store（C2 合规）

#### 交付度量

- 任务数：56（实际）
- 测试数：253 passed
- 代码文件：19 新增 + 6 修改

#### 已知限制（→ Feature 003-b）

- OAuth 端点为占位值（`auth0.openai.com`），OpenAI Codex 实际使用 Auth Code + PKCE 流（非 Device Flow）
- 需 Per-Provider OAuth 端点注册机制（参考 OpenClaw 每个 Provider 独立 auth 扩展）

---

### Feature 003-b: OAuth PKCE + Per-Provider Auth（新增）

**一句话目标**：将 003 的 OAuth 协议框架对接真实 Provider 端点，实现 OpenAI Codex Auth Code + PKCE 流，支持 Per-Provider OAuth 配置扩展。

#### 背景

Feature 003 实现了 RFC 8628 Device Flow 协议框架和完整的 Auth 基础设施（凭证模型 + Adapter + Store + Handler Chain），但 OAuth 端点使用占位值。经调研 OpenClaw 源码和 OpenAI Codex CLI 实际行为，发现：

- **OpenAI Codex** 使用 Authorization Code + PKCE 流（非 Device Flow）
  - 授权端点：`https://auth.openai.com/oauth/authorize`
  - Token 端点：`https://auth.openai.com/oauth/token`
  - 回调地址：`http://localhost:1455/auth/callback`（本地 HTTP 服务器）
  - Client ID：动态生成（格式 `app_EMoamEEZ73f0...`）
- **GitHub Copilot** 使用 Device Flow（`github.com/login/device/code`，Client ID: `Iv1.b507a08c87ecfe98`）
- **Qwen** 使用 Device Flow + PKCE（`chat.qwen.ai/api/v1/oauth2/device/code`）

#### 范围

| 维度 | 内容 |
|------|------|
| 依赖 | Feature 003 ✅（Auth 基础设施已就绪） |
| 新增模块 | `auth/pkce.py`（PKCE 生成）、`auth/callback_server.py`（本地回调服务器）、`auth/provider_registry.py`（Provider OAuth 配置注册） |
| 改造模块 | `auth/oauth.py`（扩展支持 Auth Code 流）、`dx/init_wizard.py`（OAuth 选项对接真实端点） |
| 参考实现 | OpenClaw `openai-codex-oauth.ts` + `oauth-flow.ts`、`github-copilot-auth.ts`、`qwen-portal-auth/oauth.ts` |

#### 关键交付

1. **PKCE 支持**：`code_verifier` / `code_challenge`（S256）生成
2. **本地回调服务器**：`localhost:1455` 接收 OAuth 回调，交换授权码
3. **Per-Provider OAuth 配置注册**：

   ```python
   # 每个 Provider 注册自己的 OAuth 端点和流类型
   OAUTH_PROVIDERS = {
       "openai-codex": OAuthProviderConfig(
           flow_type="authorization_code",
           authorization_endpoint="https://auth.openai.com/oauth/authorize",
           token_endpoint="https://auth.openai.com/oauth/token",
           callback_port=1455,
           scope="openid profile email offline_access",
       ),
       "github-copilot": OAuthProviderConfig(
           flow_type="device_code",
           authorization_endpoint="https://github.com/login/device/code",
           token_endpoint="https://github.com/login/oauth/access_token",
           client_id="Iv1.b507a08c87ecfe98",
       ),
   }
   ```

4. **init wizard 更新**：OAuth 选项展示真实可用的 Provider，按 Provider 选择对应流
5. **远程/VPS 环境兼容**：回调 URL 手动粘贴模式（参考 OpenClaw `oauth-flow.ts` 的 isRemote 处理）

#### 验收标准

- [ ] OpenAI Codex OAuth (Auth Code + PKCE) → token 持久化 → credential store 可读
- [ ] GitHub Copilot Device Flow → token 持久化（使用 003 已有的 Device Flow 框架）
- [ ] `octo init` OAuth 选项展示真实可用的 Provider 列表
- [ ] 远程环境下可通过手动粘贴 redirect URL 完成授权
- [ ] 原有 API Key / Setup Token 流程不受影响（回归测试通过）

#### 预估规模

- 任务数：~10-12
- 工期：1-2 天（Auth 基础设施已就绪，增量工作量小）

#### 技术风险

- OpenAI Codex Client ID 动态生成机制需进一步调研（可能需要先调用某个注册端点）
- `localhost:1455` 端口占用冲突处理
- PKCE state 参数的 CSRF 防护

---

### Feature 004: 工具契约 + ToolBroker（原 Feature 003）

**一句话目标**：建立工具治理基础设施——工具可声明、可反射、可执行、大输出可裁切。

#### 范围

| 维度 | 内容 |
|------|------|
| Blueprint FR | FR-TOOL-1（工具契约化）、FR-TOOL-2（工具调用结构化） |
| Blueprint 设计 | §8.5（Tooling）核心实现 |
| 新增 package | `packages/tooling`（schema 反射 + ToolBroker + ToolResult） |
| 新增事件类型 | TOOL_CALL_STARTED, TOOL_CALL_COMPLETED, TOOL_CALL_FAILED |
| 数据模型 | ToolMeta, ToolCall, ToolResult |
| 依赖 | Feature 002 ✅（已完成） |

#### 关键交付

1. **ToolMeta 数据模型**（对齐 §8.5.2）：
   ```yaml
   ToolMeta:
     tool_id: "namespace.name"
     version: "hash or semver"
     side_effect: none | reversible | irreversible
     risk_level: low | medium | high
     timeout_s: 30
     idempotency: supported | required | not_supported
     outputs:
       max_inline_chars: 4000
       store_full_as_artifact: true
   ```

2. **Tool Profile 分级**（v2 新增，参考 OpenClaw `tool-catalog.ts`）：
   - `minimal`：只读工具（echo, datetime, status 查询）
   - `standard`：读写工具（file_read, file_write, 数据库查询）
   - `full`：全部工具（含 exec, docker, 外部 API 调用）
   - Profile 作为 Policy Engine 的第一道过滤层

3. **Schema 反射引擎**：
   - 从 Pydantic 函数签名 + 类型注解 + docstring 自动生成 JSON Schema
   - Contract Test：反射出的 schema 与代码签名一致性验证
   - ToolRegistry：工具注册、发现、冲突检测

4. **ToolBroker**：
   - 工具执行编排（sequential / parallel / mixed）
   - 超时控制 + 取消
   - 结构化 ToolResult 回灌
   - TOOL_CALL_STARTED / COMPLETED / FAILED 事件生成

5. **大输出裁切**（v2 调整：M1 先用路径引用，summarizer 推迟到 Feature 005 或 M1.5）：
   - 工具输出 > `max_inline_chars` → 全量存 artifact
   - 裁切后保留 artifact 路径引用在上下文中（参考 AgentZero `_90_save_tool_call_file.py`）
   - summarizer 压缩可选启用（依赖 Skill Runner 就绪后激活）

6. **示例工具**：至少 3 个内置工具用于端到端验证：
   - `system.echo`（read-only，minimal profile）
   - `system.datetime`（read-only，minimal profile）
   - `system.file_read`（read-only，standard profile）

#### 验收标准

- [ ] 工具 schema 自动反射与代码签名一致（contract test 通过）
- [ ] ToolBroker 执行工具并生成完整事件链（STARTED → COMPLETED/FAILED）
- [ ] 工具输出超阈值时全量存 artifact + 路径引用回灌上下文
- [ ] 工具超时时正确生成 TOOL_CALL_FAILED 事件
- [ ] Tool Profile 正确过滤工具集（minimal 不含写操作工具）

#### 预估规模

- 任务数：~25-30
- FR 条数：~12-16
- 工期：3-4 天（v2 下调：summarizer 推迟后复杂度降低）

---

### Feature 005: Pydantic Skill Runner（结构化输出框架，原 Feature 004）

**一句话目标**：建立 Skill 运行时——LLM 产生结构化输出，可调用工具，可重试，可验证。

#### 范围

| 维度 | 内容 |
|------|------|
| Blueprint FR | FR-SKILL-1（Skill 框架） |
| Blueprint 设计 | §8.4（Skill 运行语义）全面实现 |
| 新增模块 | Skill manifest + SkillRunner + SkillRegistry |
| 集成 | Pydantic AI（structured output + tool_calls 解析） |
| 依赖 | Feature 002 ✅（LLM 调用）、Feature 004（ToolBroker 执行工具） |

#### 关键交付

1. **Skill Manifest**（对齐 §8.4.1）：
   ```yaml
   SkillManifest:
     skill_id: "namespace.name"
     version: "0.1"
     input_model: InputModel (Pydantic)
     output_model: OutputModel (Pydantic)
     model_alias: "main"
     tools_allowed: ["system.echo", "system.file_read"]
     tool_profile: "standard"          # v2 新增：引用 Tool Profile
     retry_policy:
       max_attempts: 3
       upgrade_model_on_fail: true
   ```

2. **SkillRunner**（对齐 §8.4.2 运行语义）：
   - 校验输入（InputModel）
   - 调用模型（通过 LiteLLM alias）
   - 解析并校验输出（OutputModel）— Pydantic AI structured output
   - 若输出包含 tool_calls → ToolBroker 执行 → 结果回灌模型
   - OutputModel 校验失败 → 自动重试（含 model upgrade 策略）
   - 输出最终结果（校验 + 产物）

3. **SkillRegistry**：Skill 注册、发现、元数据查询

4. **可选：激活 summarizer 压缩**：
   - Feature 004 中大输出用路径引用，此处可选启用 summarizer（通过 cheap alias 生成摘要）
   - 作为 SkillRunner 的后处理步骤

5. **示例 Skill**：至少 2 个端到端可验证的 Skill：
   - `echo_skill`（最小验证：输入→LLM→结构化输出）
   - `file_summary_skill`（工具调用验证：LLM→file_read→summary 输出）

#### 验收标准

- [ ] LLM 调用 → 结构化输出（OutputModel 校验通过）→ 工具执行 端到端通过
- [ ] OutputModel 校验失败时自动重试，重试次数不超过 max_attempts
- [ ] Skill 内 tool_calls 通过 ToolBroker 执行并结果回灌
- [ ] Skill 执行全过程事件可追溯（MODEL_CALL + TOOL_CALL 事件链完整）

#### 预估规模

- 任务数：~25-30
- FR 条数：~10-14
- 工期：4-5 天

#### 技术风险

- Pydantic AI structured output 与 LiteLLM Proxy 的兼容性需提前验证（PoC spike）
- tool_calls 解析在不同 LLM provider 间的一致性（依赖 LiteLLM 的 function calling 统一层）

---

### Feature 006: Policy Engine + Approvals + Chat UI（原 Feature 005）

**一句话目标**：建立安全治理层——工具调用可门禁、可审批；用户可通过 Chat UI 交互和审批。

#### 范围

| 维度 | 内容 |
|------|------|
| Blueprint FR | FR-TOOL-3（工具权限门禁）、FR-CH-1[M1]（Chat UI + Approvals 面板） |
| Blueprint 设计 | §8.6（Policy Engine）全面实现 |
| 新增模块 | PolicyEngine + PolicyProfile + ApprovalService |
| 状态机扩展 | 激活 WAITING_APPROVAL（M0 已在 TaskStatus 枚举中预留） |
| 新增事件类型 | APPROVAL_REQUESTED, APPROVED, REJECTED |
| 新增 API | `POST /api/approve/{approval_id}`、`GET /api/approvals` |
| 前端 | Approvals 面板 + 基础 Chat UI（SSE 流式输出） |
| 依赖 | Feature 004（ToolMeta.side_effect/risk_level）、Feature 005（Skill 运行时集成） |

#### 关键交付

1. **多层 Policy Pipeline**（v2 新增，参考 OpenClaw `tool-policy-pipeline.ts`）：
   - **Layer 1: Tool Profile 过滤**（Feature 004 已建立 Profile 分级）
   - **Layer 2: Global 规则**（side_effect 驱动的 allow/ask/deny）
   - M2 扩展方向：+ Agent 级策略 + Group 级策略
   - 每层可独立收紧权限，不可放松上层决策

2. **Two-Phase Approval**（v2 新增，参考 OpenClaw `exec-approvals.ts`）：
   ```python
   # Phase 1: 注册审批请求（立即返回，防竞态）
   approval = await approval_service.register(
       task_id=..., tool_call=..., risk_explanation=...
   )  # → { approval_id, expires_at }

   # Phase 2: 等待用户决策
   decision = await approval_service.wait_for_decision(
       approval_id, timeout_s=120
   )  # → allow / deny
   ```
   - 分离注册与等待，防止并发竞态条件
   - 超时默认策略：deny（参考 OpenClaw `DEFAULT_ASK_FALLBACK = "deny"`）

3. **PolicyEngine 核心**（对齐 §8.6.1-8.6.2）：
   - 输入：tool_call / action_plan / task_meta
   - 输出：Decision（allow / ask / deny）
   - 默认策略：irreversible → ask；reversible → allow；read-only → allow
   - Safe Bins 白名单（v2 新增，参考 OpenClaw）：预置安全命令列表（git, python, npm 等）

4. **审批工作流**（对齐 §8.6.3）：
   - 触发 ask → APPROVAL_REQUESTED 事件 → Task 进入 WAITING_APPROVAL
   - 用户 approve → APPROVED 事件 → Task 回到 RUNNING
   - 用户 reject → REJECTED 事件 → Task 进入终态
   - 审批超时：默认 120s（参考 OpenClaw），超时后 deny

5. **Approvals REST API**：
   - `POST /api/approve/{approval_id}` — 审批决策
   - `GET /api/approvals` — 待审批列表

6. **前端扩展**：
   - **Approvals 面板**：待审批动作列表，支持 approve/reject 操作
   - **基础 Chat UI**：消息输入框 + SSE 流式输出展示

#### 验收标准

- [ ] irreversible 工具触发审批流，approve 后继续执行，reject 后终止
- [ ] read-only 和 reversible 工具默认 allow，无需审批
- [ ] Two-Phase Approval 防竞态：同一审批请求不会被重复处理
- [ ] 审批超时 120s 后自动 deny
- [ ] PolicyProfile 配置变更生成事件并可审计
- [ ] Approvals 面板正确展示待审批动作，支持 approve/reject
- [ ] Chat UI 可发送消息并展示 SSE 流式响应

#### 预估规模

- 任务数：~35-40
- FR 条数：~14-18
- 工期：5-6 天

#### Constitution 对齐

| 宪法原则 | Feature 006 中的体现 |
|----------|---------------------|
| C4: Side-effect Must be Two-Phase | Two-Phase Approval：register → wait → execute |
| C7: User-in-Control + 策略可配 | 多层 Pipeline + PolicyProfile 可配 + 默认 safe |
| C8: Observability is a Feature | 审批事件可追溯 + Approvals 面板 |

---

## 4. 总量预估与历史对比

| 指标 | M0 (001) | M1 已完成 (002+003) | M1 剩余 | M1 总计 |
|------|----------|---------------------|---------|---------|
| Feature 数 | 1 | 2 | 4 | 6 |
| 总任务数 | 68 | 108 | ~100-120 | ~208-228 |
| 总 FR 数 | 31 | 25 | ~46-62 | ~71-87 |
| 总测试数 | 105 | 458 | — | — |
| 新增 package | 2 | 1 | 1-2 | 2-3 |
| 新增前端页面 | 2 | 0 | 2 | 2 |
| 新增事件类型 | 8 | 3 | ~6 | ~9 |
| 预估工期 | 10-13 天 | — | ~15-21 天 | ~25-34 天 |

---

## 5. 实施策略

### 5.1 推荐时间线（利用并行化）

```
Week 1:  Feature 003-b（OAuth PKCE）‖ Feature 004（Tool Contract）  ← 并行开发
Week 2:  Feature 005（Skill Runner）  ← 004 完成后
Week 3:  Feature 006（Policy + Approvals）→ M1 集成验证
```

**并行化收益**：Feature 003 和 004 无互相依赖，并行节省 ~3-4 天。

### 5.2 每个 Feature 的 Spec-Driven 流程

沿用 Feature 001/002 的成功模式：

1. **Research** — 产研调研 + 技术调研
2. **Spec** — 功能需求规范（FR + US + Edge Cases + Constraints）
3. **Plan** — 任务拆分 + 依赖关系 + 并行策略
4. **Implement** — 按 Phase 逐步实现
5. **Verify** — Layer 1（Spec-Code 对齐）+ Layer 2（原生工具链）+ Constitution 合规

### 5.3 风险缓解

| 风险 | 缓解措施 |
|------|---------|
| Anthropic Setup Token 获取流程复杂 | 003 research 阶段先跑通手动流程，再自动化 |
| Codex OAuth 端点未确认 | 003 参考 OpenClaw `pi-ai` 库实现，spec 阶段做 PoC spike |
| Pydantic AI + LiteLLM 兼容性 | Feature 005 spec 阶段做 PoC spike，验证 structured output + tool_calls |
| Policy Engine 复杂度膨胀 | M1 仅实现 2 层 Pipeline（Profile + Global），多 Agent/Group 推迟到 M2 |
| 前端工作量低估 | Chat UI 和 Approvals 控制在最小可用范围，不追求交互体验 |
| summarizer 压缩延迟 | Feature 004 先用路径引用（零 LLM 依赖），005 就绪后可选激活 |

---

## 6. 待确认事项

已解决（Feature 002/003 实战确认）：

- [x] **LiteLLM Proxy 部署方式** → Docker 容器 + `docker-compose.litellm.yml`
- [x] **首批 provider 选择** → OpenRouter（cheap=Qwen 3.5-27b, main=Claude Opus 4.6, fallback=GPT-5.3-codex）
- [x] **Codex OAuth 可行性** → OpenAI Codex 使用 Auth Code + PKCE 流（非 Device Flow），需本地回调服务器 `localhost:1455`，Client ID 动态生成。已拆为 Feature 003-b 独立实现

待确认（Feature 004-006 spec 阶段前冻结）：

1. **首批工具清单**：Feature 004 需要实现哪些内置工具作为验证？（建议：echo + datetime + file_read）
2. **审批超时策略**：默认 120s + deny 是否合适？是否需要 escalate 选项？
3. **Chat UI 最小范围**：仅文本输入+流式输出？是否需要消息历史？是否需要 Markdown 渲染？

---

## 附录 A: Blueprint 需求到 Feature 的完整映射

| Blueprint FR | 级别 | Feature | 说明 |
|-------------|------|---------|------|
| FR-LLM-1 | 必须 | 002 ✅ | 统一模型出口（LiteLLM Proxy） |
| FR-LLM-2 | 应该 | 002 ✅ | 双模型体系（cheap/main/fallback） |
| FR-AUTH-1 | 必须 | 003 | 多凭证类型 + DX 工具 |
| FR-TOOL-1 | 必须 | 004 | 工具契约化（schema 反射） |
| FR-TOOL-2 | 必须 | 004 | 工具调用结构化 |
| FR-SKILL-1 | 应该 | 005 | Skill 框架（Pydantic AI） |
| FR-TOOL-3 | 必须 | 006 | 工具权限门禁（Policy Engine） |
| FR-CH-1[M1] Chat UI | 必须 | 006 | 基础 Chat UI（SSE 流式输出） |
| FR-CH-1[M1] Approvals | 必须 | 006 | Approvals 面板 |

## 附录 B: M1 新增事件类型预览

| 事件类型 | Feature | 说明 |
|---------|---------|------|
| TOOL_CALL_STARTED | 004 | 工具调用开始（含 tool_id, 参数摘要） |
| TOOL_CALL_COMPLETED | 004 | 工具调用完成（含结果摘要, 耗时, artifact_ref） |
| TOOL_CALL_FAILED | 004 | 工具调用失败（含错误分类, 可恢复性） |
| APPROVAL_REQUESTED | 006 | 审批请求（含 action summary, risk explanation） |
| APPROVED | 006 | 审批通过 |
| REJECTED | 006 | 审批拒绝（区别于 Task 级 REJECTED） |

## 附录 C: M1 新增/激活的 TaskStatus

| 状态 | Feature | 说明 |
|------|---------|------|
| WAITING_APPROVAL | 006 | M0 已预留，M1 Feature 006 激活 |

M0 已预留但 M1 仍不激活的状态：QUEUED、WAITING_INPUT、PAUSED（推迟到 M1.5）。

## 附录 D: 参考项目源码索引（v2 新增）

| 项目 | 关键参考文件 | 对应 Feature |
|------|-------------|-------------|
| **OpenClaw** | `src/agents/auth-profiles/types.ts` | 003（凭证类型） |
| **OpenClaw** | `src/commands/onboard-auth.config-core.ts` | 003（引导配置） |
| **OpenClaw** | `src/agents/tool-policy-pipeline.ts` | 006（多层 Pipeline） |
| **OpenClaw** | `src/infra/exec-approvals.ts` | 006（Two-Phase Approval） |
| **OpenClaw** | `src/agents/tool-catalog.ts` | 004（Tool Profile） |
| **OpenClaw** | `src/infra/session-cost-usage.types.ts` | 002 ✅（Cost 参考） |
| **AgentZero** | `python/helpers/tool.py` | 004（Tool 基类） |
| **AgentZero** | `python/extensions/hist_add_tool_result/_90_save_tool_call_file.py` | 004（大输出裁切） |
| **AgentZero** | `python/helpers/docker.py` | M1.5（Docker 沙箱） |
| **AgentStudio** | `backend/src/services/preSendGuard/` | 006（Pre-Send Guard 参考） |
| **AgentStudio** | `backend/src/types/skills.ts` | 005（Skill 定义参考） |
| **AgentStudio** | `backend/src/services/mcpAdmin/types.ts` | 004（MCP 工具 schema） |
