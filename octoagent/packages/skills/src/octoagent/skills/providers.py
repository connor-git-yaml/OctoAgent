"""LLM Provider 抽象层。

把 Chat Completions 和 Responses API 的请求构建、流式解析、usage/cost 提取拆成
独立 Provider 类。LiteLLMSkillClient 负责编排（history 管理、compaction、tool schema
获取、provider 派发）。

Provider 类可独立 import 使用；未来加第三个 provider 只需新增 Provider 类，
不需要改 client。
"""

from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable

import httpx
import structlog

from .manifest import SkillManifest

log = structlog.get_logger(__name__)


# System Message 字符预算（合并后超出则截断尾部）
# cheap/小模型建议 8K chars（~2K tokens），main/大模型 24K chars（~6K tokens）
_SYSTEM_MESSAGE_CHAR_BUDGET = 24_000


class LLMCallError(Exception):
    """LLM 调用异常分类，供 SkillRunner 差异化处理。

    error_type:
        timeout       — API 超时，可重试（退避后）
        rate_limit    — 速率限制（429），应等待后重试
        context_overflow — 上下文超长（4xx），不可盲目重试，需压缩
        empty_input   — 发送 Responses API 前 history 被全部过滤（孤立 call_id /
                        压缩过度等），不可重试
        conversation_state_lost — step>1 但 history 已丢失（进程重启 / 淘汰 /
                        跨 client 实例），不可重试，需从 checkpoint 恢复
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


def _merge_system_messages_to_front(
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
        if len(content) <= _SYSTEM_MESSAGE_CHAR_BUDGET:
            return messages
        truncated = content[:_SYSTEM_MESSAGE_CHAR_BUDGET].rstrip()
        return [{"role": "system", "content": truncated + "\n\n[system prompt truncated]"}, *non_system]

    merged = "\n\n".join(system_parts)
    if len(merged) > _SYSTEM_MESSAGE_CHAR_BUDGET:
        original_len = len(merged)
        merged = merged[:_SYSTEM_MESSAGE_CHAR_BUDGET].rstrip()
        merged += f"\n\n[system prompt truncated: {original_len} → {len(merged)} chars]"
        log.info(
            "system_prompt_truncated",
            original_chars=original_len,
            budget_chars=_SYSTEM_MESSAGE_CHAR_BUDGET,
        )
    return [{"role": "system", "content": merged}, *non_system]


def _build_responses_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/backend-api") or base.endswith("/backend-api/codex"):
        return f"{base}/responses"
    return f"{base}/v1/responses"


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


def _history_to_responses_input(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """将统一的 Chat Completions 格式 history 转换为 Responses API input。

    内部 history 统一存 Chat Completions role-based 格式，只在发送 Responses API
    时转换。转换规则：
    - system → 跳过（已由 instructions 处理）
    - user → {role: "user", content: [{type: "input_text", text}]}
    - assistant (无 tool_calls) → {role: "assistant", content: [{type: "output_text", text}]}
    - assistant (有 tool_calls) → 多个 {type: "function_call", call_id, name, arguments}
    - tool → {type: "function_call_output", call_id, output}
    """
    # 预扫 known_call_ids：收集所有 assistant.tool_calls 的 id。用于过滤孤立
    # 的 tool message —— 防止历史片段重组、权限拒绝、压缩路径等造成的 tool
    # 消息无对应 function_call，触发 Responses API 400。
    known_call_ids: set[str] = set()
    for message in history:
        if str(message.get("role", "")).strip() == "assistant":
            for tc in message.get("tool_calls") or []:
                cid = str(tc.get("id", "")).strip()
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

    return items


@runtime_checkable
class LLMProviderProtocol(Protocol):
    """LLM provider 统一接口。

    provider 负责：请求 body 构建、流式解析、usage/cost 提取、错误分类。

    uses_responses_tool_format:
        True  → 工具 schema 用 Responses API flat 格式（{type, name, description, parameters}）
        False → 用 Chat Completions 嵌套格式（{type, function: {name, description, parameters}}）
    """

    uses_responses_tool_format: bool

    async def call(
        self,
        *,
        manifest: SkillManifest,
        history: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        http_client: httpx.AsyncClient,
    ) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
        """发送请求并解析响应。

        Returns:
            (content, tool_calls, metadata)
            - content: 文本内容
            - tool_calls: [{"id": str, "tool_name": str, "arguments": dict}]
            - metadata: model_name / provider / token_usage / cost_usd 等
        """
        ...


class ChatCompletionsProvider:
    """LiteLLM Proxy /v1/chat/completions Provider。

    走 OpenAI Chat Completions 兼容协议 + SSE 流式，从最终 chunk（由
    stream_options.include_usage=true 驱动）提取 token usage。成本由 LiteLLM
    账单统一结算，这里不回传 cost。
    """

    uses_responses_tool_format = False

    def __init__(self, proxy_url: str, master_key: str) -> None:
        self._proxy_url = proxy_url.rstrip("/")
        self._master_key = master_key

    async def call(
        self,
        *,
        manifest: SkillManifest,
        history: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        http_client: httpx.AsyncClient,
    ) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
        body: dict[str, Any] = {
            "model": manifest.model_alias,
            "messages": history,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        # 安全网：发送前再次合并 system 消息（防止上游遗漏）
        sent_messages = body.get("messages", [])
        merged = _merge_system_messages_to_front(sent_messages)
        if merged is not sent_messages:
            log.warning(
                "call_proxy_system_merged_at_send",
                model=body.get("model"),
                original_count=len(sent_messages),
                merged_count=len(merged),
            )
            body = {**body, "messages": merged}

        # 确保 LiteLLM 在流结束时返回 usage 数据
        body_with_stream = {
            **body,
            "stream": True,
            "stream_options": {"include_usage": True},
        }

        content_parts: list[str] = []
        # 按 index 合并流式 tool_call 片段
        tc_raw: dict[int, dict[str, Any]] = {}
        # 从流末 chunk 提取 token usage
        usage_data: dict[str, int] = {}

        try:
            stream_ctx = http_client.stream(
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
        tool_calls: list[dict[str, Any]] = []
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

        metadata: dict[str, Any] = {}
        if usage_data:
            metadata["token_usage"] = usage_data
            metadata["model_name"] = str(body.get("model", ""))
            metadata["provider"] = "litellm"
            # SSE 路径暂无直接成本数据，标记为不可用
            metadata["cost_usd"] = 0.0
            metadata["cost_unavailable"] = True
        return content, tool_calls, metadata


class ResponsesApiProvider:
    """OpenAI Responses API Provider，可直连 Codex backend 或走 Proxy。

    配置字典说明：
    - responses_direct_params[model_alias]: {api_base, api_key, model, headers}
      直连 Codex backend，绕过 Proxy 防止误 fallback；alias 无此配置则回落到 Proxy。
    - responses_reasoning_aliases[model_alias]: reasoning 配置对象
      （需有 .to_responses_api_param()）。
    """

    uses_responses_tool_format = True

    def __init__(
        self,
        proxy_url: str,
        master_key: str,
        *,
        responses_direct_params: dict[str, dict[str, Any]] | None = None,
        responses_reasoning_aliases: dict[str, Any] | None = None,
    ) -> None:
        self._proxy_url = proxy_url.rstrip("/")
        self._master_key = master_key
        self._responses_direct_params = dict(responses_direct_params or {})
        self._responses_reasoning_aliases = dict(responses_reasoning_aliases or {})

    async def call(
        self,
        *,
        manifest: SkillManifest,
        history: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        http_client: httpx.AsyncClient,
    ) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
        # Responses API 直连 Codex Backend（绕过 Proxy 避免误 fallback）
        direct = self._responses_direct_params.get(manifest.model_alias)

        # Codex Backend 不认别名，必须用真实模型名（如 gpt-5.4）
        wire_model = (direct.get("model") if direct else None) or manifest.model_alias

        responses_input = _history_to_responses_input(history)
        if not responses_input:
            # 所有 message 被过滤（纯 system、孤立 function_call_output 被剥离、
            # compactor 激进压缩等）→ input=[] 会直接踩 Responses API 400
            # `missing_required_parameter`。提前 fail-fast，给上层一个明确错误
            # 分类而不是把空 body 甩给 API。不可重试：重发结果一样。
            log.error(
                "responses_input_empty_after_filter",
                history_count=len(history),
                tools_count=len(tools or []),
            )
            raise LLMCallError(
                "empty_input",
                "Responses API input 为空：history 全部被系统消息/孤立 call_id 过滤掉，"
                "无可用上下文发送。请检查 history 压缩或 tool_call 配对。",
                retriable=False,
            )

        body: dict[str, Any] = {
            "model": wire_model,
            "instructions": _build_responses_instructions(manifest, history),
            "input": responses_input,
            "store": False,
            "stream": True,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        reasoning = self._responses_reasoning_aliases.get(manifest.model_alias)
        if reasoning is not None and hasattr(reasoning, "to_responses_api_param"):
            body["reasoning"] = reasoning.to_responses_api_param()

        if direct:
            target_url = _build_responses_url(direct["api_base"])
            target_key = direct.get("api_key", self._master_key)
            target_headers = {
                "Authorization": f"Bearer {target_key}",
                "Content-Type": "application/json",
                **direct.get("headers", {}),
            }
        else:
            target_url = _build_responses_url(self._proxy_url)
            target_key = self._master_key
            target_headers = {
                "Authorization": f"Bearer {target_key}",
                "Content-Type": "application/json",
            }

        text_parts: list[str] = []
        tool_calls_raw: dict[str, dict[str, Any]] = {}
        response_payload: dict[str, Any] = {}

        try:
            stream_ctx = http_client.stream(
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
                        call_id = str(item.get("call_id", "")).strip()
                        if not call_id:
                            # Responses API 规范里 function_call 必须带 call_id，缺失
                            # 说明上游异常。不再隐式 fallback 到 item.id —— item.id
                            # 形如 `fc_xxx`，和 API 期望用于配对 function_call_output
                            # 的 `call_xxx` 语义不同，混用会让后续轮的 tool result
                            # 配对失败，陷入"LLM 看不到工具输出"的循环。保持 call_id
                            # 为空，交给 tool feedback 降级分支处理。
                            log.warning(
                                "responses_api_function_call_missing_call_id",
                                item_id=item.get("id"),
                                name=item.get("name"),
                            )
                        tool_calls_raw[str(item.get("id", ""))] = {
                            "id": call_id,
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
                        call_id = str(item.get("call_id", "")).strip()
                        if not call_id:
                            log.warning(
                                "responses_api_function_call_done_missing_call_id",
                                item_id=item_id,
                                name=item.get("name"),
                            )
                        if item_id in tool_calls_raw:
                            # 已有记录（从 output_item.added 创建）——只补充 call_id，
                            # 不覆盖流式累积的 arguments
                            existing = tool_calls_raw[item_id]
                            if call_id and not existing.get("id"):
                                existing["id"] = call_id
                        else:
                            # 没有 added 事件（异常路径）——用 done 的完整数据
                            tool_calls_raw[item_id] = {
                                "id": call_id,
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


__all__ = [
    "ChatCompletionsProvider",
    "LLMCallError",
    "LLMProviderProtocol",
    "ResponsesApiProvider",
]
