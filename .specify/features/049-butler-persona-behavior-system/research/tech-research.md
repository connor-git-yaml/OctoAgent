# Tech Research - Feature 049

## 当前实现基线

- `Feature 039` 已提供 Butler -> Worker 的 message-native A2A 主链
- `Feature 041` 已解决 freshness query 的 delegation、缺城市补问和 runtime truth
- `Feature 042` 正在推进 profile-first Agent 绑定与 tool universe

但当前仍缺少：

- 通用 clarification decision
- 正式 behavior pack 载体
- runtime 层的 persona/behavior source explainability

## 参考实现对比

### OpenClaw

强项：
- workspace 文件体系完整
- `AGENTS.md`、`SOUL.md`、`USER.md`、`MEMORY.md`、`TOOLS.md`、`BOOTSTRAP.md` 形成可演化人格载体

弱项对 OctoAgent 的提醒：
- 如果完全照搬 workspace 自由编辑模式，容易绕过现有治理边界

### Agent Zero

强项：
- 主行为提示按 role / communication / solving 分层
- 更容易插入“缺信息时怎么处理”“工具边界如何说”这类逻辑层

弱项对 OctoAgent 的提醒：
- 纯 prompt slicing 仍不等于用户可见、可调的人格文件系统

## 技术结论

049 最适合采用“OpenClaw 文件载体 + Agent Zero 分层装配”的混合方案：

1. 行为源头使用 project-scoped markdown pack
2. runtime 装配时拆为 role / communication / solving / tool-boundary / memory-policy
3. update 走 proposal + review/apply
4. Worker 只消费行为切片，不读完整 pack
