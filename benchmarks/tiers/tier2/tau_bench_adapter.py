"""benchmarks/tiers/tier2/tau_bench_adapter.py — τ-bench airline adapter

15 task 分层抽样 + ToolRegistry 临时注册 contextmanager + per-task env reset.

PoC §3-4 实测决策（phase-0-poc-report.md）：
- airline tasks 数 = 50（PoC-H2 PASS，分层抽 15）
- actions 字段名 = "actions"（W5 实测：list[dict] of {"name", "arguments"}）
- MockAirlineDomainEnv.reset() 作为 per-task mock DB 重置（PoC-H4 主方案；
  Phase B T-B-5 跑 2 个连续 task 验证；不成立 → file-based isolation 降级）

引用文件:
- spec FR-E01（contextmanager 临时注册 + race 防御）
- spec FR-E02（15 task 6 类分层）
- spec FR-B05（τ-bench user simulator Sonnet 4.6）
- plan.md §4.1
- known-issues-deltas.md F-PA-1 (后 fetch_events_from_store async；不直接影响本 adapter)
"""
from __future__ import annotations

import contextlib
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from octoagent.gateway.harness.tool_registry import ToolRegistry


# ============================================================
# 15 task 分层抽样（FR-E02）
# ============================================================

# 6 类操作的目标抽样数（sum=15）；plan 处理顺序 == 桶优先级
# 稀缺桶先抢（contains-action 模式：passenger / baggage / payment），
# 因为这些 action 通常不是首要 action，会被首要 action 模式 (booking/cancellation/upgrade)
# 误抢；按 plan 顺序处理 + 单 task 单桶可保证 15 task 全分布.
#
# PoC §4 实测分布（contains-action 优先后，扣除重叠）:
#   passenger=3 / baggage=4 / payment=3 / booking=3 / cancellation=6 / upgrade=3
# 选 plan: booking=3（实际 max）/ cancellation=4 / upgrade=3
#   + passenger=2 / baggage=1 / payment=2 = 15 task 全 6 类覆盖
STRATIFIED_SAMPLING_PLAN: dict[str, int] = {
    "passenger": 2,
    "baggage": 1,
    "payment": 2,
    "booking": 3,
    "cancellation": 4,
    "upgrade": 3,
}

# 稀缺桶：actions 中含目标 action 的 task 分到此桶（**contains-action 优先**）
_CONTAINS_ACTION_BUCKETS: dict[str, str] = {
    "update_reservation_passengers": "passenger",
    "update_reservation_baggages": "baggage",
    "send_certificate": "payment",
}

# 兜底：首要 action 桶（仅在 task 不含任何稀缺 contains-action 时使用）
_FIRST_ACTION_BUCKETS: dict[str, str] = {
    "book_reservation": "booking",
    "cancel_reservation": "cancellation",
    "update_reservation_flights": "upgrade",
}


@dataclass
class TauBenchTaskMeta:
    """单个 τ-bench airline task 的元数据，对应 OctoBench BenchmarkTask schema。"""

    task_idx: int                      # 原 tasks 列表的 index（确定性映射回 τ-bench）
    task_id: str                       # OctoBench 标识（T2-TAU-<bucket>-<idx>）
    user_id: str
    instruction: str
    actions: list[dict[str, Any]]      # W5 实测：list[dict] of {"name", "arguments"}
    bucket: str                        # 6 类之一


def _bucket_for_task(task: dict[str, Any]) -> str | None:
    """返回 task 所属 bucket name；contains-action（稀缺桶）优先，首要 action 兜底.

    优先级（保证 passenger/baggage/payment 稀缺桶得到充足样本）：
    1. 如果 actions 中含 update_reservation_passengers → passenger
    2. 否则含 update_reservation_baggages → baggage
    3. 否则含 send_certificate → payment
    4. 否则首要 action ∈ {book_reservation/cancel_reservation/update_reservation_flights}
       → booking/cancellation/upgrade
    5. 都不命中 → None
    """
    actions = task.get("actions") or []
    if not actions:
        return None

    all_action_names = {a.get("name", "") for a in actions}

    # 稀缺桶优先（contains-action 模式）
    for action_name, bucket in _CONTAINS_ACTION_BUCKETS.items():
        if action_name in all_action_names:
            return bucket

    # 首要 action 兜底
    first_action_name = actions[0].get("name", "")
    if first_action_name in _FIRST_ACTION_BUCKETS:
        return _FIRST_ACTION_BUCKETS[first_action_name]

    return None


