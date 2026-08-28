"""SiliconFlow 实现：chat 与 embedding 走 OpenAI 兼容接口（langchain-openai）。

rerank 走 SiliconFlow 自有 /v1/rerank 端点（Cohere 风格），无 langchain 标准
封装，用 httpx 直调。
"""

from __future__ import annotations

from typing import Any

import httpx
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from config.settings import settings

def _api_key(override: str | None) -> str:
    """空 key 兜底为 "EMPTY"：保证无密钥环境（CI/导入）可构造，真实调用才报 401。"""
    key = override if override is not None else settings.siliconflow_api_key
    return key or "EMPTY"

def build_chat(**kwargs) -> ChatOpenAI:
    # 混合推理模型（Qwen3 等）在此默认关闭思考流；对 Qwen2.5 等纯推理
    # 模型该参数会被 SiliconFlow 忽略，无副作用
    extra_body = kwargs.pop("extra_body", {"enable_thinking": False})
    return ChatOpenAI(
        model=kwargs.pop("model", settings.chat_model),
        api_key=_api_key(kwargs.pop("api_key", None)),
        base_url=kwargs.pop("base_url", settings.siliconflow_base_url),
        temperature=kwargs.pop("temperature", 0.1),
        extra_body=extra_body,
        # openai SDK 默认 timeout=600s：网络偶发 stall 会让 agent 的 run
        # 挂住几分钟，收紧到 60s（流式首 token 远快于此）
        timeout=kwargs.pop("timeout", 60),
        max_retries=kwargs.pop("max_retries", 2),
        **kwargs,
    )

def build_embeddings(**kwargs) -> OpenAIEmbeddings:
    # SiliconFlow 上的 bge-m3 等模型不在 tiktoken 模型表里；langchain-openai
    # 会回退下载 cl100k_base（Azure blob），国内/离线环境常 Connection reset，
    # 表现为向量化阶段长时间卡住。非 OpenAI 官方 embedding 无需按 token 截断。
    kwargs.setdefault("check_embedding_ctx_length", False)
    return OpenAIEmbeddings(
        model=kwargs.pop("model", settings.embedding_model),
        api_key=_api_key(kwargs.pop("api_key", None)),
        base_url=kwargs.pop("base_url", settings.siliconflow_base_url),
        timeout=kwargs.pop("timeout", 120),
        max_retries=kwargs.pop("max_retries", 2),
        **kwargs,
    )

class SiliconFlowReranker:
    """SiliconFlow 重排器：POST {base_url}/rerank。

    请求体（Cohere 风格）: {model, query, documents, top_n, return_documents}
    响应 results: [{"index": 指向 documents 下标, "relevance_score": 相关性}]
    """

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 30.0,
    ):
        self.model = model or settings.rerank_model
        self.api_key = _api_key(api_key)
        self.base_url = (base_url or settings.siliconflow_base_url).rstrip("/")
        self.timeout = timeout

    def rerank(
        self, query: str, documents: list[str], top_n: int | None = None
    ) -> list[dict[str, Any]]:
        """对 documents 按 query 相关性重排，返回 [{index, relevance_score}] 降序。"""
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
            f"{self.base_url}/rerank",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=body,
            timeout=self.timeout,
        )
        resp.raise_for_status
        results = resp.json.get("results", [])
        return [
            {"index": int(r["index"]), "relevance_score": float(r["relevance_score"])}
            for r in results
        ]

def build_reranker(**kwargs) -> SiliconFlowReranker:
    return SiliconFlowReranker(**kwargs)
