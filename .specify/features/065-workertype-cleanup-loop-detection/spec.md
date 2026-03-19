---
feature_id: "065-workertype-cleanup-loop-detection"
title: "WorkerType 清理 + 循环检测 + Config 管理工具"
milestone: M1
status: draft
created: 2026-03-19
updated: 2026-03-19
depends_on: ["064-butler-dispatch-redesign"]
---

# Feature 065: WorkerType 清理 + 循环检测 + Config 管理工具

## 1. 动机

Feature 064 暴露了三个架构级问题：

### 1.1 WorkerType 已失去实际意义

`WorkerType(GENERAL/OPS/RESEARCH/DEV)` 枚举有 146 处引用，但：
- 20 个内建工具中 18 个声明 `worker_types=["ops", "research", "dev", "general"]` — 过滤形同虚设
- Feature 064 Phase 1 删除了 model decision（DELEGATE_RESEARCH/DEV/OPS 不再由 LLM 判断），WorkerType 的路由职能已废弃
- Agent 的差异化应通过 **PermissionPreset（MINIMAL/NORMAL/FULL）** 和 **Behavior Files（IDENTITY/SOUL/HEARTBEAT）** 表达，不是 WorkerType
- CLAUDE.md 架构规范要求"发现坏味道必须同 PR 清理"

### 1.2 无循环检测导致空转

用户发送"配置 Provider"任务，Worker 在 11 分钟内执行了 26 批 terminal.exec，反复读取相同文件却无法编辑——因为没有检测到 Worker 在重复相同操作。SkillRunner 虽有重复 tool_call signature 检测（超 3 次转 FAILED），但粒度不够——相同工具不同参数（如 `rg` 不同关键词搜同一文件）不会被捕获。

### 1.3 缺少 Config 管理工具

Worker 无法完成"配置 Provider"任务的根因是：没有 `config.edit_provider` / `config.add_model_alias` 等工具。Worker 只能用 `terminal.exec` 手写 YAML，而 YAML 编辑容易出错、需要后续 `octo config sync`。

## 2. 设计

### 2.1 WorkerType 清理

**目标**：删除 `WorkerType` 枚举，所有 Agent 共享同一个工具集，差异化通过 PermissionPreset 控制。

**变更清单**：

| 层 | 变更 | 影响文件 |
|---|------|---------|
| 数据模型 | 删除 `WorkerType` 枚举 | capability.py |
| 数据模型 | `Work.selected_worker_type` 改为 `str`（向后兼容，不再校验枚举） | delegation.py |
| 工具定义 | 删除所有 `worker_types=` 参数 | capability_pack.py (~20 处) |
| 工具索引 | `ToolIndexQuery.worker_type` 删除或改为可选 str | capability.py |
| ToolIndex 查询 | 移除 worker_type 过滤逻辑 | capability_pack.py |
| Butler 行为 | 删除 `DELEGATE_RESEARCH/DEV/OPS` 模式（已由 Feature 064 废弃） | behavior.py |
| Orchestrator | 删除 `_resolve_single_loop_worker_type()` 和 WorkerType 转换逻辑 | orchestrator.py |
| Worker 适配器 | 4 个 LLMWorkerAdapter 合并为 1 个（不再按类型分） | orchestrator.py |
| 前端 | 删除 WorkerType 相关展示（如有） | AgentCenter 等 |
| 测试 | 更新所有引用 WorkerType 的测试 | ~8 个测试文件 |

**保留**：
- `WorkerCapabilityProfile` 保留，但 `worker_type` 字段改为可选 str（兼容老数据）
- `PermissionPreset` 不变（已是工具可见性的控制机制）

### 2.2 循环检测

**目标**：检测 Agent 在重复执行无效操作，及时停止并报告。

**实现层级**：SkillRunner

**检测策略**：

```python
class LoopDetector:
    """检测 Agent 重复执行相同操作的模式。"""

    def __init__(self, window_size: int = 5, similarity_threshold: float = 0.8):
        self._recent_calls: list[tuple[str, str]] = []  # (tool_name, args_hash)

    def record_call(self, tool_name: str, arguments: dict) -> None:
        args_hash = hashlib.md5(json.dumps(arguments, sort_keys=True).encode()).hexdigest()[:8]
        self._recent_calls.append((tool_name, args_hash))
        if len(self._recent_calls) > self.window_size * 2:
            self._recent_calls = self._recent_calls[-self.window_size * 2:]

    def detect_loop(self) -> str | None:
        """返回循环描述（如果检测到），否则 None。"""
        if len(self._recent_calls) < self.window_size:
            return None

        recent = self._recent_calls[-self.window_size:]
        # 检测：最近 N 次调用中，相同 (tool_name, args_hash) 出现超过阈值
        from collections import Counter
        counter = Counter(recent)
        most_common, count = counter.most_common(1)[0]
        if count >= self.window_size * self.similarity_threshold:
            return f"检测到循环：{most_common[0]} 在最近 {self.window_size} 次中重复 {count} 次"
        return None
```

**触发行为**：检测到循环时，注入系统提示告知 LLM：
```
[系统提示] 检测到你在重复执行相同操作（{tool_name} 已连续调用 {count} 次，参数相似度 {similarity}%）。
请停下来重新评估：
1. 你是否缺少完成任务所需的工具或权限？
2. 是否需要换一种方式处理？
3. 如果确实无法完成，请告知用户原因。
```

### 2.3 Config 管理工具

**目标**：让 Agent 能直接管理 OctoAgent 配置，不需要手写 YAML。

**工具清单**：

| 工具名 | 用途 | SideEffectLevel |
|-------|------|----------------|
| `config.list_providers` | 列出当前所有 Provider 及状态 | NONE |
| `config.add_provider` | 添加新 Provider（name, auth_type, env_var, base_url） | REVERSIBLE |
| `config.edit_provider` | 编辑现有 Provider 配置 | REVERSIBLE |
| `config.remove_provider` | 删除 Provider | IRREVERSIBLE |
| `config.list_model_aliases` | 列出当前模型别名映射 | NONE |
| `config.add_model_alias` | 添加模型别名（alias_name, provider, model_name） | REVERSIBLE |
| `config.edit_model_alias` | 编辑模型别名配置 | REVERSIBLE |
| `config.sync` | 触发 `octo config sync`（重新生成 litellm-config.yaml） | REVERSIBLE |

**实现方式**：包装现有的 `config_wizard.py` / `config_commands.py` API，通过 `@tool_contract` 注册到 ToolBroker。

## 3. 分阶段实施

| 阶段 | 内容 | 预计文件数 |
|------|------|----------|
| **Step 1** | WorkerType 清理（删除枚举 + 工具定义 + 查询逻辑 + 测试） | ~15 |
| **Step 2** | 循环检测（LoopDetector + SkillRunner 集成） | ~3 |
| **Step 3** | Config 管理工具（8 个工具定义 + 执行器） | ~5 |

## 4. 验证标准

- [ ] `WorkerType` 枚举已删除，`grep -r "WorkerType" octoagent/` 返回 0 结果
- [ ] 所有 Agent 看到相同的工具集（不按类型过滤）
- [ ] SkillRunner 在连续 5 次重复工具调用后注入循环提示
- [ ] Agent 可通过 `config.add_provider` + `config.sync` 完成 Provider 配置
- [ ] 现有测试通过（更新后）
- [ ] 手动测试："配置硅基流动 Provider" 任务可在 3 轮内完成
