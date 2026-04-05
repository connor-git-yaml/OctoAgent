# 附录

> 本文件是 [blueprint.md](../blueprint.md) 附录 A + B 的完整内容。

---

## 附录 A：术语表（Glossary）

**架构层级：**

- Free Loop：LLM 驱动的自主推理循环，Orchestrator 和 Workers 的核心运行模式——自主决策下一步行动，不预设固定流程
- Orchestrator（协调器）：Free Loop 驱动的路由与监督层（目标理解、Worker 派发、全局停止条件）
- Worker（自治智能体）：Free Loop 驱动的执行层，独立上下文、自主决策，可调用 Skill/Tool/Skill Pipeline
- Gateway：渠道适配层，负责消息标准化（NormalizedMessage）、出站发送、SSE/WS 流式推送
- Skill Pipeline（Graph Engine）：Worker 的确定性编排工具（DAG/FSM + checkpoint），非独立执行模式
- Skill：强类型执行单元（Input/Output contract，Pydantic 模型校验输入输出）
- Tool：可被 LLM 调用的函数/能力（schema 反射 + 风险标注）
- Policy / 权限：工具访问由 check_permission()（PermissionPreset × SideEffectLevel 矩阵）+ ApprovalManager 审批流控制
- JobRunner：执行隔离层，统一接口（start/status/cancel/stream_logs/collect_artifacts），后端支持 Docker/SSH/远程

**数据模型：**

- Task：可追踪的工作单元（状态机：CREATED → QUEUED → RUNNING → ... → 终态）
- Event：不可变事件记录（append-only），系统的事实来源
- Artifact：任务产物（多 Part 结构：text/file/json/image，支持版本化与流式追加）
- Checkpoint：Skill Pipeline 节点级快照（node_id + state），用于崩溃后确定性恢复
- NormalizedMessage：统一消息格式，屏蔽渠道差异（Telegram/Web/导入 → 统一结构）
- A2A-Lite：内部 Agent 间通信协议 envelope（TASK/UPDATE/CANCEL/RESULT/ERROR/HEARTBEAT）

**记忆系统：**

- SoR：Source of Record，权威记忆线（同 subject_key 永远只有 1 条 current；旧版标记 superseded）
- Fragments：事件记忆线（append-only，保存对话/工具执行/摘要，用于证据与回放）
- Vault：敏感数据分区（默认不可检索，需要授权才能访问）
- WriteProposal：记忆写入提案（ADD/UPDATE/DELETE/NONE），必须经仲裁器验证后才能提交

**基础设施：**

- LiteLLM Proxy：模型网关，alias 路由（router/extractor/planner/executor/summarizer/fallback）与治理层
- Thread / Scope：消息关联维度；thread_id 标识对话线程，scope_id 标识归属范围（如 `chat:telegram:123`）

---

## 附录 B：示例配置片段（无链接版）

### B.1 system.yaml（示例）

```yaml
system:
  timezone: "Asia/Singapore"
  base_url: "http://localhost:9000"

provider:
  litellm:
    base_url: "http://localhost:4000/v1"
    api_key: "ENV:LITELLM_API_KEY"

# 对齐 §8.9.1 / §8.9.2：legacy 语义 alias fallback
model_alias_map:
  router: "cheap"                  # 意图分类、风险分级（小模型）
  extractor: "cheap"               # 结构化抽取（小/中模型）
  planner: "main"                  # 多约束规划（大模型）
  executor: "main"                 # 高风险执行前确认（大模型）
  summarizer: "cheap"              # 摘要/压缩（小模型）
  fallback: "fallback"             # 备用 provider

runtime_models:
  cheap: "alias/cheap"
  main: "alias/main"
  fallback: "alias/fallback"

storage:
  sqlite_path: "./data/sqlite/octoagent.db"
  artifacts_dir: "./data/artifacts"
  vault_dir: "./data/vault"
  lancedb_path: "./data/lancedb"   # 向量数据库（Memory + ToolIndex）

# 对齐 §8.6 权限与审批
policy:
  default:
    read_only: allow
    reversible: allow
    irreversible: ask
  # per-project 策略覆盖示例
  # projects:
  #   ops:
  #     reversible: ask            # ops 项目提升到 ask

# 对齐 §15 风险阈值
limits:
  worker_max_iterations: 50        # Free Loop 单任务迭代上限
  skill_max_retries: 3             # Skill Pipeline 节点重试上限
  tool_output_max_chars: 4000      # 超过此值自动压缩为 summary + artifact

observability:
  logfire_token: "ENV:LOGFIRE_TOKEN"
  log_format: "dev"                # dev（pretty print）| json（生产）
```

### B.2 telegram.yaml（示例）

```yaml
telegram:
  mode: "webhook"
  bot_token: "ENV:TELEGRAM_BOT_TOKEN"
  allowlist:
    users: ["123456"]
    groups: ["-10011223344"]
  thread_mapping:
    dm: "tg:{user_id}"
    group: "tg_group:{chat_id}"
```

---

**END**
