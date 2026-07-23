"""F103d Phase E 第 1 步 — host-side OctoHarness runner_fn (进 git).

CLI 入口 ``octo-bench daily --runner benchmarks.runner.octo_runner:runner_fn`` 注入此函数。

设计目标（与 spec FR-D02 / handoff §1.2 对齐）:

- **4 tier 分派**：按 ``task_meta.tier + domain`` 路由到不同执行路径
  * Tier 1 私有：起 OctoHarness → TaskService.create_task → 等 SUCCEEDED →
    fetch_events_from_store → score_tier1
  * Tier 2 τ-bench airline：``tau_bench_tool_scope`` 临时注册 + 收集
    actual_tool_calls → score_tier2_tau
  * Tier 2 GAIA fallback：直接 ProviderRouter chat → match_answer →
    score_tier2_gaia
  * Tier 3 哲学：起 OctoHarness → 跑 task → ``fetch_events_from_store_tier3``
    含 child_task_ids 递归发现 → score_tier3
- **OctoHarness 生命周期隔离**：每 task 独立 ``tmpdir`` data_dir / mcp_servers_dir，
  bootstrap → run → shutdown 严格三段；任何阶段抛错都让 outcome 退到
  ``INFRA_ERROR`` 让 worker.py 兜底（不在 runner 里 swallow）
- **零侵入 production**：本模块只 import production 的 public API（OctoHarness /
  TaskService / SqliteEventStore / ProviderRouter / scorer dispatch），不修改任何
  production 代码

控变量 LLM（CLAUDE.local.md §"Benchmark 控变量 LLM 配置"）:

- Provider: SiliconFlow（已在 ``~/.octoagent/octoagent.yaml`` 配置 providers）
- Model: ``deepseek-ai/DeepSeek-V3.2``
- alias 名：``bench``（独立 alias，不污染 main/cheap/rerank）
- ``~/.octoagent/.env`` 需含 ``SILICONFLOW_API_KEY``
- ``temperature=0`` 通过 alias config 或 runner_fn 注入

bench alias 配置见 ``benchmarks/README_BENCH_ALIAS.md``（instance 配置，不进 repo
但本文档给出操作步骤）。

第 1 步范围（本 commit）:

- ✅ runner_fn 4 tier 分派代码
- ✅ ProviderRouterJudgeAdapter wire（chat_fn 走 ProviderRouter bench alias）
- ✅ mock unit test 验证 4 tier 分派 + score 调用链通

第 2 步（用户 host 真跑）:

- ❌ 真跑 M5 baseline（需 LLM key + ~30 min）
- ❌ ``octo-bench daily --label m5-baseline --runner benchmarks.runner.octo_runner:runner_fn``
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import tempfile
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmarks.runner.llm_judge import (
    LLMJudgeTrigger,
    ProviderRouterJudgeAdapter,
)
from benchmarks.runner.score_dispatch import RunResult, score
from benchmarks.runner.scorer import (
    BenchmarkRunScore,
    TaskVerdict,
    fetch_events_from_store,
    fetch_events_from_store_tier3,
    load_scoring_rubrics,
)
from benchmarks.runner.store import (
    RESULT_ERROR,
    RESULT_FAIL,
    RESULT_INFRA_ERROR,
    RESULT_PARTIAL,
    RESULT_PASS,
    RESULT_TIMEOUT,
)
from benchmarks.runner.worker import TaskExecutionOutcome

logger = logging.getLogger("octobench.octo_runner")


# ---------------------------------------------------------------------------
# 常量 + 默认配置
# ---------------------------------------------------------------------------

# 控变量 LLM model alias（octoagent.yaml model_aliases 内须有此 alias）
DEFAULT_BENCH_MODEL_ALIAS = os.environ.get("OCTOAGENT_BENCH_MODEL", "bench")

# default chat_fn temperature（控变量 0；alias config 可覆盖但 runner 强制传 0）
DEFAULT_BENCH_TEMPERATURE = 0.0

# Tier 1/3 task 等待 SUCCEEDED/FAILED 终态的轮询间隔与超时（秒）
TASK_POLL_INTERVAL_SECONDS = 1.0
TASK_DEFAULT_OVERALL_TIMEOUT_SECONDS = 240.0  # task_meta.raw.timeout_seconds 优先

# scoring rubrics yaml 路径（runner 启动一次加载，避免每 task 重读）
_RUBRICS_PATH = Path(__file__).parent / "scoring_rubrics.yaml"


# ---------------------------------------------------------------------------
# OctoHarness 生命周期 contextmanager（每 task 独立 tmp dir）
# ---------------------------------------------------------------------------


@dataclass
class HarnessHandle:
    """OctoHarness 句柄（bootstrap 后填充）。"""

    harness: Any  # OctoHarness 实例
    app: Any  # FastAPI 实例（必须，作为 OctoHarness state 容器）
    project_root: Path  # 等同 data_dir 父，由 caller 持有 tmp dir 生命周期
    store_group: Any  # 跨段共享，runner 收事件 / events query 用
    task_runner: Any  # task_runner.enqueue 主路径
    provider_router: Any  # Tier 2 GAIA / LLM judge chat_fn 路径
    tool_registry: Any  # Tier 2 τ-bench tau_bench_tool_scope 注入用


class MissingInstanceConfigError(RuntimeError):
    """Codex HIGH-1 闭环：缺少 instance config 时 fail-fast，不在 tmp 内静默继续.

    ``octo_harness_session`` 默认要求 ``OCTOAGENT_BENCH_TEMPLATE_ROOT`` env 或
    ``~/.octoagent`` 下有 ``octoagent.yaml``（含 SiliconFlow provider）。缺失
    意味着 LLM 路径无法解析 alias → 整次 baseline 跑垃圾数字。
    """


@asynccontextmanager
async def octo_harness_session(
    *,
    credential_store: Any | None = None,
    llm_adapter: Any | None = None,
    template_root: Path | None = None,
    bench_model_alias: str | None = None,
    require_config: bool = True,
):
    """每 task 独立 tmp OctoHarness lifespan（Codex HIGH-1/HIGH-2 闭环）.

    bootstrap → yield handle → shutdown，并自动处理 tmp dir 清理 +
    instance config 准备（octoagent.yaml + main alias 重写）.

    工作流:
        1. tempdir 内建 project_root / data_dir / mcp_servers_dir 骨架
        2. 复制 ``template_root / octoagent.yaml`` → ``project_root / octoagent.yaml``
        3. 把 ``model_aliases.main`` 重写为 bench alias 指向的 (provider, model)，
           让 task_runner 透明使用控变量 LLM（无需修 NormalizedMessage 加新 control_metadata）
        4. 复制 ``USER.md`` / ``MEMORY.md`` 模板（若存在）
        5. ``OctoHarness.bootstrap`` → yield → shutdown

    Args:
        credential_store: 可选注入；None 时 OctoHarness 读 tmp project_root 下的
            auth-profiles.json（需要 caller 设置 SILICONFLOW_API_KEY env）。
        llm_adapter: 可选注入；None 时走默认 ProviderRouterMessageAdapter（控变量
            LLM 路径，alias=main 已被重写到 bench）。
        template_root: instance config 来源根路径；None 时按优先级解析：
            ① ``OCTOAGENT_BENCH_TEMPLATE_ROOT`` env
            ② ``~/.octoagent`` (host instance)
            缺 octoagent.yaml 时 raise ``MissingInstanceConfigError`` (require_config=True 时)
            或继续不复制 (require_config=False，仅 mock 测试用)
        bench_model_alias: 控变量 alias 名（默认 ``DEFAULT_BENCH_MODEL_ALIAS``）；
            该 alias 必须存在于 template_root/octoagent.yaml 的 model_aliases，
            runner 把它的 (provider, model) 重写到 main alias.
        require_config: True 时缺 octoagent.yaml fail-fast；False 时跳过（仅
            unit test 用 mock OctoHarness 时设 False）.

    Raises:
        MissingInstanceConfigError: require_config=True 但找不到 octoagent.yaml
            或 bench alias 未在 model_aliases 中定义.
    """
    # 延迟 import：benchmarks/ 不在 sys.path top-level 时也能 import
    from fastapi import FastAPI

    from octoagent.gateway.harness.octo_harness import OctoHarness

    bench_alias = bench_model_alias or DEFAULT_BENCH_MODEL_ALIAS
    resolved_template_root = _resolve_template_root(template_root)

    with tempfile.TemporaryDirectory(prefix="octobench_") as tmpdir_str:
        tmp_root = Path(tmpdir_str)
        project_root = tmp_root / "instance"
        data_dir = project_root / "data"
        mcp_servers_dir = project_root / "mcp-servers"

        project_root.mkdir(parents=True, exist_ok=True)
        data_dir.mkdir(parents=True, exist_ok=True)
        mcp_servers_dir.mkdir(parents=True, exist_ok=True)
        (project_root / "behavior" / "system").mkdir(parents=True, exist_ok=True)

        if resolved_template_root is not None:
            _materialize_instance_config(
                resolved_template_root,
                project_root,
                bench_alias=bench_alias,
                require_config=require_config,
            )
        elif require_config:
            raise MissingInstanceConfigError(
                "No instance template_root provided and "
                "OCTOAGENT_BENCH_TEMPLATE_ROOT env / ~/.octoagent both unavailable; "
                "runner cannot run benchmark without octoagent.yaml. "
                "See benchmarks/README_BENCH_ALIAS.md for setup."
            )

        harness = OctoHarness(
            project_root=project_root,
            credential_store=credential_store,
            llm_adapter=llm_adapter,
            mcp_servers_dir=mcp_servers_dir,
            data_dir=data_dir,
        )

        app = FastAPI()
        bootstrap_ok = False
        try:
            await harness.bootstrap(app)
            bootstrap_ok = True
            harness.commit_to_app(app)
            from octoagent.gateway.harness.tool_registry import get_registry

            handle = HarnessHandle(
                harness=harness,
                app=app,
                project_root=project_root,
                store_group=app.state.store_group,
                task_runner=app.state.task_runner,
                provider_router=app.state.provider_router,
                tool_registry=get_registry(),
            )
            yield handle
        finally:
            # F089 finding #1 闭环模式：不吞 shutdown 异常.
            # Codex MED-2 闭环：先关 ProviderRouter http client（避免 150 task 累积）
            try:
                router = getattr(app.state, "provider_router", None)
                if router is not None and hasattr(router, "aclose"):
                    await router.aclose()
            except Exception as exc:
                logger.warning(
                    "provider_router_aclose_failed", extra={"error": repr(exc)}
                )
            if bootstrap_ok:
                await harness.shutdown(app)


def _resolve_template_root(explicit: Path | None) -> Path | None:
    """按优先级解析 template_root：explicit → env → ~/.octoagent."""
    if explicit is not None:
        return explicit
    env_val = os.environ.get("OCTOAGENT_BENCH_TEMPLATE_ROOT", "").strip()
    if env_val:
        return Path(env_val).expanduser().resolve()
    default = Path.home() / ".octoagent"
    if default.exists():
        return default
    return None


def _materialize_instance_config(
    src_root: Path,
    dst_root: Path,
    *,
    bench_alias: str,
    require_config: bool,
) -> None:
    """复制 instance config 到 tmp project_root + 重写 main alias.

    Codex HIGH-1：从 template_root 复制 octoagent.yaml + USER.md + MEMORY.md.
    Codex HIGH-2：把 model_aliases.main 重写到 bench alias 指向的 (provider, model).

    支持两种 src_root 布局:
    - host instance 根（如 ``~/.octoagent``）：直接读 octoagent.yaml.
    - test fixture（如 tests/fixtures/local-instance/）：读 octoagent.yaml.template.

    behavior 模板同理：先找 USER.md / MEMORY.md，找不到再退到 .template 扩展.

    Raises:
        MissingInstanceConfigError: octoagent.yaml 缺失 / bench alias 不存在.
    """
    import shutil

    # Step 1: behavior 文件（USER.md / MEMORY.md）
    behavior_src = src_root / "behavior" / "system"
    behavior_dst = dst_root / "behavior" / "system"
    behavior_dst.mkdir(parents=True, exist_ok=True)
    for base_name in ("USER.md", "MEMORY.md"):
        # 优先非模板（host 实例），fallback 到 .template（test fixture）
        for candidate in (base_name, f"{base_name}.template"):
            src_path = behavior_src / candidate
            if src_path.exists():
                shutil.copy(src_path, behavior_dst / base_name)
                break

    # Step 2: octoagent.yaml（必填 + 重写 main alias）
    yaml_candidates = (
        src_root / "octoagent.yaml",
        src_root / "octoagent.yaml.template",
    )
    yaml_src = next((p for p in yaml_candidates if p.exists()), None)
    if yaml_src is None:
        if require_config:
            raise MissingInstanceConfigError(
                f"Instance config not found under {src_root}: "
                f"expected octoagent.yaml or octoagent.yaml.template. "
                f"See benchmarks/README_BENCH_ALIAS.md for setup."
            )
        return

    rewritten = _rewrite_main_alias_to_bench(yaml_src, bench_alias=bench_alias)
    if rewritten is None and require_config:
        raise MissingInstanceConfigError(
            f"Bench alias {bench_alias!r} not found in {yaml_src}: "
            f"model_aliases must contain a '{bench_alias}' entry. "
            f"See benchmarks/README_BENCH_ALIAS.md §Step 2."
        )

    (dst_root / "octoagent.yaml").write_text(
        rewritten if rewritten is not None else yaml_src.read_text(encoding="utf-8"),
        encoding="utf-8",
    )


def _rewrite_main_alias_to_bench(
    yaml_path: Path,
    *,
    bench_alias: str,
) -> str | None:
    """把 model_aliases.main 重写为 bench alias 指向的 (provider, model).

    Codex HIGH-2 闭环：task_runner 默认 model_alias="main"（看 capability_pack.py
    + worker_service.py 多处 default_model_alias="main"）；我们把 main 重写到
    bench 让所有 task 透明使用控变量 LLM，不需要侵入 NormalizedMessage 加新 control_metadata.

    Returns:
        重写后的 yaml 文本；bench alias 不存在时返回 None（caller 决定 fail-fast）.
    """
    import yaml as _yaml

    data = _yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    aliases = data.get("model_aliases") or {}
    bench_def = aliases.get(bench_alias)
    if not isinstance(bench_def, dict):
        return None  # bench alias 缺失 → caller fail-fast

    # 重写 main / cheap 都指向 bench（避免 cheap fallback 用了别的 model）
    new_main = dict(bench_def)
    new_main["description"] = f"F103d OctoBench：rewritten to {bench_alias}（控变量）"
    aliases["main"] = new_main
    aliases["cheap"] = dict(
        new_main, description=f"F103d OctoBench：cheap = main = {bench_alias}"
    )
    # 保留原 bench alias 自身（运行时显式引用时仍可用）
    data["model_aliases"] = aliases
    return _yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


# ---------------------------------------------------------------------------
# Tier 1 / Tier 3 共享：通过 TaskService 起 task + 等终态 + 收事件
# ---------------------------------------------------------------------------


async def _submit_and_wait_task(
    handle: HarnessHandle,
    *,
    prompt: str,
    timeout_seconds: float,
    iteration: int,
) -> tuple[str, dt.datetime, dict[str, int]]:
    """提交 task → 等 SUCCEEDED/FAILED → 返回 ``(task_id, started_at, token_usage)``.

    用 ``TaskService.create_task`` + ``TaskRunner.enqueue`` 主路径；和 e2e_live
    factories.submit_message_with_control_metadata 一致。

    Raises:
        TimeoutError: ``timeout_seconds`` 内未达终态
        RuntimeError: task 未创建或 task_runner 缺失
    """
    import uuid

    from octoagent.core.models.enums import TaskStatus
    from octoagent.core.models.message import NormalizedMessage
    from octoagent.gateway.services.task_service import TaskService

    sg = handle.store_group
    if sg is None:
        raise RuntimeError("store_group missing on app.state (bootstrap incomplete?)")
    sse_hub = getattr(handle.app.state, "sse_hub", None)
    task_runner = handle.task_runner
    if task_runner is None:
        raise RuntimeError("task_runner missing on app.state (bootstrap incomplete?)")

    msg = NormalizedMessage(
        channel="web",
        thread_id=f"octobench-{iteration}",
        sender_id="octobench",
        sender_name="OctoBench Runner",
        text=prompt,
        idempotency_key=f"octobench-{uuid.uuid4().hex[:12]}",
    )
    service = TaskService(sg, sse_hub)
    task_id, _ = await service.create_task(msg)
    started_at = dt.datetime.now(dt.timezone.utc)

    await task_runner.enqueue(task_id, msg.text)

    # 轮询 task_store 等终态（Codex MED-1 闭环）
    # Benchmark 终态语义：
    # - SUCCEEDED / FAILED / CANCELLED：正常终态（baseline 计入）
    # - WAITING_INPUT / WAITING_APPROVAL：benchmark task 不应进入（H3-B ask_back
    #   task 除外，但 runner_fn 不卡到 timeout——视为 FAIL，error_message 标记）
    #
    # 不含 REJECTED：TaskStatus 枚举无此值（实测 packages/core/.../enums.py）.
    terminal_statuses = {
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    }
    # 这些视为 benchmark FAIL（runner 无人值守 → 不可能继续）：
    fail_as_benchmark = {
        TaskStatus.WAITING_INPUT,
        TaskStatus.WAITING_APPROVAL,
    }

    deadline = asyncio.get_running_loop().time() + max(0.5, timeout_seconds)
    while True:
        task = await sg.task_store.get_task(task_id)
        if task is not None:
            if task.status in terminal_statuses:
                break
            if task.status in fail_as_benchmark:
                # benchmark 卡在等待状态：直接当 FAIL 终态
                raise TaskBlockedOnInputError(
                    f"task {task_id} blocked on {task.status.value} "
                    f"(benchmark runner is non-interactive; cannot supply input)"
                )
        if asyncio.get_running_loop().time() > deadline:
            raise TimeoutError(
                f"task {task_id} did not reach terminal state within {timeout_seconds}s"
            )
        await asyncio.sleep(TASK_POLL_INTERVAL_SECONDS)

    # Codex round 3 MED 闭环：_collect_token_usage 不再在轮询的 try/except
    # TimeoutError 路径内调用——caller (_run_tier1/_run_tier3) 单独调，
    # 让 EventStore TimeoutError 抛到 runner_fn 顶层 _is_infra_error → INFRA_ERROR
    # 而非被 _submit_and_wait_task 外层 except TimeoutError 误标 RESULT_TIMEOUT.
    return task_id, started_at, {"input": 0, "output": 0, "cache_read": 0}


class TaskBlockedOnInputError(RuntimeError):
    """Codex MED-1 闭环：task 进入 WAITING_INPUT/WAITING_APPROVAL 而 runner 无人值守.

    被 ``_run_tier1`` / ``_run_tier3`` 顶层抓到 → 标 FAIL（不是 INFRA_ERROR）.
    """


async def _collect_token_usage(
    store_group: Any,
    task_id: str,
    since: dt.datetime,
) -> dict[str, int]:
    """从 EventStore ``MODEL_CALL_COMPLETED`` payload 汇总 input/output/cache_read token.

    Codex MED-3 闭环：对齐 production schema (``ModelCallCompletedPayload``):
    payload 含 ``token_usage: dict[str, int]`` 嵌套，键 ``prompt_tokens`` /
    ``completion_tokens`` / ``total_tokens``（packages/core/.../payloads.py:90）.
    旧 schema 候选保留：``input_tokens`` / ``output_tokens`` / 顶层 ``token_input``.
    """
    from octoagent.core.models.enums import EventType

    # Codex round 2 MED 闭环：EventStore 查询异常透传到 runner_fn 顶层 INFRA_ERROR 分类
    # （原 except 吞掉返回全 0 会静默污染 token 指标）
    events = await store_group.event_store.get_events_by_types_since(
        task_id=task_id,
        event_types=[EventType.MODEL_CALL_COMPLETED],
        since_ts=since,
    )

    sum_input = 0
    sum_output = 0
    sum_cache_read = 0
    for evt in events:
        payload = _event_payload(evt)
        if not isinstance(payload, dict):
            continue
        # Codex MED-3: 优先 production schema token_usage 嵌套
        sum_input += _read_token_field(
            payload, ("prompt_tokens", "input_tokens", "token_input")
        )
        sum_output += _read_token_field(
            payload, ("completion_tokens", "output_tokens", "token_output")
        )
        sum_cache_read += _read_token_field(
            payload,
            ("cache_read_input_tokens", "cache_read_tokens", "token_cache_read"),
        )
    return {"input": sum_input, "output": sum_output, "cache_read": sum_cache_read}


def _event_payload(evt: Any) -> dict[str, Any] | None:
    """统一拿 event.payload（dict / pydantic model）。"""
    if isinstance(evt, dict):
        p = evt.get("payload")
        return p if isinstance(p, dict) else None
    if hasattr(evt, "payload"):
        p = getattr(evt, "payload")
        return p if isinstance(p, dict) else None
    return None


def _read_token_field(payload: dict[str, Any], candidates: tuple[str, ...]) -> int:
    """从 payload 多候选 key 中读 int token 数.

    优先顺序（Codex MED-3）：
    1. ``payload["token_usage"][key]``——production ModelCallCompletedPayload.token_usage
    2. ``payload["usage"][key]``——ProviderClient.call return metadata.usage
    3. 顶层 ``payload[key]``——旧 schema 兼容
    """
    nested_containers = ("token_usage", "usage")
    for container in nested_containers:
        inner = payload.get(container)
        if isinstance(inner, dict):
            for key in candidates:
                if key in inner:
                    try:
                        return int(inner[key])
                    except (TypeError, ValueError):
                        continue

    for key in candidates:
        if key in payload:
            try:
                return int(payload[key])
            except (TypeError, ValueError):
                continue
    return 0


def _normalize_provider_usage(raw: Any) -> dict[str, int]:
    """ProviderClient.call return metadata.usage → {input, output, cache_read}.

    Provider 真实返回 dict 形如 ``{prompt_tokens, completion_tokens, total_tokens}``
    （packages/provider/.../provider_client.py:_call_openai_chat usage_data）.
    """
    if not isinstance(raw, dict):
        return {"input": 0, "output": 0, "cache_read": 0}
    return {
        "input": _read_token_field(
            {"usage": raw}, ("prompt_tokens", "input_tokens", "token_input")
        ),
        "output": _read_token_field(
            {"usage": raw}, ("completion_tokens", "output_tokens", "token_output")
        ),
        "cache_read": _read_token_field(
            {"usage": raw},
            ("cache_read_input_tokens", "cache_read_tokens", "token_cache_read"),
        ),
    }


# ---------------------------------------------------------------------------
# Tier 3 audit chain：递归发现 child_task_ids（从 SUBAGENT_SPAWNED）
# ---------------------------------------------------------------------------


async def _discover_child_task_ids(
    store_group: Any,
    parent_task_id: str,
    since: dt.datetime,
) -> list[str]:
    """从 ``SUBAGENT_SPAWNED`` 事件 ``payload.child_task_id`` 收集 child 列表.

    一层 BFS（更深层由 ``fetch_events_from_store_tier3`` 自己递归发现）。
    """
    from octoagent.core.models.enums import EventType

    # Codex round 2 MED 闭环：EventStore 查询异常透传到 _run_tier3 → runner_fn 顶层
    # INFRA_ERROR 分类（原 except 吞掉返回空 list 会让 Tier 3 误评分而非标 infra）
    events = await store_group.event_store.get_events_by_types_since(
        task_id=parent_task_id,
        event_types=[EventType.SUBAGENT_SPAWNED],
        since_ts=since,
    )
    out: list[str] = []
    seen: set[str] = set()
    for evt in events:
        payload = _event_payload(evt)
        if not isinstance(payload, dict):
            continue
        cid = str(payload.get("child_task_id", "") or "").strip()
        if cid and cid not in seen:
            seen.add(cid)
            out.append(cid)
    return out


# ---------------------------------------------------------------------------
# Tier 2 GAIA fallback：直接 LLM call
# ---------------------------------------------------------------------------


async def _run_gaia_fallback(
    handle: HarnessHandle,
    *,
    task: Any,
    iteration: int,
    model_alias: str,
) -> tuple[str, dict[str, int]]:
    """GAIA fallback：直接 ProviderRouter.chat → 拿 actual_answer.

    不走 task_service.create_task；GAIA 是单轮答题 benchmark，不需要 task 编排。

    Codex HIGH-3 闭环：provider 异常（auth / quota / network）不在此层吞掉，
    上抛到 ``runner_fn`` 顶层 → worker.run_task_with_retry 的 except 路径分类为
    INFRA_ERROR（不污染 FAIL 分母）。空 prompt 仍走正常路径返回空 answer.

    Returns:
        (actual_answer, token_usage_dict)
    """
    router = handle.provider_router
    if router is None:
        raise RuntimeError("provider_router missing on app.state")

    # GAIA prompt 即 task.prompt（GaiaFallbackTaskMeta.prompt）
    prompt = getattr(task, "prompt", "")
    if not prompt:
        return "", {"input": 0, "output": 0, "cache_read": 0}

    messages = [
        {
            "role": "system",
            "content": (
                "You are a knowledgeable expert. Answer the user's question concisely "
                "and accurately. Output ONLY the final answer, no explanation."
            ),
        },
        {"role": "user", "content": prompt},
    ]

    # ProviderRouter 直连：alias=bench → SiliconFlow DeepSeek-V3.2.
    # 异常透传：让 runner_fn 顶层兜底标 INFRA_ERROR（Codex HIGH-3）.
    resp = await _provider_router_chat(
        router,
        messages=messages,
        model=model_alias,
        temperature=DEFAULT_BENCH_TEMPERATURE,
        max_tokens=512,
    )
    text = resp.get("text", "") if isinstance(resp, dict) else str(resp)
    token_usage = resp.get("usage", {}) if isinstance(resp, dict) else {}
    return text.strip(), _normalize_provider_usage(token_usage)


async def _provider_router_chat(
    router: Any,
    *,
    messages: list[dict[str, str]],
    model: str,
    temperature: float,
    max_tokens: int,
) -> dict[str, Any]:
    """ProviderRouter alias-driven chat 包装.

    真实 API（packages/provider/.../provider_router.py + provider_client.py）：

    1. ``router.resolve_for_alias(alias, task_scope=...) -> ResolvedAlias(client, model_name, ...)``
    2. ``client.call(instructions, history, tools, model_name, reasoning, tool_choice)``
       → ``(content: str, tool_calls: list, metadata: dict)``

    本函数把 OpenAI-style ``messages`` 拆分为：
    - 第一个 system message → ``instructions``
    - 其余（user/assistant/tool）→ ``history``

    ``temperature`` / ``max_tokens`` 在 ProviderRouter 真实 API 中不直接暴露
    （由 alias config / provider extra_body 控制）；runner 调用时仍按 signature
    传入，但实际行为靠 alias=bench 配置控制（CLAUDE.local.md 决策：温度 0）.

    Returns:
        ``{"text": str, "usage": {input_tokens, output_tokens, cache_read_tokens}}``

    Raises:
        AttributeError: router 不是 ProviderRouter 兼容对象
        Provider 层异常（LLMCallError / CredentialError）直接上抛
    """
    if not hasattr(router, "resolve_for_alias"):
        raise AttributeError(
            "provider_router does not look like ProviderRouter "
            "(missing resolve_for_alias)"
        )

    # alias 解析 + ProviderClient.call
    resolved = router.resolve_for_alias(model, task_scope=None)
    client = resolved.client
    model_name = resolved.model_name

    # 拆 system → instructions
    instructions = ""
    history: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role", "")
        content = m.get("content", "")
        if role == "system" and not instructions:
            instructions = str(content)
        else:
            history.append({"role": role, "content": content})

    content, _tool_calls, metadata = await client.call(
        instructions=instructions,
        history=history,
        tools=[],  # judge / GAIA 答题不用 tools
        model_name=model_name,
        reasoning=None,
        tool_choice=None,
    )

    # metadata 通常含 {"usage": {input_tokens, output_tokens, ...}}
    usage = metadata.get("usage", {}) if isinstance(metadata, dict) else {}
    return {"text": str(content or ""), "usage": usage}


def _normalize_chat_response(resp: Any) -> dict[str, Any]:
    """把不同形态的 chat response 规范化到 ``{text, usage}`` 字典。"""
    if isinstance(resp, dict) and ("text" in resp or "content" in resp):
        return {
            "text": str(resp.get("text") or resp.get("content") or ""),
            "usage": resp.get("usage", {}) or {},
        }
    if isinstance(resp, str):
        return {"text": resp, "usage": {}}
    # 对象/pydantic：尝试 .text / .content / .choices[0].message.content
    if hasattr(resp, "text"):
        return {"text": str(resp.text), "usage": getattr(resp, "usage", {}) or {}}
    if hasattr(resp, "content"):
        return {"text": str(resp.content), "usage": getattr(resp, "usage", {}) or {}}
    choices = getattr(resp, "choices", None)
    if choices:
        try:
            msg = choices[0].message  # type: ignore[index]
            return {
                "text": str(getattr(msg, "content", "")),
                "usage": getattr(resp, "usage", {}) or {},
            }
        except Exception:
            pass
    return {"text": str(resp), "usage": {}}


# ---------------------------------------------------------------------------
# Tier 2 τ-bench：tau_bench_tool_scope + 收集 actual_tool_calls
# ---------------------------------------------------------------------------


# Codex HIGH-4 闭环：τ-bench 真实 user_simulator + env.step 接入是独立 Feature
# 的工作量（handoff §3.E）。在它就绪前，Tier 2 τ-bench 路径不能产 baseline 数据
# ——否则 scorer 看 TOOL_CALL_STARTED tool name 即给 PASS/FAIL，是系统性假评分.
# Phase E Step 1 显式 raise，让 runner_fn 顶层兜底为 INFRA_ERROR（不进分母）.
TAU_BENCH_NOT_INTEGRATED_MESSAGE = (
    "Tier 2 τ-bench env.step + user_simulator integration is deferred "
    "(see handoff §3.E). Phase E Step 1 explicitly returns INFRA_ERROR to "
    "prevent generating false PASS/FAIL data from incomplete tool-name matching."
)


class TauBenchNotIntegratedError(RuntimeError):
    """τ-bench 完整集成（env.step + user_simulator）尚未就绪 → INFRA_ERROR."""


async def _run_tau_bench_task(
    handle: HarnessHandle,
    *,
    task: Any,
    iteration: int,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Codex HIGH-4 闭环：显式不跑（INFRA_ERROR），等真集成.

    历史尝试：tau_bench_tool_scope 注册 14 个 tool + 抓 TOOL_CALL_STARTED 收集
    tool name。但 scorer 单看 tool name 是否被覆盖给 PASS @1，系统性假评分。

    真集成需要：
    1. tau_bench env.step (mock DB state machine) 真驱动
    2. user_simulator multi-turn loop（Sonnet 4.6）
    3. score_tier2_tau order-aware + arguments-aware 比对

    在以上未就绪前，显式 raise，让 runner_fn 顶层 except 标 INFRA_ERROR ——
    符合 AC3-4：INFRA_ERROR 不进 pass rate 分母，避免污染 baseline 数据.
    """
    raise TauBenchNotIntegratedError(TAU_BENCH_NOT_INTEGRATED_MESSAGE)


