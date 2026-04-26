"""Feature 080 Phase 1：单 provider 的 LLM 调用 client。

按 ``ProviderRuntime.transport`` 字段路由到对应协议实现，401/403 时统一调用
``AuthResolver.force_refresh()`` 重试一次。

设计要点：
- 一个类承载所有 transport（按 enum dispatch），后续加 transport 不需要新增类
- 401/403 retry 是协议无关的（所有 transport 共享一份），避免代码重复
  - **F3 修复**：trigger 条件为 ``status_code in (401, 403)``，对齐现有
    LiteLLMClient._is_auth_error 行为；某些 provider/网关把过期 token 表述
    成 403 而非 401，光看 401 会丢掉自愈能力
- 不持有 ``model_alias`` 路由逻辑（那是 ``ProviderRouter`` 的职责）

复用 Feature 078/079 的成熟代码：
- ``_history_to_responses_input`` / ``_build_responses_instructions`` 来自 skills/providers.py
- 流式事件解析逻辑（response.output_text.delta 等）一字未改
- 错误分类 ``_classify_provider_error`` 与 ``_classify_proxy_error`` 同源
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import structlog

from .auth_resolver import ResolvedAuth
from .provider_runtime import ProviderRuntime
from .transport import ProviderTransport

log = structlog.get_logger()


class LLMCallError(Exception):
    """LLM 调用异常。供上层差异化处理（retry / fallback / abort）。

    error_type 与 ``skills.providers.LLMCallError`` 对齐——这两个类目前是
    协议层的镜像，Phase 4 LiteLLM Proxy 退役后会被合并。

    error_type 取值：
        timeout            — 超时，可重试
        rate_limit         — 429 限流
        context_overflow   — 上下文超长，不可重试需压缩
        empty_input        — Responses API input 被过滤为空，不可重试
        api_error          — 其他 API 错误（含 401，由 status_code 区分）
    """

    def __init__(
        self,
        error_type: str,
        message: str,
        *,
        retriable: bool = True,
        status_code: int = 0,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.retriable = retriable
        self.status_code = status_code


def _classify_provider_error(exc: Exception, status_code: int = 0) -> LLMCallError:
    """把 httpx / provider HTTP 错误转换为 LLMCallError。

    与 ``skills.providers._classify_proxy_error`` 行为一致；改名是为了反映
    Feature 080 后没有 Proxy 这一层。
    """
    msg = str(exc)
    if isinstance(
        exc,
        (
            httpx.TimeoutException,
            httpx.ReadTimeout,
            httpx.WriteTimeout,
            httpx.ConnectTimeout,
        ),
    ):
        return LLMCallError("timeout", msg, retriable=True)
    if status_code == 429:
        return LLMCallError("rate_limit", msg, retriable=True, status_code=429)
    overflow_keywords = (
        "context_length",
        "maximum context",
        "token limit",
        "too many tokens",
        "context window",
    )
    msg_lower = msg.lower()
    if status_code in (400, 413) or any(kw in msg_lower for kw in overflow_keywords):
        if any(kw in msg_lower for kw in overflow_keywords):
            return LLMCallError(
                "context_overflow",
                msg,
                retriable=False,
                status_code=status_code,
            )
    return LLMCallError("api_error", msg, retriable=True, status_code=status_code)


def _to_fn_name(tool_name: str) -> str:
    """工具名 → OpenAI function name（点替换为双下划线）。与 skills.providers 同义。"""
    return tool_name.replace(".", "__")


def _from_fn_name(fn_name: str) -> str:
    return fn_name.replace("__", ".")


_SYSTEM_MESSAGE_CHAR_BUDGET = 24_000


def _merge_system_messages_to_front(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """合并多条 system message 到一条放在最前面。

    部分模型（Qwen / Gemma 等）只接受恰好一个 system message 且必须在最前；
    多个连续 system 也会被拒。同 ``skills.providers._merge_system_messages_to_front``
    行为一致。Phase 4 LiteLLM Proxy 退役后会合并。
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
    if len(system_parts) == 1 and messages[0].get("role") == "system":
        content = messages[0].get("content", "")
        if len(content) <= _SYSTEM_MESSAGE_CHAR_BUDGET:
            return messages
        truncated = content[:_SYSTEM_MESSAGE_CHAR_BUDGET].rstrip()
        return [
            {"role": "system", "content": truncated + "\n\n[system prompt truncated]"},
            *non_system,
        ]
    merged = "\n\n".join(system_parts)
    if len(merged) > _SYSTEM_MESSAGE_CHAR_BUDGET:
        original_len = len(merged)
        merged = merged[:_SYSTEM_MESSAGE_CHAR_BUDGET].rstrip()
        merged += f"\n\n[system prompt truncated: {original_len} → {len(merged)} chars]"
    return [{"role": "system", "content": merged}, *non_system]