def stratified_sample(
    tasks: list[dict[str, Any]],
    plan: dict[str, int] | None = None,
) -> list[TauBenchTaskMeta]:
    """从完整 50 task 中分层抽 15 个 TauBenchTaskMeta。

    抽样规则（FR-E02）：
    - 6 类 bucket，每桶按 plan 中的目标数量取
    - 同桶内按 task_idx 升序（确定性，可重现）
    - 缺额时**仅返回实际可用样本**（不偷取其他桶补足），避免静默引入非分层 task；
      调用方可通过 `len(result)` 检测缺额

    Args:
        tasks: τ-bench airline tasks 完整列表（一般 50 个）
        plan: 自定义抽样 plan；None 时用 STRATIFIED_SAMPLING_PLAN

    Returns:
        list[TauBenchTaskMeta]: 按 plan 顺序拼接的样本列表
    """
    sampling_plan = plan or STRATIFIED_SAMPLING_PLAN
    buckets: dict[str, list[int]] = {b: [] for b in sampling_plan}
    for idx, task in enumerate(tasks):
        bucket = _bucket_for_task(task)
        if bucket and bucket in buckets:
            buckets[bucket].append(idx)

    sampled: list[TauBenchTaskMeta] = []
    for bucket_name, target_count in sampling_plan.items():
        available_indices = buckets.get(bucket_name, [])
        picked = available_indices[:target_count]
        for task_idx in picked:
            task = tasks[task_idx]
            sampled.append(
                TauBenchTaskMeta(
                    task_idx=task_idx,
                    task_id=f"T2-TAU-{bucket_name.upper()}-{task_idx:03d}",
                    user_id=task.get("user_id", ""),
                    instruction=task.get("instruction", ""),
                    actions=list(task.get("actions") or []),
                    bucket=bucket_name,
                )
            )
    return sampled


# ============================================================
# ToolRegistry 临时注册 contextmanager（FR-E01）
# ============================================================

# 全局 lock 保证 tau-bench 工具批量注册/清理的原子性（FR-E01 race 防御）
_REGISTRY_LOCK = threading.Lock()

# 注册名前缀：与 production 工具命名空间隔离 + 便于 audit 过滤
TAU_BENCH_TOOL_PREFIX = "tau_bench__"

# scope tag：写入 ToolEntry.metadata 供 audit 区分（FR-E01 注释要求）
TAU_BENCH_SCOPE_TAG = "tau_bench_benchmark"


