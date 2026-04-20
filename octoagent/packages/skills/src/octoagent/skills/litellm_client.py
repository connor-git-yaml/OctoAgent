"""LiteLLM Proxy StructuredModelClient 实现。

将 LiteLLM Proxy 接入 SkillRunner，支持工具调用循环。
实现 StructuredModelClientProtocol。

本模块只负责编排：history 管理、context compaction、tool schema 获取、provider 派发。
具体的请求构建、流式解析、usage 提取在 .providers 模块的 Provider 类里。
"""

from __future__ import annotations

import json
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import structlog

from .compactor import ContextCompactor
from .manifest import SkillManifest
from .models import (
    FeedbackKind,
    SkillExecutionContext,
    SkillOutputEnvelope,
    ToolCallSpec,
    ToolFeedbackMessage,
    is_runtime_exempt_tool,
    resolve_effective_tool_allowlist,
)
from .providers import (
    ChatCompletionsProvider,
    LLMCallError,
    LLMProviderProtocol,
    ResponsesApiProvider,
    _classify_proxy_error,
    _from_fn_name,
    _history_to_responses_input,
    _merge_system_messages_to_front,
    _to_fn_name,
)

log = structlog.get_logger(__name__)


__all__ = [
    "LLMCallError",
    "LiteLLMSkillClient",
    "_classify_proxy_error",
    "_from_fn_name",
    "_to_fn_name",
]


