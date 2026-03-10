# Tech Research: Feature 040 M4 Guided Experience Integration Acceptance

## 当前代码库关键发现

1. backend snapshot 已经输出 `setup_governance / policy_profiles / skill_governance`，但 frontend types 与 resource route 还没有接进去。
2. `Home` 和 `SettingsCenter` 仍完全忽略 036 资源，因此 workbench 没有真实 setup readiness 主线。
3. `WorkbenchBoard` 会把 `worker.review` 当普通 action 发出，但不会展示 plan，也没有 `worker.apply` 流程。
4. `App.test.tsx` 目前只覆盖 workbench shell 与 `config.apply` 基础保存，没有覆盖 setup review 或 worker governance。

## 设计结论

1. 040 先补 frontend integration，不新增 backend object。
2. 保存流必须收口到 `setup.review -> setup.apply`，否则 036 的 canonical setup 语义无法真正进入 workbench。
3. worker governance 的 integration 核心不是新 action，而是把已有 `worker.review / worker.apply` 结果体变成可读、可批准的 workbench UI。
4. `context_continuity` 已经存在于 snapshot，040 需要把它变成显式 degraded state，而不是继续使用占位文案隐藏 033 缺口。
