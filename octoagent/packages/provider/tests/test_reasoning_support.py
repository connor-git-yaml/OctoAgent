from octoagent.provider.reasoning_support import supports_reasoning


def test_openai_codex_always_supports_reasoning() -> None:
    assert supports_reasoning("openai-codex", "gpt-5.4") is True


def test_openrouter_qwen_alias_is_treated_as_unsupported() -> None:
    assert supports_reasoning("openrouter", "qwen/qwen3.5-9b") is False


def test_openrouter_reasoning_family_is_supported() -> None:
    assert supports_reasoning("openrouter", "deepseek/deepseek-r1") is True
