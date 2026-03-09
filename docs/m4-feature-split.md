# M4 Feature Split（In Progress v0.2）

## 1. 目标

M4 不再补 M3 的主闭环缺口，而是把已经可交付的主链扩成真正“可长期使用”的产品面：

- 工具面更完整，而不是只有少量 inspect/list
- 多 Agent / 多 Worker / Graph / subagent 变成真实可运行能力，而不是 metadata 标签
- 体验增强继续建立在既有 project / workspace / control plane / memory 治理之上

本轮已冻结并交付 **Feature 032** 作为 M4 第一项。

## 2. 约束

- 032 必须复用 Feature 025-B 的 project / workspace / secret 基线
- 032 必须复用 Feature 026 的 control plane / Web 控制台，不得重做控制台框架
- 032 必须复用 Feature 030 的 ToolIndex / Delegation Plane / Skill Pipeline 基线，但可以要求把“只有标签没有真实后端”的部分补成 live runtime
- 032 不得绕过 ToolBroker / Policy / Event / Audit
- 032 不得把 channel action packs、remote nodes、companion surfaces、marketplace 一次性吞进来

## 3. 非伪实现门禁

M4 从 032 开始启用更严格的“真实可用”门禁。以下任一条不满足，都不能标记为已交付：

1. 工具族只有 schema / registry / 测试，但主 Agent、Worker、CLI、Web、Telegram 任一真实入口都无法调起执行。
2. `graph_agent` 只有 target kind / runtime label，没有实际 `pydantic_graph` 或等价 graph backend 的运行桥接。
3. `subagent` 只有 work metadata / route reason，没有真实 child runtime、durable session、控制台可见生命周期。
4. 主 Agent 只有 `create/merge`，没有正式 `split` 语义和 operator 可见状态面，却宣称具备 worker split/merge 能力。
5. 只补 mock 测试，不补用户可达的 integration / e2e 路径验证。

## 4. Feature 队列

### Wave 1

#### Feature 032：OpenClaw Built-in Tool Suite + Live Runtime Truth

状态：**Implemented（2026-03-09）**

目标：

- 对齐 OpenClaw 的高价值 built-in tool families
- 把 Graph / subagent / main-agent child-work create-merge-split 能力补成真实可运行主链
- 让 operator 能在 control plane 中看见 availability、degraded reason、runtime truth

范围冻结：

- `sessions.* / subagents.* / session.status / agents.list`
- `web.fetch / web.search / browser.*`
- `gateway.* / cron.* / nodes.*`
- `pdf.* / image.* / tts.* / canvas.*`
- `memory.read / memory.search / memory.citations` 等只读治理入口
- `pydantic_graph` bridge、subagent spawn runtime、main-agent child-work split/merge lifecycle

明确排除：

- Telegram / Discord / Slack / WhatsApp action packs
- remote nodes / companion / PWA polish
- marketplace / external plugin hub
- 绕过 Memory / Vault / ToolBroker 治理的快捷路径

本轮实际交付摘要：

- built-in tool catalog 扩到 15+ 正式工具，并补 availability / install hint / degraded reason / entrypoints / runtime kinds
- `subagents.spawn`、`work.split`、`work.merge` 已形成真实 child task / child work / child session 主链
- `graph_agent` 已接到真实 `pydantic_graph` backend，并进入 execution console / control plane 投影
- control plane 与现有 Web 控制台已能展示 tool availability、runtime truth、child work 关系与 split/merge 动作

## 5. 交付顺序建议

1. 先补 `BuiltinToolCatalog`、availability / install hint / degraded truth
2. 再补 `sessions/subagents/graph` 这类 live runtime 主链
3. 再补 web/browser/media/doc 工具族
4. 最后把全部工具状态、运行态、降级面接入 control plane 与验证矩阵

## 6. 与后续 Feature 的边界

- 032 负责“built-in tools 与 live runtime truth”
- 033 可以单开为 `Channel Action Packs`
- 034 以后再处理 remote nodes / companion surfaces / richer multimodal surfaces
