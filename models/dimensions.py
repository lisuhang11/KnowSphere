"""Embedding 模型维度注册表：为"每个知识库独立 embedding 模型"提供维度推导。

维度决定 chunks 表写入哪一列（默认 1024 维写 embedding 列，其他写 embedding_{dim} 列），
因此创建知识库时必须确定。解析优先级：
1. 内置注册表（零成本，覆盖主流模型）；
2. 未收录模型：创建时实测一次（embed 探测文本取向量长度，进程内缓存）；
3. 实测失败（网络/密钥问题）→ 报错，禁止创建，避免把未知维度写进库。
"""

from __future__ import annotations

import logging

from config.settings import settings
from models.base import create_embeddings

logger = logging.getLogger(__name__)

# 已收录模型的输出维度（SiliconFlow 及常见开源模型）
EMBEDDING_DIMENSIONS: dict[str, int] = {
    "BAAI/bge-m3": 1024,
    "BAAI/bge-large-zh-v1.5": 1024,
    "BAAI/bge-large-en-v1.5": 1024,
    "BAAI/bge-base-zh-v1.5": 768,
    "BAAI/bge-base-en-v1.5": 768,
    "BAAI/bge-small-zh-v1.5": 512,
    "BAAI/bge-small-en-v1.5": 384,
    "Qwen/Qwen3-Embedding-0.6B": 1024,
    "Qwen/Qwen3-Embedding-4B": 2560,
    "Pro/BAAI/bge-m3": 1024,
}

# pgvector HNSW 索引的向量维度上限（超出只能建 IVF，项目不维护该路径）
MAX_HNSW_DIM = 2000

# 探测文本：取一个 token，返回维度即模型输出维度
_DIM_PROBE_TEXT = "dimension"

# 实测结果缓存（进程内，避免重复探测）
_detected: dict[str, int] = {}

def _validate_dim(model_id: str, dim: int) -> int:
    """维度合法性校验：异常值或超 pgvector HNSW 上限都拒绝。"""
    if dim <= 0:
        raise ValueError(f"embedding 模型 {model_id!r} 返回异常维度: {dim}")
    if dim > MAX_HNSW_DIM:
        raise ValueError(
            f"embedding 模型 {model_id!r} 输出维度 {dim} 超过 pgvector HNSW 索引上限 "
            f"{MAX_HNSW_DIM}，无法创建知识库/检索。请换用低维模型，"
            f"如 BAAI/bge-m3（1024 维）或 Qwen/Qwen3-Embedding-0.6B（1024 维）。"
        )
    return dim

def _resolve_model_id(ref: str) -> tuple[str, int | None]:
    """models 表 ID -> (实际模型名, 创建时登记的维度|None)。

    模型管理上线后知识库的 embedding_model_id 存的是 models 表 ID，
    需先解析出实际模型名与可选维度，再走注册表/实测。
    """
    from utils.model_store import ModelStore

    rec = ModelStore().get_model(ref)
    if rec is None:
        raise ValueError(f"embedding 模型不存在或已删除: {ref}")
    if rec["type"] != "Embedding":
        raise ValueError(f"模型 {rec['display_name']} 类型为 {rec['type']}，需要 Embedding")
    params = rec.get("parameters") or {}
    name = params.get("model") or settings.embedding_model
    return name, params.get("dimensions")

def resolve_embedding_dim(model_id: str) -> int:
    """解析 embedding 模型的输出维度；无法确定或超 HNSW 上限时抛 ValueError。"""
    model_id = (model_id or settings.embedding_model).strip()
    preset_dim: int | None = None
    if model_id.startswith("model-"):
        model_id, preset_dim = _resolve_model_id(model_id)
    if preset_dim:
        return _validate_dim(model_id, preset_dim)
    dim = EMBEDDING_DIMENSIONS.get(model_id) or _detected.get(model_id)
    if dim:
        # 注册表/缓存命中同样要过上限校验（如 Qwen3-Embedding-4B=2560）
        return _validate_dim(model_id, dim)
    try:
        embeddings = create_embeddings(model=model_id)
        dim = len(embeddings.embed_query(_DIM_PROBE_TEXT))
    except Exception as exc:
        raise ValueError(
            f"无法确定 embedding 模型 {model_id!r} 的维度：注册表未收录且实测失败。"
            f"请确认模型名正确且 embedding API 可用，或在 models/dimensions.py 中登记。"
        ) from exc
    _detected[model_id] = _validate_dim(model_id, dim)
    logger.info("embedding 模型 %s 实测维度=%d", model_id, dim)
    return dim
