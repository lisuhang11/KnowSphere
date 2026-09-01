"""Chat extra_body 只打给需要它的厂商，避免官方 OpenAI 400。"""

from unittest.mock import patch

from models.openai_compat import build_chat


def test_siliconflow_chat_disables_thinking():
    with patch("models.openai_compat.ChatOpenAI") as cls:
        build_chat("siliconflow", model="Qwen/Qwen3-32B", api_key="k")
    kwargs = cls.call_args.kwargs
    assert kwargs["extra_body"] == {"enable_thinking": False}


def test_openai_chat_has_no_extra_body():
    with patch("models.openai_compat.ChatOpenAI") as cls:
        build_chat("openai", model="gpt-4o-mini", api_key="k")
    kwargs = cls.call_args.kwargs
    assert "extra_body" not in kwargs


def test_ollama_chat_uses_local_base_and_dummy_key():
    with patch("models.openai_compat.ChatOpenAI") as cls:
        build_chat("ollama", model="llama3.2")
    kwargs = cls.call_args.kwargs
    assert kwargs["api_key"] == "ollama"
    assert kwargs["base_url"].endswith("/v1")
    assert "extra_body" not in kwargs
