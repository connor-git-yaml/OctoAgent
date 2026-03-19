# Contract: ProfileGeneratorService

**Phase**: 3 | **US**: 9 | **FR**: 022, 023

## 接口概览

```python
class ProfileGeneratorService:
    """用户画像自动生成服务。"""

    def __init__(
        self,
        memory_store: SqliteMemoryStore,
        llm_service: LlmServiceProtocol | None,
        project_root: Path,
    ) -> None: ...

    async def generate_profile(
        self,
        *,
        memory: MemoryService,
        scope_id: str,
        model_alias: str = "",
    ) -> ProfileGenerateResult: ...
```

## 输入

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `memory` | `MemoryService` | Y | 用于走 propose/validate/commit 治理流程 |
| `scope_id` | `str` | Y | 目标 scope ID |
| `model_alias` | `str` | N | LLM 模型别名 |

## 输出

```python
@dataclass(slots=True)
class ProfileGenerateResult:
    scope_id: str
    dimensions_generated: int = 0   # 新增的画像维度
    dimensions_updated: int = 0     # 更新的画像维度
    skipped: bool = False           # 数据不足跳过
    errors: list[str] = field(default_factory=list)
```

## 执行流程

1. **数据聚合**
   - 查询 `search_sor(scope_id, partition in [core, profile, work], limit=200)`
   - 查询 Derived 记录（`derived_types=["entity", "relation", "tom"]`, limit=100）
   - 查询已有画像（`search_sor(scope_id, partition=profile)`）

2. **最低数据阈值**
   - SoR 记录数 < 5 且 Derived 记录数 < 3 时，跳过生成（`skipped=True`）
   - 避免在数据不足时生成低质量画像

3. **LLM 生成**
   - 将 SoR + Derived + 已有画像格式化为 prompt
   - 调用 LLM 生成 6 个维度的画像 JSON
   - 解析 LLM 输出

4. **治理写入**
   - 逐维度通过 `propose_write -> validate_proposal -> commit_memory`
   - `partition=profile`, `subject_key="用户画像/{维度}"`
   - 已有画像维度执行 UPDATE，新维度执行 ADD
   - LLM 返回 null 的维度跳过（数据不足）

## 画像维度

| subject_key | 说明 |
|-------------|------|
| `用户画像/基本信息` | 姓名、职业、所在地 |
| `用户画像/工作领域` | 行业、方向、当前项目 |
| `用户画像/技术偏好` | 语言、框架、工具链 |
| `用户画像/个人偏好` | 饮食、习惯、兴趣 |
| `用户画像/常用工具` | 软件、平台、服务 |
| `用户画像/近期关注` | 最近关注的事情 |

## 前置条件

- `llm_service` 不为 None
- scope 中有至少 5 条 SoR 或 3 条 Derived 记录

## 后置条件

- 成功：`partition=profile` 的 SoR 记录更新/新增
- 失败：不抛异常，错误记录在 `result.errors` 中
- 所有写入走完整治理流程（符合宪法原则 12）

## 降级行为

| 场景 | 行为 |
|------|------|
| LLM 服务不可用 | 返回 `errors=["LLM 服务未配置"]`，skipped=True |
| LLM 调用失败 | 记录错误，Scheduler 下次重试 |
| LLM 输出无法解析 | 返回 errors，跳过此次生成 |
| 数据不足 | 返回 skipped=True，不调用 LLM |
| 单维度写入失败 | 记录错误，继续处理其他维度 |

## Scheduler 注册

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

## 调用方

- `ControlPlaneService._handle_memory_profile_generate()` -- Scheduler 定时触发
- 管理台手动触发（通过 execute_action API）
