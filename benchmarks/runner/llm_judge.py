"""
benchmarks/runner/llm_judge.py

LLM judge 触发逻辑模块（F-01 patch：触发常量在 Phase A 直接落地，不是 stub）.
Phase D T-D-6 升级：``invoke_judge`` 增加 ``adapter`` 注入点；提供 ``StubJudgeAdapter``
和 ``ProviderRouterJudgeAdapter``（真实 Sonnet 4.5 temperature=0 路径）。

触发常量（known-issues F-01 锁死，任何修改必须 Codex review 拦下）：
- ``LLM_JUDGE_TRIGGER_MIN_RATIO = 0.5``
- ``LLM_JUDGE_TRIGGER_MAX_RATIO = 1.0``
- ``LLM_JUDGE_MAX_CALLS_PER_TASK = 2``

设计要点（Phase D）：
- 默认 ``invoke_judge`` 行为不变（继续返回 stub）——单测/CI 不必依赖 LLM key
- caller 通过 ``adapter`` 注入 ``ProviderRouterJudgeAdapter`` 启用真路径
- Provider 调用失败（network / quota）回退 stub 中间分 0.5（degrade gracefully）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

# F-01 patch：触发常量真实落地（Phase A 不是 stub，Phase D 仍不动）
LLM_JUDGE_TRIGGER_MIN_RATIO = 0.5  # match_ratio >= 0.5 才触发 LLM judge
LLM_JUDGE_TRIGGER_MAX_RATIO = 1.0  # match_ratio < 1.0 才触发（= 1.0 即全通过，直接 PASS）
LLM_JUDGE_MAX_CALLS_PER_TASK = 2   # 每 task × iteration 最多调用 2 次 LLM judge

# Phase D：默认评分模型（控变量 Sonnet 4.5，spec FR-H02）
DEFAULT_JUDGE_MODEL = "claude-sonnet-4-5"
DEFAULT_JUDGE_TEMPERATURE = 0.0
DEFAULT_JUDGE_MAX_TOKENS = 512


JUDGE_SYSTEM_PROMPT = (
    "You are an LLM judge scoring an OctoAgent benchmark task's partial credit.\n"
    "Given (1) the task prompt, (2) expected event signals, (3) actual events the\n"
    "agent emitted, output a single floating point score in [0.0, 1.0]:\n"
    "  - 1.0 means actual events fully satisfy the task intent\n"
    "  - 0.5 means partial completion (matches the trigger boundary)\n"
    "  - 0.0 means actual events miss the intent entirely\n"
    "Respond with JUST the number on the first line, followed by a one-line reasoning.\n"
)


@dataclass
class JudgeResult:
    """LLM judge 返回结果"""

    score: float          # 0.0 ~ 1.0，越高越好
    reasoning: str        # judge 的评分理由
    is_stub: bool = False  # Phase A stub / Phase D fallback 标志


# ---------------------------------------------------------------------------
# Adapter Protocol & 内置实现
# ---------------------------------------------------------------------------


@runtime_checkable
class JudgeAdapter(Protocol):
    """LLM judge 后端协议。返回 JudgeResult。

    Phase D 起 ``LLMJudgeTrigger.invoke_judge`` 接受 ``adapter`` 参数；
    不传时用 ``StubJudgeAdapter``（Phase A 默认行为）。
    """

    def judge(
        self,
        *,
        task_id: str,
        prompt: str,
        expected_events: list[dict[str, Any]],
        actual_events: list[dict[str, Any]],
        match_ratio: float,
    ) -> JudgeResult: ...


@dataclass(frozen=True, slots=True)
class StubJudgeAdapter:
    """Phase A 默认 stub：返回固定 score=0.5（CI / 离线单测用）。"""

    def judge(
        self,
        *,
        task_id: str,
        prompt: str,
        expected_events: list[dict[str, Any]],
        actual_events: list[dict[str, Any]],
        match_ratio: float,
    ) -> JudgeResult:
        return JudgeResult(
            score=0.5,
            reasoning=(
                f"[Stub] task_id={task_id}, match_ratio={match_ratio:.3f}, "
                f"expected={len(expected_events)} actual={len(actual_events)}. "
                "Real LLM judge unavailable (no adapter wired)."
            ),
            is_stub=True,
        )


@dataclass(slots=True)
class ProviderRouterJudgeAdapter:
    """Phase D 真实 LLM 路径（Sonnet 4.5 temperature=0）。

    通过依赖注入接受一个 ``chat_fn``（sync callable 或抛错时回退 stub），避免
    benchmarks/ 包硬依赖 octoagent.provider。caller 在 Phase E baseline 跑前
    wire 真 ProviderRouter；单测可注入 fake chat_fn。

    ``chat_fn(messages, model, temperature, max_tokens) -> str``
    """

    chat_fn: Any  # Callable[[list[dict], str, float, int], str]
    model: str = DEFAULT_JUDGE_MODEL
    temperature: float = DEFAULT_JUDGE_TEMPERATURE
    max_tokens: int = DEFAULT_JUDGE_MAX_TOKENS

    def judge(
        self,
        *,
        task_id: str,
        prompt: str,
        expected_events: list[dict[str, Any]],
        actual_events: list[dict[str, Any]],
        match_ratio: float,
    ) -> JudgeResult:
        try:
            user_prompt = _build_user_prompt(
                prompt=prompt,
                expected_events=expected_events,
                actual_events=actual_events,
                match_ratio=match_ratio,
            )
            messages = [
                {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]
            text = self.chat_fn(
                messages,
                self.model,
                self.temperature,
                self.max_tokens,
            )
            score, reasoning = _parse_judge_response(text)
        except Exception as exc:
            logger.warning(
                "judge_adapter_failed_fallback_to_stub",
                extra={"task_id": task_id, "error": repr(exc)},
            )
            return JudgeResult(
                score=0.5,
                reasoning=f"[Fallback stub] LLM judge call failed: {exc!r}",
                is_stub=True,
            )

        return JudgeResult(
            score=_clip_score(score),
            reasoning=reasoning or f"(no reasoning) match_ratio={match_ratio:.3f}",
            is_stub=False,
        )


def _build_user_prompt(
    *,
    prompt: str,
    expected_events: list[dict[str, Any]],
    actual_events: list[dict[str, Any]],
    match_ratio: float,
) -> str:
    """组装 user 消息（截断防 token 暴炸）。"""
    expected_str = _truncate_json(expected_events, max_chars=1200)
    actual_str = _truncate_json(actual_events, max_chars=1200)
    return (
        f"task_prompt:\n{prompt[:600]}\n\n"
        f"expected_events ({len(expected_events)}):\n{expected_str}\n\n"
        f"actual_events ({len(actual_events)}):\n{actual_str}\n\n"
        f"match_ratio (exact-event basis): {match_ratio:.3f}\n"
        "Score 0.0..1.0:"
    )


def _truncate_json(items: list[dict[str, Any]], *, max_chars: int) -> str:
    """简单 dict-list 截断序列化（避免 token bomb）。"""
    import json

    text = json.dumps(items, ensure_ascii=False, indent=1)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n…[truncated {len(text) - max_chars} chars]"


def _parse_judge_response(text: str) -> tuple[float, str]:
    """从 LLM 回复抽 score + reasoning（首行数字，后续行 reasoning）。"""
    stripped = text.strip()
    if not stripped:
        raise ValueError("empty judge response")
    lines = stripped.splitlines()
    score_line = lines[0].strip()
    try:
        score = float(score_line)
    except ValueError as exc:
        raise ValueError(f"judge response head not float: {score_line!r}") from exc
    reasoning = "\n".join(lines[1:]).strip()
    return score, reasoning


def _clip_score(score: float) -> float:
    """clamp 到 [0.0, 1.0]。

    HIGH-4 fix：NaN / inf 防护——non-finite 时返回 stub 中间分 0.5
    （未 finite 的 score 会让 reporter weighted_score 计算抛 ValueError 或 NaN 污染）。
    """
    import math

    if not math.isfinite(score):
        return 0.5
    if score < 0.0:
        return 0.0
    if score > 1.0:
        return 1.0
    return float(score)


# ---------------------------------------------------------------------------
# LLMJudgeTrigger（升级版：adapter 注入）
# ---------------------------------------------------------------------------


@dataclass
class LLMJudgeTrigger:
    """
    LLM judge 触发器。

    职责：
    1. should_trigger_judge：根据 match_ratio 决定是否触发（真实判断，F-01 patch）
    2. invoke_judge：分发到 adapter（默认 StubJudgeAdapter；可注入真实 LLM）
    3. 每 task 最多调用 LLM_JUDGE_MAX_CALLS_PER_TASK 次（成本控制）
    """

    adapter: JudgeAdapter = field(default_factory=StubJudgeAdapter)
    _call_count: int = field(default=0, init=False)

    def should_trigger_judge(self, match_ratio: float) -> bool:
        """
        判断是否应触发 LLM judge。

        触发条件（F-01 patch 强制实现，不是 stub）：
        - match_ratio >= LLM_JUDGE_TRIGGER_MIN_RATIO（0.5）
        - match_ratio < LLM_JUDGE_TRIGGER_MAX_RATIO（1.0）
        - 未超过每 task 最大调用次数（LLM_JUDGE_MAX_CALLS_PER_TASK=2）

        边界行为：
        - match_ratio < 0.5：直接 FAIL，不触发（事件匹配不足，无需 judge）
        - match_ratio = 1.0：直接 PASS，不触发（全部命中，无需 judge）
        - match_ratio in [0.5, 1.0)：触发 judge（partial 场景）
        """
        if self._call_count >= LLM_JUDGE_MAX_CALLS_PER_TASK:
            return False
        return LLM_JUDGE_TRIGGER_MIN_RATIO <= match_ratio < LLM_JUDGE_TRIGGER_MAX_RATIO

    def invoke_judge(
        self,
        task_id: str,
        prompt: str,
        expected_events: list[dict[str, Any]],
        actual_events: list[dict[str, Any]],
        match_ratio: float,
    ) -> JudgeResult:
        """调用 LLM judge 评分（通过 adapter 后端）。

        Phase D：默认 adapter=StubJudgeAdapter（行为与 Phase A 一致）；
        caller 通过 ``LLMJudgeTrigger(adapter=ProviderRouterJudgeAdapter(...))``
        启用真实 Sonnet 4.5 路径。
        """
        if self._call_count >= LLM_JUDGE_MAX_CALLS_PER_TASK:
            # 超出最大调用次数，返回最后一次结果（使用默认中间分）
            return JudgeResult(
                score=0.5,
                reasoning=f"超出最大调用次数 {LLM_JUDGE_MAX_CALLS_PER_TASK}，使用默认中间分",
                is_stub=True,
            )

        self._call_count += 1

        return self.adapter.judge(
            task_id=task_id,
            prompt=prompt,
            expected_events=expected_events,
            actual_events=actual_events,
            match_ratio=match_ratio,
        )

    def reset_call_count(self) -> None:
        """重置调用计数（每个新 task iteration 开始前调用）"""
        self._call_count = 0


__all__ = (
    "LLM_JUDGE_TRIGGER_MIN_RATIO",
    "LLM_JUDGE_TRIGGER_MAX_RATIO",
    "LLM_JUDGE_MAX_CALLS_PER_TASK",
    "DEFAULT_JUDGE_MODEL",
    "DEFAULT_JUDGE_TEMPERATURE",
    "DEFAULT_JUDGE_MAX_TOKENS",
    "JUDGE_SYSTEM_PROMPT",
    "JudgeResult",
    "JudgeAdapter",
    "StubJudgeAdapter",
    "ProviderRouterJudgeAdapter",
    "LLMJudgeTrigger",
)
