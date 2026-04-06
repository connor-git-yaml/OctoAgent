# 修复规划

## 目标

修复 `octo update` 的两类问题：

1. `migrate` 阶段因前端类型错误导致构建失败
2. 失败摘要把 npm warning 错误地展示成主因

## 推荐方案

采用 fix-report 中的方案 A。

## 变更清单

1. **前端类型边界收口**
   - `useTaskLiveState` 改为使用统一的 `ControlPlaneResourceRef`
   - `taskId` 入参兼容 `null`
   - `refreshResources` 签名与 `useWorkbench()` 对齐
   - `ChatWorkbench` 明确构造 resource refs，而不是传资源文档

2. **升级错误摘要修复**
   - 调整 `_default_run_command()` 的失败提取策略
   - 非 0 退出时优先保留真正的失败上下文，而不是只取 `stderr`
   - 保持输出对 operator 可读，不引入难以消费的大对象错误

3. **清理多余依赖**
   - 移除 `@types/dompurify`
   - 更新 lockfile，消除无意义 warning

## 回归风险

- `useTaskLiveState` 是 Chat 实时状态核心 hook，需要验证：
  - 构建通过
  - 现有 hook 单测 / ChatWorkbench 单测不退化
- update 摘要策略变更后，需要验证：
  - 失败时能看到真实 `tsc` 错误
  - 成功路径不受影响

## 验证方案

1. 运行前端构建：`npm run build`
2. 运行与 Chat 实时状态相关的测试
3. 运行与 update service 相关的测试
4. 如有必要，增加针对 `_default_run_command()` 的单测，覆盖 stdout/stderr 混合失败场景

## GATE_DESIGN

- `[GATE] ONLINE_RESEARCH | mode=fix | required=false | decision=PASS | points=0 | reason=project-context 未强制本次 fix 做在线调研，且问题可由本地代码与构建结果直接闭环`
- `[GATE] GATE_DESIGN | mode=fix | policy=balanced | decision=AUTO_CONTINUE | reason=用户已明确授权执行该修复，且方案属于最小化实现修正`