# ---------------------------------------------------------------------------
# LLM judge wire：ProviderRouter chat_fn（控变量 alias=bench）
# ---------------------------------------------------------------------------


def make_provider_router_chat_fn(
    router: Any,
    *,
    model: str | None = None,
) -> Callable[[list[dict[str, str]], str, float, int], str]:
    """构造 ``ProviderRouterJudgeAdapter.chat_fn`` callable.

    runner 实例化一次即可（每 task 不必新建）。

    Args:
        router: ProviderRouter 实例（来自 ``handle.provider_router``）
        model: 显式 model alias；None 时用 ``DEFAULT_BENCH_MODEL_ALIAS``

    Returns:
        chat_fn(messages, model, temperature, max_tokens) -> str
    """
    chosen_model = model or DEFAULT_BENCH_MODEL_ALIAS

    def _chat_fn(
        messages: list[dict[str, str]],
        model_arg: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        # judge adapter 传进来的 model_arg 是 llm_judge.DEFAULT_JUDGE_MODEL
        # （claude-sonnet-4-5）——我们改用 alias=bench（控变量 LLM）
        actual_model = chosen_model

        # sync-from-async 桥接：score_tier1 是 sync 函数，从 _run_tier1（async）
        # 内调用；chat_fn 同步阻塞 → 用独立 thread 跑 asyncio.run 避免：
        #   1. 当前 loop 死锁（run_coroutine_threadsafe 同线程 result() 死锁）
        #   2. nest_asyncio 依赖（不引入 3rd-party）
        # ThreadPoolExecutor max_workers=1 每次新建一次性 pool，partial 路径才触发
        # （match_ratio in [0.5, 1.0)），开销可接受（每 task 最多 2 次 judge）。
        import concurrent.futures

        async def _do():
            return await _provider_router_chat(
                router,
                messages=messages,
                model=actual_model,
                temperature=temperature,
                max_tokens=max_tokens,
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(lambda: asyncio.run(_do()))
            resp = future.result(timeout=60.0)

        return resp.get("text", "") if isinstance(resp, dict) else str(resp)

    return _chat_fn


# ---------------------------------------------------------------------------
# 主 runner_fn：octo-bench daily --runner benchmarks.runner.octo_runner:runner_fn
# ---------------------------------------------------------------------------


async def runner_fn(task_meta: Any, iteration: int) -> TaskExecutionOutcome:
    """单 task 执行入口：按 tier 分派 → 收事件 → score → TaskExecutionOutcome.

    Phase E 第 1 步实现。Phase E 第 2 步 caller 在 host 上跑：
    ``octo-bench daily --label m5-baseline --runner benchmarks.runner.octo_runner:runner_fn``

    Args:
        task_meta: YamlTaskMeta（cli.py 加载的 task）或 TauBench/GaiaTaskMeta
            （Tier 2 adapter 直接返回的 dataclass）
        iteration: 第几次（1-indexed）

    Returns:
        TaskExecutionOutcome（含 result / score / duration / token_usage /
        audit_assertions_json / error_message）
    """
    rubrics = _load_rubrics_safe()
    tier, domain = _resolve_tier_domain(task_meta)

    started_at = _now_clock()
    try:
        if tier == 1:
            return await _run_tier1(task_meta, iteration, rubrics, started_at)
        if tier == 2:
            domain_lower = domain.lower()
            if "tau" in domain_lower:
                return await _run_tier2_tau(task_meta, iteration, rubrics, started_at)
            if "gaia" in domain_lower:
                return await _run_tier2_gaia(task_meta, iteration, rubrics, started_at)
            return _outcome_error(started_at, f"tier 2 unsupported domain={domain!r}")
        if tier == 3:
            return await _run_tier3(task_meta, iteration, rubrics, started_at)
        return _outcome_error(started_at, f"unsupported tier={tier!r}")
    except Exception as exc:
        # 异常分类：所有结构化 infra-level 异常都标 INFRA_ERROR（不进分母）.
        # Codex round 2 HIGH 闭环：provider 异常（ProviderError 基类含
        # CredentialError / AuthenticationError 等）从
        # _run_gaia_fallback 透传到这里，必须明确分类为 INFRA_ERROR——而非
        # broad except Exception 误为 RESULT_ERROR.
        if _is_infra_error(exc):
            logger.info(
                "runner_fn_infra_error",
                extra={
                    "task": _task_id_repr(task_meta),
                    "error_type": type(exc).__name__,
                },
            )
            return TaskExecutionOutcome(
                result=RESULT_INFRA_ERROR,
                score=None,
                duration_seconds=_elapsed(started_at),
                error_message=f"{type(exc).__name__}: {exc}",
            )
        # 真未知异常 → RESULT_ERROR（scorer / runner 内部 bug，要被发现）
        logger.exception("runner_fn_unexpected_error")
        return _outcome_error(started_at, f"runner_fn unexpected: {exc!r}")


def _is_infra_error(exc: BaseException) -> bool:
    """判断异常是否属于 infrastructure 类（应进 INFRA_ERROR 不进 pass-rate 分母）.

    Codex round 2 HIGH 闭环：原 runner_fn broad except Exception 把 provider
    异常误为 RESULT_ERROR。识别以下类别为 infra：

    1. ``MissingInstanceConfigError``：缺 octoagent.yaml / bench alias
    2. ``TauBenchNotIntegratedError``：τ-bench 集成 deferred
    3. ``ProviderError`` 及子类：CredentialError / AuthenticationError /
       OAuthFlowError 等（packages/provider/exceptions.py）
    4. ``ConnectionError`` / ``TimeoutError`` / ``OSError`` 等网络层异常
    """
    # 已知结构化 infra 异常
    if isinstance(exc, (MissingInstanceConfigError, TauBenchNotIntegratedError)):
        return True
    # 网络 / IO 类
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return True
    # Provider 异常（lazy import 避免 benchmarks 模块加载时强依赖 provider）
    try:
        from octoagent.provider.exceptions import ProviderError

        if isinstance(exc, ProviderError):
            return True
    except ImportError:
        pass
    return False


def _task_id_repr(task_meta: Any) -> str:
    """安全 repr task_id（log 用）."""
    return str(
        _raw_dict(task_meta).get("task_id") or getattr(task_meta, "task_id", "?")
    )


# ---------------------------------------------------------------------------
# 各 tier 执行 helper
# ---------------------------------------------------------------------------


async def _run_tier1(
    task_meta: Any,
    iteration: int,
    rubrics: dict[str, dict[str, Any]] | None,
    started_at: float,
) -> TaskExecutionOutcome:
    """Tier 1 私有 task：起 OctoHarness → submit task → fetch events → score_tier1.

    LLM judge wire：``score_tier1`` 通过 ``score_dispatch.score`` 调用，
    传入 ``LLMJudgeTrigger(adapter=ProviderRouterJudgeAdapter(chat_fn))``，
    chat_fn 走 ``handle.provider_router`` 的 bench alias 路径（控变量 DeepSeek-V3.2）.
    每 task 新建 trigger 重置 ``_call_count``，符合 ``reset_call_count`` 语义.
    """
    raw = _raw_dict(task_meta)
    timeout_seconds = float(
        raw.get("timeout_seconds", TASK_DEFAULT_OVERALL_TIMEOUT_SECONDS)
    )
    prompt = str(raw.get("prompt", ""))

    async with octo_harness_session() as handle:
        try:
            task_id, started_dt, _ = await _submit_and_wait_task(
                handle,
                prompt=prompt,
                timeout_seconds=timeout_seconds,
                iteration=iteration,
            )
        except TimeoutError as exc:
            return TaskExecutionOutcome(
                result=RESULT_TIMEOUT,
                score=None,
                duration_seconds=_elapsed(started_at),
                error_message=str(exc),
            )
        except TaskBlockedOnInputError as exc:
            # Codex MED-1：waiting on input → benchmark FAIL（无人值守）
            return TaskExecutionOutcome(
                result=RESULT_FAIL,
                score=0.0,
                duration_seconds=_elapsed(started_at),
                error_message=str(exc),
            )

        # Codex round 3 闭环：fetch_events + _collect_token_usage 异常不在此 swallow.
        # ProviderError / ConnectionError / TimeoutError 等 infra 异常透传到 runner_fn
        # 顶层 _is_infra_error → INFRA_ERROR（不被这里 except Exception 吞成 RESULT_ERROR）.
        actual_events = await fetch_events_from_store(
            event_store=handle.store_group.event_store,
            task_id=task_id,
            task_start_time=started_dt,
        )
        token_usage = await _collect_token_usage(
            handle.store_group, task_id, started_dt
        )

        # LLM judge wire：真实控变量 LLM 路径（DeepSeek-V3.2 via SiliconFlow alias=bench）
        judge_trigger = _build_judge_trigger(handle.provider_router)
        run_result = RunResult(
            actual_events=actual_events,
            token_usage=_total_tokens(token_usage),
        )
        bench_score = score(
            raw, run_result, rubrics=rubrics, judge_trigger=judge_trigger
        )

    return _outcome_from_score(bench_score, started_at, token_usage)


def _build_judge_trigger(provider_router: Any) -> LLMJudgeTrigger | None:
    """构造 LLM judge trigger（ProviderRouterJudgeAdapter wire 到 bench alias）。

    Returns:
        LLMJudgeTrigger 实例（adapter=ProviderRouterJudgeAdapter）；
        provider_router 缺失时返回 None（让 score_tier1 走默认 stub 路径）。
    """
    if provider_router is None:
        return None
    chat_fn = make_provider_router_chat_fn(provider_router)
    adapter = ProviderRouterJudgeAdapter(chat_fn=chat_fn)
    return LLMJudgeTrigger(adapter=adapter)


async def _run_tier2_tau(
    task_meta: Any,
    iteration: int,
    rubrics: dict[str, dict[str, Any]] | None,
    started_at: float,
) -> TaskExecutionOutcome:
    """Tier 2 τ-bench：tau_bench_tool_scope + 收集 actual_tool_calls + score_tier2_tau."""
    async with octo_harness_session() as handle:
        actual_tool_calls, token_usage = await _run_tau_bench_task(
            handle, task=task_meta, iteration=iteration
        )

    run_result = RunResult(
        actual_tool_calls=actual_tool_calls,
        token_usage=_total_tokens(token_usage),
    )
    bench_score = score(task_meta, run_result, rubrics=rubrics)
    return _outcome_from_score(bench_score, started_at, token_usage)


async def _run_tier2_gaia(
    task_meta: Any,
    iteration: int,
    rubrics: dict[str, dict[str, Any]] | None,
    started_at: float,
) -> TaskExecutionOutcome:
    """Tier 2 GAIA fallback：ProviderRouter chat → match_answer → score_tier2_gaia."""
    async with octo_harness_session() as handle:
        actual_answer, token_usage = await _run_gaia_fallback(
            handle,
            task=task_meta,
            iteration=iteration,
            model_alias=DEFAULT_BENCH_MODEL_ALIAS,
        )

    run_result = RunResult(
        actual_answer=actual_answer,
        token_usage=_total_tokens(token_usage),
    )
    bench_score = score(task_meta, run_result, rubrics=rubrics)
    return _outcome_from_score(bench_score, started_at, token_usage)


async def _run_tier3(
    task_meta: Any,
    iteration: int,
    rubrics: dict[str, dict[str, Any]] | None,
    started_at: float,
) -> TaskExecutionOutcome:
    """Tier 3 哲学 task：起 OctoHarness → submit → fetch_events_tier3 → score_tier3."""
    raw = _raw_dict(task_meta)
    timeout_seconds = float(
        raw.get("timeout_seconds", TASK_DEFAULT_OVERALL_TIMEOUT_SECONDS)
    )
    prompt = str(raw.get("prompt", ""))

    async with octo_harness_session() as handle:
        try:
            task_id, started_dt, _ = await _submit_and_wait_task(
                handle,
                prompt=prompt,
                timeout_seconds=timeout_seconds,
                iteration=iteration,
            )
        except TimeoutError as exc:
            return TaskExecutionOutcome(
                result=RESULT_TIMEOUT,
                score=None,
                duration_seconds=_elapsed(started_at),
                error_message=str(exc),
            )
        except TaskBlockedOnInputError as exc:
            # Codex MED-1：T3-H3B 等 ask_back 类 task 可能命中；视为 FAIL（无人值守）
            return TaskExecutionOutcome(
                result=RESULT_FAIL,
                score=0.0,
                duration_seconds=_elapsed(started_at),
                error_message=str(exc),
            )

        # Codex round 3 闭环：fetch_events_tier3 + _discover_child_task_ids +
        # _collect_token_usage 异常不在此 swallow——透传到 runner_fn 顶层
        # _is_infra_error → INFRA_ERROR（避免 EventStore 抖动被误标 RESULT_ERROR）.
        # H1/H2/H3 audit chain 信号常写在 child task_id 上——
        # _discover_child_task_ids 拿一层，fetch_events_from_store_tier3 内部
        # 递归发现 grandchild。
        child_task_ids = await _discover_child_task_ids(
            handle.store_group, task_id, started_dt
        )
        actual_events = await fetch_events_from_store_tier3(
            event_store=handle.store_group.event_store,
            task_id=task_id,
            task_start_time=started_dt,
            child_task_ids=child_task_ids,
        )
        token_usage = await _collect_token_usage(
            handle.store_group, task_id, started_dt
        )

        run_result = RunResult(
            actual_events=actual_events,
            token_usage=_total_tokens(token_usage),
        )
        bench_score = score(raw, run_result, rubrics=rubrics)

    return _outcome_from_score(bench_score, started_at, token_usage)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _load_rubrics_safe() -> dict[str, dict[str, Any]] | None:
    """加载 scoring_rubrics.yaml；失败返回 None（不阻塞 runner）。"""
    try:
        return load_scoring_rubrics(_RUBRICS_PATH)
    except Exception as exc:
        logger.warning("rubrics_load_failed", extra={"error": repr(exc)})
        return None


def _raw_dict(task_meta: Any) -> dict[str, Any]:
    """从 YamlTaskMeta（cli.py）或 task dataclass 拿原始 dict。"""
    raw = getattr(task_meta, "raw", None)
    if isinstance(raw, dict):
        return raw
    if isinstance(task_meta, dict):
        return task_meta
    return {}


def _resolve_tier_domain(task_meta: Any) -> tuple[int, str]:
    """从 task_meta 拿 (tier, domain)（兼容 YamlTaskMeta dataclass / dict）。"""
    if isinstance(task_meta, dict):
        return int(task_meta.get("tier", 0)), str(task_meta.get("domain", ""))
    tier = int(getattr(task_meta, "tier", 0))
    domain = str(getattr(task_meta, "domain", ""))
    return tier, domain


def _total_tokens(tu: dict[str, int]) -> int:
    """合计 input+output（不含 cache_read，避免 cache 命中拉低 efficiency 真实成本）。"""
    return int(tu.get("input", 0)) + int(tu.get("output", 0))


def _now_clock() -> float:
    """统一时间源：async 场景用 loop.time()，否则用 time.monotonic().

    runner_fn 主要在 async 内调用（loop.time 与 worker.py 同源）；
    单测同步调用 _outcome_from_score 时退到 time.monotonic（不依赖 running loop）。
    """
    try:
        return asyncio.get_running_loop().time()
    except RuntimeError:
        return time.monotonic()


def _elapsed(started_at: float) -> float:
    return max(0.0, _now_clock() - started_at)


def _outcome_from_score(
    bench_score: BenchmarkRunScore,
    started_at: float,
    token_usage: dict[str, int],
) -> TaskExecutionOutcome:
    """把 BenchmarkRunScore 映射为 TaskExecutionOutcome（store.RESULT_*）。"""
    verdict_to_result = {
        TaskVerdict.PASS: RESULT_PASS,
        TaskVerdict.FAIL: RESULT_FAIL,
        TaskVerdict.PARTIAL: RESULT_PARTIAL,
        TaskVerdict.ERROR: RESULT_ERROR,
    }
    result = verdict_to_result.get(bench_score.verdict, RESULT_ERROR)
    audit_json: str | None = None
    if bench_score.audit_chain_failures:
        try:
            audit_json = json.dumps(
                [
                    {
                        "assertion_id": f.assertion_id,
                        "kind": f.kind,
                        "event_type": f.event_type,
                        "reason": f.reason,
                    }
                    for f in bench_score.audit_chain_failures
                ],
                ensure_ascii=False,
            )
        except Exception:
            audit_json = None
    return TaskExecutionOutcome(
        result=result,
        score=bench_score.weighted_score,
        duration_seconds=_elapsed(started_at),
        token_input=int(token_usage.get("input", 0)),
        token_output=int(token_usage.get("output", 0)),
        token_cache_read=int(token_usage.get("cache_read", 0)),
        audit_assertions_json=audit_json,
        error_message=bench_score.error_message,
    )


def _outcome_error(started_at: float, message: str) -> TaskExecutionOutcome:
    """unified ERROR outcome（runner 内部异常，不混入 INFRA_ERROR 三态）。"""
    return TaskExecutionOutcome(
        result=RESULT_ERROR,
        score=None,
        duration_seconds=_elapsed(started_at),
        error_message=message,
    )


__all__ = (
    "DEFAULT_BENCH_MODEL_ALIAS",
    "DEFAULT_BENCH_TEMPERATURE",
    "HarnessHandle",
    "octo_harness_session",
    "runner_fn",
    "make_provider_router_chat_fn",
)
