"""model_credentials 单元测试。"""

from __future__ import annotations

import pytest

from utils.model_credentials import PLACEHOLDER_KEYS, model_has_usable_key, validate_model_for_use


def test_placeholder_keys():
    assert "sk-xxx" in PLACEHOLDER_KEYS
    assert "pk-lf-xxx" in PLACEHOLDER_KEYS
    assert "sk-lf-xxx" in PLACEHOLDER_KEYS
    assert "EMPTY" in PLACEHOLDER_KEYS

def test_validate_bare_name_without_env(monkeypatch):
    monkeypatch.setattr("config.settings.settings.siliconflow_api_key", "")
    with pytest.raises(ValueError, match="裸模型名"):
        validate_model_for_use("BAAI/bge-m3", "Embedding", label="向量化")

def test_model_has_usable_key_env_fallback(monkeypatch):
    monkeypatch.setattr("config.settings.settings.siliconflow_api_key", "sk-real-key")
    monkeypatch.setattr(
        "utils.model_credentials.get_active_model",
        lambda ref, mtype: None,
    )
    assert model_has_usable_key(None, "KnowledgeQA")


def test_local_ollama_does_not_require_api_key():
    from utils.model_credentials import _key_from_record

    rec = {
        "source": "local",
        "parameters": {"provider": "ollama", "model": "llama3.2"},
    }
    assert _key_from_record(rec) == "ollama"


def test_generic_remote_allows_empty_key():
    from utils.model_credentials import _key_from_record

    rec = {
        "source": "remote",
        "parameters": {"provider": "generic", "base_url": "http://localhost:8000/v1"},
    }
    assert _key_from_record(rec) == "EMPTY"
