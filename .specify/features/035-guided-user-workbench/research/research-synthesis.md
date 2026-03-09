# Research Synthesis: Feature 035 — Guided User Workbench + Visual Config Center

## 1. 综合判断

035 可以在现有 master 上以 additive 方式推进，而且应该现在就推进；因为问题已经不是“后端没能力”，而是“能力存在，但用户默认入口没有组织成能用的产品”。

## 2. 已确认的事实

### 2.1 OctoAgent 已有足够的 canonical backend

仓库已经有：

- 015 的 wizard / doctor
- 017 的 operator inbox
- 025 的 project/workspace/secret/wizard
- 026 的 control-plane snapshot/resources/actions/events
- 027 的 memory/vault/proposal
- 030 的 sessions/work/delegation/runtime truth
- 034 的 compaction evidence

这意味着 035 不需要再造“后台协议”。

### 2.2 缺的是用户工作台级别的入口组织

当前缺口集中在：

- 默认首页不对
- 图形化配置没有产品化
- chat/task/control plane 三套入口割裂
- 033/034 等关键运行时信息没有可理解的用户表达

## 3. 冻结后的 035 定义

035 的正确定位应是：

> 在复用 Feature 015 / 017 / 025 / 026 / 027 / 030 / 033 / 034 的既有 contract 前提下，交付一个默认面向普通用户的 Guided Workbench，使配置、聊天、任务、记忆和高级控制面形成连续路径。

## 4. 四个必须同时成立的支柱

### A. Default Entry Must Change

如果 `/` 继续是资源导向的 operator console，035 就没有成立。

### B. Config Must Stay Canonical

如果图形化配置不复用 `ConfigSchemaDocument + ui_hints + config.apply`，035 只会制造新配置债务。

### C. Chat Must Become the Working Surface

如果聊天仍然只是 SSE 输出框，没有 task/execution/work/memory/context 抽屉，它就不是 Personal AI OS 的主入口。

### D. Advanced Must Survive

如果为了“小白”把 operator/diagnostics/control plane 删掉，系统会失去高级可观测性和治理面。

## 5. 非伪实现门禁

035 必须显式启用以下硬门禁：

1. 页面显示的状态必须能追溯到 canonical backend。
2. 页面按钮触发的动作必须走既有 action registry 或 detail route。
3. 033/034 未就绪时必须显式 degraded，不能 UI 伪造“连续上下文”和“压缩状态”。
4. 验收必须证明用户不用终端也能走通至少一条完整路径。

## 6. 最终建议

035 应作为 M4 的下一项正式 Feature 推进，并明确依赖：

- 033 负责主 Agent 上下文对象与 provenance
- 034 负责 compaction evidence
- 035 负责把这些能力变成用户真能看懂、真能操作的工作台

只有这样，OctoAgent 才能从“能力已经很多的系统”真正变成“普通人可以日常使用的产品”。
