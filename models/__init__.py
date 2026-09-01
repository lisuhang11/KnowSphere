"""模型工厂对外入口：按目录注册 Chat / Embedding / Rerank builder。"""

from models.base import (
    create_chat_model,
    create_embeddings,
    create_reranker,
    create_vlm_model,
    register_provider,
)
from models.openai_compat import make_chat_builder, make_embeddings_builder
from models.providers import PROVIDERS
from models.rerank import make_rerank_builder

# 旧 source=openai_compatible 运行时仍能解析
_generic = next(p for p in PROVIDERS if p.id == "generic")
register_provider(
    "openai_compatible",
    chat=make_chat_builder("generic"),
    embeddings=make_embeddings_builder("generic"),
    rerank=make_rerank_builder("generic", _generic.rerank_style),
)

for _spec in PROVIDERS:
    register_provider(
        _spec.id,
        chat=make_chat_builder(_spec.id) if "KnowledgeQA" in _spec.types or "VLLM" in _spec.types else None,
        embeddings=make_embeddings_builder(_spec.id) if "Embedding" in _spec.types else None,
        rerank=make_rerank_builder(_spec.id, _spec.rerank_style)
        if "Rerank" in _spec.types and _spec.rerank_style != "none"
        else None,
    )

__all__ = [
    "create_chat_model",
    "create_embeddings",
    "create_reranker",
    "create_vlm_model",
    "register_provider",
]
