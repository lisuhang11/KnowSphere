"""Rerank 适配：默认 OpenAI/Cohere 风格 /rerank；阿里云 DashScope 用独立请求体。"""

from __future__ import annotations

from typing import Any

import httpx

from config.settings import settings
from models.providers import default_base_url

_ALIYUN_RERANK_URL = (
    "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
)


def _api_key(provider: str | None, override: str | None) -> str:
    if override is not None and str(override).strip():
        return str(override).strip()
    if provider == "siliconflow" and settings.siliconflow_api_key:
        return settings.siliconflow_api_key
    return "EMPTY"


def rerank_endpoint(base_url: str) -> str:
    """已是完整 rerank 路径则原样 POST，否则拼 /rerank。"""
    url = (base_url or "").rstrip("/")
    if not url:
        return url
    lower = url.lower()
    if lower.endswith("/rerank") or "text-rerank" in lower:
        return url
    return f"{url}/rerank"


class OpenAICompatReranker:
    """POST {base}/rerank（Cohere 风格）：SiliconFlow / Jina / 智谱 / 千帆等。"""

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 30.0,
        provider: str | None = None,
    ):
        self.provider = provider
        self.model = model or settings.rerank_model
        self.api_key = _api_key(provider, api_key)
        fallback = default_base_url(provider, "Rerank") or settings.siliconflow_base_url
        self.base_url = (base_url or fallback).rstrip("/")
        self.timeout = timeout

    def rerank(
        self, query: str, documents: list[str], top_n: int | None = None
    ) -> list[dict[str, Any]]:
        if not documents:
            return []
        body: dict[str, Any] = {
            "model": self.model,
            "query": query,
            "documents": documents,
            "return_documents": False,
        }
        if top_n is not None:
            body["top_n"] = top_n
        resp = httpx.post(
            rerank_endpoint(self.base_url),
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=body,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        return [
            {"index": int(r["index"]), "relevance_score": float(r["relevance_score"])}
            for r in results
        ]


class AliyunReranker:
    """DashScope 原生 rerank：{input: {query, documents}, parameters: {top_n}}。"""

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 30.0,
        provider: str | None = None,
    ):
        self.model = model or settings.rerank_model
        self.api_key = _api_key(provider, api_key)
        self.base_url = (base_url or _ALIYUN_RERANK_URL).rstrip("/")
        self.timeout = timeout

    def rerank(
        self, query: str, documents: list[str], top_n: int | None = None
    ) -> list[dict[str, Any]]:
        if not documents:
            return []
        n = top_n if top_n is not None else len(documents)
        body = {
            "model": self.model,
            "input": {"query": query, "documents": documents},
            "parameters": {"return_documents": False, "top_n": n},
        }
        resp = httpx.post(
            rerank_endpoint(self.base_url) if "text-rerank" not in self.base_url else self.base_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=body,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        payload = resp.json()
        raw = (payload.get("output") or {}).get("results") or payload.get("results") or []
        return [
            {"index": int(r["index"]), "relevance_score": float(r["relevance_score"])}
            for r in raw
        ]


def build_openai_reranker(provider: str | None = None, **kwargs) -> OpenAICompatReranker:
    kwargs.setdefault("provider", provider)
    return OpenAICompatReranker(**kwargs)


def build_aliyun_reranker(provider: str | None = None, **kwargs) -> AliyunReranker:
    kwargs.setdefault("provider", provider or "aliyun")
    return AliyunReranker(**kwargs)


def make_rerank_builder(provider_id: str, style: str):
    if style == "aliyun":
        def _aliyun(**kwargs):
            return build_aliyun_reranker(provider_id, **kwargs)

        return _aliyun

    def _openai(**kwargs):
        return build_openai_reranker(provider_id, **kwargs)

    return _openai
