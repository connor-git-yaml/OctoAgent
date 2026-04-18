"""LiteLLM Proxy StructuredModelClient 实现。

将 LiteLLM Proxy 接入 SkillRunner，支持工具调用循环。
实现 StructuredModelClientProtocol。
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import structlog

from .compactor import CompactionResult, ContextCompactor
from .manifest import SkillManifest
from .models import (
    SkillExecutionContext,
    SkillOutputEnvelope,
    ToolCallSpec,
    ToolFeedbackMessage,
    is_runtime_exempt_tool,
    resolve_effective_tool_allowlist,
)

log = structlog.get_logger(__name__)


class LLMCallError(Exception):
    """LLM 调用异常分类，供 SkillRunner 差异化处理。

    error_type:
        timeout       — API 超时，可重试（退避后）
        rate_limit    — 速率限制（429），应等待后重试
        context_overflow — 上下文超长（4xx），不可盲目重试，需压缩
        api_error     — 其他 API 错误，可重试
    """

    def __init__(self, error_type: str, message: str, *, retriable: bool = True, status_code: int = 0):
        super().__init__(message)
        self.error_type = error_type
        self.retriable = retriable
        self.status_code = status_code


def _classify_proxy_error(exc: Exception, status_code: int = 0) -> LLMCallError:
    """将 httpx / Proxy 异常转换为 LLMCallError。"""
    msg = str(exc)

    if isinstance(exc, (httpx.TimeoutException, httpx.ReadTimeout, httpx.WriteTimeout, httpx.ConnectTimeout)):
        return LLMCallError("timeout", msg, retriable=True)

    if status_code == 429:
        return LLMCallError("rate_limit", msg, retriable=True, status_code=429)

    # LiteLLM Proxy 对上下文超长通常返回 400 + 特定错误信息
    overflow_keywords = ("context_length", "maximum context", "token limit", "too many tokens", "context window")
    msg_lower = msg.lower()
    if status_code in (400, 413) or any(kw in msg_lower for kw in overflow_keywords):
        if any(kw in msg_lower for kw in overflow_keywords):
            return LLMCallError("context_overflow", msg, retriable=False, status_code=status_code)

    return LLMCallError("api_error", msg, retriable=True, status_code=status_code)


def _to_fn_name(tool_name: str) -> str:
    """工具名 → OpenAI function name（点替换为双下划线）。"""
    return tool_name.replace(".", "__")


def _from_fn_name(fn_name: str) -> str:
    """OpenAI function name → 工具名（双下划线替换为点）。"""
    return fn_name.replace("__", ".")


class LiteLLMSkillClient:
    """LiteLLM Proxy 接入 SkillRunner 的 ModelClient 实现。

    实现 StructuredModelClientProtocol，将 LiteLLM Proxy 的 OpenAI 兼容接口
    接入 SkillRunner，支持多轮工具调用循环。

    每个 (task_id, trace_id) 维护独立的对话历史，跨 generate() 调用保持上下文。
    """

    # Feature 064 P3 优化 5: 对话历史条目数上限，防止内存泄漏
    _MAX_HISTORY_ENTRIES = 100

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
    ) -> None:
        self._proxy_url = proxy_url.rstrip("/")
        self._master_key = master_key
        self._tool_broker = tool_broker
        self._timeout_s = timeout_s
        self._responses_model_aliases = set(responses_model_aliases or ())
        self._responses_reasoning_aliases = dict(responses_reasoning_aliases or {})
        self._responses_direct_params = dict(responses_direct_params or {})
        # 对话历史：key = "{task_id}:{trace_id}"
        self._histories: dict[str, list[dict[str, Any]]] = {}
        # Feature 064 P3 优化 4: per-instance 长生命周期 httpx.AsyncClient
        self._http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s, connect=10.0),
        )

    async def close(self) -> None:
        """关闭 per-instance httpx.AsyncClient。"""
        await self._http_client.aclose()

    def clear_history(self, key: str) -> None:
        """清理指定 key 的对话历史。

        Feature 064 P3 优化 5: Task 终态后由 SkillRunner 调用，释放已完成 task 的对话。
        """
        self._histories.pop(key, None)

    def _key(self, ctx: SkillExecutionContext) -> str:
        return f"{ctx.task_id}:{ctx.trace_id}"

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
            is_mcp = is_runtime_exempt_tool(tool_meta.name, getattr(tool_meta, "tool_group", ""))
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

    @staticmethod
    def _build_responses_url(base_url: str) -> str:
        base = base_url.rstrip("/")
        if base.endswith("/backend-api") or base.endswith("/backend-api/codex"):
            return f"{base}/responses"
        return f"{base}/v1/responses"

    # System Message 字符预算（合并后超出则截断尾部）
    # cheap/小模型建议 8K chars（~2K tokens），main/大模型 24K chars（~6K tokens）
    _SYSTEM_MESSAGE_CHAR_BUDGET = 24_000

    @classmethod
    def _merge_system_messages_to_front(
        cls,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """将所有 system 消息合并为一条放在开头，并限制总长度。

        部分模型（Qwen、Gemma 等）只接受恰好一个 system 消息且必须在最前面。
        多个连续的 system 消息也会被拒绝。
        合并后如果超过字符预算，截断尾部并加省略标记。
        """
        if not messages:
            return messages
        system_parts: list[str] = []
        non_system: list[dict[str, Any]] = []
        for msg in messages:
            if msg.get("role") == "system":
                content = str(msg.get("content", "")).strip()
                if content:
                    system_parts.append(content)
            else:
                non_system.append(msg)
        if not system_parts:
            return messages
        # 已经只有一个 system 且在开头——不需要变（但仍检查预算）
        if len(system_parts) == 1 and messages[0].get("role") == "system":
            content = messages[0].get("content", "")
            if len(content) <= cls._SYSTEM_MESSAGE_CHAR_BUDGET:
                return messages
            # 超预算——截断
            truncated = content[:cls._SYSTEM_MESSAGE_CHAR_BUDGET].rstrip()
            return [{"role": "system", "content": truncated + "\n\n[system prompt truncated]"}, *non_system]

        merged = "\n\n".join(system_parts)
        if len(merged) > cls._SYSTEM_MESSAGE_CHAR_BUDGET:
            original_len = len(merged)
            merged = merged[:cls._SYSTEM_MESSAGE_CHAR_BUDGET].rstrip()
            merged += f"\n\n[system prompt truncated: {original_len} → {len(merged)} chars]"
            log.info(
                "system_prompt_truncated",
                original_chars=original_len,
                budget_chars=cls._SYSTEM_MESSAGE_CHAR_BUDGET,
            )
        return [{"role": "system", "content": merged}, *non_system]

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

        Responses API 路径的 system message 在发送前由
        _build_responses_instructions() 提取为 instructions 参数。
        """
        history = cls._normalize_history_messages(execution_context.conversation_messages)
        if not history and prompt.strip():
            history = [{"role": "user", "content": prompt.strip()}]
        system_msg = manifest.load_description() or "You are a helpful assistant."
        return [{"role": "system", "content": system_msg}, *history]

    @staticmethod
    def _build_responses_instructions(
        manifest: SkillManifest,
        history: list[dict[str, Any]],
    ) -> str:
        instruction_parts: list[str] = []
        manifest_description = manifest.load_description() or "You are a helpful assistant."
        if manifest_description:
            instruction_parts.append(manifest_description)

        system_parts = [
            str(message.get("content", "")).strip()
            for message in history
            if str(message.get("role", "user")).strip().lower() == "system"
            and str(message.get("content", "")).strip()
        ]
        if system_parts:
            instruction_parts.append("\n\n".join(system_parts))

        return "\n\n".join(part for part in instruction_parts if part)

    @staticmethod
    def _history_to_responses_input(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """将统一的 Chat Completions 格式 history 转换为 Responses API input。

        内部 history 统一存 Chat Completions 格式，只在发送 Responses API 时转换。
        转换规则：
        - system → 跳过（已由 instructions 处理）
        - user → {role: "user", content: [{type: "input_text", text}]}
        - assistant (无 tool_calls) → {role: "assistant", content: [{type: "output_text", text}]}
        - assistant (有 tool_calls) → 多个 {type: "function_call", call_id, name, arguments}
        - tool → {type: "function_call_output", call_id, output}
        """
        # 预扫 known_call_ids：收集所有 function_call（assistant.tool_calls 以及旧
        # type-based 格式）的 call_id。用于在转换 Responses API input 时过滤孤立
        # 的 function_call_output——防止历史片段重组、权限拒绝、压缩路径等造成的
        # tool 消息无对应 function_call，触发 Responses API 400。
        known_call_ids: set[str] = set()
        for message in history:
            if str(message.get("role", "")).strip() == "assistant":
                for tc in message.get("tool_calls") or []:
                    cid = str(tc.get("id", "")).strip()
                    if cid:
                        known_call_ids.add(cid)
            elif str(message.get("type", "")).strip() == "function_call":
                cid = str(message.get("call_id", "")).strip()
                if cid:
                    known_call_ids.add(cid)

        items: list[dict[str, Any]] = []
        for message in history:
            role = str(message.get("role", "")).strip()

            if role == "system":
                continue

            if role == "tool":
                call_id = str(message.get("tool_call_id", "")).strip()
                if call_id and call_id in known_call_ids:
                    items.append({
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": str(message.get("content", "")),
                    })
                elif call_id:
                    log.warning(
                        "orphan_tool_message_skipped",
                        call_id=call_id,
                        known_count=len(known_call_ids),
                    )
                continue

            if role == "assistant":
                tc_list = message.get("tool_calls")
                if tc_list and isinstance(tc_list, list):
                    # assistant 有 tool_calls → 转为 function_call items
                    for tc in tc_list:
                        fn = tc.get("function", {})
                        call_id = str(tc.get("id", "")).strip()
                        if call_id:
                            items.append({
                                "type": "function_call",
                                "call_id": call_id,
                                "name": str(fn.get("name", "")),
                                "arguments": str(fn.get("arguments", "")),
                            })
                else:
                    # 纯文本 assistant 回复
                    items.append({
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": str(message.get("content", ""))}],
                    })
                continue

            if role == "user":
                items.append({
                    "role": "user",
                    "content": [{"type": "input_text", "text": str(message.get("content", ""))}],
                })
                continue

            # 兼容旧格式：type-based messages（不应再出现）
            message_type = str(message.get("type", "")).strip()
            if message_type == "function_call":
                call_id = str(message.get("call_id", "")).strip()
                if call_id:
                    items.append({
                        "type": "function_call",
                        "call_id": call_id,
                        "name": str(message.get("name", "")),
                        "arguments": str(message.get("arguments", "")),
                    })
            elif message_type == "function_call_output":
                call_id = str(message.get("call_id", "")).strip()
                if call_id and call_id in known_call_ids:
                    items.append({
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": str(message.get("output", "")),
                    })
                elif call_id:
                    log.warning(
                        "orphan_legacy_function_call_output_skipped",
                        call_id=call_id,
                        known_count=len(known_call_ids),
                    )

        return items

    async def _call_proxy_responses(
        self,
        *,
        manifest: SkillManifest,
        history: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
        # Responses API 直连 Codex Backend（绕过 Proxy 避免误 fallback）
        direct = self._responses_direct_params.get(manifest.model_alias)

        # Codex Backend 不认别名，必须用真实模型名（如 gpt-5.4）
        wire_model = (direct.get("model") if direct else None) or manifest.model_alias

        body: dict[str, Any] = {
            "model": wire_model,
            "instructions": self._build_responses_instructions(manifest, history),
            "input": self._history_to_responses_input(history),
            "store": False,
            "stream": True,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        reasoning = self._responses_reasoning_aliases.get(manifest.model_alias)
        if reasoning is not None and hasattr(reasoning, "to_responses_api_param"):
            body["reasoning"] = reasoning.to_responses_api_param()

        text_parts: list[str] = []
        tool_calls_raw: dict[str, dict[str, Any]] = {}
        response_payload: dict[str, Any] = {}

        if direct:
            target_url = self._build_responses_url(direct["api_base"])
            target_key = direct.get("api_key", self._master_key)
            target_headers = {
                "Authorization": f"Bearer {target_key}",
                "Content-Type": "application/json",
                **direct.get("headers", {}),
            }
        else:
            target_url = self._build_responses_url(self._proxy_url)
            target_key = self._master_key
            target_headers = {
                "Authorization": f"Bearer {target_key}",
                "Content-Type": "application/json",
            }

        try:
            stream_ctx = self._http_client.stream(
                "POST",
                target_url,
                json=body,
                headers=target_headers,
            )
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            raise _classify_proxy_error(exc) from exc

        async with stream_ctx as resp:
            if resp.status_code >= 400:
                body_text = await resp.aread()
                error_body = body_text.decode(errors="replace")[:500]
                log.error(
                    "litellm_responses_proxy_error",
                    status=resp.status_code,
                    body=error_body,
                )
                raise _classify_proxy_error(
                    httpx.HTTPStatusError(
                        f"Responses API returned {resp.status_code}: {error_body}",
                        request=resp.request,
                        response=resp,
                    ),
                    status_code=resp.status_code,
                )
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    continue

                event_type = str(event.get("type", ""))
                if event_type == "response.output_text.delta":
                    delta = str(event.get("delta", ""))
                    if delta:
                        text_parts.append(delta)
                    continue

                if event_type == "response.output_item.added":
                    item = event.get("item", {})
                    if isinstance(item, dict) and item.get("type") == "function_call":
                        tool_calls_raw[str(item.get("id", ""))] = {
                            "id": str(item.get("call_id") or item.get("id") or ""),
                            "raw_name": str(item.get("name", "")),
                            "tool_name": _from_fn_name(str(item.get("name", ""))),
                            "arguments": str(item.get("arguments") or ""),
                        }
                    continue

                if event_type == "response.function_call_arguments.delta":
                    item_id = str(event.get("item_id", ""))
                    if item_id in tool_calls_raw:
                        tool_calls_raw[item_id]["arguments"] += str(event.get("delta", ""))
                    continue

                if event_type == "response.function_call_arguments.done":
                    item_id = str(event.get("item_id", ""))
                    if item_id in tool_calls_raw:
                        tool_calls_raw[item_id]["arguments"] = str(
                            event.get("arguments") or tool_calls_raw[item_id]["arguments"]
                        )
                    continue

                if event_type == "response.output_item.done":
                    item = event.get("item", {})
                    if isinstance(item, dict) and item.get("type") == "function_call":
                        item_id = str(item.get("id", ""))
                        if item_id in tool_calls_raw:
                            # 已有记录（从 output_item.added 创建）——只补充 call_id，
                            # 不覆盖流式累积的 arguments
                            existing = tool_calls_raw[item_id]
                            call_id = str(item.get("call_id") or item.get("id") or "")
                            if call_id and not existing.get("id"):
                                existing["id"] = call_id
                        else:
                            # 没有 added 事件（异常路径）——用 done 的完整数据
                            tool_calls_raw[item_id] = {
                                "id": str(item.get("call_id") or item.get("id") or ""),
                                "raw_name": str(item.get("name", "")),
                                "tool_name": _from_fn_name(str(item.get("name", ""))),
                                "arguments": str(item.get("arguments") or ""),
                            }
                    continue

                if event_type == "response.completed":
                    response_payload = event.get("response", {}) or {}

        tool_calls: list[dict[str, Any]] = []
        for tool_call in tool_calls_raw.values():
            try:
                arguments = json.loads(tool_call["arguments"]) if tool_call["arguments"] else {}
            except json.JSONDecodeError:
                arguments = {}
            tool_calls.append(
                {
                    "id": tool_call["id"],
                    "tool_name": tool_call["tool_name"],
                    "arguments": arguments,
                }
            )

        if not text_parts:
            for item in response_payload.get("output", []):
                if not isinstance(item, dict):
                    continue
                item_type = str(item.get("type", ""))
                if item_type != "message":
                    continue
                for content_item in item.get("content", []):
                    if (
                        isinstance(content_item, dict)
                        and content_item.get("type") == "output_text"
                        and content_item.get("text")
                    ):
                        text_parts.append(str(content_item.get("text", "")))

        usage = response_payload.get("usage", {})
        # 尝试从 LiteLLM response 提取成本（可能在顶层或 usage 子对象中）
        cost_raw = (
            response_payload.get("cost")
            or response_payload.get("_cost")
            or usage.get("cost")
            or 0.0
        )
        try:
            cost_value = float(cost_raw)
        except (TypeError, ValueError):
            cost_value = 0.0
        metadata = {
            "model_name": str(response_payload.get("model", "") or manifest.model_alias),
            "provider": "openai",
            "token_usage": {
                "prompt_tokens": int(usage.get("input_tokens", 0) or 0),
                "completion_tokens": int(usage.get("output_tokens", 0) or 0),
                "total_tokens": int(usage.get("total_tokens", 0) or 0),
            },
            "cost_usd": cost_value,
            "cost_unavailable": cost_value == 0.0,
            "function_call_items": [
                {
                    "type": "function_call",
                    "call_id": str(item.get("id", "") or ""),
                    "name": str(item.get("raw_name", "")),
                    "arguments": str(item.get("arguments", "")),
                }
                for item in tool_calls_raw.values()
            ],
        }
        return "".join(text_parts), tool_calls, metadata

    async def _call_proxy(
        self, body: dict[str, Any]
    ) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
        """调用 LiteLLM Proxy（SSE 流式），返回 (content, tool_calls, metadata)。

        tool_calls 格式: [{"id": str, "tool_name": str, "arguments": dict}]
        metadata 包含 token_usage / cost_usd / model_name 等（如可用）。
        """
        # 安全网：发送前再次合并 system 消息（防止上游遗漏）
        sent_messages = body.get("messages", [])
        merged = self._merge_system_messages_to_front(sent_messages)
        if merged is not sent_messages:
            log.warning(
                "call_proxy_system_merged_at_send",
                model=body.get("model"),
                original_count=len(sent_messages),
                merged_count=len(merged),
            )
            body = {**body, "messages": merged}
        content_parts: list[str] = []
        # 按 index 合并流式 tool_call 片段
        tc_raw: dict[int, dict[str, Any]] = {}
        # 从流末 chunk 提取 token usage
        usage_data: dict[str, int] = {}

        # 确保 LiteLLM 在流结束时返回 usage 数据
        body_with_stream = {
            **body,
            "stream": True,
            "stream_options": {"include_usage": True},
        }

        try:
            stream_ctx = self._http_client.stream(
                "POST",
                f"{self._proxy_url}/v1/chat/completions",
                json=body_with_stream,
                headers={
                    "Authorization": f"Bearer {self._master_key}",
                    "Content-Type": "application/json",
                },
            )
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            raise _classify_proxy_error(exc) from exc

        async with stream_ctx as resp:
            if resp.status_code >= 400:
                body_text = await resp.aread()
                error_body = body_text.decode(errors="replace")[:500]
                log.error(
                    "litellm_proxy_error",
                    status=resp.status_code,
                    body=error_body,
                )
                raise _classify_proxy_error(
                    httpx.HTTPStatusError(
                        f"Proxy returned {resp.status_code}: {error_body}",
                        request=resp.request,
                        response=resp,
                    ),
                    status_code=resp.status_code,
                )
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload.strip() == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue

                # 提取流末 usage 数据（LiteLLM 在 stream_options.include_usage=true 时
                # 会在最终 chunk 返回 usage 字段）
                chunk_usage = chunk.get("usage")
                if isinstance(chunk_usage, dict):
                    usage_data = {
                        "prompt_tokens": int(chunk_usage.get("prompt_tokens", 0) or 0),
                        "completion_tokens": int(
                            chunk_usage.get("completion_tokens", 0) or 0
                        ),
                        "total_tokens": int(chunk_usage.get("total_tokens", 0) or 0),
                    }

                choices = chunk.get("choices", [])
                if not choices:
                    continue
                delta = choices[0].get("delta", {})
                if delta.get("content"):
                    content_parts.append(delta["content"])
                for tc_delta in delta.get("tool_calls", []):
                    idx = tc_delta.get("index", 0)
                    if idx not in tc_raw:
                        tc_raw[idx] = {"id": "", "name": "", "arguments": ""}
                    tc = tc_raw[idx]
                    if tc_delta.get("id"):
                        tc["id"] = tc_delta["id"]
                    fn = tc_delta.get("function", {})
                    if fn.get("name"):
                        tc["name"] += fn["name"]
                    if fn.get("arguments"):
                        tc["arguments"] += fn["arguments"]

        content = "".join(content_parts)
        tool_calls = []
        for idx in sorted(tc_raw):
            tc = tc_raw[idx]
            try:
                arguments = json.loads(tc["arguments"]) if tc["arguments"] else {}
            except json.JSONDecodeError:
                arguments = {}
            tool_calls.append(
                {
                    "id": tc["id"] or f"call_{idx}",
                    "tool_name": _from_fn_name(tc["name"]),
                    "arguments": arguments,
                }
            )

        # 构建 metadata，包含 token usage 和 cost 信息
        metadata: dict[str, Any] = {}
        if usage_data:
            metadata["token_usage"] = usage_data
            metadata["model_name"] = str(body.get("model", ""))
            metadata["provider"] = "litellm"
            # SSE 路径暂无直接成本数据，标记为不可用
            metadata["cost_usd"] = 0.0
            metadata["cost_unavailable"] = True
        return content, tool_calls, metadata

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
        use_responses_api = manifest.model_alias in self._responses_model_aliases

        if step == 1:
            # 初始化对话历史
            history = self._build_initial_history(
                manifest=manifest,
                execution_context=execution_context,
                prompt=prompt,
            )
            self._histories[key] = history

            # Feature 064 P3 优化 5: maxsize 兜底，清理最老条目防止内存泄漏
            if len(self._histories) > self._MAX_HISTORY_ENTRIES:
                oldest_key = next(iter(self._histories))
                self._histories.pop(oldest_key, None)

        history = self._histories[key]

        if step > 1 and feedback:
            # 统一使用 Chat Completions tool role 格式回填
            # 如果 LLM 没提供 tool_call_id，合成一个（避免降级为自然语言 user message）
            for fb in feedback:
                call_id = fb.tool_call_id or ""
                if call_id:
                    history.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": fb.output if not fb.is_error else f"ERROR: {fb.error}",
                    })
                else:
                    # 系统错误 feedback（如 _tool/_runner）没有对应的 function_call，
                    # 不能作为 tool role 发送，否则 Responses API 会因找不到 call_id 报 400。
                    # 降级为 user role 的错误提示。
                    error_text = fb.error or fb.output or "工具执行异常"
                    history.append({
                        "role": "user",
                        "content": f"[系统提示] 工具 {fb.tool_name} 执行失败: {error_text}",
                    })

        # ---- Feature 064 P2-A: 上下文压缩 ----
        # 在构建 LLM 请求前检测并压缩对话历史。
        # 压缩 token 消耗不计入 UsageTracker（基础设施开销）。
        # compaction_threshold_ratio 设为 1.0 时永不触发（回滚方案）。
        compaction_result: CompactionResult | None = None
        threshold_ratio = manifest.compaction_threshold_ratio
        if threshold_ratio < 1.0:
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

        tools = await self._get_tool_schemas(
            manifest,
            execution_context,
            responses_api=use_responses_api,
        )

        log.debug(
            "litellm_skill_client_generate",
            key=key,
            step=step,
            attempt=attempt,
            tools_count=len(tools),
            responses_api=use_responses_api,
        )

        # 非 OpenAI 模型（Qwen 等）要求 system 消息在开头，
        # 合并分散的 system 消息到第一条
        pre_merge_roles = [m.get("role", "?") for m in history]
        history = self._merge_system_messages_to_front(history)
        post_merge_roles = [m.get("role", "?") for m in history]
        if pre_merge_roles != post_merge_roles:
            log.info(
                "system_messages_merged",
                pre_roles=pre_merge_roles[:20],
                post_roles=post_merge_roles[:20],
                step=step,
            )

        if use_responses_api:
            content, tool_calls, metadata = await self._call_proxy_responses(
                manifest=manifest,
                history=history,
                tools=tools,
            )
        else:
            body: dict[str, Any] = {"model": manifest.model_alias, "messages": history}
            if tools:
                body["tools"] = tools
                body["tool_choice"] = "auto"
            content, tool_calls, metadata = await self._call_proxy(body)

        # 追加 assistant 消息到历史（统一使用 Chat Completions 格式）
        if tool_calls:
            has_ids = any(tc.get("id") for tc in tool_calls)

            if has_ids:
                # 统一格式：Chat Completions assistant tool_calls message
                # Responses API 在发送前由 _history_to_responses_input() 转换
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
        else:
            history.append({"role": "assistant", "content": content})
            return SkillOutputEnvelope(
                content=content,
                complete=True,
                metadata=metadata,
                token_usage=metadata.get("token_usage", {}),
                cost_usd=float(metadata.get("cost_usd", 0.0) or 0.0),
            )
