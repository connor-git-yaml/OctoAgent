# F149 main 产品决定记录：Tasks / Settings 管理能力位置

> 决策状态：8/8 已决定（2026-07-20）。本文件是 Claude Design 与后续 Plan/Tasks 的产品事实源，不再含待决项。
> 源码基线：`octoagent/frontend/src/pages/TaskList.tsx:58-93` 当前在所有页面状态都渲染 `OperatorInboxPanel` 与 `RecoveryPanel`；Reference pack 如实保留该现状，但目标设计必须按下表重新归位。

| 能力 | 当前入口/证据 | main 决定 | 目标产品规则 | 状态 |
|---|---|---|---|---|
| Operator inbox | `octoagent/frontend/src/components/OperatorInboxPanel.tsx:20-104` + `/api/operator/inbox/actions` | A：继续属于 Tasks 的管理区 | 用户文案固定为“待处理事项”，不得出现 operator/ops；有待处理项时显著提示并可进入，0 项时折叠或隐藏，不占普通首屏；不得与 F145 三类知识候选审批合并 | DECIDED A |
| Recovery summary | `octoagent/frontend/src/components/RecoveryPanel.tsx:176-241` | 从 Tasks 移出 | 放入现有 Settings → Advanced → 维护与恢复 | DECIDED B |
| Backup create | `octoagent/frontend/src/components/RecoveryPanel.tsx:97` | 从 Tasks 移出 | Settings → Advanced → 维护与恢复；执行前危险确认 | DECIDED B |
| Chat export | `octoagent/frontend/src/components/RecoveryPanel.tsx:110` | 从 Tasks 移出 | Settings → Advanced → 维护与恢复；显示导出范围说明 | DECIDED B |
| Update dry-run | `octoagent/frontend/src/components/RecoveryPanel.tsx:123-125` | 从 Tasks 移出 | Settings → Advanced → 维护与恢复；必须先于 update apply | DECIDED B |
| Update apply | `octoagent/frontend/src/components/RecoveryPanel.tsx:136` | 从 Tasks 移出 | Settings → Advanced → 维护与恢复；强确认并展示最近一次有效 dry-run 摘要 | DECIDED B |
| Runtime restart | `octoagent/frontend/src/components/RecoveryPanel.tsx:150-153` | 从 Tasks 移出 | Settings → Advanced → 维护与恢复；强确认并说明短暂不可用 | DECIDED B |
| Runtime verify | `octoagent/frontend/src/components/RecoveryPanel.tsx:164-167` | 从 Tasks 移出 | Settings → Advanced → 维护与恢复 | DECIDED B |

## 组合与范围约束

- “从 Tasks 移出”不是隐藏未来落点：F149 必须直接设计上述 Settings Advanced 落点。
- 不新增独立页面、management service、registry、transport 或第二状态源；生产阶段以窄的 `MaintenanceRecoverySection` 一类 UI 组件由 Settings 组合，禁止继续增大 Settings God component。
- 复用现有 `api/client`、config/secret store 与 application boundary；所有现有 endpoint/handler 保留。只有后续实现发现真实不可达时，才单独返回 Gate，不在本决定中删除。
- 无论页面归位如何，F149 都不新增 task cancel/resume。
- 8/8 决策已满足 Prompt 发送前置；F149 总 Design Gate 仍必须等待 Claude Design 20/20 页面 frame、Shared-States、Advanced-Pattern 与 References 回存。
