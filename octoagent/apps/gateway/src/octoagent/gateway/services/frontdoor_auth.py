"""Gateway front-door auth / trusted proxy 边界。"""

from __future__ import annotations

import ipaddress
import math
import os
import secrets
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

import structlog
from fastapi import HTTPException, Request
from octoagent.gateway.services.config.config_schema import FrontDoorConfig
from octoagent.gateway.services.config.config_wizard import load_config
from pydantic import ValidationError

log = structlog.get_logger()

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}
_SSE_QUERY_TOKEN_PARAM = "access_token"
_PROXY_HINT_HEADERS = (
    "forwarded",
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
    "x-real-ip",
)

# ---------------------------------------------------------------------------
# F134 认证失败限流（仿 OpenClaw auth-rate-limit，单进程内存态）
# ---------------------------------------------------------------------------

#: 滑动窗口长度（秒）：窗口内失败达阈值才进入 lockout。
_RATE_LIMIT_WINDOW_SECONDS = 60.0
#: 窗口内允许的「带错凭证」失败次数；达到即 lockout。
_RATE_LIMIT_MAX_FAILURES = 10
#: lockout 时长（秒）：期间该源的错误凭证请求一律 429。
_RATE_LIMIT_LOCKOUT_SECONDS = 300.0
#: 追踪的源上限（防伪造源撑爆内存；单用户实例足够）。
_RATE_LIMIT_MAX_ENTRIES = 256


class _RateLimitEntry:
    """单个源的失败状态（时间戳滑动窗口 + lockout 截止时刻）。"""

    __slots__ = ("failures", "locked_until")

    def __init__(self) -> None:
        self.failures: deque[float] = deque()
        self.locked_until: float | None = None


