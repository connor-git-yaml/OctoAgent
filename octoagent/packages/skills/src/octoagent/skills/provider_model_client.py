"""Feature 080 Phase 3：基于 ProviderRouter 的 StructuredModelClient 实现。

替代 ``LiteLLMSkillClient``，让 SkillRunner 直接走 provider 直连路径——
不再依赖 LiteLLM Proxy。

设计要点：
- 实现 ``StructuredModelClientProtocol.generate()``，与 LiteLLMSkillClient 兼容
- 内部用 ``ProviderRouter`` 做 alias → ProviderClient 路由
- 把 task_scope（``f"{task_id}:{trace_id}"``）传给 router，让同 task 内 alias
  钉死（F1 修复）
- 对话历史管理（per (task_id, trace_id) 缓存 + idle eviction）从 LiteLLMSkillClient
  原样复用——历史管理逻辑与 LLM 调用层无关，复制是为了独立演进
- 工具 schema 统一以 OpenAI Chat 嵌套格式生产；ProviderClient 内部按 transport
  转换为 Responses flat / Anthropic flat 格式

Phase 4 LiteLLM Proxy 退役后，``LiteLLMSkillClient`` 整个文件删除，
``ProviderModelClient`` 改名为 ``SkillModelClient`` 成为唯一实现。
"""

from __future__ import annotations

import json
import time
from collections import OrderedDict
from typing import Any

import structlog
from octoagent.provider.provider_router import ProviderRouter
from octoagent.tooling.security_render import render_tool_result_for_llm  # F124 T021

# Feature 081 P4：compactor.py 已删除。运行时 compaction 主线在
# gateway/services/context_compaction.py（走 llm_service.call → ProviderRouter），
# 此处不再依赖 LiteLLM-Proxy-bound ContextCompactor。
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

log = structlog.get_logger(__name__)


def _to_fn_name(tool_name: str) -> str:
    """工具名 → OpenAI function name（与 skills.providers._to_fn_name 同义）。"""
    return tool_name.replace(".", "__")


def _from_fn_name(fn_name: str) -> str:
    return fn_name.replace("__", ".")


def _render_validation_errors(
    error: str | None, validation_errors: list[dict[str, Any]]
) -> str:
    """F126 项1：把字段级结构化校验错误渲染为 LLM 可精确修正的文本。

    输出形如：
        ERROR: 参数校验失败（tool）：path: Field required
        校验错误（字段级）：
        - loc=path | type=missing | msg=Field required
    """
    lines = [f"ERROR: {error}" if error else "ERROR: 参数校验失败"]
    if validation_errors:
        lines.append("校验错误（字段级）：")
        for item in validation_errors:
            loc = ".".join(str(x) for x in (item.get("loc") or []))
            lines.append(
                f"- loc={loc} | type={item.get('type', '')} | msg={item.get('msg', '')}"
            )
    return "\n".join(lines)


