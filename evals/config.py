"""评测运行时配置覆盖（临时 patch settings，线程内生效）。"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from config.settings import settings
from evals.schemas import EvalConfig


def default_metric_layers(suite: str, dataset_id: str = "") -> list[str]:
    """按 suite / 数据集选择默认指标层。"""
    if suite == "rag_quality":
        return ["ragas"]
    if suite == "intent_bench":
        return ["intent"]
    if str(dataset_id).startswith("squad"):
        return ["retrieval", "squad"]
    return ["retrieval", "generation"]

# 允许在 config_overrides 中覆盖的 settings 字段
ALLOWED_OVERRIDE_KEYS = frozenset(
    {
        "chunk_size",
        "chunk_overlap",
        "retrieval_top_k",
        "retrieval_candidate_k",
        "rerank_enabled",
        "rerank_model",
        "mmr_enabled",
        "mmr_lambda",
        "enable_rewrite",
        "multi_query_enabled",
        "multi_query_count",
        "query_expansion_enabled",
        "citation_enabled",
        "chat_model",
        "embedding_model",
    }
)

@contextmanager
def apply_config_overrides(overrides: dict[str, Any] | None):
    """在 with 块内临时覆盖 settings 字段，退出后恢复。"""
    if not overrides:
        yield
        return
    saved: dict[str, Any] = {}
    for key, value in overrides.items():
        if key not in ALLOWED_OVERRIDE_KEYS:
            continue
        if hasattr(settings, key):
            saved[key] = getattr(settings, key)
            setattr(settings, key, value)
    try:
        yield
    finally:
        for key, value in saved.items():
            setattr(settings, key, value)


_EVAL_CHAT_KWARGS = {"temperature": 0, "extra_body": {"enable_thinking": False}}


def eval_chat_model_kwargs(
    config: EvalConfig | None = None,
    extra: dict[str, Any] | None = None,
    *,
    chat_model_id: str | None = None,
) -> dict[str, Any]:
    """评测用聊天模型参数：显式传入 models 表 ID，避免落到系统默认模型。"""
    kw = dict(_EVAL_CHAT_KWARGS)
    if extra:
        kw.update(extra)
    model_id = chat_model_id or (config.chat_model_id if config else None)
    if model_id:
        kw["model"] = model_id
    return kw


def eval_embedding_kwargs(config: EvalConfig | None = None, *, embedding_model_id: str | None = None) -> dict[str, Any]:
    model_id = embedding_model_id or (config.embedding_model_id if config else None)
    if model_id:
        return {"model": model_id}
    return {}


def eval_invoke_config(
    kb_id: int,
    config: EvalConfig | None = None,
    *,
    chat_model_id: str | None = None,
    extra_configurable: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from config.settings import settings as _settings

    configurable: dict[str, Any] = {"kb_ids": [kb_id]}
    model_id = chat_model_id or (config.chat_model_id if config else None)
    if model_id:
        configurable["chat_model_id"] = model_id
    if extra_configurable:
        configurable.update(extra_configurable)
    from utils.observability import attach_langfuse

    return attach_langfuse(
        {
            "configurable": configurable,
            "recursion_limit": _settings.agent_max_steps,
        },
        name="eval_agent",
        user_id=(config.owner if config else None) or "eval",
        tags=["eval", "langgraph"],
        metadata={"kb_id": kb_id},
    )
