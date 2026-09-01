"""模型 Provider 目录与运行时解析。"""

from models.providers import (
    default_base_url,
    list_remote_providers,
    normalize_provider,
    runtime_provider,
)


def test_normalize_alias():
    assert normalize_provider("openai_compatible") == "generic"
    assert normalize_provider("siliconflow") == "siliconflow"


def test_runtime_provider_local_is_ollama():
    assert runtime_provider("local", {}) == "ollama"
    assert runtime_provider("local", {"provider": "siliconflow"}) == "ollama"


def test_runtime_provider_remote_and_legacy_source():
    assert runtime_provider("remote", {"provider": "aliyun"}) == "aliyun"
    assert runtime_provider("siliconflow", {}) == "siliconflow"
    assert runtime_provider("openai_compatible", {}) == "generic"


def test_remote_catalog_excludes_ollama():
    ids = {p.id for p in list_remote_providers()}
    assert "ollama" not in ids
    assert "siliconflow" in ids
    assert "aliyun" in ids
    assert "generic" in ids


def test_filter_by_type():
    rerank = {p.id for p in list_remote_providers("Rerank")}
    assert "jina" in rerank
    assert "aliyun" in rerank
    assert "deepseek" not in rerank
    chat = {p.id for p in list_remote_providers("KnowledgeQA")}
    assert "deepseek" in chat
    assert "jina" not in chat


def test_aliyun_rerank_default_url_is_native_endpoint():
    url = default_base_url("aliyun", "Rerank")
    assert "text-rerank" in url
    assert default_base_url("aliyun", "KnowledgeQA").endswith("/compatible-mode/v1")
