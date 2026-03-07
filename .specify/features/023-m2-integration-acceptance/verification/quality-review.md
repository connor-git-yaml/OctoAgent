# Quality Review: Feature 023 — M2 Integration Acceptance

**特性分支**: `codex/feat-023-m2-integration-acceptance`  
**审查日期**: 2026-03-07

## 代码质量结论

- 结论: **PASS（无阻塞问题）**
- 静态检查: 023 相关 provider / integration 变更 `ruff check` 通过
- 测试结果: provider / gateway / protocol / integration 回归通过

## 审查要点

1. 范围控制
- 023 只修补阻塞联合验收的 DX 断点和 durability 闭环，没有扩张到新产品域。
- `backup_service.py` 的改动只是在显式过滤时允许导出 `ops-chat-import`，不会把所有系统任务默认暴露给 `export_chats()`。

2. 首次使用闭环
- `config init`、`doctor`、Telegram verifier 现在围绕同一份 YAML runtime config 收敛，减少了首次使用的前置分叉。
- onboarding 的 `first_message` 从“bot 出站成功”改为“真实入站 task 证据”，更符合用户视角闭环。

3. 多渠道一致性
- 023 新增同 item 的 Web/Telegram parity 联合验收，并且 audit 事件进入同一 `ops-operator-inbox` 任务链。
- 重复动作语义由现有 gateway 测试补强，避免 023 只靠单一 happy path。

4. 协议到执行面
- A2A 验收不再停留在 schema round-trip；成功和 timeout 失败路径都真实穿过 runtime。
- interactive/input-required 没有在 023 新增一条大而脆的全链路测试，而是复用 018/019 的现有回归支撑，风险和成本更可控。

5. Durability boundary
- 导入后的系统任务现在可以被显式 `export_chats(task_id="ops-chat-import")` 导出，避免 chat import 结果停留在 backup-only 角落。
- 023 durability 验收还额外断言了 `RecoverySummary.ready_for_restore`，使 dry-run 证据从计划级落到用户可回看的状态源。

## 非阻塞风险

- 真实 Telegram 外网注册、Webhook secret、LiteLLM live provider 连通性仍需要部署环境验证，023 不试图把这些网络变量纳入单元/集成测试。
- 若后续需要把 approval / retry / cancel / ack 全部提升到单条跨端 E2E 旅程，应新建后续 feature，而不是继续在 023 内膨胀。
- 若后续要证明 A2A interactive resume/cancel 的完整跨层旅程，建议基于现有 `execution_api` fixture 单独落一个 focused acceptance，而不是耦合到当前五条 gate 主测试里。
