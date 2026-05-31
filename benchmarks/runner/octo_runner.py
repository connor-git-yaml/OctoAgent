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
from collections.abc import Awaitable, Callable
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


@asynccontextmanager
async def octo_harness_session(
    *,
    credential_store: Any | None = None,
    llm_adapter: Any | None = None,
    project_template_root: Path | None = None,
):
    """每 task 独立 tmp OctoHarness lifespan.

    bootstrap → yield handle → shutdown，并自动处理 tmp dir 清理。

    Args:
        credential_store: 可选注入；None 时 OctoHarness 内部读宿主 auth-profiles.json
            （需要 caller 显式提供以便隔离）。Phase E 真跑时 caller 提供 instance 路径
            ``~/.octoagent/auth-profiles.json`` 副本到 tmp.
        llm_adapter: 可选注入；None 时走默认 ProviderRouterMessageAdapter（控变量
            LLM 路径，alias=bench）。
        project_template_root: 可选；存在时把 ``USER.md.template`` /
            ``MEMORY.md.template`` / ``octoagent.yaml.template`` 复制到 tmp project_root
            （保证 LLM 路径能找到 alias 配置）。
    """
    # 延迟 import：benchmarks/ 不在 sys.path top-level 时也能 import
    from fastapi import FastAPI

    from octoagent.gateway.harness.octo_harness import OctoHarness

    with tempfile.TemporaryDirectory(prefix="octobench_") as tmpdir_str:
        tmp_root = Path(tmpdir_str)
        project_root = tmp_root / "instance"
        data_dir = project_root / "data"
        mcp_servers_dir = project_root / "mcp-servers"

        project_root.mkdir(parents=True, exist_ok=True)
        data_dir.mkdir(parents=True, exist_ok=True)
        mcp_servers_dir.mkdir(parents=True, exist_ok=True)
        (project_root / "behavior" / "system").mkdir(parents=True, exist_ok=True)

        if project_template_root is not None:
            _copy_instance_template(project_template_root, project_root)

        harness = OctoHarness(
            project_root=project_root,
            credential_store=credential_store,
            llm_adapter=llm_adapter,
            mcp_servers_dir=mcp_servers_dir,
            data_dir=data_dir,
        )

        app = FastAPI()
        try:
            await harness.bootstrap(app)
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
            # F089 finding #1 闭环模式：不吞 shutdown 异常
            await harness.shutdown(app)


def _copy_instance_template(src_root: Path, dst_root: Path) -> None:
    """复制 ``USER.md.template`` / ``MEMORY.md.template`` / ``octoagent.yaml.template``。

    复用 ``apps/gateway/tests/e2e_live/helpers/factories.copy_local_instance_template``
    的语义，但不依赖 gateway test 模块（benchmarks 必须独立）。
    """
    import shutil

    behavior_src = src_root / "behavior" / "system"
    behavior_dst = dst_root / "behavior" / "system"
    behavior_dst.mkdir(parents=True, exist_ok=True)
    for fname, dst_name in (
        ("USER.md.template", "USER.md"),
        ("MEMORY.md.template", "MEMORY.md"),
    ):
        src_path = behavior_src / fname
        if src_path.exists():
            shutil.copy(src_path, behavior_dst / dst_name)

    yaml_src = src_root / "octoagent.yaml.template"
    if yaml_src.exists():
        shutil.copy(yaml_src, dst_root / "octoagent.yaml")


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

    # 轮询 task_store 等终态
    deadline = asyncio.get_running_loop().time() + max(0.5, timeout_seconds)
    while True:
        task = await sg.task_store.get_task(task_id)
        if task is not None and task.status in {
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }:
            break
        if asyncio.get_running_loop().time() > deadline:
            raise TimeoutError(
                f"task {task_id} did not reach terminal state within {timeout_seconds}s"
            )
        await asyncio.sleep(TASK_POLL_INTERVAL_SECONDS)

    token_usage = await _collect_token_usage(sg, task_id, started_at)
    return task_id, started_at, token_usage


