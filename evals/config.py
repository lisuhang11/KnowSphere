"""评测运行时配置覆盖（临时 patch settings，线程内生效）。"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from config.settings import settings

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
