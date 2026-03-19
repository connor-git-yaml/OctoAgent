# Data Model: Memory Automation Pipeline -- Phase 3

**Date**: 2026-03-19 | **Spec**: `spec.md` | **Plan**: `plan-phase3.md`

## 概述

Phase 3 不新增数据库表，完全复用 Phase 1+2 已有的 `sor`、`derived_memory` 表结构。以下记录各功能如何使用现有模型。

---

## 1. ToM 记录（US-7）

**存储位置**: `derived_memory` 表（已有）

**使用 DerivedMemoryRecord 模型**:

| 字段 | Phase 3 ToM 用法 | 示例 |
|------|------------------|------|
| `derived_id` | `derived:tom:{scope_id}:{timestamp_ms}:{idx}` | `derived:tom:scope-001:1710856400000:0` |
| `scope_id` | 继承自触发 Consolidate 的 scope | `scope-001` |
| `partition` | 继承自源 SoR 的 partition | `work` |
| `derived_type` | 固定为 `"tom"` | `"tom"` |
| `subject_key` | `ToM/{dimension}/{topic}` | `ToM/preference/编程语言` |
| `summary` | 推断的自然语言描述 | `Connor 偏好使用 Python + SQLite 的轻量级方案` |
| `confidence` | 推断置信度 (0.0-1.0) | `0.8` |
| `payload` | ToM 特有结构 | 见下方 |
| `source_fragment_refs` | 源 Fragment ID 列表 | `["frag-001", "frag-002"]` |
| `created_at` | 提取时间 | `2026-03-19T12:00:00Z` |

**payload 结构**:

```json
{
  "tom_dimension": "intent|preference|knowledge_level|emotional_state",
  "domain": "领域标识",
  "evidence": "支持此推断的简要证据描述",
  "source_memory_ids": ["mem-001"]
}
```

**ToM 维度说明**:

| tom_dimension | 语义 | 示例 subject_key | 示例 summary |
|---------------|------|-----------------|-------------|
| `intent` | 用户当前意图/目标 | `ToM/intent/Memory系统优化` | `Connor 近期关注 OctoAgent 的 Memory 系统优化` |
| `preference` | 持续性偏好 | `ToM/preference/技术栈` | `Connor 偏好 Python + SQLite 的轻量方案` |
| `knowledge_level` | 某领域知识水平 | `ToM/knowledge_level/分布式系统` | `Connor 在分布式系统领域具有高级工程师水平` |
| `emotional_state` | 情绪/态度倾向 | `ToM/emotional_state/检索质量` | `Connor 对当前 Memory 检索质量不太满意` |

---

## 2. Temporal Decay + MMR（US-8）

**无持久化**: Temporal Decay 和 MMR 是查询时计算，不产出新记录。

**MemorySearchHit.metadata 新增字段**:

| 字段 | 类型 | 说明 |
|------|------|------|
| `recall_temporal_decay_factor` | `float` | 时间衰减因子 (0.0-1.0)，1.0 表示无衰减 |
| `recall_decay_adjusted_score` | `float` | 衰减调整后的综合分数 |
| `recall_mmr_rank` | `int` | MMR 选择后的排名 (0-based) |

**MemoryRecallHookOptions 新增字段**:

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `temporal_decay_enabled` | `bool` | `False` | 是否启用时间衰减 |
| `temporal_decay_half_life_days` | `float` | `30.0` | 半衰期天数 |
| `mmr_enabled` | `bool` | `False` | 是否启用 MMR 去重 |
| `mmr_lambda` | `float` | `0.7` | MMR 权衡参数 (0=纯多样性, 1=纯相关性) |

**MemoryRecallHookTrace 新增字段**:

| 字段 | 类型 | 说明 |
|------|------|------|
| `temporal_decay_applied` | `bool` | 是否实际应用了时间衰减 |
| `temporal_decay_half_life_days` | `float` | 实际使用的半衰期 |
| `mmr_applied` | `bool` | 是否实际应用了 MMR |
| `mmr_lambda` | `float` | 实际使用的 MMR lambda |
| `mmr_removed_count` | `int` | MMR 去重移除的候选数 |

---

## 3. 用户画像（US-9）

**存储位置**: `sor` 表（已有）

**使用 SorRecord 模型**:

| 字段 | Phase 3 画像用法 | 示例 |
|------|------------------|------|
| `memory_id` | 标准 ULID | `01J...` |
| `scope_id` | 用户的 scope | `scope-001` |
| `partition` | 固定为 `profile` | `profile` |
| `subject_key` | `用户画像/{维度}` | `用户画像/技术偏好` |
| `content` | 自然语言画像描述 | `Connor 主要使用 Python 3.12+ 开发...` |
| `version` | 自增版本号 | `3` |
| `status` | `CURRENT`（最新版本） | `CURRENT` |
| `metadata` | 画像元信息 | 见下方 |

**metadata 结构**:

```json
{
  "source": "profile_generator",
  "generated_at": "2026-03-19T02:00:00Z",
  "sor_count": 52,
  "derived_count": 28,
  "tom_count": 5
}
```

**画像维度预定义**:

| subject_key | 描述 | 示例 content |
|-------------|------|-------------|
| `用户画像/基本信息` | 姓名、职业、所在地 | `Connor Lu 是一名软件工程师，从事 AI 相关开发工作` |
| `用户画像/工作领域` | 行业、方向 | `主要从事 AI Agent 系统开发，当前项目是 OctoAgent（个人智能操作系统）` |
| `用户画像/技术偏好` | 语言、框架、工具 | `偏好 Python 3.12+ + FastAPI + SQLite WAL 的轻量本地优先方案` |
| `用户画像/个人偏好` | 饮食、习惯、爱好 | `喜欢日式料理，工作时喜欢听环境音乐` |
| `用户画像/常用工具` | 软件、平台、服务 | `日常使用 Claude Code、VS Code、GitHub、Telegram` |
| `用户画像/近期关注` | 最近的关注点 | `近期关注 Memory 自动化管线优化和 Recall 质量提升` |

---

## 4. Scheduler 作业（US-9）

**存储位置**: AutomationJob（已有模型）

**新增作业**:

```python
AutomationJob(
    job_id="system:memory-profile-generate",
    name="Memory Profile Generate (用户画像)",
    action_id="memory.profile_generate",
    params={},
    schedule_kind=AutomationScheduleKind.CRON,
    schedule_expr="0 2 * * *",  # 每天凌晨 2 点 UTC
    timezone="UTC",
    enabled=True,
)
```

---

## 5. 返回值数据模型（新增）

### ToMExtractionResult

```python
@dataclass(slots=True)
class ToMExtractionResult:
    scope_id: str
    extracted: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
```

### ProfileGenerateResult

```python
@dataclass(slots=True)
class ProfileGenerateResult:
    scope_id: str
    dimensions_generated: int = 0   # 新增的画像维度数
    dimensions_updated: int = 0     # 更新的画像维度数
    skipped: bool = False           # 数据不足跳过
    errors: list[str] = field(default_factory=list)
```

### ConsolidationScopeResult 扩展

```python
@dataclass(slots=True)
class ConsolidationScopeResult:
    scope_id: str
    consolidated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    derived_extracted: int = 0     # Phase 2 已有
    tom_extracted: int = 0         # Phase 3 新增
```