class LiteLLMSkillClient:
    """LiteLLM Proxy 接入 SkillRunner 的 ModelClient 实现。

    实现 StructuredModelClientProtocol，将 LiteLLM Proxy 的 OpenAI 兼容接口
    接入 SkillRunner，支持多轮工具调用循环。

    每个 (task_id, trace_id) 维护独立的对话历史，跨 generate() 调用保持上下文。

    Provider 派发：根据 manifest.model_alias 是否在 responses_model_aliases
    集合中，在 ResponsesApiProvider 和 ChatCompletionsProvider 之间选择。
    本 client 只负责编排（history 管理 / compaction / tool schema / provider
    派发），具体的请求构建/流式解析/usage 提取在 provider 类中。
    """

    # 对话历史条目数软上限：超过阈值后尝试淘汰"空闲足够久"的 key；
    # 如果所有 key 都处于活跃窗口内，则宁愿 memory 涨一点也不淘汰活跃会话
    # （丢失活跃会话会让 LLM 看不到之前的 tool_calls，重放非幂等工具风险极高）。
    _MAX_HISTORY_ENTRIES = 1024
    # 仅当 key 最后访问时间超过此窗口才允许淘汰。Gateway 常规 skill run 通常
    # 几秒到几分钟就结束，30 分钟内仍活跃的会话几乎可以确定是"仍在跑的 task"。
    _HISTORY_IDLE_EVICT_SECONDS = 30 * 60

    def __init__(
        self,
        proxy_url: str,
        master_key: str,
        tool_broker: Any | None = None,
        timeout_s: float = 60.0,
        *,
        responses_model_aliases: set[str] | None = None,
        responses_reasoning_aliases: dict[str, Any] | None = None,
        responses_direct_params: dict[str, dict[str, Any]] | None = None,
        auth_refresh_callback: Callable[..., Awaitable[Any]] | None = None,
    ) -> None:
        self._proxy_url = proxy_url.rstrip("/")
        self._master_key = master_key
        self._tool_broker = tool_broker
        self._timeout_s = timeout_s
        self._responses_model_aliases = set(responses_model_aliases or ())
        self._auth_refresh_callback = auth_refresh_callback
        # 对话历史：key = "{task_id}:{trace_id}"；OrderedDict 用来维护 LRU 顺序
        self._histories: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
        # 每个 conversation key 的最后一次 access 时间（monotonic），用于
        # "仅淘汰足够空闲的 key"；避免 maxsize 误伤仍在跑的任务。
        self._last_access: dict[str, float] = {}
        # Feature 064 P3 优化 4: per-instance 长生命周期 httpx.AsyncClient
        self._http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s, connect=10.0),
        )
        # 内部组装 provider 实例——callers 不感知 provider 存在，保持构造器向后兼容
        self._chat_provider: LLMProviderProtocol = ChatCompletionsProvider(
            proxy_url=self._proxy_url,
            master_key=self._master_key,
            auth_refresh_callback=auth_refresh_callback,
        )
        self._responses_provider: LLMProviderProtocol = ResponsesApiProvider(
            proxy_url=self._proxy_url,
            master_key=self._master_key,
            responses_direct_params=responses_direct_params,
            responses_reasoning_aliases=responses_reasoning_aliases,
            auth_refresh_callback=auth_refresh_callback,
        )

    async def close(self) -> None:
        """关闭 per-instance httpx.AsyncClient。"""
        await self._http_client.aclose()

    def clear_history(self, key: str) -> None:
        """清理指定 key 的对话历史。

        Feature 064 P3 优化 5: Task 终态后由 SkillRunner 调用，释放已完成 task 的对话。
        """
        self._histories.pop(key, None)
        self._last_access.pop(key, None)

    def _evict_idle_histories_if_needed(self, *, protect_key: str) -> None:
        """空间兜底：只淘汰"空闲时间超过阈值"的 conversation，绝不触碰活跃会话。

        Gateway 启动时共用单例 LiteLLMSkillClient，`_histories` 被所有并发 task
        共享。旧实现是"超 maxsize 就 pop(next(iter(...)))" 做 FIFO 淘汰 —— 即使
        `move_to_end` 维护了 LRU 顺序，FIFO 仍然可能淘汰一个"最近才开始但本轮
        恰好不是最活跃"的会话，被淘汰的 task 下一轮会走 conversation_state_lost
        fail-fast 路径，等于用户任务被静默中断。

        改为"仅淘汰 idle 超阈值的 key"：
        - 若数量 ≤ maxsize，直接返回
        - 按最后访问时间升序找出所有 idle 超过阈值的候选
        - 如果没有候选（所有 key 都在活跃窗口内），log.warning 不淘汰
        - 有候选就淘汰最久空闲那个（一次只淘汰一个，避免批量操作）
        """
        if len(self._histories) <= self._MAX_HISTORY_ENTRIES:
            return

        now = time.monotonic()
        oldest_key: str | None = None
        oldest_access = float("inf")
        for k, access in self._last_access.items():
            if k == protect_key:
                continue
            if (now - access) < self._HISTORY_IDLE_EVICT_SECONDS:
                continue
            if access < oldest_access:
                oldest_access = access
                oldest_key = k

        if oldest_key is None:
            # 所有 key 都在活跃窗口内，拒绝淘汰：宁愿短暂多占内存，也不能
            # 丢任何活跃会话的 tool_call/tool_result 历史（丢了会让 LLM
            # 重放已执行工具）。
            log.warning(
                "history_pressure_no_idle_to_evict",
                current_count=len(self._histories),
                max_entries=self._MAX_HISTORY_ENTRIES,
                idle_window_seconds=self._HISTORY_IDLE_EVICT_SECONDS,
            )
            return

        self._histories.pop(oldest_key, None)
        self._last_access.pop(oldest_key, None)
        log.info(
            "history_evicted_idle",
            evicted_key=oldest_key,
            idle_seconds=int(now - oldest_access),
        )

    @staticmethod
    def _append_feedback_to_history(
        history: list[dict[str, Any]],
        feedback: list[ToolFeedbackMessage],
    ) -> None:
        """将 runner 传来的 feedback 按 ``kind`` 分派写入 history。

        三种 kind 对应三种写入策略：

        - ``TOOL_RESULT``：若有 call_id，写 tool role 消息并与 function_call 配对；
          若意外缺 call_id，降级为 user role 的"工具结果"提示（保留原始输出语义，
          不再错误地把成功输出标记为"执行失败"）。
        - ``LOOP_GUARD``：runner 注入的循环警示，以 user role 写入系统警告。
        - ``SYSTEM_NOTICE``：runner/工具层内部异常，以 user role 写入系统提示。

        同一个 call_id 的 tool_result 只会出现一次：runner 的 feedback buffer 虽然
        已做单次清空，但 model_client 再做一层去重防御（即使调用方传了重复 feedback，
        history 也不会膨胀或让 LLM 看到多份相同结果）。
        """
        already_emitted_call_ids = {
            str(msg.get("tool_call_id", "")).strip()
            for msg in history
            if msg.get("role") == "tool"
        }
        for fb in feedback:
            if fb.kind == FeedbackKind.TOOL_RESULT:
                call_id = fb.tool_call_id or ""
                if call_id and call_id in already_emitted_call_ids:
                    continue
                if call_id:
                    history.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": fb.output
                        if not fb.is_error
                        else f"ERROR: {fb.error}",
                    })
                    already_emitted_call_ids.add(call_id)
                else:
                    # tool_result 却没 call_id：通常是上游 LLM 响应解析失败或兼容路径。
                    # 不能以 tool role 写入（Responses API 会因无匹配 function_call 报错），
                    # 降级为 user role 但保留真实语义（不再错误地把成功输出标记为失败）。
                    label = "执行出错" if fb.is_error else "执行结果"
                    body = fb.error if fb.is_error else fb.output
                    body = body or "（空输出）"
                    history.append({
                        "role": "user",
                        "content": f"[工具 {fb.tool_name} {label}] {body}",
                    })
            elif fb.kind == FeedbackKind.LOOP_GUARD:
                warning = fb.error or fb.output or "检测到重复工具调用"
                history.append({
                    "role": "user",
                    "content": f"[循环警告] {warning}",
                })
            else:  # FeedbackKind.SYSTEM_NOTICE
                notice = fb.error or fb.output or "系统内部异常"
                history.append({
                    "role": "user",
                    "content": f"[系统提示] {fb.tool_name}: {notice}",
                })

    def _key(self, ctx: SkillExecutionContext) -> str:
        return f"{ctx.task_id}:{ctx.trace_id}"

    def _select_provider(self, manifest: SkillManifest) -> LLMProviderProtocol:
        if manifest.model_alias in self._responses_model_aliases:
            return self._responses_provider
        return self._chat_provider

    async def _get_tool_schemas(
        self,
        manifest: SkillManifest,
        execution_context: SkillExecutionContext,
        *,
        responses_api: bool,
    ) -> list[dict[str, Any]]:
        """从 ToolBroker 获取工具 schema，转换为目标 API 的工具格式。"""
        if not self._tool_broker:
            return []
        allowed_tool_names = resolve_effective_tool_allowlist(
            permission_mode=manifest.permission_mode,
            tools_allowed=list(manifest.tools_allowed),
            metadata=execution_context.metadata,
        )
        if not allowed_tool_names:
            return []
        try:
            all_tools = await self._tool_broker.discover()
        except Exception:
            log.warning("tool_discovery_failed", exc_info=True)
            return []
        result = []
        mcp_extra = []
        filtered_out = []
        for tool_meta in all_tools:
            # MCP 动态工具额外放行（不受静态 tools_allowed 白名单限制）
            is_mcp = is_runtime_exempt_tool(
                tool_meta.name,
                getattr(tool_meta, "tool_group", ""),
                getattr(tool_meta, "metadata", None),
            )
            if is_mcp and tool_meta.name not in allowed_tool_names:
                mcp_extra.append(tool_meta.name)
            if tool_meta.name not in allowed_tool_names and not is_mcp:
                filtered_out.append(tool_meta.name)
                continue
            # 能到这里说明：工具在白名单内 或 MCP 豁免工具
            if responses_api:
                result.append(
                    {
                        "type": "function",
                        "name": _to_fn_name(tool_meta.name),
                        "description": tool_meta.description,
                        "parameters": tool_meta.parameters_json_schema,
                    }
                )
            else:
                result.append(
                    {
                        "type": "function",
                        "function": {
                            "name": _to_fn_name(tool_meta.name),
                            "description": tool_meta.description,
                            "parameters": tool_meta.parameters_json_schema,
                        },
                    }
                )
        if mcp_extra:
            log.info(
                "mcp_tools_injected",
                mcp_tools=mcp_extra,
                total_tools=len(result),
            )
        log.debug(
            "tool_schema_resolved",
            total=len(result),
            allowed=len(allowed_tool_names),
            discovered=len(all_tools),
            filtered_out_count=len(filtered_out),
            mcp_extra_count=len(mcp_extra),
        )
        return result

    # ──────────────── history 组装辅助（编排侧） ────────────────

    @staticmethod
    def _normalize_history_messages(
        messages: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        normalized: list[dict[str, str]] = []
        for item in messages:
            content = str(item.get("content", "")).strip()
            if not content:
                continue
            role = str(item.get("role", "user")).strip().lower() or "user"
            if role not in {"system", "user", "assistant"}:
                role = "user"
            normalized.append({"role": role, "content": content})
        return normalized

    @classmethod
    def _build_initial_history(
        cls,
        *,
        manifest: SkillManifest,
        execution_context: SkillExecutionContext,
        prompt: str,
    ) -> list[dict[str, str]]:
        """统一构建初始 history（始终为 Chat Completions 格式）。

        Responses API 路径的 system message 在发送前由 ResponsesApiProvider
        提取为 instructions 参数。
        """
        history = cls._normalize_history_messages(execution_context.conversation_messages)
        if not history and prompt.strip():
            history = [{"role": "user", "content": prompt.strip()}]
        system_msg = manifest.load_description() or "You are a helpful assistant."
        return [{"role": "system", "content": system_msg}, *history]

    async def _maybe_compact_history(
        self,
        manifest: SkillManifest,
        history: list[dict[str, Any]],
        *,
        key: str,
        step: int,
    ) -> None:
        """Feature 064 P2-A: 按需压缩对话历史（原地）。

        compaction_threshold_ratio 设为 1.0 时永不触发（回滚方案）。压缩 token
        消耗不计入 UsageTracker（基础设施开销）。
        """
        threshold_ratio = manifest.compaction_threshold_ratio
        if threshold_ratio >= 1.0:
            return
        # 从 resource_limits 获取 max_tokens 作为上下文窗口上限
        max_context_tokens = int(
            manifest.resource_limits.get("max_context_tokens", 0)
            or manifest.resource_limits.get("max_tokens", 0)
            or 128000  # 默认 128k
        )
        compactor = ContextCompactor(
            proxy_url=self._proxy_url,
            master_key=self._master_key,
            recent_turns=manifest.compaction_recent_turns,
            http_client=self._http_client,
        )
        compaction_result = await compactor.compact(
            history=history,
            max_tokens=max_context_tokens,
            threshold_ratio=threshold_ratio,
            compaction_model_alias=manifest.compaction_model_alias,
        )
        if compaction_result.strategy_used != "none":
            log.info(
                "context_compaction_applied",
                key=key,
                step=step,
                strategy=compaction_result.strategy_used,
                before_tokens=compaction_result.before_tokens,
                after_tokens=compaction_result.after_tokens,
                messages_compressed=compaction_result.messages_compressed,
            )

    @staticmethod
    def _append_assistant_and_build_envelope(
        history: list[dict[str, Any]],
        *,
        content: str,
        tool_calls: list[dict[str, Any]],
        metadata: dict[str, Any],
    ) -> SkillOutputEnvelope:
        """把 assistant 回复写回 history，并构建 SkillOutputEnvelope。

        统一使用 Chat Completions 格式（tool_calls 数组）存储；Responses API
        发送前由 _history_to_responses_input() 转换。无 tool_call_id 时
        回落到自然语言摘要（向后兼容）。
        """
        if not tool_calls:
            history.append({"role": "assistant", "content": content})
            return SkillOutputEnvelope(
                content=content,
                complete=True,
                metadata=metadata,
                token_usage=metadata.get("token_usage", {}),
                cost_usd=float(metadata.get("cost_usd", 0.0) or 0.0),
            )

        has_ids = any(tc.get("id") for tc in tool_calls)
        if has_ids:
            history.append({
                "role": "assistant",
                "content": content or None,
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": _to_fn_name(tc["tool_name"]),
                            "arguments": json.dumps(tc["arguments"]),
                        },
                    }
                    for tc in tool_calls
                ],
            })
        else:
            # 向后兼容：无 tool_call_id 的自然语言摘要
            tc_summary = ", ".join(
                f"{tc['tool_name']}({tc['arguments']})" for tc in tool_calls
            )
            history.append({"role": "assistant", "content": f"[Calling tools: {tc_summary}]"})

        return SkillOutputEnvelope(
            content=content,
            complete=False,
            tool_calls=[
                ToolCallSpec(
                    tool_name=tc["tool_name"],
                    arguments=tc["arguments"],
                    tool_call_id=tc.get("id", ""),
                )
                for tc in tool_calls
            ],
            metadata=metadata,
            token_usage=metadata.get("token_usage", {}),
            cost_usd=float(metadata.get("cost_usd", 0.0) or 0.0),
        )

    # ──────────────── bw-compat shims（测试 / 下游调用点） ────────────────

    @staticmethod
    def _merge_system_messages_to_front(
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return _merge_system_messages_to_front(messages)

    @staticmethod
    def _history_to_responses_input(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return _history_to_responses_input(history)

    async def _call_proxy_responses(
        self,
        *,
        manifest: SkillManifest,
        history: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
        """bw-compat shim：委托给 ResponsesApiProvider。"""
        return await self._responses_provider.call(
            manifest=manifest,
            history=history,
            tools=tools,
            http_client=self._http_client,
        )

    # ──────────────── 主编排 ────────────────

    async def generate(
        self,
        *,
        manifest: SkillManifest,
        execution_context: SkillExecutionContext,
        prompt: str,
        feedback: list[ToolFeedbackMessage],
        attempt: int,
        step: int,
    ) -> SkillOutputEnvelope:
        key = self._key(execution_context)

        if key not in self._histories:
            if step > 1:
                # step>1 说明调用方认为 history 早已存在，但 dict 里找不到 ——
                # 通常意味着：进程重启、maxsize 淘汰策略踢掉了本会话、或者跨
                # client 实例传 key。绝不能静默用 initial history 重建：initial
                # 只含 system + user，后续 feedback 里的 tool_result 会因为找
                # 不到配对的 assistant.tool_calls 被 _history_to_responses_input
                # 当成孤儿过滤。LLM 回到初态，大概率会重放之前的 tool_call，
                # 对 write_file / send_message 这类非幂等工具是真实风险。
                #
                # 让主链路显式 fail，由上层（resume engine）从 checkpoint 恢复
                # 完整 history 后再重试；比静默丢上下文安全。
                log.error(
                    "conversation_history_missing_on_resume",
                    key=key,
                    step=step,
                    attempt=attempt,
                    has_feedback=bool(feedback),
                )
                raise LLMCallError(
                    "conversation_state_lost",
                    (
                        f"step={step} 但 conversation history (key={key}) 已丢失，"
                        "可能是进程重启或活跃会话被淘汰；不能凭 initial history "
                        "重建 tool_call 配对。请从 checkpoint 恢复完整对话轨迹后重试。"
                    ),
                    retriable=False,
                )
            self._histories[key] = self._build_initial_history(
                manifest=manifest,
                execution_context=execution_context,
                prompt=prompt,
            )

        # 进入前刷新 LRU 位置和最后访问时间，让 maxsize 淘汰走"最久未用优先"，
        # 避免误伤活跃会话。
        self._histories.move_to_end(key)
        self._last_access[key] = time.monotonic()
        self._evict_idle_histories_if_needed(protect_key=key)

        history = self._histories[key]

        if step > 1 and feedback:
            self._append_feedback_to_history(history, feedback)

        await self._maybe_compact_history(manifest, history, key=key, step=step)

        provider = self._select_provider(manifest)
        tools = await self._get_tool_schemas(
            manifest,
            execution_context,
            responses_api=provider.uses_responses_tool_format,
        )

        log.debug(
            "litellm_skill_client_generate",
            key=key,
            step=step,
            attempt=attempt,
            tools_count=len(tools),
            responses_api=provider.uses_responses_tool_format,
        )

        # 非 OpenAI 模型（Qwen 等）要求 system 消息在开头——合并分散的 system 消息
        pre_merge_roles = [m.get("role", "?") for m in history]
        history = _merge_system_messages_to_front(history)
        post_merge_roles = [m.get("role", "?") for m in history]
        if pre_merge_roles != post_merge_roles:
            log.info(
                "system_messages_merged",
                pre_roles=pre_merge_roles[:20],
                post_roles=post_merge_roles[:20],
                step=step,
            )

        content, tool_calls, metadata = await provider.call(
            manifest=manifest,
            history=history,
            tools=tools,
            http_client=self._http_client,
        )

        return self._append_assistant_and_build_envelope(
            history,
            content=content,
            tool_calls=tool_calls,
            metadata=metadata,
        )
