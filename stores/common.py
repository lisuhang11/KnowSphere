"""stores 公共工具：JSONB、KB 列映射、embedding 列名。"""

from __future__ import annotations

import json
from typing import Any

from config.settings import settings

# knowledge_bases 行 → dict 的列序
KB_COLS = (
    "id, name, description, chunk_size, chunk_overlap, "
    "embedding_model_id, embedding_dim, chunk_strategy, summary_model_id, "
    "enable_parent_child, parent_chunk_size, child_chunk_size, graph_enabled, "
    "created_at, updated_at"
)
KB_COL_COUNT = 15

# 混合检索只命中可检索子块（parent_text 仅用于上下文回捞）
RETRIEVABLE_CHUNK_WHERE = "chunk_type = 'text'"

def load_jsonb(value: Any) -> Any:
    """JSONB 列读取兜底：psycopg3 默认返回 str，统一解成 Python 对象。"""
    if value is None or isinstance(value, (dict, list, int, float, bool)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return value

def kb_cols_prefixed(alias: str) -> str:
    return ", ".join(f"{alias}.{c}" for c in KB_COLS.split(", "))

def embedding_column(dim: int) -> str:
    if dim == settings.embedding_dim:
        return "embedding"
    return f"embedding_{dim}"

def kb_row_to_dict(row) -> dict[str, Any]:
    return {
        "id": row[0],
        "name": row[1],
        "description": row[2],
        "chunk_size": row[3],
        "chunk_overlap": row[4],
        "embedding_model_id": row[5],
        "embedding_dim": row[6],
        "chunk_strategy": row[7],
        "summary_model_id": row[8] or None,
        "enable_parent_child": bool(row[9]),
        "parent_chunk_size": row[10],
        "child_chunk_size": row[11],
        "graph_enabled": bool(row[12]),
        "created_at": row[13].isoformat() if row[13] else None,
        "updated_at": row[14].isoformat() if row[14] else None,
    }
