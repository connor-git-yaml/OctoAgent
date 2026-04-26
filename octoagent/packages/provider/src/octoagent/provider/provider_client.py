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
        # Phase 2 / Phase 3 实现：
        if self._runtime.transport == ProviderTransport.OPENAI_CHAT:
            raise NotImplementedError(
                "OPENAI_CHAT transport 将在 Feature 080 Phase 2 实现",
            )
        if self._runtime.transport == ProviderTransport.ANTHROPIC_MESSAGES:
            raise NotImplementedError(
                "ANTHROPIC_MESSAGES transport 将在 Feature 080 Phase 3 实现",
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


__all__ = [
    "LLMCallError",
    "ProviderClient",
    "_classify_provider_error",
    "_from_fn_name",
    "_to_fn_name",
]
