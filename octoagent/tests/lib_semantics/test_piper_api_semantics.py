"""piper 真库 API 签名钉住（F142 件1 / L4，importorskip 门控）。

钉住对象：F110 踩坑（H1）——`piper_backend.py` 曾错用旧 API
``synthesize(text, buf)``，hermetic Fake 符合我们自定义 Protocol 所以全绿，
真机才炸；双评审而非测试抓到。事后回归（`test_tts_service.py:250+`）用 fake
模块注入 sys.modules 锁签名——钉的是**我们的调用姿势**，piper 升级改 API 时
不会红。本文件补真库半边：直接 inspect 真 `piper.PiperVoice`。

诚实声明（spec 件1 复核表）：piper-tts 是 voice optional extra
（`apps/gateway/pyproject.toml` ``[voice]``），dev venv / CI 默认未装 →
本文件默认 SKIP。价值在装了 piper 的环境（生产近似环境 / F110 式 ephemeral
venv 真验证流程）自动激活：升级 piper 前跑一次全量即可暴露 API 破坏。
fake 签名锁与本文件互补共存：前者恒可跑（钉我们侧），后者门控跑（钉库侧）。

真库消费点（`piper_backend.py:131-155`）：
    from piper import PiperVoice
    self._model = PiperVoice.load(str(model_p))
    voice.synthesize_wav(text, wav_file)   # wav_file: wave.Wave_write
"""

from __future__ import annotations

import inspect

import pytest

piper = pytest.importorskip(
    "piper",
    reason="piper-tts 是 voice optional extra（GPL-3.0，F110 D1），dev venv/CI "
    "默认未装；装了 piper 的环境本钉住自动激活",
)


def test_piper_voice_exposes_load_and_synthesize_wav() -> None:
    """`piper_backend.py` 依赖的两个入口在真库上存在。"""
    voice_cls = piper.PiperVoice
    assert hasattr(voice_cls, "load"), "PiperVoice.load 不存在——加载入口 API 破坏"
    assert hasattr(voice_cls, "synthesize_wav"), (
        "PiperVoice.synthesize_wav 不存在——F110 H1 修复所依赖的 API 被移除/改名"
    )


def test_synthesize_wav_business_params_are_text_and_wav_file() -> None:
    """unbound 签名剔除 self 后前两个业务参数为 (text, wav_file)。

    （Codex spec review P3：unbound 方法首参是 self，不加载真实语音模型即可
    检查签名；只钉我们传参的前两位，不锁库自由追加的可选参数。）
    """
    sig = inspect.signature(piper.PiperVoice.synthesize_wav)
    business = [p.name for p in sig.parameters.values() if p.name != "self"]
    assert business[:2] == ["text", "wav_file"], (
        f"synthesize_wav 业务参数前两位应为 ['text', 'wav_file']，实际 {business[:2]}"
        "——piper_backend._synthesize_sync 的位置传参会错位"
    )


def test_load_accepts_model_path_positionally() -> None:
    """`PiperVoice.load(str(model_p))` 位置传参姿势可用（首个业务参数存在）。"""
    sig = inspect.signature(piper.PiperVoice.load)
    params = [
        p
        for p in sig.parameters.values()
        if p.name not in {"self", "cls"}
    ]
    assert params, "PiperVoice.load 应至少接受一个模型路径参数"
    first = params[0]
    assert first.kind in (
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    ), f"load 首参 {first.name!r} 不可位置传参——piper_backend 调用姿势会破"