def _build_responses_url(api_base: str) -> str:
    """根据 api_base 推断 Responses API 端点。

    - ``chatgpt.com/backend-api`` 或 ``backend-api/codex`` 结尾 → 加 ``/responses``
    - 其他（含 OpenAI 标准 ``api.openai.com``）→ 加 ``/v1/responses``
    """
    base = api_base.rstrip("/")
    if base.endswith("/backend-api") or base.endswith("/backend-api/codex"):
        return f"{base}/responses"
    return f"{base}/v1/responses"


def _history_to_responses_input(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Chat Completions 格式 history → Responses API input。

    转换规则（与 skills.providers._history_to_responses_input 一致）：
    - system → 跳过（已由 instructions 处理）
    - user → ``{role: "user", content: [{type: "input_text", text}]}``
    - assistant 无 tool_calls → ``{role: "assistant", content: [{type: "output_text", text}]}``
    - assistant 有 tool_calls → 多个 ``{type: "function_call", call_id, name, arguments}``
    - tool → ``{type: "function_call_output", call_id, output}``，配对 known_call_ids 防孤儿
    """
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
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": str(message.get("content", "")),
                    }
                )
            continue
        if role == "assistant":
            tc_list = message.get("tool_calls")
            if tc_list and isinstance(tc_list, list):
                for tc in tc_list:
                    fn = tc.get("function", {})
                    call_id = str(tc.get("id", "")).strip()
                    if call_id:
                        items.append(
                            {
                                "type": "function_call",
                                "call_id": call_id,
                                "name": str(fn.get("name", "")),
                                "arguments": str(fn.get("arguments", "")),
                            }
                        )
            else:
                items.append(
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": str(message.get("content", "")),
                            }
                        ],
                    }
                )
            continue
        if role == "user":
            items.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": str(message.get("content", ""))}
                    ],
                }
            )
    return items


def _build_responses_instructions(
    description: str,
    history: list[dict[str, Any]],
) -> str:
    """构造 Responses API 的 instructions 字段（system message 等价物）。

    description 来自 skill manifest（之前是 ``manifest.load_description()``），
    Phase 1 让调用方传字符串以保持 ProviderClient 与 SkillManifest 解耦。
    """
    parts: list[str] = []
    if description:
        parts.append(description)
    system_parts = [
        str(message.get("content", "")).strip()
        for message in history
        if str(message.get("role", "user")).strip().lower() == "system"
        and str(message.get("content", "")).strip()
    ]
    if system_parts:
        parts.append("\n\n".join(system_parts))
    return "\n\n".join(part for part in parts if part)


class ProviderClient:
    """单 provider 的 LLM 调用 client。所有 transport 通过这一个类处理。

    构造由 ``ProviderRouter._build_client()`` 完成；调用方拿到这个对象就只
    管 ``call()``。401 retry / 错误分类 / 流式解析全部内化。
    """

    def __init__(
        self,
        runtime: ProviderRuntime,
        http_client: httpx.AsyncClient,
    ) -> None:
        self._runtime = runtime
        self._http = http_client

    @property
    def runtime(self) -> ProviderRuntime:
        return self._runtime

    async def call(
        self,
        *,
        instructions: str,
        history: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model_name: str,
        reasoning: dict[str, Any] | None = None,
    ) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
        """按 transport 路由到对应协议实现。

        Args:
            instructions: 用作 Responses ``instructions`` / Anthropic ``system`` /
                OpenAI Chat 的 system message 内容
            history: Chat Completions 格式的 message list（system / user /
                assistant / tool 四种 role）
            tools: 工具 schema 列表（OpenAI Chat 嵌套格式：
                ``[{type: "function", function: {name, description, parameters}}, ...]``）
            model_name: 实际发给 provider 的 model 字符串（如 ``gpt-5.5`` /
                ``Qwen/Qwen3.5-32B``）
            reasoning: 可选的 reasoning 配置（如 ``{type: "enabled",
                budget_tokens: 8000}``，仅 Responses API + Anthropic 用到）

        Returns:
            ``(content, tool_calls, metadata)`` triple
        """
        try:
            return await self._dispatch(
                auth=await self._runtime.auth_resolver.resolve(),
                instructions=instructions,
                history=history,
                tools=tools,
                model_name=model_name,
                reasoning=reasoning,
            )
        except LLMCallError as exc:
            # F3 修复：401 和 403 都触发 auth refresh。某些 provider/网关把
            # 过期 token / scope 不足 表述成 403 而非 401（如 GitHub Copilot、
            # 某些 OpenAI Beta 端点），光看 401 会丢掉自愈能力。对齐现有
            # client.py::LiteLLMClient._is_auth_error 的 (401, 403) 集合。
            if exc.status_code not in (401, 403):
                raise
            log.info(
                "provider_client_auth_error_force_refresh",
                provider_id=self._runtime.provider_id,
                transport=self._runtime.transport.value,
                status_code=exc.status_code,
                model=model_name,
            )
            fresh = await self._runtime.auth_resolver.force_refresh()
            if fresh is None:
                log.warning(
                    "provider_client_force_refresh_returned_none",
                    provider_id=self._runtime.provider_id,
                    status_code=exc.status_code,
                )
                raise
            log.info(
                "provider_client_auth_error_retry_after_refresh",
                provider_id=self._runtime.provider_id,
                status_code=exc.status_code,
                model=model_name,
            )
            return await self._dispatch(
                auth=fresh,
                instructions=instructions,
                history=history,
                tools=tools,
                model_name=model_name,
                reasoning=reasoning,
            )

    async def _dispatch(
        self,
        *,
        auth: ResolvedAuth,
        instructions: str,
        history: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model_name: str,
        reasoning: dict[str, Any] | None,
    ) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
        if self._runtime.transport == ProviderTransport.OPENAI_RESPONSES:
            return await self._call_openai_responses(
                auth=auth,
                instructions=instructions,
                history=history,
                tools=tools,
                model_name=model_name,
                reasoning=reasoning,
            )
        if self._runtime.transport == ProviderTransport.OPENAI_CHAT:
            return await self._call_openai_chat(
                auth=auth,
                instructions=instructions,
                history=history,
                tools=tools,
                model_name=model_name,
            )
        if self._runtime.transport == ProviderTransport.ANTHROPIC_MESSAGES:
            return await self._call_anthropic_messages(
                auth=auth,
                instructions=instructions,
                history=history,
                tools=tools,
                model_name=model_name,
                reasoning=reasoning,
            )
        raise NotImplementedError(f"unsupported transport: {self._runtime.transport}")

    # ──────────────── OPENAI_RESPONSES ────────────────

    async def _call_openai_responses(
        self,
        *,
        auth: ResolvedAuth,
        instructions: str,
        history: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model_name: str,
        reasoning: dict[str, Any] | None,
    ) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
        """OpenAI Responses API 协议实现（直连，无中间代理）。

        这是 Feature 080 的核心路径：用户日常的 ChatGPT Pro Codex 调用走这里，
        Phase 4 退役 LiteLLM Proxy 后所有 OpenAI Responses 请求都走它。
        """
        responses_input = _history_to_responses_input(history)
        if not responses_input:
            log.error(
                "provider_client_responses_input_empty",
                history_count=len(history),
                tools_count=len(tools or []),
                provider_id=self._runtime.provider_id,
            )
            raise LLMCallError(
                "empty_input",
                "Responses API input 为空：history 全部被过滤，无可用上下文。",
                retriable=False,
            )

        body: dict[str, Any] = {
            "model": model_name,
            "instructions": _build_responses_instructions(instructions, history),
            "input": responses_input,
        }
        # extra_body 默认包含 store=False / stream=True；调用方不应需要再传一遍
        body.update(self._runtime.extra_body)
        # 兜底：缺省值（保持 Feature 078 行为）
        body.setdefault("store", False)
        body.setdefault("stream", True)

        if tools:
            # Responses API 用 flat 格式：{type, name, description, parameters}
            body["tools"] = [
                {
                    "type": "function",
                    "name": _to_fn_name(t.get("function", t).get("name", "")),
                    "description": t.get("function", t).get("description", ""),
                    "parameters": t.get("function", t).get("parameters", {}),
                }
                if isinstance(t.get("function"), dict)
                else t  # 已是 flat 格式，直接用
                for t in tools
            ]
            body["tool_choice"] = "auto"

        if reasoning is not None:
            body["reasoning"] = reasoning

        target_url = _build_responses_url(self._runtime.api_base)
        # 头部合并：static extra_headers（含 OpenAI-Beta 等）+ resolver 动态 headers
        # （含刷新后的 chatgpt-account-id），后者覆盖前者
        target_headers: dict[str, str] = {
            "Authorization": f"Bearer {auth.bearer_token}",
            "Content-Type": "application/json",
            **self._runtime.extra_headers,
            **auth.extra_headers,
        }

        text_parts: list[str] = []
        tool_calls_raw: dict[str, dict[str, Any]] = {}
        response_payload: dict[str, Any] = {}

        try:
            stream_ctx = self._http.stream(
                "POST",
                target_url,
                json=body,
                headers=target_headers,
            )
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            raise _classify_provider_error(exc) from exc

        async with stream_ctx as resp:
            if resp.status_code >= 400:
                body_text = await resp.aread()
                error_body = body_text.decode(errors="replace")[:500]
                log.error(
                    "provider_client_responses_error",
                    status=resp.status_code,
                    body=error_body,
                    provider_id=self._runtime.provider_id,
                    model=model_name,
                )
                raise _classify_provider_error(
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
                            log.warning(
                                "provider_client_responses_function_call_missing_call_id",
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
                        tool_calls_raw[item_id]["arguments"] += str(
                            event.get("delta", "")
                        )
                    continue
                if event_type == "response.function_call_arguments.done":
                    item_id = str(event.get("item_id", ""))
                    if item_id in tool_calls_raw:
                        tool_calls_raw[item_id]["arguments"] = str(
                            event.get("arguments")
                            or tool_calls_raw[item_id]["arguments"]
                        )
                    continue
                if event_type == "response.output_item.done":
                    item = event.get("item", {})
                    if isinstance(item, dict) and item.get("type") == "function_call":
                        item_id = str(item.get("id", ""))
                        call_id = str(item.get("call_id", "")).strip()
                        if item_id in tool_calls_raw:
                            existing = tool_calls_raw[item_id]
                            if call_id and not existing.get("id"):
                                existing["id"] = call_id
                        else:
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
        for tc in tool_calls_raw.values():
            try:
                arguments = json.loads(tc["arguments"]) if tc["arguments"] else {}
            except json.JSONDecodeError:
                arguments = {}
            tool_calls.append(
                {
                    "id": tc["id"],
                    "tool_name": tc["tool_name"],
                    "arguments": arguments,
                }
            )

        # 兜底：流式漏掉了 text 的话，从 response.completed 的 output 里捞
        if not text_parts:
            for item in response_payload.get("output", []):
                if not isinstance(item, dict):
                    continue
                if item.get("type") != "message":
                    continue
                for content_item in item.get("content", []):
                    if (
                        isinstance(content_item, dict)
                        and content_item.get("type") == "output_text"
                        and content_item.get("text")
                    ):
                        text_parts.append(str(content_item.get("text", "")))

        usage = response_payload.get("usage", {})
        metadata: dict[str, Any] = {
            "model_name": str(response_payload.get("model", "") or model_name),
            "provider": self._runtime.provider_id,
            "transport": self._runtime.transport.value,
            "token_usage": {
                "prompt_tokens": int(usage.get("input_tokens", 0) or 0),
                "completion_tokens": int(usage.get("output_tokens", 0) or 0),
                "total_tokens": int(usage.get("total_tokens", 0) or 0),
            },
            # cost 留给后续 Feature 接 cost calculator；本 Feature 标 unavailable
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


    # ──────────────── OPENAI_CHAT ────────────────

    async def _call_openai_chat(
        self,
        *,
        auth: ResolvedAuth,
        instructions: str,
        history: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model_name: str,
    ) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
        """OpenAI Chat Completions API 协议（直连 provider，无中间代理）。

        覆盖 OpenAI / SiliconFlow / DeepSeek / Groq / OpenRouter / Together /
        Mistral / vLLM 等所有 OpenAI Chat 兼容 provider。

        与 ``skills.providers.ChatCompletionsProvider._call_once`` 95% 同源；
        差异只是用 ``self._runtime.api_base`` 替代了硬编码的 proxy_url。
        """
        # 把 instructions（manifest description）prepend 到 history 作为 system；
        # 与现有 LiteLLMSkillClient._build_initial_history 行为对齐
        merged_history: list[dict[str, Any]] = list(history)
        has_system = any(m.get("role") == "system" for m in merged_history)
        if instructions and not has_system:
            merged_history = [{"role": "system", "content": instructions}, *merged_history]

        # 安全网：合并散落的 system 消息（兼容 Qwen / Gemma 单 system 限制）
        merged_history = _merge_system_messages_to_front(merged_history)

        body: dict[str, Any] = {
            "model": model_name,
            "messages": merged_history,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        body.update(self._runtime.extra_body)
        body.setdefault("stream", True)

        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        target_url = f"{self._runtime.api_base}/v1/chat/completions"
        target_headers: dict[str, str] = {
            "Authorization": f"Bearer {auth.bearer_token}",
            "Content-Type": "application/json",
            **self._runtime.extra_headers,
            **auth.extra_headers,
        }

        content_parts: list[str] = []
        tc_raw: dict[int, dict[str, Any]] = {}
        usage_data: dict[str, int] = {}

        try:
            stream_ctx = self._http.stream(
                "POST", target_url, json=body, headers=target_headers,
            )
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            raise _classify_provider_error(exc) from exc

        async with stream_ctx as resp:
            if resp.status_code >= 400:
                body_text = await resp.aread()
                error_body = body_text.decode(errors="replace")[:500]
                log.error(
                    "provider_client_chat_error",
                    status=resp.status_code,
                    body=error_body,
                    provider_id=self._runtime.provider_id,
                    model=model_name,
                )
                raise _classify_provider_error(
                    httpx.HTTPStatusError(
                        f"Chat Completions returned {resp.status_code}: {error_body}",
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

        metadata: dict[str, Any] = {
            "model_name": str(body.get("model", "") or model_name),
            "provider": self._runtime.provider_id,
            "transport": self._runtime.transport.value,
            "token_usage": usage_data
            or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "cost_usd": 0.0,
            "cost_unavailable": True,
        }
        return content, tool_calls, metadata

    # ──────────────── ANTHROPIC_MESSAGES ────────────────

    async def _call_anthropic_messages(
        self,
        *,
        auth: ResolvedAuth,
        instructions: str,
        history: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model_name: str,
        reasoning: dict[str, Any] | None,
    ) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
        """Anthropic Messages API 协议（直连 api.anthropic.com）。

        协议关键差异（与 OpenAI Chat 对比）：
        - ``messages`` 中**不能**包含 ``role: "system"``，system 走顶层 ``system`` 字段
        - ``tools`` 用 ``[{name, description, input_schema}]`` 而非 ``[{type: function, ...}]``
        - 流式事件类型：``message_start`` / ``content_block_start`` /
          ``content_block_delta`` / ``content_block_stop`` / ``message_delta`` / ``message_stop``
        - tool_use 通过 ``content_block_delta`` 的 ``input_json_delta.partial_json`` 累积
        - usage 在 ``message_delta`` 的 ``usage`` 字段（output_tokens 增量）
          + ``message_start`` 的 ``usage`` 字段（input_tokens + 初始 output_tokens）
        - thinking 通过顶层 ``thinking: {type: "enabled", budget_tokens: N}``
        - Claude OAuth 需要 ``anthropic-beta: oauth-2025-04-20`` 头

        message 转换：
        - OpenAI Chat 的 ``role: "tool"`` → Anthropic 的 ``role: "user"`` +
          ``content: [{type: "tool_result", tool_use_id, content}]``
        - OpenAI ``tool_calls`` 列表 → Anthropic ``content: [{type: "tool_use",
          id, name, input}]``
        """
        # 拆系统消息和对话消息
        system_parts: list[str] = []
        anthropic_messages: list[dict[str, Any]] = []
        for msg in history:
            role = msg.get("role")
            if role == "system":
                content = str(msg.get("content", "")).strip()
                if content:
                    system_parts.append(content)
                continue
            if role == "tool":
                # OpenAI tool result → Anthropic tool_result block in user message
                tool_use_id = str(msg.get("tool_call_id", "")).strip()
                if not tool_use_id:
                    log.warning(
                        "provider_client_anthropic_tool_result_missing_id",
                        provider_id=self._runtime.provider_id,
                    )
                    continue
                anthropic_messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_use_id,
                                "content": str(msg.get("content", "")),
                            }
                        ],
                    }
                )
                continue
            if role == "assistant":
                tc_list = msg.get("tool_calls") or []
                if tc_list:
                    blocks: list[dict[str, Any]] = []
                    text = str(msg.get("content", "") or "").strip()
                    if text:
                        blocks.append({"type": "text", "text": text})
                    for tc in tc_list:
                        fn = tc.get("function", {})
                        try:
                            args = json.loads(fn.get("arguments", "{}") or "{}")
                        except json.JSONDecodeError:
                            args = {}
                        blocks.append(
                            {
                                "type": "tool_use",
                                "id": str(tc.get("id", "")),
                                "name": _from_fn_name(str(fn.get("name", ""))),
                                "input": args,
                            }
                        )
                    anthropic_messages.append({"role": "assistant", "content": blocks})
                else:
                    anthropic_messages.append(
                        {"role": "assistant", "content": str(msg.get("content", ""))}
                    )
                continue
            if role == "user":
                anthropic_messages.append(
                    {"role": "user", "content": str(msg.get("content", ""))}
                )

        # instructions 也作为 system 的一部分（manifest description）
        full_system = instructions
        if system_parts:
            extra = "\n\n".join(system_parts)
            full_system = f"{instructions}\n\n{extra}" if instructions else extra

        body: dict[str, Any] = {
            "model": model_name,
            "messages": anthropic_messages,
            "max_tokens": 4096,  # Anthropic Messages API 必填字段
            "stream": True,
        }
        body.update(self._runtime.extra_body)
        body.setdefault("stream", True)
        # extra_body 可以覆盖 max_tokens 默认值
        body.setdefault("max_tokens", 4096)
        if full_system:
            body["system"] = full_system

        if tools:
            # OpenAI Chat 格式 → Anthropic 格式：{name, description, input_schema}
            body["tools"] = [
                {
                    "name": _from_fn_name(
                        t.get("function", t).get("name", "")
                        if isinstance(t.get("function"), dict)
                        else t.get("name", "")
                    ),
                    "description": (
                        t.get("function", t).get("description", "")
                        if isinstance(t.get("function"), dict)
                        else t.get("description", "")
                    ),
                    "input_schema": (
                        t.get("function", t).get("parameters", {})
                        if isinstance(t.get("function"), dict)
                        else t.get("input_schema", t.get("parameters", {}))
                    ),
                }
                for t in tools
            ]

        if reasoning is not None:
            body["thinking"] = reasoning

        target_url = f"{self._runtime.api_base}/v1/messages"
        target_headers: dict[str, str] = {
            "Authorization": f"Bearer {auth.bearer_token}",
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",  # 默认 API version
            **self._runtime.extra_headers,
            **auth.extra_headers,
        }

        text_parts: list[str] = []
        # tool_use 累积：index → {id, name, input_json}
        tool_use_raw: dict[int, dict[str, Any]] = {}
        # 记录每个 content_block 的 type，方便 delta 时分流
        block_types: dict[int, str] = {}
        usage_data: dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        message_meta: dict[str, Any] = {}

        try:
            stream_ctx = self._http.stream(
                "POST", target_url, json=body, headers=target_headers,
            )
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            raise _classify_provider_error(exc) from exc

        async with stream_ctx as resp:
            if resp.status_code >= 400:
                body_text = await resp.aread()
                error_body = body_text.decode(errors="replace")[:500]
                log.error(
                    "provider_client_anthropic_error",
                    status=resp.status_code,
                    body=error_body,
                    provider_id=self._runtime.provider_id,
                    model=model_name,
                )
                raise _classify_provider_error(
                    httpx.HTTPStatusError(
                        f"Anthropic Messages returned {resp.status_code}: {error_body}",
                        request=resp.request,
                        response=resp,
                    ),
                    status_code=resp.status_code,
                )
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:].strip()
                if not payload:
                    continue
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    continue

                event_type = str(event.get("type", ""))
                if event_type == "message_start":
                    msg = event.get("message", {})
                    if isinstance(msg, dict):
                        message_meta = {
                            "id": msg.get("id"),
                            "model": msg.get("model"),
                        }
                        usage = msg.get("usage", {}) or {}
                        usage_data["prompt_tokens"] = int(
                            usage.get("input_tokens", 0) or 0
                        )
                        usage_data["completion_tokens"] = int(
                            usage.get("output_tokens", 0) or 0
                        )
                    continue
                if event_type == "content_block_start":
                    idx = int(event.get("index", -1))
                    block = event.get("content_block", {}) or {}
                    btype = str(block.get("type", ""))
                    block_types[idx] = btype
                    if btype == "tool_use":
                        tool_use_raw[idx] = {
                            "id": str(block.get("id", "")),
                            "name": _from_fn_name(str(block.get("name", ""))),
                            "input_json": "",
                        }
                    continue
                if event_type == "content_block_delta":
                    idx = int(event.get("index", -1))
                    delta = event.get("delta", {}) or {}
                    btype = str(delta.get("type", ""))
                    if btype == "text_delta":
                        text_parts.append(str(delta.get("text", "")))
                    elif btype == "input_json_delta":
                        if idx in tool_use_raw:
                            tool_use_raw[idx]["input_json"] += str(
                                delta.get("partial_json", "")
                            )
                    continue
                if event_type == "content_block_stop":
                    # 不做特殊处理，content_block_delta 已累积完
                    continue
                if event_type == "message_delta":
                    delta_usage = event.get("usage", {}) or {}
                    if "output_tokens" in delta_usage:
                        usage_data["completion_tokens"] = int(
                            delta_usage.get("output_tokens", 0) or 0
                        )
                    continue
                if event_type == "message_stop":
                    continue

        usage_data["total_tokens"] = (
            usage_data["prompt_tokens"] + usage_data["completion_tokens"]
        )

        tool_calls: list[dict[str, Any]] = []
        for idx in sorted(tool_use_raw):
            entry = tool_use_raw[idx]
            try:
                args = json.loads(entry["input_json"]) if entry["input_json"] else {}
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(
                {
                    "id": entry["id"],
                    "tool_name": entry["name"],
                    "arguments": args,
                }
            )

        metadata: dict[str, Any] = {
            "model_name": str(message_meta.get("model", "") or model_name),
            "provider": self._runtime.provider_id,
            "transport": self._runtime.transport.value,
            "token_usage": usage_data,
            "cost_usd": 0.0,
            "cost_unavailable": True,
        }
        return "".join(text_parts), tool_calls, metadata


__all__ = [
    "LLMCallError",
    "ProviderClient",
    "_classify_provider_error",
    "_from_fn_name",
    "_to_fn_name",
]
