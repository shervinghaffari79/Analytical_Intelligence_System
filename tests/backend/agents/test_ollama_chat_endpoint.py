"""The ``ollama_chat`` endpoint: native /api/chat, with thinking off by default.

Measured against Ollama: pointing the ``openai`` endpoint at /v1 silently
ignores the ``think`` flag, so a reasoning model cannot be quietened and spends
its whole budget reasoning instead of reaching a tool call. /api/chat honours
it, and LiteLLM derives the flag from ``reasoning_effort``.
"""

import pytest

from data_formulator.agents.client_utils import (
    Client,
    _DEFAULT_OLLAMA_NUM_CTX,
    _apply_ollama_num_ctx,
    _apply_ollama_think,
)


@pytest.mark.parametrize("given, expected", [
    (None, "http://localhost:11434"),
    ("http://localhost:11434", "http://localhost:11434"),
    # LiteLLM appends the path itself, so any of these must be trimmed back to
    # the bare host or the request lands on /api/chat/api/chat.
    ("http://localhost:11434/", "http://localhost:11434"),
    ("http://localhost:11434/v1", "http://localhost:11434"),
    ("http://localhost:11434/api", "http://localhost:11434"),
    ("http://localhost:11434/api/chat", "http://localhost:11434"),
    ("http://ollama.internal:11434/v1/", "http://ollama.internal:11434"),
])
def test_api_base_is_normalised_to_the_bare_host(given, expected):
    client = Client("ollama_chat", "qwen3.5:4b", api_base=given)
    assert client.params["api_base"] == expected


def test_model_gets_the_ollama_chat_prefix():
    assert Client("ollama_chat", "qwen3.5:4b").model == "ollama_chat/qwen3.5:4b"


def test_model_prefix_is_not_doubled():
    assert Client("ollama_chat", "ollama_chat/qwen3.5:4b").model == "ollama_chat/qwen3.5:4b"


def test_plain_ollama_endpoint_is_untouched():
    """The older /api/generate provider keeps its own behaviour."""
    client = Client("ollama", "qwen3.5:4b")
    assert client.model == "ollama/qwen3.5:4b"


def test_thinking_is_off_by_default(monkeypatch):
    """LiteLLM: think = reasoning_effort in {low, medium, high}."""
    monkeypatch.delenv("DF_OLLAMA_THINK", raising=False)
    assert _apply_ollama_think({"reasoning_effort": "low"})["reasoning_effort"] == "none"
    assert _apply_ollama_think({"reasoning_effort": "high"})["reasoning_effort"] == "none"


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes"])
def test_thinking_can_be_switched_back_on(monkeypatch, value):
    monkeypatch.setenv("DF_OLLAMA_THINK", value)
    assert _apply_ollama_think({"reasoning_effort": "none"})["reasoning_effort"] == "low"


@pytest.mark.parametrize("value", ["0", "false", "no", ""])
def test_other_env_values_leave_thinking_off(monkeypatch, value):
    monkeypatch.setenv("DF_OLLAMA_THINK", value)
    assert _apply_ollama_think({"reasoning_effort": "low"})["reasoning_effort"] == "none"


def test_other_params_survive_and_input_is_not_mutated(monkeypatch):
    monkeypatch.delenv("DF_OLLAMA_THINK", raising=False)
    original = {"api_base": "http://localhost:11434", "reasoning_effort": "low"}
    out = _apply_ollama_think(original)
    assert out["api_base"] == "http://localhost:11434"
    assert original["reasoning_effort"] == "low", "caller's dict must not change"


def test_num_ctx_defaults_to_32768(monkeypatch):
    monkeypatch.delenv("DF_OLLAMA_NUM_CTX", raising=False)
    assert _apply_ollama_num_ctx({})["num_ctx"] == _DEFAULT_OLLAMA_NUM_CTX == 32768


def test_num_ctx_is_overridable(monkeypatch):
    monkeypatch.setenv("DF_OLLAMA_NUM_CTX", "65536")
    assert _apply_ollama_num_ctx({})["num_ctx"] == 65536


def test_num_ctx_falls_back_on_garbage_env_value(monkeypatch):
    monkeypatch.setenv("DF_OLLAMA_NUM_CTX", "not-a-number")
    assert _apply_ollama_num_ctx({})["num_ctx"] == _DEFAULT_OLLAMA_NUM_CTX


def test_num_ctx_does_not_mutate_caller_dict(monkeypatch):
    monkeypatch.delenv("DF_OLLAMA_NUM_CTX", raising=False)
    original = {"api_base": "http://localhost:11434"}
    out = _apply_ollama_num_ctx(original)
    assert "num_ctx" not in original
    assert out["num_ctx"] == _DEFAULT_OLLAMA_NUM_CTX


def test_dispatch_sends_num_ctx_on_the_wire(monkeypatch):
    """End-to-end through Client._dispatch, not just the helper in isolation."""
    import litellm
    from unittest.mock import patch

    monkeypatch.delenv("DF_OLLAMA_NUM_CTX", raising=False)
    client = Client("ollama_chat", "qwen3.5:4b", api_base="http://localhost:11434")
    with patch.object(litellm, "completion") as mocked:
        client.get_completion_with_tools(
            [{"role": "user", "content": "hi"}],
            tools=[{"type": "function", "function": {"name": "t", "parameters": {}}}],
            stream=True,
        )
        assert mocked.call_args.kwargs["num_ctx"] == 32768


def test_openai_endpoint_does_not_get_num_ctx(monkeypatch):
    """num_ctx is Ollama-specific wire syntax; sending it to a real OpenAI-
    compatible server that doesn't expect it would be a foot-gun, not a fix."""
    import litellm
    from unittest.mock import patch

    client = Client("openai", "gpt-5.4-nano")
    with patch.object(litellm, "completion") as mocked:
        client.get_completion([{"role": "user", "content": "hi"}], stream=True)
        assert "num_ctx" not in mocked.call_args.kwargs
