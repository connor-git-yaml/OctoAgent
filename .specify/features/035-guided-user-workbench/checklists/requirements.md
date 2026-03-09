# Requirements Checklist: Feature 035 Guided User Workbench + Visual Config Center

- [x] 明确 035 不是重做 control plane backend，而是重组用户入口与图形化配置路径
- [x] 明确默认首页必须从 operator/resource console 切到用户导向 `Home`
- [x] 明确设置中心必须复用 `ConfigSchemaDocument + ui_hints + config.apply`
- [x] 明确聊天工作台必须复用 `chat.send + SSE + task/execution + control-plane resources`
- [x] 明确 Work 页面必须复用 `sessions` / `delegation` / `work.*` canonical contract
- [x] 明确 Memory 页面必须复用 027/028 的 canonical memory/vault/proposal contract
- [x] 明确 `Advanced` 模式必须保留现有 ControlPlane 能力
- [x] 明确 035 必须直接消费 015/017/025/026/027/030/033/034 的既有接口，不得另起平行协议
- [x] 明确 secret refs-only、project/workspace 边界与 degraded graceful handling
- [x] 明确需要 frontend integration / backend regression / e2e 的“非伪实现”测试矩阵
