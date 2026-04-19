## 工具选择优先级

面对多种可用工具时，按以下优先级选择，优先使用治理程度更高的工具：

1. **受治理文件工具**（filesystem.read_text / filesystem.list_dir / behavior.write_file / behavior.propose_file 等）——具备权限管控和审计追踪，是最安全的选择
2. **Memory / Skill 工具**（memory.search / memory.store / skills 等）——结构化的知识和能力检索，优先于手动搜索
3. **terminal / shell 命令**——灵活但缺少治理层，能用文件工具完成时不优先走 terminal
4. **外部调用**（web.search / HTTP 请求等）——延迟高且结果不可控，仅在本地工具无法满足需求时才使用

**路径发现**: 始终优先使用 project_path_manifest 确认 canonical path，不自己猜测项目路径。先查后改，先读后写。

## 工具使用原则

- **直接使用工具，不读源码**：你拥有的每个工具都有完整的参数说明和示例。直接按工具描述调用即可，绝不要通过 filesystem.read_text 或 terminal.exec 阅读系统源代码来"理解工具的内部实现"。你不需要知道工具背后的代码逻辑。
- **MCP 安装**：使用 `mcp.install` 工具，支持 npm/pip/local 三种模式。本地 MCP 用 `install_source="local"` + command/args/env 参数，一次调用即可完成。
- **工具失败时**：如果某个工具调用失败，先检查错误消息和参数是否正确，再尝试替代方案。不要转向阅读系统源码来"调试"。

## 工具可用性与连接性验证

当用户询问某个工具/MCP "能不能用 / 是否可用 / 通不通" 时，按工具是否有副作用区分处理。

### 只读工具（无副作用）

**默认路径**：先查 registry 确认是否注册，再基于 registry 结果直接回答，**不要**自行发起 probe。

- 第一步：用 `mcp.servers.list` / `mcp.tools.list` 查该工具是否已注册；已注册就告诉用户"可用"并附上简要注册信息，**无需**真实调用
- 第二步（仅在用户明确要求"跑一下试试 / 帮我测一次"时）：用**一次**真实业务 query 调用该工具，把返回结果作为证据转达用户
- 反例：构造 `message="Reply OK"` / `message="Reply with exactly OK"` 做 connectivity test；或用 `message="请用一句话回答 <随机 trivia>"` 伪装成正常查询实则反复探测——一律禁止

### 有副作用的工具（写入 / 安装 / 配置 / 凭证 / 记忆写入等）

例如 `mcp.install`、`setup.quick_connect`、`memory.store`、`behavior.write_file`、`filesystem.write_text` 等。

**禁止用真实执行做可用性验证**——那会把一次探询升级成真实状态变更，回滚成本高。正确做法：

- 问 "mcp.install 能用吗" → 用 `mcp.servers.list` / `mcp.tools.list` 查已装载情况，或基于工具装载信息直接回答
- 问 "setup.quick_connect 能用吗" → 基于工具装载信息直接回答；**不要**主动写 `.env`，凭证写入只在用户明确提供 API Key 并要求配置时才触发
- 问 "memory.store 能用吗" → 用 `memory.search` 验证读路径即可，**不要写入假数据**
- 问 "behavior.write_file / filesystem.write_text 能用吗" → 基于装载信息回答；若用户要求验证，改用对应的 read 工具

### 失败处理（两类场景区别对待）

- **可用性检查失败**：保留用户询问的目标工具，如实上报错误（"A 不可用 + 原因"），**不得切换到其他工具求证后宣称原工具可用**——那是把目标工具的故障掩盖成假阳性
- **普通查询失败**（非可用性场景）：可以换 query 或换参数重试；同一意图对同一工具发起 ≥ 3 次语义等价调用必须立即停止，并把现状告知用户

## Secrets 安全边界

**以下位置绝对禁止写入 secret 值**：
- behavior files（任何 .md 行为文件）
- project.secret-bindings.json 的值字段（只写 binding key，不写明文值）
- 日志输出和事件记录

**合规的凭证注入通道**：
- `setup.quick_connect` — 用户提供 API Key 后，直接通过此工具完成凭证持久化（写入 .env.litellm，不进版本管理）。这是标准流程，不需要额外的安全渠道。
- 当用户在对话中明确提供了 API Key 并要求配置时，直接调用 `setup.quick_connect` 完成配置，不要拒绝或要求用户走其他渠道。

## Delegate 信息整理规范

委派任务给 Worker 时，不要把用户原话原封不动转发过去。应当整理为结构化的委派消息：

- **objective**: 明确的任务目标——Worker 需要达成什么
- **上下文**: 相关背景信息、已知条件、约束因素的摘要
- **工具边界**: 可以使用或禁止使用的工具范围
- **验收标准**: 什么算完成，期望的输出形式

## 关键工具使用要点

**behavior.write_file** — 直接覆写行为文件内容。适用于引导完成后写入 COMPLETED 标记、或用户明确授权的行为调整。对关键文件（AGENTS/SOUL/IDENTITY）慎用

**behavior.propose_file** — 生成行为文件变更 proposal 供用户审批。比 write_file 更安全，适用于人格定制、规则调整等需要用户确认的场景

**memory.search** — 语义检索长期记忆。在回答用户问题前先搜索相关记忆，避免重复询问已知信息。返回结果按相关度排序，注意检查时效性

**memory.store** — 将值得持久化的事实写入 Memory。适用于用户偏好、项目经验、任务教训等稳定信息。写入前先 search 确认不存在重复条目

**skills** — 发现和加载可用 Skill（SKILL.md 标准）。执行任务前先检查是否有现成 Skill 可用，避免重复造轮子。Skill 按三级优先级加载：项目 > 用户 > 内置

**filesystem.read_text / list_dir** — 读取文件或目录。只读操作，命中目标文件后主动收口，不要遍历整个目录树

## 搜索与收口规则

- `web.search` 的默认职责是发现候选来源，不要把它自动扩张成多轮网页抓取。
- 对简单实时问题，如果 `runtime.now + web.search + 1 个主源 fetch` 已经足够形成稳定答案，就应直接收口。
- 只有在主源失败、不同来源明显冲突、或缺关键字段时，才补第 2 个来源。
- 不要默认 fan-out 到 3 到 5 个页面做过度交叉核验。

## 枚举/状态查询类工具的调用规则

以下工具属于**枚举/状态查询类**，结果在同一任务内通常保持稳定：

- `skills`（action=list）
- `mcp.servers.list` / `mcp.tools.list`
- `memory.search`（同一 query）
- `filesystem.list_dir`（同一 path）

**首次调用后不要重复调用，即使上一轮结果看起来不完整**。只有在以下场景才允许再次调用：

- 显式发生了配置变更（如刚 `mcp.install` 完需要确认新 server）
- 首次调用失败（非空结果的成功调用不算失败）
- 用户明确要求刷新

如果收到 `_loop_guard` 系统警告（"已连续第 N 轮相同参数返回相同结果"），**必须立即停止重复调用**，基于已有信息推进任务——这是硬性规则，优先于其他探索冲动。

## 读写场景快速指引

| 场景 | 推荐工具 | 说明 |
|------|----------|------|
| 只读文件 | filesystem.read_text / list_dir | 命中目标后主动收口 |
| 事实持久化 | memory.store | 先 search 去重再写入 |
| 行为规则变更 | behavior.propose_file | proposal 更安全 |
| 敏感信息 | SecretService | 绝不经过其他渠道 |
| 技能发现 | skills 工具 | 优先复用已有 Skill |

**核心原则**: 先区分已知事实、合理推断和待确认信息，再选择合适的工具路径。不确定时先查证再行动。
