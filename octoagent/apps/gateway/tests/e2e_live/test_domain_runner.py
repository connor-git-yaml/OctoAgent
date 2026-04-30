"""F087 P2 T-P2-15 helpers/domain_runner 自身单测。"""

from __future__ import annotations

import pytest

from apps.gateway.tests.e2e_live.helpers.domain_runner import (
    DOMAIN_REGISTRY,
    find_domain,
    list_domains,
    run_domain,
)


pytestmark = [pytest.mark.e2e_live]


def test_registry_has_13_domains() -> None:
    assert len(DOMAIN_REGISTRY) == 13
    ids = [d.domain_id for d in DOMAIN_REGISTRY]
    assert ids == list(range(1, 14))


def test_smoke_full_partition() -> None:
    """smoke = 5 域（#1 #2 #3 #11 #12）；full = 8 域（其余）。"""
    smoke = [d for d in DOMAIN_REGISTRY if d.marker == "e2e_smoke"]
    full = [d for d in DOMAIN_REGISTRY if d.marker == "e2e_full"]
    assert len(smoke) == 5
    assert len(full) == 8
    smoke_ids = sorted(d.domain_id for d in smoke)
    assert smoke_ids == [1, 2, 3, 11, 12]


def test_list_domains_formatting() -> None:
    rows = list_domains()
    assert len(rows) == 13
    assert "#" in rows[0]
    assert "工具调用基础" in rows[0]


def test_find_domain_hit() -> None:
    d = find_domain(5)
    assert d is not None
    assert "Perplexity" in d.name


def test_find_domain_miss() -> None:
    assert find_domain(999) is None


def test_run_domain_unknown_returns_usage_error() -> None:
    assert run_domain(999) == 2
