# Verification Report: Feature 035 Guided User Workbench + Visual Config Center

## 状态

- 阶段：设计完成，待实现
- 日期：2026-03-09

## 本次验证内容

1. 已核对现有 feature 编号，确认 `034` 已被 `context-compression-main-worker` 占用，因此本轮按 `035` 建档。
2. 已核对 Feature 015 / 017 / 025 / 026 / 027 / 030 / 033 / 034 与当前 frontend/api/control-plane 接缝。
3. 已完成 OpenClaw / Agent Zero 的产品与技术对照调研。
4. 已输出 035 的 spec / plan / tasks / contracts / research 制品，并回写到 M4 backlog。

## 本次未执行

- 未执行代码级自动化测试。
- 未执行 frontend build 或运行时 smoke。
- 未提交任何运行时代码。

原因：本轮目标是把“小白可用工作台”冻结成正式 Feature 035，而不是提前做半实现。

## 实施前硬门禁

- 必须先建立 frontend contract tests，防止新增私有 workbench API。
- 必须先建立 `settings -> config.apply` 与 `chat -> task/execution/context` 的 failing integration tests。
- 033/034 的 canonical output 必须定义清楚；035 只能消费，不能重做。
