# 契约：System Prompt Pipeline 注入

**Feature**: 065
**改动文件**: `octoagent/apps/gateway/src/octoagent/gateway/services/llm_service.py`、`butler_behavior.py`

---

## Worker / Subagent System Prompt 注入

### 注入位置

在现有 `## Available Skills` 段落之后，紧接注入 `## Available Pipelines` 段落。

### 注入条件

- PipelineRegistry 缓存中有至少 1 个 Pipeline → 注入
- 缓存为空 → **不注入**（避免空状态噪音，FR-065-07 AC-04）

### 注入格式

```markdown
## Available Pipelines

Deterministic workflow pipelines you can start, monitor and manage via `graph_pipeline` tool.

- **deploy-staging**: 将代码部署到 staging 环境 (trigger: 当用户要求部署到 staging 时使用)
- **data-migration**: 数据迁移流程 (trigger: 当需要跨环境数据同步时使用)

### Pipelines vs Subagents

Use **Pipeline** (graph_pipeline tool) when:
- The task follows a known, repeatable sequence of steps
- Steps need checkpoint/recovery guarantees (e.g., deploy, data migration)
- Steps include approval gates or human review points
- Deterministic execution is preferred over LLM reasoning

Use **Subagent** (subagents tool) when:
- The task requires exploration, reasoning, or multi-turn interaction
- The approach is not predetermined and needs LLM judgment
- The task involves creative work (writing, analysis, research)
- Flexibility is more important than determinism
```

### Token 预算控制

- 单个 Pipeline 摘要不超过 3 行（id + 一句描述 + trigger hint）
- 语义区分指引固定约 150 tokens
- 总体注入不超过 `len(pipelines) * 50 + 200` 字符

---

## Butler System Prompt 注入

### 注入位置

在 `build_butler_behavior_prompt()` 的 `decision_modes` 描述之后。

### 注入内容

```
decision_modes: direct_answer, ask_once, delegate_research, delegate_dev, delegate_ops, best_effort_answer, delegate_graph

delegate_graph: 当用户请求精确匹配某个预定义 Pipeline 时使用。需要在 decision 中设置 pipeline_id 和 pipeline_params。

Available Pipelines for delegation:
- deploy-staging: 将代码部署到 staging 环境 (trigger: 当用户要求部署到 staging 时使用)
  input: branch (string, required), skip_tests (boolean)
```

### Butler Decision 解析扩展

`_parse_butler_decision()` 中新增对 `delegate_graph` mode 的识别：

```python
if normalized.get("mode") == "delegate_graph":
    if not normalized.get("pipeline_id"):
        # fallback 到就近 Worker 类型
        return _fallback_to_worker(normalized)
```
