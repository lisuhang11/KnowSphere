"""模型工厂对外入口：注册内置 provider。新增 provider 时在此 import 并注册。"""

from models.base import create_chat_model, create_embeddings, create_reranker, create_vlm_model, register_provider
from models.siliconflow import build_chat, build_embeddings, build_reranker

register_provider(
    "siliconflow", chat=build_chat, embeddings=build_embeddings, rerank=build_reranker
)
# OpenAI 兼容接口（OpenAI/Azure 之外的任意兼容服务）复用 SiliconFlow 的 builder
register_provider(
    "openai_compatible", chat=build_chat, embeddings=build_embeddings, rerank=build_reranker
)

__all__ = [
    "create_chat_model",
    "create_vlm_model",
    "create_embeddings",
    "create_reranker",
    "register_provider",
]
