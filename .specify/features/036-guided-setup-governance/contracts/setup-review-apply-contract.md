# Contract: Setup Review / Apply Safety Semantics

## 1. 设计目标

036 要解决的不是“如何多存几份配置”，而是“用户在 apply 之前，是否真正知道会发生什么”。

因此必须引入统一的 review/apply 语义。

## 2. Review Summary 结构

`setup.review` 返回的审查摘要至少包含五个 section：

1. `provider_runtime_risks`
2. `channel_exposure_risks`
3. `agent_autonomy_risks`
4. `tool_skill_readiness_risks`
5. `secret_binding_risks`

每个 risk item 至少包含：

- `risk_id`
- `severity` (`info / warning / high`)
- `title`
- `summary`
- `blocking`
- `recommended_action`
- `source_ref`

## 3. 默认 preset 语义

036 必须提供面向普通用户的 preset，而不是直接暴露底层术语。

### 推荐 preset

#### `谨慎`

- 对应较低 autonomy
- `allowed_tool_profile = minimal`
- reversible / irreversible 默认需要确认
- 推荐给首次使用和公网暴露场景

#### `平衡`

- 对应当前默认生产建议
- `allowed_tool_profile = standard`
- irreversible 需要确认
- 推荐给本地开发和可信内网

#### `自主`

- 对应高 autonomy
- `allowed_tool_profile = privileged`
- 仅限受信任环境
- 必须在 review 中附带高风险提示

约束：

- preset 必须映射到真实 policy profile / tool profile / approval 行为
- 不允许只改显示名称，不改真实运行边界

## 4. Channel 暴露面审查

review 必须显式判断以下场景：

- `front_door.mode = loopback / bearer / trusted_proxy`
- Telegram `dm_policy`
- Telegram `group_policy`
- allow_users / allowed_groups / group_allow_users 是否为空
- webhook 模式是否配置 URL 和 secret

输出必须回答：

1. 当前谁能访问
2. 需要 owner approval 的路径是什么
3. 哪些暴露面超出推荐默认

## 5. Skills / MCP 审查

review 不能只基于“skill 已注册”。

必须同时检查：

- required secrets 是否存在
- binary / command 依赖是否存在
- MCP server 是否 enabled 且可发现
- 当前 policy/profile 是否允许默认使用对应 tools

当某个 skill 不满足条件时，输出必须是：

- 不可用原因
- 需要的补救动作
- 是否会阻塞 apply

## 6. Event / Audit 要求

`setup.review` 与 `setup.apply` 都必须进入 control-plane event 流，并满足：

- payload summary 可读
- 不泄露 secrets
- 能追溯到 project/workspace
- 能追溯到实际影响到的 config / profile / capability resources
