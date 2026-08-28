"""模型工厂：多 provider 统一接口。首个实现为 SiliconFlow。

运行时解析顺序（models 表为主，且兼容现有 .env 部署）：
1. 显式传入 models 表 ID（"model-..." 前缀）-> 从 models 表解析参数（优先）
2. 未传模型引用 -> 查 models 表对应 type 的 is_default 模型（若有）
3. 以上均不可用（无 DB / 未配置）-> 回退 .env 静态配置
4. 显式传入裸模型名 -> 不查 DB，直接使用（兼容旧存量/维度探测等场景）
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Optional, Protocol

from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel

from config.settings import settings

logger = logging.getLogger(__name__)

# provider 注册表: {provider: {"chat": fn, "embeddings": fn, "rerank": fn}}
_REGISTRY: dict[str, dict[str, Callable]] = {}

# capability -> models 表 type
_MODEL_TYPE_BY_CAPABILITY = {
    "chat": "KnowledgeQA",
    "embeddings": "Embedding",
    "rerank": "Rerank",
    "vlm": "VLLM",
}

class Reranker(Protocol):
    """重排器统一接口：返回 [{"index": i, "relevance_score": s}]，按相关性降序。"""

    def rerank(self, query: str, documents: list[str], top_n: int | None = None) -> list[dict[str, Any]]: ...

def register_provider(provider: str, chat=None, embeddings=None, rerank=None) -> None:
    _REGISTRY[provider] = {"chat": chat, "embeddings": embeddings, "rerank": rerank}

def _create(provider: str | None, capability: str, **kwargs):
    """按能力从注册表取 builder 并实例化，统一校验与报错。"""
    if provider not in _REGISTRY:
        raise ValueError(f"未知模型 provider: {provider}，已注册: {list(_REGISTRY)}")
    builder = _REGISTRY[provider][capability]
    if builder is None:
        raise ValueError(f"provider '{provider}' 未实现 {capability} 模型")
    return builder(**kwargs)

# ---------------------------------------------------------------------------
# models 表解析（DB 优先、.env 兜底）
# ---------------------------------------------------------------------------

def _resolve_from_db(capability: str, ref: Optional[str]) -> Optional[dict[str, Any]]:
    """按 models 表解析出 {source, model, api_key, base_url}。

    - ref 以 "model-" 开头：按 ID 精确查找，并校验 type 匹配
    - ref 为 None：查找该 type 的 is_default 模型
    - DB 不可用（未建表/连接失败）：静默返回 None，由 .env 兜底
    """
    mtype = _MODEL_TYPE_BY_CAPABILITY[capability]
    try:
        from utils.model_store import ModelStore, is_model_ref

        store = ModelStore()
        if is_model_ref(ref):
            rec = store.get_model(ref)
            if rec is None:
                raise ValueError(f"模型不存在或已删除: {ref}")
            if rec["type"] != mtype:
                raise ValueError(f"模型 {rec['display_name']} 类型为 {rec['type']}，需要 {mtype}")
        else:
            rec = store.get_default_model(mtype)
        if rec is None:
            return None
        params = rec.get("parameters") or {}
        return {
            "source": rec["source"],
            "model": params.get("model"),
            "api_key": params.get("api_key"),
            "base_url": params.get("base_url"),
        }
    except Exception as exc:  # noqa: BLE001 - DB 异常统一降级 .env
        logger.warning("models 表解析失败，回退 .env 配置: %s", exc)
        return None

def _apply_resolved(kwargs: dict[str, Any], resolved: dict[str, Any], ref: Optional[str]) -> tuple[dict[str, Any], Optional[str]]:
    """把 DB 解析结果并入 kwargs；ref 为模型 ID 时替换 model 名，否则 setdefault。"""
    kwargs = dict(kwargs)
    if ref and ref.startswith("model-"):
        kwargs["model"] = resolved["model"]
    else:
        kwargs.setdefault("model", resolved["model"])
    kwargs.setdefault("api_key", resolved["api_key"])
    kwargs.setdefault("base_url", resolved["base_url"])
    return kwargs, resolved["source"]

def create_chat_model(provider: str | None = None, **kwargs) -> BaseChatModel:
    """创建聊天模型实例。provider 缺省时用配置值；模型引用支持 models 表 ID。"""
    ref = kwargs.get("model") if isinstance(kwargs.get("model"), str) else None
    resolved = _resolve_from_db("chat", ref)
    if resolved:
        kwargs, provider = _apply_resolved(kwargs, resolved, ref)
    return _create(provider or settings.chat_provider, "chat", **kwargs)

def create_vlm_model(provider: str | None = None, **kwargs) -> BaseChatModel:
    """创建 VLLM 视觉模型实例（图片理解 / OCR）。"""
    ref = kwargs.get("model") if isinstance(kwargs.get("model"), str) else None
    resolved = _resolve_from_db("vlm", ref)
    if resolved:
        kwargs, provider = _apply_resolved(kwargs, resolved, ref)
    return _create(provider or settings.chat_provider, "chat", **kwargs)

def create_embeddings(provider: str | None = None, **kwargs) -> Embeddings:
    """创建 Embeddings 实例。provider 缺省时用配置值；模型引用支持 models 表 ID。"""
    ref = kwargs.get("model") if isinstance(kwargs.get("model"), str) else None
    resolved = _resolve_from_db("embeddings", ref)
    if resolved:
        kwargs, provider = _apply_resolved(kwargs, resolved, ref)
    return _create(provider or settings.embedding_provider, "embeddings", **kwargs)

def create_reranker(provider: str | None = None, **kwargs) -> Reranker:
    """创建 Reranker 实例。provider 缺省时用配置值；模型引用支持 models 表 ID。"""
    ref = kwargs.get("model") if isinstance(kwargs.get("model"), str) else None
    resolved = _resolve_from_db("rerank", ref)
    if resolved:
        kwargs, provider = _apply_resolved(kwargs, resolved, ref)
    return _create(provider or settings.rerank_provider, "rerank", **kwargs)
