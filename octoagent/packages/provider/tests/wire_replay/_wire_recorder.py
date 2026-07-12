"""F139 wire 录制回放基建（test-only，零第三方新依赖）。

照 pydantic-ai VCR 范式移植**设计**（secret 过滤 serializer / 松 matcher /
fail_partially_used 消费护栏 / 重录文档化），不移植**依赖**（spec D1：零新依赖
让回放测试在主仓 venv / pre-commit hook / CI 处处默认可跑；无全局 monkeypatch
与 xdist 并行零互扰）。挂点 = ``ProviderClient.__init__(runtime, http_client)``
的 httpx ``AsyncBaseTransport`` 注入缝。

Constitution #5 硬前置——cassette 唯一落盘口内建六道过滤管线（spec D3）：

1. drop token 端点交互（防御深度；OAuth refresh 本就不经注入 client）；
2. 请求头 **allowlist**（denylist 会漏新 auth 头；``authorization`` /
   ``x-api-key`` / ``cookie`` / ``chatgpt-account-id`` 一律不落盘）；
3. 响应头 allowlist（仅 ``content-type``，回放唯一需要）；
4. 响应 body 文本过 ``octoagent.core.log_redaction``（规则源复用）；
5. 落盘前机械断言（fail-closed）：模式扫描 + **已知凭证禁串逐字比对**
   （录制进程内拿得到真凭证明文，比模式匹配更硬），命中即 raise 拒绝落盘；
6. 事务式原子落盘：serialize → 扫描通过 → 同目录 temp → ``os.replace``，
   任何一道失败都不产生目标文件（无半成品）。

request 侧**不落完整 body**（spec review H1）：只存结构摘要 ``body_summary``
（结构化构造，不走文本正则）；URL 拆存 scheme/host/path、query 非空即 raise
（永久不持久化 query）；非 2xx 响应拒绝录制（错误回显不落盘）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from octoagent.core.log_redaction import redact_sensitive_text
from octoagent.provider.auth_resolver import ResolvedAuth

CASSETTE_FORMAT_VERSION = 1

#: 请求头 allowlist（lower）——不在表内的头整个丢弃（auth 类头结构性不可达落盘）。
REQUEST_HEADER_ALLOWLIST = frozenset(
    {
        "accept",
        "accept-encoding",
        "connection",
        "content-length",
        "content-type",
        "host",
        "openai-beta",
        "anthropic-version",
        "originator",
    }
)

#: 响应头 allowlist（lower）——回放只需要 content-type。
RESPONSE_HEADER_ALLOWLIST = frozenset({"content-type"})

#: 录制侧存解码后 body，这三个头必须剥除（否则回放时 httpx 二次解压失败）。
_CONTENT_TRANSFER_HEADERS = frozenset({"content-encoding", "content-length", "transfer-encoding"})

#: codex Responses 后端在 response.created/in_progress/completed 事件里回显的
#: 身份/请求内容字段——录制时定点洗刷（string 值 → "[scrubbed]"）：
#: - safety_identifier / prompt_cache_key：账户关联标识（真录实锤 user-xxx / UUID）；
#: - instructions：请求内容经响应回流（spec review H1 同面——重录换输入时宿主
#:   内容会经此回显落盘，结构上必须堵死）；
#: - user：现值 null（null 不动），若未来变 string 一并洗。
#: 用 JSON string 字面量精确正则（``(?:[^"\\]|\\.)*`` 是 JSON string 完整词法），
#: 替换后行内 JSON 仍合法、其余字节保真。刻意不整行 parse/re-serialize——那会
#: 重排 key/空白，为洗 4 个字段破坏整个 body 的字节保真（结构化 redaction 用在
#: body_summary；SSE body 的正确工具是定点字面量手术）。
IDENTITY_SCRUB_FIELDS: tuple[str, ...] = (
    "instructions",
    "safety_identifier",
    "prompt_cache_key",
    "user",
)
_SCRUBBED_MARK = "[scrubbed]"
_IDENTITY_SCRUB_PATTERNS: dict[str, re.Pattern[str]] = {
    field: re.compile(rf'"{field}":\s*"(?:[^"\\]|\\.)*"') for field in IDENTITY_SCRUB_FIELDS
}
#: 扫描侧强制不变量：**无歧义**身份键若以 string 值出现，必须已是 "[scrubbed]"
#: （容忍序列化后的 \" 转义形态——scan 跑在最终 serialized JSON 上）。
#: 刻意只含 safety_identifier / prompt_cache_key（Codex final P2-1）：
#: ``user`` / ``instructions`` 是通用词，模型输出里合法出现（JSON 示例/代码），
#: 放进违规检查会把正常输出误判成泄漏拒绝落盘；这两个通用键的回显面由录制侧
#: scrub_identity_fields 处理（handwritten golden 另有人眼 review 兜底）。
IDENTITY_VIOLATION_FIELDS: tuple[str, ...] = ("safety_identifier", "prompt_cache_key")
_IDENTITY_VIOLATION_PATTERNS: dict[str, re.Pattern[str]] = {
    field: re.compile(rf'\\?"{field}\\?":\s*\\?"(?!\[scrubbed\])')
    for field in IDENTITY_VIOLATION_FIELDS
}


def scrub_identity_fields(text: str) -> str:
    """响应 body 身份/回显字段定点洗刷（录制管线第 4b 道）。"""
    for field_name, pattern in _IDENTITY_SCRUB_PATTERNS.items():
        text = pattern.sub(f'"{field_name}":"{_SCRUBBED_MARK}"', text)
    return text


#: 落盘前全文扫描的 secret 形状（与 AC-2 的人工 grep 模式一致）。
SECRET_SCAN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"tskey-"),
    re.compile(r"eyJ[A-Za-z0-9_\-]{4,}\.[A-Za-z0-9_\-]{4,}\.[A-Za-z0-9_\-]{4,}"),
)


class CassetteRecordError(RuntimeError):
    """录制被拒绝（非 2xx / query string / 半成品防护等）。

    刻意继承 RuntimeError：录制脚本里它必须炸出来交人裁决，不得被
    provider 异常处理链（``except LLMCallError`` 等）吞掉。
    """


class CassetteSecretError(CassetteRecordError):
    """落盘前扫描发现 secret 形状/禁串——拒绝产出 cassette（fail-closed）。"""


class ReplayMismatchError(AssertionError):
    """回放请求与 cassette 交互不匹配（method/host/path 或交互耗尽）。"""


def _is_token_endpoint(host: str, path: str) -> bool:
    lowered = path.lower()
    return "/token" in lowered or "/oauth" in lowered or host.lower().startswith("auth.")


def _build_body_summary(content: bytes) -> dict[str, Any]:
    """从请求 body 结构化构造摘要（不落完整 body，不走文本正则——spec D2）。"""
    summary: dict[str, Any] = {
        "body_sha256": hashlib.sha256(content).hexdigest(),
    }
    try:
        body = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        summary["shape"] = "non-json"
        return summary
    if not isinstance(body, dict):
        summary["shape"] = "non-object"
        return summary
    if "model" in body:
        summary["model"] = str(body["model"])
    if "stream" in body:
        summary["stream"] = bool(body["stream"])
    roles: list[str] = []
    for key in ("messages", "input"):
        items = body.get(key)
        if isinstance(items, list):
            summary[f"{key}_count"] = len(items)
            roles.extend(
                str(item.get("role", item.get("type", "?")))
                for item in items
                if isinstance(item, dict)
            )
    if roles:
        summary["message_roles"] = roles
    if isinstance(body.get("instructions"), str):
        summary["has_instructions"] = True
    if isinstance(body.get("system"), str):
        summary["has_system"] = True
    tools = body.get("tools")
    if isinstance(tools, list):
        names: list[str] = []
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            fn = tool.get("function") if isinstance(tool.get("function"), dict) else None
            names.append(str((fn or tool).get("name", "?")))
        summary["tool_names"] = names
    return summary


@dataclass
class RecordedInteraction:
    """单条录制交互。request 侧仅结构摘要；response 侧解码后完整文本。"""

    method: str
    scheme: str
    host: str
    path: str
    request_headers: dict[str, str]
    body_summary: dict[str, Any]
    status_code: int
    response_headers: dict[str, str]
    body_text: str

    def to_json_obj(self) -> dict[str, Any]:
        return {
            "request": {
                "method": self.method,
                "scheme": self.scheme,
                "host": self.host,
                "path": self.path,
                "headers": dict(self.request_headers),
                "body_summary": dict(self.body_summary),
            },
            "response": {
                "status_code": self.status_code,
                "headers": dict(self.response_headers),
                "body_text": self.body_text,
            },
        }

    @classmethod
    def from_json_obj(cls, obj: dict[str, Any]) -> RecordedInteraction:
        request = obj["request"]
        response = obj["response"]
        return cls(
            method=str(request["method"]),
            scheme=str(request["scheme"]),
            host=str(request["host"]),
            path=str(request["path"]),
            request_headers=dict(request.get("headers", {})),
            body_summary=dict(request.get("body_summary", {})),
            status_code=int(response["status_code"]),
            response_headers=dict(response.get("headers", {})),
            body_text=str(response["body_text"]),
        )


@dataclass
class Cassette:
    """cassette 的运行时形态（load 后含 per-interaction 播放计数）。"""

    meta: dict[str, Any]
    interactions: list[RecordedInteraction] = field(default_factory=list)
    play_counts: list[int] = field(default_factory=list)
    source_path: Path | None = None

    def __post_init__(self) -> None:
        if not self.play_counts:
            self.play_counts = [0] * len(self.interactions)

    @classmethod
    def load(cls, path: Path) -> Cassette:
        payload = json.loads(path.read_text(encoding="utf-8"))
        version = payload.get("format_version")
        if version != CASSETTE_FORMAT_VERSION:
            raise CassetteRecordError(
                f"cassette {path.name} format_version={version!r} "
                f"≠ 当前支持的 {CASSETTE_FORMAT_VERSION}",
            )
        interactions = [
            RecordedInteraction.from_json_obj(obj) for obj in payload.get("interactions", [])
        ]
        return cls(
            meta=dict(payload.get("meta", {})),
            interactions=interactions,
            play_counts=[0] * len(interactions),
            source_path=path,
        )

    def unplayed_indexes(self) -> list[int]:
        """完整消费护栏判定核心（纯函数语义，供 conftest autouse 护栏调用）。

        消费按**交互**计不按字节计（spec D1 自证矩阵：解析器早停后 buffered
        body 剩余未读不算未消费——交互一经取出即 played）。
        """
        return [idx for idx, count in enumerate(self.play_counts) if count == 0]

    def describe(self) -> str:
        source = self.source_path.name if self.source_path else "<memory>"
        return f"{source}({self.meta.get('scenario', '?')})"


class CassetteRecorder:
    """录制聚合器：接收 RecordingTransport 的交互，负责过滤与事务式落盘。"""

    def __init__(self, meta: dict[str, Any]) -> None:
        self.meta = dict(meta)
        self.interactions: list[RecordedInteraction] = []
        self._forbidden: dict[str, str] = {}  # value -> label（label 仅诊断用）

    # ---------------------------------------------------------------- 禁串
    def register_forbidden_secret(self, *values: str | None, label: str = "credential") -> None:
        """登记已知凭证明文为禁串（ResolvedAuth.bearer_token / 身份类 header 值 /
        相关 env 值）。落盘前全文逐字比对，命中即拒绝（spec D3 第 5 道 b）。
        label 仅用于命中时的诊断输出（值本身永不出现在任何输出里）。

        短值（<8 字符）不登记：子串比对对短值有大面积误伤（如 ``pi``），
        且真实凭证不会这么短；持久化侧的头 allowlist 仍兜底。"""
        for value in values:
            if value and len(value.strip()) >= 8:
                self._forbidden[value.strip()] = label

    def register_resolved_auth(self, auth: ResolvedAuth) -> None:
        """登记现役凭证。extra_headers 只登记**不在**请求头持久化 allowlist 里的
        值——allowlist 内的是良性协议头（OpenAI-Beta / originator 等），其值本就
        允许出现在 cassette（真录实测：blanket 登记会让协议头值假阳性拒绝落盘）；
        身份类头（chatgpt-account-id 等）不在 allowlist → 照常登记。"""
        self.register_forbidden_secret(auth.bearer_token, label="bearer_token")
        for name, value in auth.extra_headers.items():
            if name.lower() not in REQUEST_HEADER_ALLOWLIST:
                self.register_forbidden_secret(value, label=f"auth-header:{name}")

    # ---------------------------------------------------------------- 录制
    def record(
        self,
        *,
        request: httpx.Request,
        status_code: int,
        response_headers: httpx.Headers | dict[str, str],
        body_text: str,
    ) -> None:
        url = request.url
        if _is_token_endpoint(url.host, url.path):
            # 防御深度：token 交换整条丢弃（正常构造下根本不经注入 client）。
            return
        if url.query:
            # 报错只给 scheme://host/path——query 可能含签名/token，错误文本
            # 打到 console/日志同样不得回显（Codex final P2-3）。
            raise CassetteRecordError(
                f"请求带 query string（{url.scheme}://{url.host}{url.path}?<redacted>）"
                "——cassette 永久不持久化 query（spec D2 / Codex L1），出现即人工裁决。",
            )
        if not (200 <= status_code < 300):
            raise CassetteRecordError(
                f"非 2xx 响应（{status_code}）拒绝录制：provider 错误 body 可能"
                "回显请求内容/身份信息，本套件只钉 happy-path 真样本（spec D2）。"
                f"调试摘要（console-only，不落盘）: {body_text[:300]!r}",
            )
        # Opus final LOW-1：禁串逐字比对必须跑在 redact **之前**的 raw body 上
        # ——shaped 凭证（sk-/JWT）会被 redact 掩成 6+4 形态，dump 时的扫描拿
        # 不到全串；「已知凭证出现在响应体」是高危回显信号，直接硬 raise
        # （redact/scrub/dump-scan 仍在其后作为纵深后网）。
        for value, label in self._forbidden.items():
            if value in body_text:
                raise CassetteSecretError(
                    f"响应 body 逐字命中已登记凭证（{label}）——拒绝录制"
                    "（spec D3 第 5 道 b：真硬 stop 在 raw 层）。",
                )
        headers_obj = (
            response_headers
            if isinstance(response_headers, httpx.Headers)
            else httpx.Headers(response_headers)
        )
        self.interactions.append(
            RecordedInteraction(
                method=request.method.upper(),
                scheme=url.scheme,
                host=url.host,
                path=url.path,
                request_headers={
                    k.lower(): v
                    for k, v in request.headers.items()
                    if k.lower() in REQUEST_HEADER_ALLOWLIST
                },
                body_summary=_build_body_summary(request.content),
                status_code=status_code,
                response_headers={
                    k.lower(): v
                    for k, v in headers_obj.items()
                    if k.lower() in RESPONSE_HEADER_ALLOWLIST
                },
                body_text=scrub_identity_fields(redact_sensitive_text(body_text)),
            )
        )

    # ---------------------------------------------------------------- 落盘
    def scan_serialized(self, serialized: str) -> list[str]:
        """落盘前机械断言（第 5 道）：模式 + 禁串（含 JSON 转义形态）。"""
        findings = [
            f"pattern:{pattern.pattern}"
            for pattern in SECRET_SCAN_PATTERNS
            if pattern.search(serialized)
        ]
        findings.extend(
            f"identity-field-unscrubbed:{field}"
            for field, pattern in _IDENTITY_VIOLATION_PATTERNS.items()
            if pattern.search(serialized)
        )
        for value, label in self._forbidden.items():
            escaped = json.dumps(value, ensure_ascii=True)[1:-1]
            if value in serialized or escaped in serialized:
                findings.append(f"forbidden-literal:{label}")
        return findings

    def debug_locate_forbidden(self, serialized: str, *, window: int = 70) -> list[str]:
        """定位禁串命中上下文（诊断用，console-only）：值本身以 <SECRET:label>
        掩码呈现，绝不回显。"""
        contexts: list[str] = []
        for value, label in self._forbidden.items():
            for needle in (value, json.dumps(value, ensure_ascii=True)[1:-1]):
                idx = serialized.find(needle)
                if idx < 0:
                    continue
                before = serialized[max(0, idx - window) : idx]
                after = serialized[idx + len(needle) : idx + len(needle) + window]
                # Opus final LOW-4：上下文窗口里可能出现**相邻的另一个**禁串，
                # 全部登记值一并掩码后才可打印（值永不回显）。
                for other_value, other_label in self._forbidden.items():
                    for form in (
                        other_value,
                        json.dumps(other_value, ensure_ascii=True)[1:-1],
                    ):
                        before = before.replace(form, f"<SECRET:{other_label}>")
                        after = after.replace(form, f"<SECRET:{other_label}>")
                contexts.append(f"{label}: ...{before}<SECRET:{label}>{after}...")
                break
        return contexts

    def serialize(self) -> str:
        meta = dict(self.meta)
        meta.setdefault(
            "recorded_at",
            datetime.now(UTC).isoformat(timespec="seconds"),
        )
        payload = {
            "format_version": CASSETTE_FORMAT_VERSION,
            "meta": meta,
            "interactions": [item.to_json_obj() for item in self.interactions],
        }
        return json.dumps(payload, ensure_ascii=True, indent=2) + "\n"

    def dump(self, path: Path) -> None:
        """事务式原子落盘（第 6 道）：serialize → 扫描 → temp → os.replace。

        扫描失败时目标文件与 temp 均不存在（fail-closed，无半成品）。
        """
        serialized = self.serialize()
        findings = self.scan_serialized(serialized)
        if findings:
            raise CassetteSecretError(
                f"cassette 落盘前扫描命中 secret（拒绝产出 {path.name}）: {findings}",
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(path.name + ".tmp")
        try:
            tmp_path.write_text(serialized, encoding="utf-8")
            os.replace(tmp_path, path)
        finally:
            tmp_path.unlink(missing_ok=True)


class RecordingTransport(httpx.AsyncBaseTransport):
    """录制 transport：透传真实请求，缓冲**解码后**响应，喂给 CassetteRecorder。

    返回给调用方的响应 = 解码后 content + 剥除 content-transfer 三头（与
    cassette 存储形态一致；流式语义退化为 buffered，录制脚本不受影响）。
    """

    def __init__(
        self,
        recorder: CassetteRecorder,
        inner: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._recorder = recorder
        self._inner = inner or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._inner.handle_async_request(request)
        try:
            decoded = await response.aread()  # 按 content-encoding 解码后的 bytes
        finally:
            await response.aclose()
        passthrough_headers = {
            k.lower(): v
            for k, v in response.headers.items()
            if k.lower() not in _CONTENT_TRANSFER_HEADERS
        }
        self._recorder.record(
            request=request,
            status_code=response.status_code,
            response_headers=passthrough_headers,
            body_text=decoded.decode("utf-8", errors="replace"),
        )
        return httpx.Response(
            status_code=response.status_code,
            headers=passthrough_headers,
            content=decoded,
            request=request,
        )

    async def aclose(self) -> None:
        """转发关闭到被包装的真 transport（Codex final P2-2：基类 aclose 是
        no-op，不转发会让录制脚本的连接池悬空）。"""
        await self._inner.aclose()


class ReplayTransport(httpx.AsyncBaseTransport):
    """回放 transport：顺序 pop cassette 交互，松匹配 method/host/path。

    请求 body 刻意不参与匹配（spec §3：body shape 回归由既有 23 用例 +
    F142 边界族钉；cassette 只负责真响应样本穿透解析栈）。
    """

    def __init__(self, cassette: Cassette) -> None:
        self._cassette = cassette
        self._cursor = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        idx = self._cursor
        interactions = self._cassette.interactions
        if idx >= len(interactions):
            raise ReplayMismatchError(
                f"cassette {self._cassette.describe()} 交互已耗尽"
                f"（共 {len(interactions)} 个），仍收到请求 "
                f"{request.method} {request.url}",
            )
        interaction = interactions[idx]
        expected = (interaction.method, interaction.host, interaction.path)
        actual = (request.method.upper(), request.url.host, request.url.path)
        if expected != actual:
            raise ReplayMismatchError(
                f"cassette {self._cassette.describe()} 交互 #{idx} 不匹配: "
                f"expected {expected}, actual {actual}",
            )
        self._cursor += 1
        self._cassette.play_counts[idx] += 1
        return httpx.Response(
            status_code=interaction.status_code,
            headers=interaction.response_headers,
            content=interaction.body_text.encode("utf-8"),
            request=request,
        )


def replay_client(cassette: Cassette) -> httpx.AsyncClient:
    """构造回放用 AsyncClient（结构性无 socket：唯一 transport 是 ReplayTransport）。"""
    return httpx.AsyncClient(transport=ReplayTransport(cassette))


class ReplayAuthResolver:
    """回放用假凭证 resolver——hermetic：不读 env、不读宿主 ~/.octoagent。"""

    def __init__(self, bearer_token: str = "replay-token") -> None:
        self._token = bearer_token

    async def resolve(self) -> ResolvedAuth:
        return ResolvedAuth(bearer_token=self._token)

    async def force_refresh(self) -> ResolvedAuth | None:
        return ResolvedAuth(bearer_token=self._token)


__all__ = [
    "CASSETTE_FORMAT_VERSION",
    "Cassette",
    "CassetteRecordError",
    "CassetteRecorder",
    "CassetteSecretError",
    "RecordedInteraction",
    "RecordingTransport",
    "ReplayAuthResolver",
    "ReplayMismatchError",
    "ReplayTransport",
    "REQUEST_HEADER_ALLOWLIST",
    "RESPONSE_HEADER_ALLOWLIST",
    "IDENTITY_SCRUB_FIELDS",
    "IDENTITY_VIOLATION_FIELDS",
    "SECRET_SCAN_PATTERNS",
    "replay_client",
    "scrub_identity_fields",
]