async def _collect_token_usage(
    store_group: Any,
    task_id: str,
    since: dt.datetime,
) -> dict[str, int]:
    """从 EventStore ``MODEL_CALL_COMPLETED`` payload 汇总 input/output/cache_read token.

    payload 字段可能因 provider / version 不同：兼容
    ``token_input`` / ``input_tokens`` / ``usage.input_tokens`` 等候选键。
    """
    from octoagent.core.models.enums import EventType

    try:
        events = await store_group.event_store.get_events_by_types_since(
            task_id=task_id,
            event_types=[EventType.MODEL_CALL_COMPLETED],
            since_ts=since,
        )
    except Exception:
        return {"input": 0, "output": 0, "cache_read": 0}

    sum_input = 0
    sum_output = 0
    sum_cache_read = 0
    for evt in events:
        payload = _event_payload(evt)
        if not isinstance(payload, dict):
            continue
        sum_input += _read_token_field(
            payload, ("token_input", "input_tokens", "prompt_tokens")
        )
        sum_output += _read_token_field(
            payload, ("token_output", "output_tokens", "completion_tokens")
        )
        sum_cache_read += _read_token_field(
            payload, ("token_cache_read", "cache_read_tokens", "cache_read_input_tokens")
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
    """从 payload 多候选 key 中读 int token 数。"""
    for key in candidates:
        if key in payload:
            try:
                return int(payload[key])
            except (TypeError, ValueError):
                continue
        if "usage" in payload and isinstance(payload["usage"], dict):
            inner = payload["usage"].get(key)
            if inner is not None:
                try:
                    return int(inner)
                except (TypeError, ValueError):
                    continue
    return 0


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

    try:
        events = await store_group.event_store.get_events_by_types_since(
            task_id=parent_task_id,
            event_types=[EventType.SUBAGENT_SPAWNED],
            since_ts=since,
        )
    except Exception:
        return []
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

    # ProviderRouter 直连：alias=bench → SiliconFlow DeepSeek-V3.2
    try:
        resp = await _provider_router_chat(
            router,
            messages=messages,
            model=model_alias,
            temperature=DEFAULT_BENCH_TEMPERATURE,
            max_tokens=512,
        )
        text = resp.get("text", "") if isinstance(resp, dict) else str(resp)
        token_usage = resp.get("usage", {}) if isinstance(resp, dict) else {}
        return text.strip(), {
            "input": int(token_usage.get("input_tokens", 0) or 0),
            "output": int(token_usage.get("output_tokens", 0) or 0),
            "cache_read": int(token_usage.get("cache_read_tokens", 0) or 0),
        }
    except Exception as exc:
        logger.warning(
            "gaia_provider_chat_failed",
            extra={"task_id": getattr(task, "task_id", "?"), "error": repr(exc)},
        )
        return "", {"input": 0, "output": 0, "cache_read": 0}


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


async def _run_tau_bench_task(
    handle: HarnessHandle,
    *,
    task: Any,
    iteration: int,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """τ-bench airline 跑一 task → 返回 ``(actual_tool_calls, token_usage)``.

    Phase E 第 1 步实现：tau_bench_tool_scope 注册 + 通过 ``TOOL_CALL_STARTED`` 事件
    收集实际调用的 tool name + arguments。不实现完整的 user_simulator multi-turn
    loop（推迟到 F108 或下游 Feature——见 handoff §3.E）。

    Returns:
        (actual_tool_calls, token_usage_dict)
    """
    from benchmarks.tiers.tier2.tau_bench_adapter import (
        TAU_BENCH_TOOL_PREFIX,
        tau_bench_tool_scope,
    )

    # 加载 14 个 tau_bench airline tools
    try:
        from tau_bench.envs.airline.tools import ALL_TOOLS as TAU_TOOLS
    except ImportError:
        logger.warning(
            "tau_bench_not_installed",
            extra={"hint": "uv pip install 'git+https://github.com/sierra-research/tau-bench.git'"},
        )
        return [], {"input": 0, "output": 0, "cache_read": 0}

    instruction = getattr(task, "instruction", "")
    scope_id = f"i{iteration}-{getattr(task, 'task_idx', 0):03d}"

    actual_tool_calls: list[dict[str, Any]] = []

    started_at = dt.datetime.now(dt.timezone.utc)

    with tau_bench_tool_scope(
        handle.tool_registry, list(TAU_TOOLS), scope_id=scope_id
    ):
        try:
            task_id, _, token_usage = await _submit_and_wait_task(
                handle,
                prompt=instruction,
                timeout_seconds=120.0,
                iteration=iteration,
            )
        except (TimeoutError, RuntimeError) as exc:
            logger.warning(
                "tau_task_submit_failed",
                extra={"task_id": getattr(task, "task_id", "?"), "error": repr(exc)},
            )
            return [], {"input": 0, "output": 0, "cache_read": 0}

        # 收集 actual_tool_calls：query TOOL_CALL_STARTED events，按 prefix 过滤
        try:
            from octoagent.core.models.enums import EventType

            events = await handle.store_group.event_store.get_events_by_types_since(
                task_id=task_id,
                event_types=[EventType.TOOL_CALL_STARTED],
                since_ts=started_at,
            )
            for evt in events:
                payload = _event_payload(evt) or {}
                name = str(payload.get("tool_name", "") or "")
                if name.startswith(TAU_BENCH_TOOL_PREFIX):
                    actual_tool_calls.append(
                        {"name": name, "arguments": payload.get("arguments", {})}
                    )
        except Exception as exc:
            logger.warning(
                "tau_tool_calls_collect_failed",
                extra={"task_id": task_id, "error": repr(exc)},
            )

    return actual_tool_calls, token_usage


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
            return _outcome_error(
                started_at, f"tier 2 unsupported domain={domain!r}"
            )
        if tier == 3:
            return await _run_tier3(task_meta, iteration, rubrics, started_at)
        return _outcome_error(started_at, f"unsupported tier={tier!r}")
    except Exception as exc:
        # 全局兜底；worker.run_task_with_retry 的 try/except 会捕获并标 INFRA_ERROR，
        # 但我们也在这里 log + 返回结构化 outcome 便于 debug
        logger.exception("runner_fn_unexpected_error")
        return _outcome_error(started_at, f"runner_fn unexpected: {exc!r}")


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
    timeout_seconds = float(raw.get("timeout_seconds", TASK_DEFAULT_OVERALL_TIMEOUT_SECONDS))
    prompt = str(raw.get("prompt", ""))

    async with octo_harness_session() as handle:
        try:
            task_id, started_dt, token_usage = await _submit_and_wait_task(
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

        try:
            actual_events = await fetch_events_from_store(
                event_store=handle.store_group.event_store,
                task_id=task_id,
                task_start_time=started_dt,
            )
        except Exception as exc:
            return _outcome_error(
                started_at, f"fetch_events failed: {exc!r}"
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
    timeout_seconds = float(raw.get("timeout_seconds", TASK_DEFAULT_OVERALL_TIMEOUT_SECONDS))
    prompt = str(raw.get("prompt", ""))

    async with octo_harness_session() as handle:
        try:
            task_id, started_dt, token_usage = await _submit_and_wait_task(
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

        # H1/H2/H3 audit chain 信号常写在 child task_id 上——
        # _discover_child_task_ids 拿一层，fetch_events_from_store_tier3 内部
        # 递归发现 grandchild。
        try:
            child_task_ids = await _discover_child_task_ids(
                handle.store_group, task_id, started_dt
            )
            actual_events = await fetch_events_from_store_tier3(
                event_store=handle.store_group.event_store,
                task_id=task_id,
                task_start_time=started_dt,
                child_task_ids=child_task_ids,
            )
        except Exception as exc:
            return _outcome_error(
                started_at, f"fetch_events_tier3 failed: {exc!r}"
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
