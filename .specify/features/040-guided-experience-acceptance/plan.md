# Implementation Plan: Feature 040 M4 Guided Experience Integration Acceptance

## 目标

把 035/036/039 之间现有但未串起来的接口补成单条 workbench 用户旅程。

## 设计原则

1. 不发明新 backend
   - 只消费现有 `snapshot / resources / actions`
2. 不替代 036
   - `setup.review / setup.apply` 进入 workbench
   - 040 只验收 036 是否已经把 `skills.selection.save` 与 CLI/Web setup 汇流做实
3. 不替代 039
   - `worker.review / worker.apply` 只做 UI 集成与验收
4. 033 状态显式可见
   - 040 保留 `context_continuity` 的显式状态展示，而不是回退到隐式魔法

## 实施阶段

### Phase 1: Contracts & Types

- frontend types 补齐 036 资源文档
- workbench resource 路由表补齐

### Phase 2: Workbench Integration

- `Home` 增 setup readiness 入口
- `SettingsCenter` 改为 `setup.review -> setup.apply`
- `WorkbenchBoard` 增 worker review/apply plan UI
- `ChatWorkbench` 接入 `context_continuity` degraded state

### Phase 3: Acceptance Tests

- frontend integration tests
- backend e2e smoke for setup/control path

### Phase 4: Release Gate Follow-up

- `memory -> operator -> export/recovery` acceptance path
- 040 acceptance matrix / release report
- 033/036 block 已关闭后的最终 release 结论

## 验收路径

1. 打开 `Home`
2. 看到 setup readiness / blocking reasons
3. 在 `Settings` 改配置，先触发 `setup.review`，再执行 `setup.apply`
4. 在 `Work` 查看 worker plan，执行 `worker.apply`
5. 在 `Chat` 查看当前 `context_continuity` 是否 degraded
6. 验证 workbench 和 control-plane state 同步刷新
