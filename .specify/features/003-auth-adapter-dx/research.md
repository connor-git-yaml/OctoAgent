# Feature 003 技术决策研究

**Feature**: Auth Adapter + DX 工具
**Date**: 2026-03-01
**Status**: Finalized

---

## RD-001: Credential Store 存储格式

**Decision**: 使用 JSON 文件（`~/.octoagent/auth-profiles.json`），文件权限 `0o600`。

**Rationale**:
- Blueprint SS8.9.4 明确指定 `auth-profiles.json` 作为凭证存储
- OpenClaw 参考实现验证了 JSON profile 方案的可行性
- 单用户场景下 JSON 文件足够，无需引入 keyring/密钥链等系统级存储
- Pydantic 原生支持 JSON 序列化/反序列化，与项目技术栈一致
- `SecretStr` 在序列化时默认脱敏（`**********`），需要显式 `get_secret_value()` 获取明文

**Alternatives**:
1. **系统 keyring（keyring 库）**: 安全性更高，但增加系统级依赖（macOS Keychain / Linux Secret Service），调试不便，跨平台行为差异大。决定 M2+ 考虑。
2. **加密 JSON（cryptography + Fernet）**: 增加复杂度，单用户场景下文件权限 `0o600` 已足够。密钥管理本身引入新问题。
3. **SQLite 存储**: 与 Task/Event Store 混合，违反 Config/Credential 分离原则（FR-013）。

---

## RD-002: CLI 交互框架选型

**Decision**: 使用 `rich` + `questionary`（或 `InquireAPI` 风格的 `prompt_toolkit` 封装）实现交互式引导。

**Rationale**:
- `rich` 已在 structlog 生态中广泛使用，与项目风格一致
- `questionary` 提供 select/confirm/text 等交互原语，开箱即用
- Blueprint SS12.9.1 的 `octo init` 和 SS12.9.2 的 `octo doctor` 均需要格式化输出
- CLI 入口使用 Python `click` 或标准 `argparse` 均可；考虑到 scope 限制，选择轻量的 `click` 作为 CLI 框架

**Alternatives**:
1. **纯 input() + print()**: 无格式化能力，用户体验差。
2. **typer**: 基于 click 的封装，引入额外依赖但功能类似。项目目前无 typer 使用先例。
3. **textual（TUI）**: 过重，超出 CLI 工具的需求范围。

---

## RD-003: Codex OAuth Device Flow 实现

**Decision**: 自行实现 Device Flow（RFC 8628），不引入第三方 OAuth 库。

**Rationale**:
- Device Flow 协议流程简单明确：POST device_authorization -> 轮询 token 端点 -> 获取 access_token
- OpenClaw 参考实现中使用 `pi-ai` 库，但该库面向通用 OAuth，引入不必要的依赖
- 项目已有 `httpx` 依赖，可直接用于 HTTP 请求
- 需要的端点仅两个（device_authorization + token），自行实现约 100 行代码
- Codex OAuth 端点信息：`https://auth0.openai.com`（Device Flow 标准端点）

**Alternatives**:
1. **authlib**: 全功能 OAuth 库，功能远超需求，依赖树大。
2. **oauthlib**: 偏底层，需要大量组装代码。
3. **pi-ai**: OpenClaw 使用的库，但项目较小众，维护风险。

---

## RD-004: 凭证脱敏策略

**Decision**: 使用统一的 `mask_secret(value: str) -> str` 函数，保留前缀 + 末尾 3 字符，中间替换为 `***`。

**Rationale**:
- 对齐 FR-011 凭证脱敏要求
- 保留前缀便于识别凭证类型（如 `sk-ant-oat01-***xyz`）
- 保留末尾少量字符便于开发者确认是哪个凭证
- Pydantic `SecretStr` 的 `__repr__` 已默认脱敏，但 structlog 日志需要额外处理
- 在 structlog processor chain 中添加 secret 过滤器，自动拦截包含凭证模式的日志字段

**Alternatives**:
1. **完全隐藏**: `***hidden***`，无法区分不同凭证。
2. **仅保留前缀**: `sk-***`，无法确认是否为目标凭证。
3. **Hash 摘要**: `sha256:abc123...`，对开发者不直观。

---

## RD-005: Handler Chain 实现模式

**Decision**: 使用 Chain of Responsibility 模式，每个 AuthAdapter 子类作为一个 handler，通过注册表管理 handler 顺序。

**Rationale**:
- Blueprint SS8.9.4 明确指定 Handler Chain 模式
- 解析优先级：显式 profile > credential store > 环境变量 > 默认值
- 每个 handler 独立实现 `can_handle(provider: str) -> bool` 和 `resolve() -> str`
- handler 注册使用简单的列表，新增 Provider 只需追加 handler
- 与现有 FallbackManager 的 primary/fallback 模式互补（FallbackManager 管降级，Handler Chain 管凭证解析）

