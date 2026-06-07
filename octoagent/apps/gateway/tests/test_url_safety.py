"""F123 出站 URL SSRF 预检单测。

hermetic：DNS 解析通过 monkeypatch `url_safety._resolve_host` 注入确定性结果，
不依赖真实网络。
"""

from __future__ import annotations

import os

import pytest

from octoagent.gateway.harness import url_safety
from octoagent.gateway.harness.url_safety import UnsafeUrlError, ensure_url_safe


@pytest.fixture(autouse=True)
def _clean_toggle(monkeypatch: pytest.MonkeyPatch):
    """每个用例前清掉开关 env + yaml 缓存，保证默认 fail-closed。"""
    monkeypatch.delenv("OCTOAGENT_ALLOW_PRIVATE_URLS", raising=False)
    url_safety.reset_allow_private_cache()
    yield
    url_safety.reset_allow_private_cache()


def _stub_resolver(monkeypatch: pytest.MonkeyPatch, mapping: dict[str, list[str]]) -> None:
    monkeypatch.setattr(url_safety, "_resolve_host", lambda h: mapping[h])


# --------------------------------------------------------------------------- #
# AC-1 字面量云元数据端点
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
        "http://169.254.170.2/v2/credentials/",  # AWS ECS
        "http://169.254.169.253/",  # Azure IMDS wire server
        "http://[fd00:ec2::254]/latest/meta-data/",  # AWS IPv6
        "http://100.100.100.200/latest/meta-data/",  # Alibaba
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://metadata.goog/",
    ],
)
def test_literal_metadata_blocked(url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        ensure_url_safe(url)


# --------------------------------------------------------------------------- #
# AC-2 各私网段 / loopback / link-local / CGNAT
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "url",
    [
        "http://10.0.0.1/",  # RFC1918 10/8
        "http://172.16.5.4/",  # RFC1918 172.16/12
        "http://192.168.1.1/",  # RFC1918 192.168/16
        "http://127.0.0.1:8080/admin",  # loopback v4
        "http://[::1]/",  # loopback v6
        "http://169.254.1.5/",  # link-local v4（含整段 169.254/16）
        "http://[fe80::1]/",  # link-local v6
        "http://100.64.0.1/",  # CGNAT 100.64/10
        "http://0.0.0.0/",  # unspecified
    ],
)
def test_private_ranges_blocked(url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        ensure_url_safe(url)


# --------------------------------------------------------------------------- #
# AC-3 IPv4-mapped IPv6
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "url",
    [
        "http://[::ffff:169.254.169.254]/",  # 映射元数据
        "http://[::ffff:10.0.0.1]/",  # 映射私网
        "http://[::ffff:127.0.0.1]/",  # 映射 loopback
    ],
)
def test_ipv4_mapped_ipv6_blocked(url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        ensure_url_safe(url)


# --------------------------------------------------------------------------- #
# AC-4 hostname 解析到内网/元数据
# --------------------------------------------------------------------------- #
def test_hostname_resolving_to_internal_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_resolver(
        monkeypatch,
        {
            "evil-metadata.example": ["169.254.169.254"],
            "evil-private.example": ["10.5.5.5"],
            "mixed.example": ["93.184.216.34", "127.0.0.1"],  # 任一内网即拦
        },
    )
    for host in ("evil-metadata.example", "evil-private.example", "mixed.example"):
        with pytest.raises(UnsafeUrlError):
            ensure_url_safe(f"https://{host}/path")


# --------------------------------------------------------------------------- #
# AC-5 公网放行
# --------------------------------------------------------------------------- #
def test_public_url_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_resolver(monkeypatch, {"example.com": ["93.184.216.34"]})
    assert ensure_url_safe("http://93.184.216.34/") == "http://93.184.216.34/"  # 字面量公网
    assert ensure_url_safe("https://example.com/path?q=1") == "https://example.com/path?q=1"


# --------------------------------------------------------------------------- #
# AC-5b 翻译形态（NAT64/6to4/IPv4-mapped）包装公网 → 按真实目标放行（修复 Codex round-2 MED）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "url",
    [
        "http://[64:ff9b::0808:0808]/",  # NAT64 → 8.8.8.8（包装前缀 is_reserved，但目标公网）
        "http://[2002:0808:0808::1]/",  # 6to4 → 8.8.8.8（包装前缀 is_private，但目标公网）
        "http://[::ffff:8.8.8.8]/",  # IPv4-mapped → 8.8.8.8
    ],
)
def test_public_translation_forms_allowed(url: str) -> None:
    assert ensure_url_safe(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "http://[64:ff9b::7f00:0001]/",  # NAT64 → 127.0.0.1 loopback
        "http://[64:ff9b::0a00:0001]/",  # NAT64 → 10.0.0.1 private（toggle off）
        "http://[2002:7f00:0001::1]/",  # 6to4 → 127.0.0.1 loopback
    ],
)
def test_translation_forms_wrapping_internal_blocked(url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        ensure_url_safe(url)


# --------------------------------------------------------------------------- #
# AC-6 开关放开普通私网
# --------------------------------------------------------------------------- #
def test_allow_private_toggle_permits_private(monkeypatch: pytest.MonkeyPatch) -> None:
    """开关开启：普通私网（RFC1918 / CGNAT）放行。"""
    monkeypatch.setenv("OCTOAGENT_ALLOW_PRIVATE_URLS", "true")
    assert ensure_url_safe("http://10.0.0.1/") == "http://10.0.0.1/"
    assert ensure_url_safe("http://192.168.1.1/") == "http://192.168.1.1/"
    assert ensure_url_safe("http://172.16.5.4/") == "http://172.16.5.4/"
    assert ensure_url_safe("http://100.64.0.1/") == "http://100.64.0.1/"  # CGNAT


# --------------------------------------------------------------------------- #
# AC-6b 开关开启时本机/内部地址仍永远拦（修复 Codex F2）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",  # loopback v4
        "http://[::1]/",  # loopback v6
        "http://0.0.0.0/",  # unspecified v4
        "http://[::]/",  # unspecified v6
        "http://[fe80::1]/",  # link-local v6
        "http://169.254.1.5/",  # link-local v4
        "http://224.0.0.1/",  # multicast
    ],
)
def test_always_unsafe_blocked_even_with_toggle(
    url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """allow_private_urls=true 只放开普通私网；loopback/unspecified/link-local/multicast
    任何部署都非合法目标，永远拦。"""
    monkeypatch.setenv("OCTOAGENT_ALLOW_PRIVATE_URLS", "true")
    with pytest.raises(UnsafeUrlError):
        ensure_url_safe(url)


# --------------------------------------------------------------------------- #
# AC-7 开关开启时元数据仍拦（floor 不可绕过，含 NAT64/6to4 内嵌形态）
# --------------------------------------------------------------------------- #
def test_metadata_always_blocked_even_with_toggle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOAGENT_ALLOW_PRIVATE_URLS", "true")
    _stub_resolver(monkeypatch, {"meta.evil.example": ["169.254.169.254"]})
    for url in (
        "http://169.254.169.254/",  # 字面量
        "http://metadata.google.internal/",  # 主机名
        "http://meta.evil.example/",  # 解析到元数据
        "http://[64:ff9b::a9fe:a9fe]/",  # NAT64 内嵌 169.254.169.254
        "http://[2002:a9fe:a9fe::1]/",  # 6to4 内嵌 169.254.169.254
    ):
        with pytest.raises(UnsafeUrlError):
            ensure_url_safe(url)


# --------------------------------------------------------------------------- #
# AC-7b IPv6 zone identifier 不得绕过 floor/内部判定（修复 Codex round-3 HIGH）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "url",
    [
        "http://[fd00:ec2::254%25en0]/",  # AWS IPv6 metadata + zone（floor）
        "http://[64:ff9b::a9fe:a9fe%25en0]/",  # NAT64 元数据 + zone
        "http://[fe80::1%25en0]/",  # link-local + zone
        "http://[::1%25lo0]/",  # loopback + zone
    ],
)
def test_ipv6_zone_id_does_not_bypass(url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """zone id（%en0）只是本地接口选择器，剥离后按纯地址判；开关开启时元数据/内部仍拦。"""
    monkeypatch.setenv("OCTOAGENT_ALLOW_PRIVATE_URLS", "true")
    with pytest.raises(UnsafeUrlError):
        ensure_url_safe(url)


# --------------------------------------------------------------------------- #
# AC-8 非法 scheme / 空 host
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "ftp://example.com/",
        "gopher://127.0.0.1/",
        "http://",  # 空 host
        "not-a-url",
    ],
)
def test_invalid_scheme_blocked(url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        ensure_url_safe(url)


# --------------------------------------------------------------------------- #
# AC-9 DNS 解析失败 fail-closed
# --------------------------------------------------------------------------- #
def test_dns_failure_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(_host: str):
        raise OSError("name resolution failed")

    monkeypatch.setattr(url_safety, "_resolve_host", _boom)
    with pytest.raises(UnsafeUrlError):
        ensure_url_safe("https://does-not-resolve.example/")


def test_dns_empty_result_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(url_safety, "_resolve_host", lambda _h: [])
    with pytest.raises(UnsafeUrlError):
        ensure_url_safe("https://empty.example/")


# --------------------------------------------------------------------------- #
# AC-11 yaml security.allow_private_urls 解析 + 向后兼容 + env 优先级
# --------------------------------------------------------------------------- #
def test_security_config_yaml_parsed() -> None:
    from octoagent.gateway.services.config.config_schema import OctoAgentConfig

    cfg = OctoAgentConfig.from_yaml(
        "updated_at: '2026-06-08'\nsecurity:\n  allow_private_urls: true\n"
    )
    assert cfg.security.allow_private_urls is True


def test_security_config_backward_compat_default_false() -> None:
    from octoagent.gateway.services.config.config_schema import OctoAgentConfig

    cfg = OctoAgentConfig.from_yaml("updated_at: '2026-06-08'\n")  # 无 security 段
    assert cfg.security.allow_private_urls is False


def test_yaml_toggle_drives_allow_private(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.delenv("OCTOAGENT_ALLOW_PRIVATE_URLS", raising=False)
    (tmp_path / "octoagent.yaml").write_text(
        "updated_at: '2026-06-08'\nsecurity:\n  allow_private_urls: true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OCTOAGENT_PROJECT_ROOT", str(tmp_path))
    url_safety.reset_allow_private_cache()
    # yaml 开关 true → 普通私网放行，但元数据仍拦
    assert ensure_url_safe("http://10.0.0.1/") == "http://10.0.0.1/"
    with pytest.raises(UnsafeUrlError):
        ensure_url_safe("http://169.254.169.254/")


def test_env_overrides_yaml_toggle(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    (tmp_path / "octoagent.yaml").write_text(
        "updated_at: '2026-06-08'\nsecurity:\n  allow_private_urls: true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OCTOAGENT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("OCTOAGENT_ALLOW_PRIVATE_URLS", "false")  # env 显式关，优先于 yaml
    url_safety.reset_allow_private_cache()
    with pytest.raises(UnsafeUrlError):
        ensure_url_safe("http://10.0.0.1/")


def test_yaml_toggle_mtime_invalidation(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """修复 Codex F3：yaml 开关 true→false 后，无需重启、无需 reset 缓存即刻生效。"""
    monkeypatch.delenv("OCTOAGENT_ALLOW_PRIVATE_URLS", raising=False)
    cfg = tmp_path / "octoagent.yaml"
    cfg.write_text(
        "updated_at: '2026-06-08'\nsecurity:\n  allow_private_urls: true\n", encoding="utf-8"
    )
    monkeypatch.setenv("OCTOAGENT_PROJECT_ROOT", str(tmp_path))
    url_safety.reset_allow_private_cache()
    # 初始 true：普通私网放行（建立 mtime 缓存）
    assert ensure_url_safe("http://10.0.0.1/") == "http://10.0.0.1/"
    # 改回 false 并 bump mtime（不调 reset，模拟运维改 yaml）
    cfg.write_text(
        "updated_at: '2026-06-08'\nsecurity:\n  allow_private_urls: false\n", encoding="utf-8"
    )
    st = cfg.stat()
    os.utime(cfg, ns=(st.st_atime_ns + 10_000_000_000, st.st_mtime_ns + 10_000_000_000))
    # mtime 变化 → 立即重读 → 10.0.0.1 被拦（不再 fail-open）
    with pytest.raises(UnsafeUrlError):
        ensure_url_safe("http://10.0.0.1/")


# --------------------------------------------------------------------------- #
# async 包装等价性
# --------------------------------------------------------------------------- #
async def test_async_ensure_url_safe_blocks_and_allows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_resolver(monkeypatch, {"example.com": ["93.184.216.34"]})
    assert await url_safety.async_ensure_url_safe("https://example.com/") == "https://example.com/"
    with pytest.raises(UnsafeUrlError):
        await url_safety.async_ensure_url_safe("http://169.254.169.254/")
