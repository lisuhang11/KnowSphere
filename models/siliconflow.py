"""兼容旧 import：SiliconFlow 实现已并入 OpenAI 兼容层。"""

from models.openai_compat import build_chat as _build_chat
from models.openai_compat import build_embeddings as _build_embeddings
from models.rerank import OpenAICompatReranker as SiliconFlowReranker
from models.rerank import build_openai_reranker as _build_reranker


def build_chat(**kwargs):
    return _build_chat("siliconflow", **kwargs)


def build_embeddings(**kwargs):
    return _build_embeddings("siliconflow", **kwargs)


def build_reranker(**kwargs):
    return _build_reranker("siliconflow", **kwargs)


__all__ = [
    "SiliconFlowReranker",
    "build_chat",
    "build_embeddings",
    "build_reranker",
]
