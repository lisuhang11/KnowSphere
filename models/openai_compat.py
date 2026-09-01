"""OpenAI 兼容 Chat / Embedding（LangChain ChatOpenAI / OpenAIEmbeddings）。

SiliconFlow、DashScope compatible-mode、DeepSeek、Ollama /v1 等都走这里。
Chat 的 extra_body 仅对需要它的厂商注入，避免官方 OpenAI 因未知字段 400。
"""

from __future__ import annotations

from typing import Any

import httpx
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from config.settings import settings
from models.providers import default_base_url

_TYPE_BY_CAPABILITY = {
    "chat": "KnowledgeQA",
    "vlm": "VLLM",
    "embeddings": "Embedding",
}


def ollama_openai_base_url() -> str:
    return settings.ollama_base_url.rstrip("/") + "/v1"


def _api_key(provider: str | None, override: str | None) -> str:
    """空 key 兜底为 EMPTY / ollama：保证无密钥环境可构造，真实调用才 401。"""
    if override is not None and str(override).strip():
        return str(override).strip()
    if provider == "siliconflow" and settings.siliconflow_api_key:
        return settings.siliconflow_api_key
    if provider == "ollama":
        return "ollama"
    return "EMPTY"


def _fallback_base_url(provider: str | None, capability: str) -> str:
    if provider == "ollama":
        return ollama_openai_base_url()
    typed = _TYPE_BY_CAPABILITY.get(capability, "KnowledgeQA")
    url = default_base_url(provider, typed)
    if url:
        return url
    if provider == "siliconflow":
        return settings.siliconflow_base_url
    return settings.siliconflow_base_url


def _chat_extra_body(provider: str | None, explicit: Any) -> dict[str, Any] | None:
    if explicit is not None:
        return explicit
    # SiliconFlow / 阿里云 Qwen 混思模型：默认关 thinking，避免非流式首 token 极慢
    if provider in ("siliconflow", "aliyun"):
        return {"enable_thinking": False}
    return None


def build_chat(provider: str | None = None, **kwargs) -> ChatOpenAI:
    extra_body = _chat_extra_body(provider, kwargs.pop("extra_body", None))
    timeout = kwargs.pop("timeout", settings.chat_llm_timeout_sec)
    if isinstance(timeout, (int, float)):
        timeout = httpx.Timeout(
            connect=20.0, read=float(timeout), write=60.0, pool=20.0
        )
    chat_kwargs: dict[str, Any] = {
        "model": kwargs.pop("model", settings.chat_model),
        "api_key": _api_key(provider, kwargs.pop("api_key", None)),
        "base_url": kwargs.pop("base_url", None) or _fallback_base_url(provider, "chat"),
        "temperature": kwargs.pop("temperature", 0.1),
        "timeout": timeout,
        "max_retries": kwargs.pop("max_retries", settings.chat_llm_max_retries),
    }
    if extra_body is not None:
        chat_kwargs["extra_body"] = extra_body
    return ChatOpenAI(**chat_kwargs, **kwargs)


def build_embeddings(provider: str | None = None, **kwargs) -> OpenAIEmbeddings:
    # 非 OpenAI 官方模型不在 tiktoken 表里；langchain-openai 会回退下载 cl100k_base，
    # 国内/离线环境常卡住。Ollama / SiliconFlow / 国产兼容口一律关闭截断检查。
    if "check_embedding_ctx_length" not in kwargs:
        kwargs["check_embedding_ctx_length"] = provider == "openai"
    return OpenAIEmbeddings(
        model=kwargs.pop("model", settings.embedding_model),
        api_key=_api_key(provider, kwargs.pop("api_key", None)),
        base_url=kwargs.pop("base_url", None) or _fallback_base_url(provider, "embeddings"),
        timeout=kwargs.pop("timeout", 120),
        max_retries=kwargs.pop("max_retries", 2),
        **kwargs,
    )


def make_chat_builder(provider_id: str):
    def _builder(**kwargs):
        return build_chat(provider_id, **kwargs)

    return _builder


def make_embeddings_builder(provider_id: str):
    def _builder(**kwargs):
        return build_embeddings(provider_id, **kwargs)

    return _builder
