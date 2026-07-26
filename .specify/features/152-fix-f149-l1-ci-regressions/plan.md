# F152 实施计划

## Gate

- Diagnosis：PASS
- Design：PASS（Fix 默认继续；不改变产品/视觉方向）
- Tasks：PASS
- Implement：PASS
- Verify：LOCAL PASS；等待权威 `master` CI

## 实施顺序

1. 先补 Gateway 与 Web decoder 的 RED 合同。
2. 在现有 `task_sse_contract.py` 内增加有限 model-call projection，不新增 endpoint 或第二协议。
3. TaskDetail 对该 projection 明确不驱动业务 UI。
4. 修正 A-wave fixture 的过期时间。
5. 修正 390px 空状态无障碍断言，不改页面 UI。
6. 运行定向测试、完整前端与 Gateway 测试、完整 L1、架构门。
7. 更新架构说明和验证报告，提交后复核权威 `master` CI。

## 架构约束

- 单 Gateway、单 Task SSE endpoint、单后端 projector。
- model-call 只暴露 finite allowlist。
- TaskDetail 与 Chat consumer 的职责保持分离。
- 不新增兼容层、registry、service、store 或 transport。
- 不改变 Claude Design 视觉输出。
