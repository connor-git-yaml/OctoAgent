"""出站 URL 安全校验（SSRF 预检）。

阻止 LLM 被诱导让 Agent 出站抓取**内网 / 云元数据**端点（SSRF）——典型攻击是
诱导 `web.fetch` / `browser.*` 抓 `169.254.169.254`（云实例凭证元数据）、loopback /
私网服务，或通过公网 URL 的 302 重定向绕进内网。

设计要点：
- 解析目标 host：字面量 IP 直接判；hostname 经 DNS 解析后逐个 IP 判（getaddrinfo
  会顺便归一化十六进制 / 十进制 / IPv4-mapped 等混淆写法）。
- **云元数据 always-block 地板**：即便 `allow_private_urls=true` 也拦——这些地址
  对 Agent 永无合法用途。
- 全局开关 `security.allow_private_urls`（默认 false）：放开**普通私网** IP（用于
  DNS 把外域解析到 benchmark/内网段的特殊部署），但**不**放开元数据地板。
- fail-closed：scheme 非法 / DNS 解析失败 / 解析异常 → 一律拦截。

本模块**已挡住**：
- 字面量内网/元数据 IP（含 IPv4-mapped / NAT64 / 6to4 内嵌形态）；
- hostname 静态解析到内网/元数据（解析后逐 IP 判，多记录任一内网即拦）；
- 公网 URL 经 302 重定向到内网/元数据（httpx request event-hook 逐跳重校验，连接前中断）；
- 八进制/十六进制/十进制等混淆 IP（依赖"解析后判"——预检与 httpx 走同一 OS resolver，
  解析一致，故混淆形态被 resolver 归一后命中私网判定）。

v0.1 已知 limitation（pre-flight 层无法根治，**未**宣称修复）：
- **DNS rebinding（TOCTOU）**：攻击者控的 TTL=0 DNS 可在"预检解析"与"httpx 实连解析"
  之间换 IP（公网→内网）。彻底修需**连接级**校验（pinned-IP transport + 保留 Host/SNI，
  或 egress proxy / 网络层 deny 私网），列为后续（M6/M7 egress 域）。Hermes 参考实现同样
  未在 pre-flight 层修。
- `0177.0.0.1` 等平台相关的字面量八进制解析：安全性依赖 precheck/connect 同 resolver
  一致，不依赖本模块字面量解析；非 SSRF 漏洞。
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import socket
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_IpAddr = ipaddress.IPv4Address | ipaddress.IPv6Address


class UnsafeUrlError(RuntimeError):
    """出站 URL 命中 SSRF 拦截规则时抛出。

    继承 RuntimeError：保持既有工具错误事件路径（ToolBroker 把 RuntimeError
    记为 tool-error）与 `is_error` 语义不变。
    """


# 永远拦截的 hostname —— 云元数据主机名，忽略任何开关。
_BLOCKED_HOSTNAMES = frozenset(
    {
        "metadata.google.internal",
        "metadata.goog",
    }
)

# 永远拦截的 IP —— 云元数据 / 凭证端点（SSRF 头号目标）。即便
# allow_private_urls=true 也拦。IPv4-mapped / NAT64 / 6to4 内嵌形态由
# `_embedded_ipv4` 解包出嵌入的 IPv4 后再比对，无需在此重复列举各变体。
_ALWAYS_BLOCKED_IPS = frozenset(
    {
        ipaddress.ip_address("169.254.169.254"),  # AWS/GCP/Azure/DO/Oracle 元数据
        ipaddress.ip_address("169.254.170.2"),  # AWS ECS 任务元数据（任务 IAM 凭证）
        ipaddress.ip_address("169.254.169.253"),  # Azure IMDS wire server
        ipaddress.ip_address("100.100.100.200"),  # 阿里云元数据
        ipaddress.ip_address("fd00:ec2::254"),  # AWS 元数据（IPv6）
    }
)

# 永远拦截的网段 —— 整段 link-local（169.254.0.0/16）对 Agent 无合法出站用途，
# 元数据地址都落在这里。
_ALWAYS_BLOCKED_NETWORKS = (ipaddress.ip_network("169.254.0.0/16"),)

# CGNAT / Shared Address Space（RFC 6598）。`ipaddress.is_private` **不覆盖**
# 100.64.0.0/10（is_private 与 is_global 都返回 False），必须显式拦。运营商级
# NAT、Tailscale/WireGuard、部分云内网用它。
_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")

# IPv6→IPv4 翻译前缀：内嵌一个目标 IPv4，但前缀本身 is_global=True，不会被任何
# IPv4 私网/floor 判定命中。必须解包出内嵌 IPv4 再按 IPv4 规则判，否则
# `[64:ff9b::a9fe:a9fe]`（NAT64 形态的 169.254.169.254）等可绕过元数据地板。
_NAT64_PREFIX = ipaddress.ip_network("64:ff9b::/96")  # RFC 6052 well-known NAT64
_6TO4_PREFIX = ipaddress.ip_network("2002::/16")  # RFC 3056 6to4（IPv4 在 bit 16..48）


# ---------------------------------------------------------------------------
# 全局开关：allow_private_urls
# ---------------------------------------------------------------------------
# env `OCTOAGENT_ALLOW_PRIVATE_URLS` 每次读取（便于测试 monkeypatch）；
# yaml `security.allow_private_urls` 涉及文件 I/O（load_config 全量解析较重），
# 按 octoagent.yaml 的 (path, mtime_ns) 缓存：stat 廉价、内容不变不重解析，但运维
# 改 yaml（含把开关从 true 改回 false）后 mtime 变化即重读 → 安全关关立即生效，
# 不依赖进程重启（修复 Codex F3 fail-open 回滚）。
_yaml_cache: tuple[str, int, bool] | None = None


def _yaml_allow_private_urls() -> bool:
    """读取 octoagent.yaml 的 `security.allow_private_urls`（mtime 失效缓存 + fail-safe）。

    惰性 import config_wizard 避免 import 期循环；任何异常（无配置 / 解析失败）
    一律退回 False（安全默认）。
    """
    global _yaml_cache
    try:
        root = Path(os.environ.get("OCTOAGENT_PROJECT_ROOT", os.getcwd()))
        cfg_path = root / "octoagent.yaml"
        try:
            mtime_ns = cfg_path.stat().st_mtime_ns
        except OSError:
            # 文件不存在 → 无 yaml 开关；不缓存，下次文件出现即生效。
            _yaml_cache = None
            return False

        path_str = str(cfg_path)
        if (
            _yaml_cache is not None
            and _yaml_cache[0] == path_str
            and _yaml_cache[1] == mtime_ns
        ):
            return _yaml_cache[2]

        # 首次或 mtime 变化 → 重读
        from octoagent.gateway.services.config.config_wizard import load_config

        value = False
        config = load_config(root)
        if config is not None:
            value = bool(config.security.allow_private_urls)
        _yaml_cache = (path_str, mtime_ns, value)
        return value
    except Exception:  # noqa: BLE001 —— 配置不可用时 fail-closed 到 False
        return False


def _allow_private_urls() -> bool:
    """是否放开普通私网 IP（env 优先，其次 yaml，默认 False）。

    注意：本开关**不**放开云元数据地板（见 `_is_always_blocked_ip`）。
    """
    env_val = os.environ.get("OCTOAGENT_ALLOW_PRIVATE_URLS", "").strip().lower()
    if env_val in {"true", "1", "yes"}:
        return True
    if env_val in {"false", "0", "no"}:
        return False
    return _yaml_allow_private_urls()


def reset_allow_private_cache() -> None:
    """仅供测试：重置 yaml 开关缓存。"""
    global _yaml_cache
    _yaml_cache = None


# ---------------------------------------------------------------------------
# IP 判定
# ---------------------------------------------------------------------------


def _embedded_ipv4(ip: _IpAddr) -> ipaddress.IPv4Address | None:
    """若 ip 是内嵌 IPv4 的 IPv6 翻译形态，返回内嵌的 IPv4；否则 None。

    覆盖 IPv4-mapped（``::ffff:x.x.x.x``）/ NAT64（64:ff9b::/96）/ 6to4（2002::/16）。
    这些前缀本身 is_global=True，不会命中任何 IPv4 私网/floor 判定，必须解包出真实
    目标 IPv4 再按 IPv4 规则判（否则 NAT64/6to4 形态的元数据/私网可绕过）。
    """
    if not isinstance(ip, ipaddress.IPv6Address):
        return None
    if ip.ipv4_mapped is not None:  # ::ffff:x.x.x.x
        return ip.ipv4_mapped
    if ip in _NAT64_PREFIX:  # 低 32 bit 为目标 IPv4
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    if ip in _6TO4_PREFIX:  # 2002:V4:V4::/48 —— bit 16..48 为目标 IPv4
        return ipaddress.IPv4Address((int(ip) >> 80) & 0xFFFFFFFF)
    return None


def _effective_ip(ip: _IpAddr) -> _IpAddr:
    """翻译形态（IPv4-mapped / NAT64 / 6to4）→ 内嵌 IPv4（真实路由目标）；否则原样返回。

    安全分类必须基于**真实目标 IP**：包装 IPv6 前缀自身的 is_reserved / is_private 是该
    前缀的 IANA 属性、**非目标属性**（如 64:ff9b::/96 is_reserved、2002::/16 is_private）。
    若据包装地址判定，会把 NAT64/6to4 到公网（如 64:ff9b::808:808 = 8.8.8.8）误拦，
    破坏"正常公网行为不变"（修复 Codex round-2 MED）。元数据地板里唯一的 IPv6 字面量
    fd00:ec2::254 非翻译形态，按原样命中，不受影响。
    """
    embedded = _embedded_ipv4(ip)
    return embedded if embedded is not None else ip


def _is_always_blocked_ip(ip: _IpAddr) -> bool:
    """是否命中云元数据地板（**忽略** allow_private_urls 开关，永远拦）。"""
    eff = _effective_ip(ip)
    if eff in _ALWAYS_BLOCKED_IPS:
        return True
    return any(eff in net for net in _ALWAYS_BLOCKED_NETWORKS)


def _is_always_unsafe_ip(ip: _IpAddr) -> bool:
    """无论开关如何都不安全：loopback / link-local / multicast / unspecified / reserved。

    这些任何部署都非合法出站目标，allow_private_urls 也**不**放开（修复 Codex F2：
    开关只为"DNS 把外域解析到私网/benchmark 段"的场景，绝不应放开本机控制面如
    127.0.0.1 / ::1 / 0.0.0.0 / fe80::）。基于真实目标 IP 判定（见 `_effective_ip`）。
    """
    eff = _effective_ip(ip)
    return (
        eff.is_loopback
        or eff.is_link_local
        or eff.is_multicast
        or eff.is_unspecified
        or eff.is_reserved
    )


def _is_toggle_openable_private_ip(ip: _IpAddr) -> bool:
    """allow_private_urls=true 时**可放行**的"普通私网"：RFC1918 / benchmark(198.18) /
    ULA(fc00::/7) 等 is_private 段 + CGNAT（is_private 不覆盖）。

    loopback 等已被 `_is_always_unsafe_ip` 先行拦截，不会落到这里。基于真实目标 IP 判定。
    """
    eff = _effective_ip(ip)
    if eff.is_private:
        return True
    return eff.version == 4 and eff in _CGNAT_NETWORK


# ---------------------------------------------------------------------------
# DNS 解析 seam（测试可 monkeypatch，保持 hermetic）
# ---------------------------------------------------------------------------


def _resolve_host(hostname: str) -> list[str]:
    """解析 hostname → IP 字符串列表（去重）。

    默认走 `socket.getaddrinfo`（会顺便归一化十六进制 / 十进制 / 短写等混淆形式）。
    测试通过 monkeypatch 本函数注入确定性结果，避免真实网络依赖。
    """
    addr_info = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    seen: list[str] = []
    for entry in addr_info:
        ip_str = entry[4][0]
        if ip_str not in seen:
            seen.append(ip_str)
    return seen


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------


def _parse_host(url: str) -> tuple[str, str]:
    """解析并校验 scheme，返回 (normalized_url, hostname)。非法即抛 UnsafeUrlError。"""
    normalized = url.strip()
    parsed = urlparse(normalized)
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"http", "https"}:
        raise UnsafeUrlError("url 必须是 http/https 地址")
    # parsed.hostname 已小写并剥离 userinfo / port / IPv6 方括号——SSRF 判定基于真实
    # 连接目标 host（如 http://trusted@169.254.169.254/ 的 host 是 169.254.169.254）。
    hostname = (parsed.hostname or "").strip().rstrip(".")
    if not hostname:
        raise UnsafeUrlError("url 缺少有效的主机名")
    return normalized, hostname


def _try_parse_ip(value: str) -> _IpAddr | None:
    # 去掉 IPv6 zone identifier（如 fe80::1%en0 的 %en0 / URL 里的 %25en0）：它只是本地
    # 接口选择器，对 SSRF 分类无意义；但带 scope 的 IPv6Address 不等于 floor 里无 scope 的
    # 字面量、也会干扰网段判定 → 会绕过 always-block（Codex round-3 HIGH：开关开启时
    # fd00:ec2::254%en0 fail-open）。统一剥离后按纯地址判定。
    candidate = value.split("%", 1)[0]
    try:
        return ipaddress.ip_address(candidate)
    except ValueError:
        return None


def ensure_url_safe(url: str) -> str:
    """校验出站 URL，安全则返回 normalized url，不安全抛 UnsafeUrlError（同步）。

    含阻塞 `getaddrinfo`，async 热路径请用 `async_ensure_url_safe`。
    """
    normalized, hostname = _parse_host(url)

    # 1. 元数据 hostname —— 永远拦（忽略开关）
    if hostname in _BLOCKED_HOSTNAMES:
        logger.warning("拦截出站请求（云元数据主机名）：%s", hostname)
        raise UnsafeUrlError(f"拒绝访问云元数据端点：{hostname}")

    allow_private = _allow_private_urls()

    # 2. 字面量 IP —— 无需 DNS
    literal_ip = _try_parse_ip(hostname)
    if literal_ip is not None:
        _reject_ip_if_unsafe(literal_ip, hostname, allow_private)
        return normalized

    # 3. hostname —— 解析后逐 IP 判（fail-closed）
    try:
        resolved = _resolve_host(hostname)
    except Exception as exc:  # noqa: BLE001 —— DNS 失败 fail-closed
        logger.warning("拦截出站请求（DNS 解析失败）：%s（%s）", hostname, exc)
        raise UnsafeUrlError(f"无法解析主机名，已拒绝：{hostname}") from exc

    if not resolved:
        logger.warning("拦截出站请求（DNS 无解析结果）：%s", hostname)
        raise UnsafeUrlError(f"主机名无解析结果，已拒绝：{hostname}")

    for ip_str in resolved:
        parsed_ip = _try_parse_ip(ip_str)
        if parsed_ip is None:
            continue
        _reject_ip_if_unsafe(parsed_ip, hostname, allow_private)

    return normalized


def _reject_ip_if_unsafe(ip: _IpAddr, hostname: str, allow_private: bool) -> None:
    """三层判定 → 抛 UnsafeUrlError：
    ①云元数据地板（永远拦）②本机/内部地址（永远拦）③普通私网（开关关闭时拦）。
    """
    if _is_always_blocked_ip(ip):
        logger.warning("拦截出站请求（云元数据地址）：%s -> %s", hostname, ip)
        raise UnsafeUrlError(f"拒绝访问云元数据地址：{hostname} -> {ip}")
    if _is_always_unsafe_ip(ip):
        logger.warning("拦截出站请求（本机/内部地址）：%s -> %s", hostname, ip)
        raise UnsafeUrlError(f"拒绝访问本机/内部地址：{hostname} -> {ip}")
    if not allow_private and _is_toggle_openable_private_ip(ip):
        logger.warning("拦截出站请求（私网地址）：%s -> %s", hostname, ip)
        raise UnsafeUrlError(f"拒绝访问私网地址：{hostname} -> {ip}")


async def async_ensure_url_safe(url: str) -> str:
    """`ensure_url_safe` 的 async 包装：把阻塞 DNS 放到线程池，不阻塞 event loop。"""
    return await asyncio.to_thread(ensure_url_safe, url)
