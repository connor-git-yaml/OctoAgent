# Product Research - Feature 048

## 调研目标

从普通用户视角总结“首次可用路径”和“等待中的信任反馈”应该是什么样，而不是继续沿用控制台式首页。

## OpenClaw 参考结论

### 1. wizard 明确把“最快首次聊天”当成主目标

参考：
- `_references/opensource/openclaw/docs/start/wizard.md`

启发：
- 首次配置不应先教育用户完整架构
- 应优先告诉用户“最快怎么开始聊天”
- 复杂配置保留在后续页面或 advanced path

### 2. dashboard 明确是 admin surface，而不是普通入口解释页

参考：
- `_references/opensource/openclaw/docs/web/dashboard.md`
- `_references/opensource/openclaw/docs/web/control-ui.md`

启发：
- admin surface 可以有 token、pairing、channel status、logs
- 普通用户路径不该直接继承这些表达方式
- “深度控制面”和“日常使用面”必须在产品层分层

## 对 OctoAgent 的产品结论

1. `Home` 不该是控制台摘要页，而应是“当前状态 + 下一步”
2. `Settings` 不该先是结构目录，而应先是“最少必要配置”
3. `Chat` 等待态不应是沉默，需要可理解的协作反馈
4. `Advanced` 继续存在，但必须更明确地承担“需要排查时再进”的角色