class ProviderModelClient:
    """基于 ProviderRouter 的 SkillRunner 模型客户端。

    Phase 3 入口：把 SkillRunner 从 LiteLLMSkillClient（→ LiteLLM Proxy）切到
    ProviderRouter（→ provider 直连）。

    与 LiteLLMSkillClient 的主要差异：
    - 路由层：用 ProviderRouter 替代 ``_chat_provider`` / ``_responses_provider``
      硬编码组合
    - alias scoping：传入 ``task_scope=key`` 让 router 在 task 内钉死 alias
      → provider 映射（F1 修复）
    - 配置 source of truth：每次 task 起开始时现读 octoagent.yaml（不依赖
      Gateway 启动时的 frozen ``responses_model_aliases``）
    - 没有 LiteLLM master_key / proxy_url 概念
    """

    _MAX_HISTORY_ENTRIES = 1024
    _HISTORY_IDLE_EVICT_SECONDS = 30 * 60

    # F126 项2: tail eviction 占位前缀（确定性检测"已折叠"，避免二次改写）
    _EVICTION_PLACEHOLDER_PREFIX = "[已折叠，见 artifact:"

    def __init__(
        self,
        *,
        provider_router: ProviderRouter,
        tool_broker: Any | None = None,
        event_store: Any | None = None,
    ) -> None:
        self._router = provider_router
        self._tool_broker = tool_broker
        self._event_store = event_store  # F126 项2: emit TOOL_RESULT_EVICTED
        self._histories: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
        self._last_access: dict[str, float] = {}
        # F126 项2: key → tool_call_id → {artifact_ref, tool_name}，供 tail eviction
        # 构造确定性占位（仅记录有 artifact_ref=可 read-back 恢复的 tool 结果）。
        self._fold_meta: dict[str, dict[str, dict[str, str]]] = {}

    async def aclose(self) -> None:
        """释放本客户端持有的会话状态，不关闭共享 ProviderRouter。"""
        self._histories.clear()
        self._last_access.clear()
        self._fold_meta.clear()

    def clear_history(self, key: str) -> None:
        """task 终态后由 SkillRunner 调用。

        同时清理 router 的 task scope alias 缓存，避免长期残留。
        """
        self._histories.pop(key, None)
        self._last_access.pop(key, None)
        self._fold_meta.pop(key, None)  # F126 项2
        # task scope 在 router 内也用同一个 key（task_id:trace_id），一并清理
        self._router.invalidate_task(key)

    # ──────────────── 历史管理（与 LiteLLMSkillClient 同源） ────────────────

    def _key(self, ctx: SkillExecutionContext) -> str:
        return f"{ctx.task_id}:{ctx.trace_id}"

    def _evict_idle_histories_if_needed(self, *, protect_key: str) -> None:
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
            log.warning(
                "history_pressure_no_idle_to_evict",
                current_count=len(self._histories),
                max_entries=self._MAX_HISTORY_ENTRIES,
                idle_window_seconds=self._HISTORY_IDLE_EVICT_SECONDS,
            )
            return
        self._histories.pop(oldest_key, None)
        self._last_access.pop(oldest_key, None)
        self._router.invalidate_task(oldest_key)
        log.info("history_evicted_idle", evicted_key=oldest_key)

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
        history = cls._normalize_history_messages(
            execution_context.conversation_messages,
        )
        if not history and prompt.strip():
            history = [{"role": "user", "content": prompt.strip()}]
        system_msg = manifest.load_description() or "You are a helpful assistant."
        return [{"role": "system", "content": system_msg}, *history]

    @staticmethod
    def _append_feedback_to_history(
        history: list[dict[str, Any]],
        feedback: list[ToolFeedbackMessage],
    ) -> None:
        """与 LiteLLMSkillClient._append_feedback_to_history 同源。"""
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
                # F126 项1：错误回灌时附字段级结构化校验错误（loc/type/msg），便于 LLM 精确修正
                error_text = f"ERROR: {fb.error}"
                if fb.is_error and fb.validation_errors:
                    error_text = _render_validation_errors(fb.error, fb.validation_errors)
                if call_id:
                    history.append(
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            # F124 T021：finding 派生 [security-warning] 前缀，不改 fb.output/error
                            "content": render_tool_result_for_llm(
                                fb.output if not fb.is_error else error_text,
                                fb.security_findings,
                            ),
                        }
                    )
                    already_emitted_call_ids.add(call_id)
                else:
                    label = "执行出错" if fb.is_error else "执行结果"
                    if fb.is_error and fb.validation_errors:
                        body = _render_validation_errors(fb.error, fb.validation_errors)
                    else:
                        body = fb.error if fb.is_error else fb.output
                    body = body or "（空输出）"
                    # F124 T021：finding 派生 [security-warning] 前缀，不改 fb.output/error
                    body = render_tool_result_for_llm(body, fb.security_findings)
                    history.append(
                        {
                            "role": "user",
                            "content": f"[工具 {fb.tool_name} {label}] {body}",
                        }
                    )
            elif fb.kind == FeedbackKind.LOOP_GUARD:
                warning = fb.error or fb.output or "检测到重复工具调用"
                history.append(
                    {"role": "user", "content": f"[循环警告] {warning}"}
                )
            else:  # FeedbackKind.SYSTEM_NOTICE
                notice = fb.error or fb.output or "系统内部异常"
                history.append(
                    {"role": "user", "content": f"[系统提示] {fb.tool_name}: {notice}"}
                )

    # ──────────────── 工具 schema ────────────────

    async def _get_tool_schemas(
        self,
        manifest: SkillManifest,
        execution_context: SkillExecutionContext,
    ) -> list[dict[str, Any]]:
        """生产 OpenAI Chat 嵌套格式的工具 schema；ProviderClient 内部按 transport
        转换。"""
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
        result: list[dict[str, Any]] = []
        for tool_meta in all_tools:
            is_mcp = is_runtime_exempt_tool(
                tool_meta.name,
                getattr(tool_meta, "tool_group", ""),
                getattr(tool_meta, "metadata", None),
            )
            if tool_meta.name not in allowed_tool_names and not is_mcp:
                continue
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
        log.debug(
            "tool_schema_resolved",
            total=len(result),
            allowed=len(allowed_tool_names),
        )
        return result

    # ──────────────── compaction ────────────────

    def _record_fold_meta(
        self, key: str, feedback: list[ToolFeedbackMessage]
    ) -> None:
        """F126 项2：记录有 artifact_ref（可 read-back 恢复）的 tool 结果的折叠元数据。

        仅记录 TOOL_RESULT 且有 artifact_ref 的 fb——这些是被 LargeOutputHandler
        卸载为 artifact 的大输出，折叠后能经 artifact.read_content 读回（SD-2 闭环）。
        """
        meta = self._fold_meta.setdefault(key, {})
        for fb in feedback:
            if fb.kind != FeedbackKind.TOOL_RESULT:
                continue
            if not fb.artifact_ref or not fb.tool_call_id:
                continue
            meta[fb.tool_call_id] = {
                "artifact_ref": fb.artifact_ref,
                "tool_name": fb.tool_name,
            }

    @staticmethod
    def _estimate_history_tokens(history: list[dict[str, Any]]) -> int:
        """chars/4 近似 token（与 runner per-turn 预算同启发式，无 tokenizer 依赖）。

        纳入 content + assistant tool_calls 的 arguments（各 transport 都会序列化发送，
        大参数 tool call 不计会低估 → 折叠偏晚，评审 Codex MED 闭环）。CJK 低估约 4×，
        仅影响折叠触发时机；真溢出仍由 runner context_overflow 兜底。
        """
        total = 0
        for msg in history:
            content = msg.get("content")
            if isinstance(content, str):
                total += len(content)
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function") if isinstance(tc, dict) else None
                if isinstance(fn, dict):
                    args = fn.get("arguments")
                    if isinstance(args, str):
                        total += len(args)
        return total // 4

    def _is_folded(self, content: Any) -> bool:
        return isinstance(content, str) and content.startswith(
            self._EVICTION_PLACEHOLDER_PREFIX
        )

    async def _maybe_compact_history(
        self,
        manifest: SkillManifest,
        history: list[dict[str, Any]],
        *,
        key: str,
        step: int,
    ) -> None:
        """F126 项2：tool_call_id 确定性 tail eviction。

        上下文接近预算时，把**最旧**的、已卸载为 artifact 的 tool 结果折叠成确定性
        占位（指向 artifact，可经 read-back 恢复）。占位一旦写入即冻结（下轮检测到
        已折叠形态则跳过），位置不动、只改 role:tool 消息 content，不碰 system /
        assistant / user 消息——保证前缀单调收敛（KV-cache 实测验证，见 kv-cache-probe.md）。
        """
        threshold_ratio = manifest.compaction_threshold_ratio
        if threshold_ratio >= 1.0:
            return
        max_context_tokens = int(
            manifest.resource_limits.get("max_context_tokens", 0)
            or manifest.resource_limits.get("max_tokens", 0)
            or 128000
        )
        budget_tokens = int(threshold_ratio * max_context_tokens)
        if self._estimate_history_tokens(history) <= budget_tokens:
            return

        fold_meta = self._fold_meta.get(key, {})
        evicted: list[dict[str, Any]] = []
        # 从最旧（前部）向后扫，优先折叠旧 tool 结果，直到回到预算内或无可折叠
        for msg in history:
            if self._estimate_history_tokens(history) <= budget_tokens:
                break
            if msg.get("role") != "tool":
                continue
            content = msg.get("content")
            if not isinstance(content, str) or self._is_folded(content):
                continue
            call_id = str(msg.get("tool_call_id", "")).strip()
            meta = fold_meta.get(call_id)
            if not meta:
                # 无 artifact_ref = 不可 read-back 恢复 → 不折叠（避免信息单向丢失）
                continue
            # 诚实标注：N = 折叠时刻 history content 的字节数（可能是 LargeOutputHandler
            # 截断后的 preview，非 artifact 完整大小）。标"折叠前"而非"原始"避免误导
            # LLM 对 artifact 真实数据量的判断（评审 Codex MED 闭环）。
            folded_bytes = len(content.encode("utf-8"))
            placeholder = (
                f"{self._EVICTION_PLACEHOLDER_PREFIX}{meta['artifact_ref']}"
                f"（工具 {meta['tool_name']}，折叠前 {folded_bytes} 字节）]"
            )
            msg["content"] = placeholder  # 原地改写、位置不动、内容确定性冻结
            evicted.append(
                {
                    "tool_call_id": call_id,
                    "artifact_ref": meta["artifact_ref"],
                    "tool_name": meta["tool_name"],
                    "folded_bytes": folded_bytes,
                    "placeholder_bytes": len(placeholder.encode("utf-8")),
                }
            )

        if evicted:
            await self._emit_evicted(key, step, evicted)

    async def _emit_evicted(
        self, key: str, step: int, evicted: list[dict[str, Any]]
    ) -> None:
        """emit TOOL_RESULT_EVICTED（Constitution #2；event_store 缺失时降级 log）。"""
        if self._event_store is None:
            log.info("tool_result_evicted_no_event_store", key=key, count=len(evicted))
            return
        from octoagent.core.event_helpers import emit_task_event
        from octoagent.core.models.enums import ActorType, EventType

        task_id, _, trace_id = key.partition(":")  # key = f"{task_id}:{trace_id}"
        for item in evicted:
            try:
                await emit_task_event(
                    self._event_store,
                    task_id=task_id,
                    event_type=EventType.TOOL_RESULT_EVICTED,
                    payload={**item, "step": step},
                    actor=ActorType.WORKER,
                    trace_id=trace_id,
                )
            except Exception:
                log.warning("tool_result_evicted_emit_failed", exc_info=True)

    # ──────────────── 主编排 ────────────────

    @staticmethod
    def _append_assistant_and_build_envelope(
        history: list[dict[str, Any]],
        *,
        content: str,
        tool_calls: list[dict[str, Any]],
        metadata: dict[str, Any],
    ) -> SkillOutputEnvelope:
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
            history.append(
                {
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
                }
            )
        else:
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
        from octoagent.provider import ProviderLLMCallError as _LLMCallError

        key = self._key(execution_context)

        if key not in self._histories:
            if step > 1:
                log.error(
                    "conversation_history_missing_on_resume",
                    key=key,
                    step=step,
                    attempt=attempt,
                    has_feedback=bool(feedback),
                )
                raise _LLMCallError(
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

        self._histories.move_to_end(key)
        self._last_access[key] = time.monotonic()
        self._evict_idle_histories_if_needed(protect_key=key)

        history = self._histories[key]
        if step > 1 and feedback:
            self._append_feedback_to_history(history, feedback)
            self._record_fold_meta(key, feedback)  # F126 项2

        await self._maybe_compact_history(manifest, history, key=key, step=step)

        # F1 修复：传入 task_scope=key 让 router 在同 task 内钉死 alias→provider，
        # 避免 task 中途改 yaml 触发跨 transport history 错乱
        resolved = self._router.resolve_for_alias(
            manifest.model_alias, task_scope=key,
        )

        tools = await self._get_tool_schemas(manifest, execution_context)

        # instructions = manifest description（system prompt 等价物）
        instructions = manifest.load_description() or ""

        # reasoning：从 manifest 推断（与现有 LiteLLMSkillClient 行为对齐）
        reasoning = self._resolve_reasoning(manifest)

        # F087 followup：从 execution_context.metadata 读 force_tool_choice。
        # 用途：e2e 测试 / 上层服务强制 LLM 选定目标工具（绕开 LLM 自主决策不
        # 确定性）。值可为：
        # - 字符串："auto" / "required" / "none"（Anthropic "required" → any）
        # - dict：OpenAI Chat 格式 {"type": "function", "function": {"name": "x"}}
        # - JSON 字符串：上述 dict 的 JSON 编码（用于 NormalizedMessage.metadata
        #   只接受 dict[str, str] 的场景，自动 decode）
        # ProviderClient 内部按 transport 转换。
        force_tool_choice = execution_context.metadata.get("force_tool_choice")
        if isinstance(force_tool_choice, str):
            stripped = force_tool_choice.strip()
            if stripped.startswith("{") and stripped.endswith("}"):
                try:
                    force_tool_choice = json.loads(stripped)
                except json.JSONDecodeError:
                    log.warning(
                        "provider_model_client_force_tool_choice_json_decode_failed",
                        raw=stripped[:200],
                    )
                    force_tool_choice = None
        elif not isinstance(force_tool_choice, dict):
            force_tool_choice = None

        log.debug(
            "provider_model_client_generate",
            key=key,
            step=step,
            attempt=attempt,
            alias=manifest.model_alias,
            provider_id=resolved.provider_id,
            model=resolved.model_name,
            transport=resolved.client.runtime.transport.value,
            tools_count=len(tools),
            force_tool_choice=force_tool_choice if force_tool_choice else None,
        )

        content, tool_calls, metadata = await resolved.client.call(
            instructions=instructions,
            history=history,
            tools=tools,
            model_name=resolved.model_name,
            reasoning=reasoning,
            tool_choice=force_tool_choice,
        )

        return self._append_assistant_and_build_envelope(
            history,
            content=content,
            tool_calls=tool_calls,
            metadata=metadata,
        )

    @staticmethod
    def _resolve_reasoning(manifest: SkillManifest) -> dict[str, Any] | None:
        """从 manifest 推断 reasoning 配置。

        本 phase 仅支持 manifest 直接声明 ``reasoning`` 字段；对接现有
        ``responses_reasoning_aliases`` 在 Phase 4 完成（与 LiteLLM Proxy
        一起退役）。
        """
        reasoning = getattr(manifest, "reasoning", None)
        if reasoning is None:
            return None
        if hasattr(reasoning, "to_responses_api_param"):
            return reasoning.to_responses_api_param()
        if isinstance(reasoning, dict):
            return reasoning
        return None


__all__ = ["ProviderModelClient"]
