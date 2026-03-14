"""LiteLLM Proxy StructuredModelClient 实现。

将 LiteLLM Proxy 接入 SkillRunner，支持工具调用循环。
实现 StructuredModelClientProtocol。
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import structlog

from .manifest import SkillManifest
from .models import (
    SkillExecutionContext,
    SkillOutputEnvelope,
    ToolCallSpec,
    ToolFeedbackMessage,
    resolve_effective_tool_allowlist,
)

log = structlog.get_logger(__name__)


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

    def __init__(
        self,
        proxy_url: str,
        master_key: str,
        tool_broker: Any | None = None,
        timeout_s: float = 60.0,
        *,
        responses_model_aliases: set[str] | None = None,
        responses_reasoning_aliases: dict[str, Any] | None = None,
    ) -> None:
        self._proxy_url = proxy_url.rstrip("/")
        self._master_key = master_key
        self._tool_broker = tool_broker
        self._timeout_s = timeout_s
        self._responses_model_aliases = set(responses_model_aliases or ())
        self._responses_reasoning_aliases = dict(responses_reasoning_aliases or {})
        # 对话历史：key = "{task_id}:{trace_id}"
        self._histories: dict[str, list[dict[str, Any]]] = {}
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
            return []
        result = []
        for tool_meta in all_tools:
            if tool_meta.name in allowed_tool_names:
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
        return result

    @staticmethod
    def _build_responses_url(base_url: str) -> str:
        base = base_url.rstrip("/")
        if base.endswith("/backend-api") or base.endswith("/backend-api/codex"):
            return f"{base}/responses"
        return f"{base}/v1/responses"

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
        use_responses_api: bool,
    ) -> list[dict[str, str]]:
        history = cls._normalize_history_messages(execution_context.conversation_messages)
        if not history and prompt.strip():
            history = [{"role": "user", "content": prompt.strip()}]
        if use_responses_api:
            return history

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
    def _build_responses_input(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for message in history:
            message_type = str(message.get("type", "")).strip()
            if message_type == "function_call_output":
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": str(message.get("call_id", "")),
                        "output": str(message.get("output", "")),
                    }
                )
                continue
            if message_type == "function_call":
                items.append(
                    {
                        "type": "function_call",
                        "call_id": str(message.get("call_id", "")),
                        "name": str(message.get("name", "")),
                        "arguments": str(message.get("arguments", "")),
                    }
                )
                continue

            role = str(message.get("role", "user")).strip() or "user"
            if role == "system":
                continue
            content_type = "output_text" if role == "assistant" else "input_text"
            items.append(
                {
                    "role": role,
                    "content": [
                        {
                            "type": content_type,
                            "text": str(message.get("content", "")),
                        }
                    ],
                }
            )
        return items

    async def _call_proxy_responses(
        self,
        *,
        manifest: SkillManifest,
        history: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
        body: dict[str, Any] = {
            "model": manifest.model_alias,
            "instructions": self._build_responses_instructions(manifest, history),
            "input": self._build_responses_input(history),
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
        async with (
            httpx.AsyncClient(timeout=self._timeout_s) as client,
            client.stream(
                "POST",
                self._build_responses_url(self._proxy_url),
                json=body,
                headers={
                    "Authorization": f"Bearer {self._master_key}",
                    "Content-Type": "application/json",
                },
            ) as resp,
        ):
            if resp.status_code >= 400:
                body_text = await resp.aread()
                log.error(
                    "litellm_responses_proxy_error",
                    status=resp.status_code,
                    body=body_text.decode(errors="replace")[:500],
                )
            resp.raise_for_status()
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
        metadata = {
            "model_name": str(response_payload.get("model", "") or manifest.model_alias),
            "provider": "openai",
            "token_usage": {
                "prompt_tokens": int(usage.get("input_tokens", 0) or 0),
                "completion_tokens": int(usage.get("output_tokens", 0) or 0),
                "total_tokens": int(usage.get("total_tokens", 0) or 0),
            },
            "cost_usd": 0.0,
            "cost_unavailable": True,
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
        """调用 LiteLLM Proxy（SSE 流式），返回 (content, tool_calls)。

        tool_calls 格式: [{"id": str, "tool_name": str, "arguments": dict}]
        """
        content_parts: list[str] = []
        # 按 index 合并流式 tool_call 片段
        tc_raw: dict[int, dict[str, Any]] = {}

        async with (
            httpx.AsyncClient(timeout=self._timeout_s) as client,
            client.stream(
                "POST",
                f"{self._proxy_url}/v1/chat/completions",
                json={**body, "stream": True},
                headers={
                    "Authorization": f"Bearer {self._master_key}",
                    "Content-Type": "application/json",
                },
            ) as resp,
        ):
            if resp.status_code >= 400:
                body_text = await resp.aread()
                log.error(
                    "litellm_proxy_error",
                    status=resp.status_code,
                    body=body_text.decode(errors="replace")[:500],
                )
            resp.raise_for_status()
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
        return content, tool_calls, {}

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
                use_responses_api=use_responses_api,
            )
            self._histories[key] = history

        history = self._histories[key]

        if step > 1 and feedback:
            # 注意：Responses API 在 Codex 代理链路上复用 function_call_output 时，
            # call_id 可能与上一轮 function_call 脱节，导致 400 invalid_request。
            # 为保证多轮工具调用稳定，统一把工具结果折叠成自然语言回填。
            results = []
            for fb in feedback:
                if fb.is_error:
                    results.append(f"- {fb.tool_name}: ERROR: {fb.error}")
                else:
                    results.append(f"- {fb.tool_name}: {fb.output}")
            history.append(
                {
                    "role": "user",
                    "content": (
                        "Tool execution results:\n"
                        + "\n".join(results)
                        + "\n\nBased on these results, either call the next necessary tool "
                        "immediately or provide the final answer now. "
                        "Do not reply with plans like '我先查一下' or '我再看看'."
                    ),
                }
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

        # 追加 assistant 消息到历史，后续轮次通过自然语言摘要回填工具结果。
        if tool_calls:
            tc_summary = ", ".join(f"{tc['tool_name']}({tc['arguments']})" for tc in tool_calls)
            history.append({"role": "assistant", "content": f"[Calling tools: {tc_summary}]"})
            return SkillOutputEnvelope(
                content=content,
                complete=False,
                tool_calls=[
                    ToolCallSpec(tool_name=tc["tool_name"], arguments=tc["arguments"])
                    for tc in tool_calls
                ],
                metadata=metadata,
            )
        else:
            history.append({"role": "assistant", "content": content})
            return SkillOutputEnvelope(content=content, complete=True, metadata=metadata)
