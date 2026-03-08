---
required: true
mode: full
points_count: 3
tools:
  - web.search_query
queries:
  - "Python keyring official documentation get_password set_password delete_password"
  - "Pydantic SecretStr official docs get_secret_value model_dump repr masked"
  - "JSON Schema annotations official docs writeOnly readOnly title description examples"
findings:
  - source: "https://keyring.readthedocs.io/en/stable/"
    summary: "keyring 提供统一的 `get_password` / `set_password` / `delete_password` 抽象，并按环境自动选择 credential backend；当系统没有推荐 backend 时，会出现 `NoKeyringError` 之类的不可用信号。"
  - source: "https://docs.pydantic.dev/latest/api/types/#pydantic.types.SecretStr"
    summary: "`SecretStr` 在 `repr` / `model_dump` / `model_dump_json` 中默认输出掩码；只有显式调用 `get_secret_value()` 或自定义 serializer 才会暴露明文。"
  - source: "https://json-schema.org/understanding-json-schema/reference/annotations"
    summary: "`title` / `description` / `default` / `examples` / `readOnly` / `writeOnly` 属于 annotation，不参与 validation 语义。"
impacts_on_design:
  - "025-B 可以把 `keychain` 视为可选、优先级受环境约束的 `SecretRef` source，而不是强依赖；当 backend 不可用时必须显式降级到 `env/file/exec`。"
  - "Secret material 在运行时边界前都应保持 `SecretStr`/redacted 语义，只有真正注入 runtime env snapshot 时才允许受控解密。"
  - "026-A 的 `config schema + uiHints` 拆分是正确的：validation 规则继续由 schema 承担，CLI/Web 展示意图由 annotations / uiHints 承担。"
---

# Online Research Notes

## Point 1 — OS keychain 只能是可选 secret backend，不能是假定总存在

官方 keyring 文档把 keychain 描述为统一凭证抽象层，而不是保证任何环境都天然可用的能力：

- 调用面是 `get_password` / `set_password` / `delete_password`
- 实际 backend 由运行环境决定
- 没有合适 backend 时，调用方会得到显式不可用信号

**Design take-away**:

- 025-B 应支持 `SecretRef(keychain)`，但不能把它当成唯一后端。
- CLI 默认可以优先推荐 keychain；如果不可用，必须清楚引导用户改走 `env/file/exec`。
- 这与 `Degrade Gracefully` 一致，比“静默写入某个新的明文本地文件”更安全。

## Point 2 — Secret redaction 应建立在类型系统和显式解密边界上

Pydantic 官方文档明确：

- `SecretStr` 的默认 `repr` 与序列化输出是掩码
- 明文暴露只能通过显式 `get_secret_value()` 或自定义 serializer

**Design take-away**:

- 025-B 的 secret 相关模型、CLI 输出、审计结果和事件摘要应统一采用 redacted 语义。
- 只有 runtime short-lived injection 的最后边界允许解出明文，并且不应把解出的值再次写回日志、事件、artifact 或 YAML。
- 测试需要专门覆盖 “没有误调 `get_secret_value()` 导致日志泄漏” 这一类错误。

## Point 3 — `config schema` 与 `uiHints` 的职责边界应继续分离

JSON Schema 官方 annotations 文档强调：

- `title`、`description`、`default`、`examples`、`readOnly`、`writeOnly` 是注释信息
- 它们不等同于 validation rule

**Design take-away**:

- 025-B 消费 026-A `ConfigSchemaDocument` 时，应继续把 schema 视为验证真相源，把 `uiHints` 视为 CLI/Web 展示辅助信息。
- CLI 可以忽略自己不支持的 hints，但不能因此改变字段校验语义。
- 这让 026-B 以后做 Web 消费层时不会和 CLI 形成两套字段定义。
