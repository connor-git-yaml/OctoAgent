# Product Research - Feature 049

## 调研目标

总结“什么样的默认助手行为”更像 Personal AI OS 的 Butler，而不是一个一会儿很聪明、一会儿又装懂的聊天框。

## OpenClaw 参考结论

### 1. 行为不只是一段 prompt，而是一套 workspace 文件

参考：
- `_references/opensource/openclaw/docs/reference/AGENTS.default.md`
- `_references/opensource/openclaw/docs/reference/templates/AGENTS.md`
- `_references/opensource/openclaw/docs/reference/templates/BOOTSTRAP.md`

启发：
- 行为、人格、用户偏好、工具使用规则、first-run ritual 都有正式文件载体
- 这让“行为调整”变成产品能力，而不是开发者私藏的 prompt patch

### 2. first-run ritual 也是人格建立的一部分

OpenClaw 的 `BOOTSTRAP.md` 明确把“你是谁、你的 vibe、如何称呼用户”当成 first-run 的正式步骤。  
启发：Butler 的人格不应只在代码里硬编码，也不应只存在于一段不可见的 system prompt。

## Agent Zero 参考结论

### 1. 主提示词按 role / communication / solving 分层

参考：
- `_references/opensource/agent-zero/prompts/agent.system.main.role.md`
- `_references/opensource/agent-zero/prompts/agent.system.main.communication.md`
- `_references/opensource/agent-zero/prompts/agent.system.main.solving.md`

启发：
- 默认行为最好按职责分层装配
- “怎么说话”和“怎么解题”应是独立层
- 这比一段大 prompt 更适合长期调优

## 对 OctoAgent 的产品结论

1. Butler 行为改进必须走通用 persona/behavior system，而不是继续加单案补丁
2. 缺信息补问应该成为默认行为准则
3. 人格与行为应具备用户可理解的文件化载体
4. 这些载体必须 project-scoped，且受治理