**Alternatives**:
1. **Strategy Pattern**: 不支持自动 fallthrough 到下一个 handler。
2. **Plugin 注册 + 优先级排序**: 过度工程化，M1 阶段不需要动态加载。
3. **单一 resolve 函数 + if/elif**: 扩展性差，违反开闭原则。

---

## RD-006: dotenv 加载方案

**Decision**: 使用 `python-dotenv` 库，在 Gateway `main.py` 启动时调用 `load_dotenv(override=False)`。

**Rationale**:
- Blueprint SS12.9.3 明确指定使用 `python-dotenv`
- `override=False` 确保已设置的环境变量不被文件覆盖（FR-009）
- `python-dotenv` 是 FastAPI 生态中的事实标准
- 文件不存在时静默跳过，不影响启动

**Alternatives**:
1. **pydantic-settings**: Pydantic v2 的 settings 管理，功能更强但引入额外依赖层。考虑 M2 统一配置管理时引入。
2. **手动 os.environ.update()**: 需自行实现 .env 解析，容易出 bug（引号处理、多行值等）。
3. **direnv**: 系统级工具，不是所有开发者都安装。

---

## RD-007: CLI 入口组织

**Decision**: 在 provider 包中新增 `auth/` 子目录放置 Auth 逻辑；CLI 工具（`octo init` / `octo doctor`）作为独立的 `packages/provider` 的 CLI 入口点。

**Rationale**:
- Blueprint SS8.9.4 明确指定 Auth Adapter 代码位于 `packages/provider/auth/`
- CLI 工具是 Auth 和 Provider 配置的消费者，逻辑上属于 provider 包
- 通过 pyproject.toml 的 `[project.scripts]` 注册 CLI 入口点
- 保持包的自包含性：provider 包同时提供 API 和 CLI

**Alternatives**:
1. **独立 CLI 包（packages/cli/）**: 增加包管理复杂度，M1 阶段 CLI 功能有限，不值得。
2. **根目录脚本（scripts/）**: 不符合 Python 包管理规范，不可 pip install。
3. **gateway 集成**: CLI 与 Web API 混合，职责不清。

---

## RD-008: 文件锁实现（Credential Store 并发写入）

**Decision**: 使用 `filelock` 库实现跨进程文件锁。

**Rationale**:
- EC-5 要求防止多进程同时写入 credential store
- `filelock` 是 Python 标准的跨平台文件锁库，pip install 即可
- 锁文件路径：`~/.octoagent/auth-profiles.json.lock`
- 写入失败时重试 3 次，间隔 100ms

**Alternatives**:
1. **fcntl.flock()**: 仅 Unix 系统支持，不跨平台。
2. **自实现 PID 文件锁**: 容易出现竞态条件和陈旧锁问题。
3. **不加锁**: 单用户场景下概率低，但不符合防御性编程原则。

---

## RD-009: Setup Token TTL 管理

**Decision**: 在 `TokenCredential` 中记录 `acquired_at` 时间戳，运行时计算过期。默认 TTL 24 小时，可通过 `OCTOAGENT_SETUP_TOKEN_TTL_HOURS` 环境变量覆盖。

**Rationale**:
- EC-1 明确要求：Setup Token 过期时间无法从 Token 本身解析
- Anthropic 未公开精确过期时间，24 小时是社区保守估算（spec Q3 已确认）
- 环境变量覆盖机制提供灵活性
- `acquired_at` 持久化到 credential store，重启后仍可计算过期

**Alternatives**:
1. **固定硬编码 24h**: 不可配置，不灵活。
2. **每次调用前 ping 验证**: 增加延迟，且 Anthropic 可能无 token validation 端点。
3. **不管过期**: 依赖 API 返回 401 再处理——用户体验差。

---

## RD-010: EventType 扩展方式

**Decision**: 在现有 `EventType` 枚举中新增凭证相关事件类型：`CREDENTIAL_LOADED`、`CREDENTIAL_EXPIRED`、`CREDENTIAL_FAILED`。

**Rationale**:
- FR-012 要求凭证生命周期事件记录到 Event Store
- 现有 EventType 使用 StrEnum，直接追加新成员即可
- 事件 payload 仅包含元信息（provider、credential_type、timestamp），不含凭证值
- 对齐 Constitution C2（Everything is an Event）

**Alternatives**:
1. **独立的 CredentialEventType 枚举**: 增加类型管理复杂度，与现有 Event Store 查询不兼容。
2. **使用 payload 中的 sub_type 字段**: 失去类型安全，不利于 Event Store 查询索引。