class _FailureRateLimiter:
    """认证失败限流器（spec F134 §1）。

    语义（D1，与 OpenClaw check-before-verify 的显式差异）：**验证优先**——
    调用方先做凭证验证，只有「带了凭证但验证失败」才 ``record_failure``；
    验证成功恒放行并 ``reset``。因此正确凭证永不被锁（反向隧道回源场景
    所有远程请求可能共享 127.0.0.1 一个桶，锁定式会让任一失控客户端把唯一
    用户锁在门外）。缺凭证（SPA 首屏裸请求渲染 FrontDoorGate 的正常路径）
    不经过本组件。

    key = TCP 层 client_host（不可伪造；不用 XFF——直连 LAN 场景 XFF 可被
    伪造成每请求换桶绕过限流）；loopback 源**不豁免**（D2，反向隧道回源就是
    127.0.0.1，豁免即失效）。时钟经构造注入（默认 ``time.monotonic``，测试
    可控无 sleep）。FastAPI 单 event loop 内方法为同步段，无锁需求。
    """

    def __init__(
        self,
        *,
        max_failures: int = _RATE_LIMIT_MAX_FAILURES,
        window_seconds: float = _RATE_LIMIT_WINDOW_SECONDS,
        lockout_seconds: float = _RATE_LIMIT_LOCKOUT_SECONDS,
        max_entries: int = _RATE_LIMIT_MAX_ENTRIES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_failures = max_failures
        self._window_seconds = window_seconds
        self._lockout_seconds = lockout_seconds
        self._max_entries = max_entries
        self._clock = clock
        self._entries: dict[str, _RateLimitEntry] = {}

    def record_failure(self, source: str) -> float | None:
        """记录一次「带错凭证」失败；返回当前 lockout 剩余秒数（未锁 None）。

        已处 lockout 时不再累计（lockout 固定时长不滑动，可预测），只回报
        剩余秒。窗口内失败达到阈值的那一次触发 lockout 并记一条 warning
        （只在状态转变时记，防攻击期间日志洪流；不含任何凭证字节）。
        """
        now = self._clock()
        entry = self._entries.get(source)
        if entry is None:
            entry = self._admit_entry(source, now)
            if entry is None:
                # 表满且全员处于 lockout（极端态）：不为新源建条目，也不升级
                # 响应——宁可少限流也不误伤/膨胀（可用性优先，D3）。
                return None

        remaining = self._locked_remaining(entry, now)
        if remaining is not None:
            return remaining

        self._slide_window(entry, now)
        entry.failures.append(now)
        if len(entry.failures) >= self._max_failures:
            entry.locked_until = now + self._lockout_seconds
            entry.failures.clear()
            log.warning(
                "frontdoor_rate_limited",
                source=source or "<unknown>",
                retry_after_seconds=math.ceil(self._lockout_seconds),
            )
            return self._lockout_seconds
        return None

    def reset(self, source: str) -> None:
        """凭证验证成功后清除该源全部失败状态（FR-1b）。"""
        self._entries.pop(source, None)

    def _locked_remaining(self, entry: _RateLimitEntry, now: float) -> float | None:
        if entry.locked_until is None:
            return None
        if now >= entry.locked_until:
            entry.locked_until = None
            entry.failures.clear()
            return None
        return entry.locked_until - now

    def _slide_window(self, entry: _RateLimitEntry, now: float) -> None:
        cutoff = now - self._window_seconds
        while entry.failures and entry.failures[0] <= cutoff:
            entry.failures.popleft()

    def _admit_entry(self, source: str, now: float) -> _RateLimitEntry | None:
        """新源入表；表满先清过期、再逐最旧的未锁定条目（保住攻击者的锁）。"""
        if len(self._entries) >= self._max_entries:
            self._prune(now)
        if len(self._entries) >= self._max_entries:
            for key, candidate in self._entries.items():
                if self._locked_remaining(candidate, now) is None:
                    del self._entries[key]
                    break
            else:
                return None
        entry = _RateLimitEntry()
        self._entries[source] = entry
        return entry

    def _prune(self, now: float) -> None:
        stale = []
        for key, entry in self._entries.items():
            if self._locked_remaining(entry, now) is not None:
                continue
            self._slide_window(entry, now)
            if not entry.failures:
                stale.append(key)
        for key in stale:
            del self._entries[key]


def _http_error(
    status_code: int,
    code: str,
    message: str,
    *,
    hint: str | None = None,
    headers: dict[str, str] | None = None,
) -> HTTPException:
    detail = {"code": code, "message": message}
    if hint:
        detail["hint"] = hint
    return HTTPException(status_code=status_code, detail=detail, headers=headers)


class FrontDoorGuard:
    """统一保护 owner-facing API。"""

    def __init__(self, project_root: Path) -> None:
        self._project_root = project_root
        # F134：guard 经 deps.get_front_door_guard 缓存到 app.state（单例），
        # 内存态限流状态随 app 生命周期存活。
        self._rate_limiter = _FailureRateLimiter()

    def _reject_invalid_credential(
        self,
        client_host: str,
        *,
        status_code: int,
        code: str,
        message: str,
        hint: str,
        rate_limited_code: str,
        headers: dict[str, str] | None = None,
    ) -> HTTPException:
        """带错凭证的拒绝出口（F134 FR-1a）：计失败、lockout 中升级 429。

        只有「带了凭证但验证失败」走这里（缺凭证不计数，FR-1c）；正确凭证
        在调用方直接放行 + reset（FR-1b），故本方法不可能拦下合法用户。

        ``rate_limited_code`` 按凭证类别区分（Codex 十四轮 P2，对称既有
        ``TOKEN_INVALID`` / ``PROXY_TOKEN_INVALID`` 命名法）：前端把 bearer
        版归 token gate（输对 token 即恢复），proxy 版归 trusted_proxy 指引
        ——统一 code 会让 trusted_proxy 用户被误导去输无用的 bearer token。
        """
        retry_after = self._rate_limiter.record_failure(client_host)
        if retry_after is not None:
            retry_seconds = max(1, math.ceil(retry_after))
            return _http_error(
                429,
                rate_limited_code,
                "认证失败次数过多，请稍后再试。",
                hint=f"该来源已被暂时限流，约 {retry_seconds} 秒后可重试；"
                "使用正确凭证的请求不受影响。",
                headers={"Retry-After": str(retry_seconds)},
            )
        return _http_error(status_code, code, message, hint=hint, headers=headers)

    async def authorize(self, request: Request) -> None:
        config = self._load_front_door_config()
        client_host = self._resolve_client_host(request)

        if config.mode == "loopback":
            if self._is_loopback_host(client_host):
                if self._has_proxy_forwarding_headers(request):
                    raise _http_error(
                        403,
                        "FRONT_DOOR_LOOPBACK_PROXY_REJECTED",
                        "loopback 模式拒绝代理转发的 owner-facing API 请求。",
                        hint=(
                            "如果需要经反向代理访问，请切换到 bearer 或 trusted_proxy 模式；"
                            "loopback 仅允许本机直连。"
                        ),
                    )
                return
            raise _http_error(
                403,
                "FRONT_DOOR_LOOPBACK_ONLY",
                "当前实例仅允许本机直连访问 owner-facing API。",
                hint="如需通过公网或反向代理访问，请切换到 bearer 或 trusted_proxy 模式。",
            )

        if config.mode == "bearer":
            expected = self._read_secret(
                config.bearer_token_env,
                code="FRONT_DOOR_TOKEN_ENV_MISSING",
                hint="请设置 bearer_token_env 指向的环境变量后重试。",
            )
            presented = self._extract_bearer_token(
                request,
                allow_query_token=request.url.path.startswith("/api/stream/"),
            )
            if not presented:
                raise _http_error(
                    401,
                    "FRONT_DOOR_TOKEN_REQUIRED",
                    "当前实例要求 Bearer Token。",
                    hint="Web 控制台可在页面中输入 token；SSE 连接使用 access_token 查询参数。",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            if not secrets.compare_digest(presented, expected):
                raise self._reject_invalid_credential(
                    client_host,
                    status_code=401,
                    code="FRONT_DOOR_TOKEN_INVALID",
                    message="Bearer Token 无效。",
                    hint="请确认本地保存的 token 与服务端环境变量一致。",
                    rate_limited_code="FRONT_DOOR_RATE_LIMITED",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            self._rate_limiter.reset(client_host)
            return

        if config.mode == "trusted_proxy":
            if not self._is_trusted_proxy_client(client_host, config.trusted_proxy_cidrs):
                raise _http_error(
                    403,
                    "FRONT_DOOR_TRUSTED_PROXY_REQUIRED",
                    "当前实例仅接受受信代理转发的请求。",
                    hint="请确认 Gateway 仅暴露给反向代理，并正确配置 trusted_proxy_cidrs。",
                )
            expected = self._read_secret(
                config.trusted_proxy_token_env,
                code="FRONT_DOOR_PROXY_TOKEN_ENV_MISSING",
                hint="请设置 trusted_proxy_token_env 指向的环境变量后重试。",
            )
            header_value = request.headers.get(config.trusted_proxy_header, "").strip()
            if not header_value:
                raise _http_error(
                    403,
                    "FRONT_DOOR_PROXY_TOKEN_REQUIRED",
                    "缺少 trusted proxy 共享鉴权 header。",
                    hint=(
                        f"请让反向代理注入 {config.trusted_proxy_header}，"
                        "并保证 Gateway 只接受代理来源地址。"
                    ),
                )
            if not secrets.compare_digest(header_value, expected):
                raise self._reject_invalid_credential(
                    client_host,
                    status_code=403,
                    code="FRONT_DOOR_PROXY_TOKEN_INVALID",
                    message="trusted proxy 共享鉴权 header 无效。",
                    hint="请确认代理注入的共享 token 与服务端环境变量一致。",
                    rate_limited_code="FRONT_DOOR_PROXY_RATE_LIMITED",
                )
            self._rate_limiter.reset(client_host)
            return

        raise _http_error(
            503,
            "FRONT_DOOR_MODE_UNSUPPORTED",
            f"不支持的 front-door 模式：{config.mode}",
        )

    def _load_front_door_config(self) -> FrontDoorConfig:
        base_data: dict[str, object] = {}
        try:
            loaded = load_config(self._project_root)
            if loaded:
                base_data = loaded.front_door.model_dump(mode="python")
        except ValueError as exc:
            log.warning(
                "front_door_config_source_invalid_fallback",
                error_type=type(exc).__name__,
                project_root=str(self._project_root),
            )
        env_overrides = self._read_env_overrides()
        try:
            return FrontDoorConfig.model_validate({**base_data, **env_overrides})
        except (ValidationError, ValueError) as exc:
            log.warning(
                "front_door_config_invalid",
                error_type=type(exc).__name__,
                project_root=str(self._project_root),
            )
            raise _http_error(
                503,
                "FRONT_DOOR_CONFIG_INVALID",
                "front-door 配置无效，已拒绝开放 owner-facing API。",
                hint="请检查 octoagent.yaml 的 front_door 配置或对应环境变量。",
            ) from exc

    def _read_env_overrides(self) -> dict[str, object]:
        overrides: dict[str, object] = {}
        if mode := os.environ.get("OCTOAGENT_FRONTDOOR_MODE"):
            overrides["mode"] = mode.strip()
        if bearer_token_env := os.environ.get("OCTOAGENT_FRONTDOOR_TOKEN_ENV"):
            overrides["bearer_token_env"] = bearer_token_env.strip()
        if trusted_proxy_header := os.environ.get("OCTOAGENT_TRUSTED_PROXY_HEADER"):
            overrides["trusted_proxy_header"] = trusted_proxy_header.strip()
        if trusted_proxy_token_env := os.environ.get("OCTOAGENT_TRUSTED_PROXY_TOKEN_ENV"):
            overrides["trusted_proxy_token_env"] = trusted_proxy_token_env.strip()
        if trusted_proxy_cidrs := os.environ.get("OCTOAGENT_TRUSTED_PROXY_CIDRS"):
            overrides["trusted_proxy_cidrs"] = trusted_proxy_cidrs
        return overrides

    def _read_secret(self, env_name: str, *, code: str, hint: str) -> str:
        value = os.environ.get(env_name, "").strip()
        if value:
            return value
        raise _http_error(
            503,
            code,
            f"front-door 凭证环境变量未设置：{env_name}",
            hint=hint,
        )

    @staticmethod
    def _extract_bearer_token(request: Request, *, allow_query_token: bool) -> str | None:
        authorization = request.headers.get("authorization", "").strip()
        if authorization:
            scheme, _, credentials = authorization.partition(" ")
            if scheme.lower() == "bearer" and credentials.strip():
                return credentials.strip()
        if allow_query_token:
            query_token = request.query_params.get(_SSE_QUERY_TOKEN_PARAM, "").strip()
            if query_token:
                return query_token
        return None

    @staticmethod
    def _resolve_client_host(request: Request) -> str:
        client = request.client
        if client is None or not client.host:
            return ""
        return client.host.strip()

    @staticmethod
    def _is_loopback_host(host: str) -> bool:
        if host in _LOOPBACK_HOSTS:
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    @staticmethod
    def _has_proxy_forwarding_headers(request: Request) -> bool:
        return any(request.headers.get(name, "").strip() for name in _PROXY_HINT_HEADERS)

    @classmethod
    def _is_trusted_proxy_client(cls, host: str, cidrs: list[str]) -> bool:
        if not host:
            return False
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return False
        for cidr in cidrs:
            if ip in ipaddress.ip_network(cidr, strict=False):
                return True
        return False