class _TauBenchToolArgs(BaseModel):
    """Placeholder schema for tau-bench tools.

    Phase B 不引入完整 schema 推导（每个 tool 的 args schema 不同）；
    Phase D runner 接入实际调用路径时再生成精确 schema（从 tool.get_info() 推导）。
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")


def _make_tool_handler(tau_tool: Any) -> Any:
    """把 τ-bench tool 对象包装为 OctoAgent ToolRegistry handler 可接受的 callable.

    Phase B placeholder: 仅返回 metadata，不真实跑 env.step（避免 Phase B
    在 ToolRegistry contextmanager 验证范围引入完整 user simulator loop 依赖）.
    Phase D T-D-3 runner 实施时改为真实 env.step + user simulator 桥接.
    """
    info = tau_tool.get_info()["function"]
    tool_name = info["name"]

    def _handler(**kwargs: Any) -> dict[str, Any]:
        return {
            "_phase_b_placeholder": True,
            "tool_name": tool_name,
            "args": kwargs,
        }

    _handler.__name__ = f"tau_bench__{tool_name}"
    return _handler


class TauBenchScopeConflictError(RuntimeError):
    """注册阶段检测到 TAU_BENCH_TOOL_PREFIX 命名空间冲突时抛出."""


@contextlib.contextmanager
def tau_bench_tool_scope(
    registry: "ToolRegistry",
    tau_tools: list[Any],
    *,
    scope_id: str | None = None,
) -> Iterator[list[str]]:
    """临时把 τ-bench airline tools 注册到 OctoAgent ToolRegistry.

    Codex Phase B review HIGH-1 + HIGH-2 修复 2026-05-29:
    - **HIGH-1**: `_REGISTRY_LOCK` 只在 register / deregister 期间持有，
      yield 期间 release（避免阻塞 async event loop，Phase D 8 并发不卡死）
    - **HIGH-2**: 注册前 fail-fast 检测 prefixed_name 冲突，避免覆盖已存在的同名工具；
      支持 scope_id（per-run 唯一前缀）让并发 task 共享同一 registry 不冲突

    保证（FR-E01 race + leak 防御）:
    - 注册期原子性: 全局 lock 仅包 register 操作，yield 期间不阻塞
    - 冲突检测: 注册前 check prefixed_name 是否已存在；冲突 → TauBenchScopeConflictError
    - 命名空间隔离: TAU_BENCH_TOOL_PREFIX[+ scope_id +]{tool_name}
    - audit scope: metadata["benchmark_scope"] = TAU_BENCH_SCOPE_TAG +
      metadata["scope_id"] = scope_id（供 scorer 按 scope 过滤）
    - 清理保证: try/finally 在 lock 内执行 deregister

    Args:
        registry: OctoAgent ToolRegistry 单例
        tau_tools: τ-bench tool 列表（一般 14 个 airline ALL_TOOLS）
        scope_id: 可选，per-run 唯一标识（推荐 task_idx / uuid）。None 时仅用全局前缀。

    Yields:
        list[str]: 实际注册的 prefixed tool name 列表（按 tau_tools 输入顺序）.

    Raises:
        TauBenchScopeConflictError: prefixed_name 在 registry 已存在.
        ImportError: octoagent.gateway.harness.tool_registry 不可用.
    """
    from octoagent.gateway.harness.tool_registry import SideEffectLevel, ToolEntry

    prefix = (
        f"{TAU_BENCH_TOOL_PREFIX}{scope_id}__"
        if scope_id is not None
        else TAU_BENCH_TOOL_PREFIX
    )
    registered_names: list[str] = []

    # Phase 1: 注册（持锁；含冲突检测；失败时回滚）
    with _REGISTRY_LOCK:
        try:
            for tau_tool in tau_tools:
                info = tau_tool.get_info()["function"]
                tool_name = info["name"]
                prefixed_name = f"{prefix}{tool_name}"
                # HIGH-2: fail-fast 冲突检测（不 silent overwrite production tool）
                if prefixed_name in registry:
                    raise TauBenchScopeConflictError(
                        f"tau_bench_tool_scope 命名空间冲突: '{prefixed_name}' "
                        f"已存在于 ToolRegistry（可能与并发 task / production tool 冲突）"
                    )
                handler = _make_tool_handler(tau_tool)
                entry = ToolEntry(
                    name=prefixed_name,
                    entrypoints=frozenset({"agent_runtime"}),
                    toolset=TAU_BENCH_SCOPE_TAG,
                    handler=handler,
                    schema=_TauBenchToolArgs,
                    side_effect_level=SideEffectLevel.REVERSIBLE,
                    description=f"τ-bench airline: {info.get('description', tool_name)[:200]}",
                    metadata={
                        "benchmark_scope": TAU_BENCH_SCOPE_TAG,
                        "scope_id": scope_id or "",
                    },
                )
                registry.register(entry)
                registered_names.append(prefixed_name)
        except Exception:
            # 注册期失败回滚（包括 TauBenchScopeConflictError 抛出前已注册的）
            for name in registered_names:
                registry.deregister(name)
            raise

    # Phase 2: yield（**不持锁**，async runner 不阻塞 event loop）
    try:
        yield registered_names
    finally:
        # Phase 3: 清理（持锁）
        with _REGISTRY_LOCK:
            for name in registered_names:
                registry.deregister(name)


# ============================================================
# 主 adapter 入口
# ============================================================


@dataclass
class TauBenchAdapter:
    """τ-bench airline domain adapter.

    职责:
    1. lazy load 50 个 airline task + 14 tools
    2. 暴露 stratified_15_tasks() 给 runner 消费
    3. 提供 tau_bench_tool_scope contextmanager 用于 task 执行期间临时注册
    4. per-task 提供 fresh MockAirlineDomainEnv 实例（PoC-H4 主方案）

    user_simulator_model 默认 "claude-sonnet-4-6"（FR-B05 GATE_DESIGN OQ-1 拍板）.
    """

    user_simulator_model: str = "claude-sonnet-4-6"
    _tasks_cache: list[dict[str, Any]] = field(default_factory=list)
    _tools_cache: list[Any] = field(default_factory=list)

    def load_tasks(self) -> list[dict[str, Any]]:
        """Lazy import + 缓存 airline tasks.

        W5 实测：字段名是 `tasks.tasks`（小写），不是 `TASKS`（旧 spec 假设）.
        """
        if not self._tasks_cache:
            from tau_bench.envs.airline.tasks import tasks as airline_tasks
            self._tasks_cache = list(airline_tasks)
        return self._tasks_cache

    def load_tools(self) -> list[Any]:
        """Lazy import + 缓存 airline ALL_TOOLS (14 个工具实例)."""
        if not self._tools_cache:
            from tau_bench.envs.airline.tools import ALL_TOOLS
            self._tools_cache = list(ALL_TOOLS)
        return self._tools_cache

    def stratified_15_tasks(self) -> list[TauBenchTaskMeta]:
        """返回分层抽样的 15 个 task (FR-E02 6 类覆盖).

        Codex Phase B review MED-6 修复 2026-05-29: 缺额 fail-fast (raise),
        不再 silent drop 让 Daily Bench pass rate 分母错误.
        """
        result = stratified_sample(self.load_tasks())
        if len(result) != 15:
            actual_dist: dict[str, int] = {}
            for t in result:
                actual_dist[t.bucket] = actual_dist.get(t.bucket, 0) + 1
            raise ValueError(
                f"stratified_15_tasks 缺额: 实际 {len(result)} / 期望 15. "
                f"实际分桶: {actual_dist}; 期望 plan: {STRATIFIED_SAMPLING_PLAN}. "
                f"可能原因: 上游 τ-bench 数据集变化 / 自定义 plan 不匹配."
            )
        return result

    def make_env(self, task_index: int) -> Any:
        """Per-task 创建 fresh MockAirlineDomainEnv 实例 (Phase B placeholder).

        Codex Phase B review MED-8 修复 2026-05-29: 删 "Phase B T-B-5 实测" 字眼;
        PoC-H4 (mock DB per-task reset 无污染) 实际**未在 Phase B 验证**,
        推迟到 Phase D runner 接入前作为 blocker（必须补 2 个连续 task 实测,
        或直接采用 file-based isolation 降级）.

        Phase D 前 known issue: 当前 MockAirlineDomainEnv 行为依赖 tau-bench 上游
        实现; 连续 task 间 mock DB 状态如有 side effect，分层抽样的后续 task
        会被前面 task 污染（reservation 状态残留等）.
        """
        from tau_bench.envs.airline.env import MockAirlineDomainEnv
        return MockAirlineDomainEnv(
            user_model=self.user_simulator_model,
            task_split="test",
            task_index=task_index,
        )
