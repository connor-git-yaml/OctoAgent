"""benchmarks/tiers/tier2/gaia_fallback_adapter.py — GAIA Level 2 fallback adapter

PoC-H1 FAIL（HF gaia-benchmark/GAIA 是 gated dataset，匿名访问拒绝），按用户 2026-05-28
拍板走 fallback：从 gaia_fallback_tasks.yaml 加载 5 个 OctoBench 设计 Level 2 task.

引用文件:
- spec FR-E03（normalized 字符串匹配）
- spec FR-E04（5 task 分层：web search 2 + 文档解析 2 + 多工具串联 1）
- spec FR-B01（GAIA：normalized 字符串精确匹配）
- spec FR-B03（LLM-as-judge fallback；Phase D T-D-6 真实施）
- phase-0-poc-report.md §3 §5（PoC-H1 FAIL 决策记录）
- known-issues-deltas.md F-01（LLM judge 触发常量，Phase A 已落地）
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# 默认 fallback yaml 路径（相对 benchmarks/ 根目录）
DEFAULT_FALLBACK_YAML: Path = Path(__file__).parent / "gaia_fallback_tasks.yaml"

# 期望分层（spec FR-E04 严格执行）
EXPECTED_CATEGORY_DISTRIBUTION: dict[str, int] = {
    "web_search": 2,
    "doc_parse": 2,
    "multi_tool_chain": 1,
}


# ============================================================
# Task meta
# ============================================================


@dataclass
class GaiaFallbackTaskMeta:
    """单个 GAIA fallback task 元数据."""

    task_id: str
    domain: str
    category: str               # web_search / doc_parse / multi_tool_chain
    source_provenance: str      # 标 [GAIA-FALLBACK]
    prompt: str
    expected_answer: str
    expected_answer_alternates: list[str] = field(default_factory=list)
    expected_answer_tolerance: int | None = None  # 数值容差（仅 numeric task）
    timeout_seconds: int = 480
    rubric_id: str = "tier2-gaia-v1"
    notes: str = ""


def load_fallback_tasks(yaml_path: Path | None = None) -> list[GaiaFallbackTaskMeta]:
    """从 gaia_fallback_tasks.yaml 加载 5 个 task.

    Args:
        yaml_path: 自定义路径（用于测试），None 时用 DEFAULT_FALLBACK_YAML

    Returns:
        list[GaiaFallbackTaskMeta]: 全部 5 个 task（顺序按 yaml 内排列）

    Raises:
        FileNotFoundError: yaml 文件不存在
        ValueError: yaml schema 不符合（task 数 != 5 或分层不符合 FR-E04）
    """
    path = yaml_path or DEFAULT_FALLBACK_YAML
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    raw_tasks = data.get("tasks", [])

    if not raw_tasks:
        raise ValueError(f"GAIA fallback yaml 为空: {path}")

    result: list[GaiaFallbackTaskMeta] = []
    for t in raw_tasks:
        result.append(
            GaiaFallbackTaskMeta(
                task_id=t["task_id"],
                domain=t.get("domain", ""),
                category=t["category"],
                source_provenance=t.get("source_provenance", ""),
                prompt=t["prompt"],
                expected_answer=str(t["expected_answer"]),
                expected_answer_alternates=[str(x) for x in t.get("expected_answer_alternates", [])],
                expected_answer_tolerance=t.get("expected_answer_tolerance"),
                timeout_seconds=int(t.get("timeout_seconds", 480)),
                rubric_id=t.get("rubric_id", "tier2-gaia-v1"),
                notes=t.get("notes", ""),
            )
        )

    # 验证 FR-E04 分层
    actual_distribution: dict[str, int] = {}
    for task in result:
        actual_distribution[task.category] = actual_distribution.get(task.category, 0) + 1
    if actual_distribution != EXPECTED_CATEGORY_DISTRIBUTION:
        raise ValueError(
            f"GAIA fallback 分层与 FR-E04 不符: "
            f"expected={EXPECTED_CATEGORY_DISTRIBUTION}, actual={actual_distribution}"
        )

    return result


# ============================================================
# Normalized 字符串匹配（spec FR-E03）
# ============================================================


def normalize_answer(s: str) -> str:
    """规范化字符串以做 GAIA-style 答案匹配.

    规则（spec FR-E03 + plan §4.2）:
    - 转小写 + strip 首尾空格
    - 数字格式统一（"1,000" → "1000"；保留 "."）
    - 去除标点（保留数字 / 字母 / 下划线 / 小数点 / 减号）
    """
    s = s.strip().lower()
    # 去千分位逗号
    s = re.sub(r"(?<=\d),(?=\d)", "", s)
    # 去除多数标点（保留 .-_ 与字母数字）
    s = re.sub(r"[^a-z0-9._\- ]+", "", s)
    # 多空格 → 单空格
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _try_numeric_match(
    actual: str,
    expected: str,
    tolerance: int | None,
) -> bool | None:
    """如果 actual / expected 都能解析为数字，做 tolerance 范围比较.

    Returns:
        True: 数字匹配（含 tolerance）
        False: 数字不匹配
        None: 无法解析为数字（调用方继续走字符串匹配路径）
    """
    try:
        actual_num = float(actual.replace(",", ""))
        expected_num = float(expected.replace(",", ""))
    except (ValueError, AttributeError):
        return None
    if tolerance is None:
        return abs(actual_num - expected_num) < 1e-9
    return abs(actual_num - expected_num) <= tolerance


def match_answer(actual: str, task: GaiaFallbackTaskMeta) -> bool:
    """判定 agent 实际回答是否匹配 expected_answer.

    Codex Phase B review MED-4 修复 2026-05-29: 改为严格 normalized 精确匹配
    （spec FR-E03 + plan §4.2 明确"字符串精确匹配 / normalized 比较"）,
    不再支持 substring / token-sequence。LLM 带 prefix 的答案（如"the answer is X"）
    将判 FAIL；GAIA fallback yaml prompt 已要求 LLM "仅返回 X" minimal answer.
    更宽松的 LLM-judge fallback 留 Phase D T-D-6 实施.

    匹配顺序:
    1. 数字 tolerance 匹配（如果 task.expected_answer_tolerance 设置；含 alternates）
    2. normalized 严格相等: actual_norm == expected_norm（含 alternates）

    Args:
        actual: agent 实际回答（一般是 LLM 最终回复）
        task: 任务定义（含 expected_answer / alternates / tolerance）

    Returns:
        True 命中任一允许答案
    """
    # 数字 tolerance 优先（主答案 + alternates 都试一遍）
    for candidate in [task.expected_answer, *task.expected_answer_alternates]:
        numeric_result = _try_numeric_match(actual, candidate, task.expected_answer_tolerance)
        if numeric_result is True:
            return True

    # 数字明确不匹配（任一 candidate 是数字但 actual 也是数字不在 tolerance 内） → FAIL
    # 注意：所有 candidate 都 not numeric 时 _try_numeric_match 全 None，仍走字符串路径
    any_numeric_seen = False
    for candidate in [task.expected_answer, *task.expected_answer_alternates]:
        if _try_numeric_match(actual, candidate, task.expected_answer_tolerance) is False:
            any_numeric_seen = True
    if any_numeric_seen:
        return False

    # normalized 严格相等（无 substring fallback）
    actual_norm = normalize_answer(actual)
    expected_norms = [normalize_answer(task.expected_answer)] + [
        normalize_answer(a) for a in task.expected_answer_alternates
    ]
    return any(e and actual_norm == e for e in expected_norms)


# ============================================================
# 主 adapter 入口
# ============================================================


@dataclass
class GaiaFallbackAdapter:
    """GAIA Level 2 fallback adapter.

    PoC-H1 FAIL 后激活：替代 HF gated dataset 用本地 yaml.
    """

    yaml_path: Path | None = None
    _tasks_cache: list[GaiaFallbackTaskMeta] = field(default_factory=list)

    def load(self) -> list[GaiaFallbackTaskMeta]:
        """Lazy load 5 个 fallback task（含 FR-E04 分层验证）."""
        if not self._tasks_cache:
            self._tasks_cache = load_fallback_tasks(self.yaml_path)
        return self._tasks_cache

    def evaluate(self, task: GaiaFallbackTaskMeta, actual_answer: str) -> bool:
        """评分单个 task：normalized 字符串匹配（spec FR-E03）.

        Phase B 主路径；Phase D T-D-6 升级时增加 LLM-judge fallback（spec FR-B03）.
        """
        return match_answer(actual_answer, task)
