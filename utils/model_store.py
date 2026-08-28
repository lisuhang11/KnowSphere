"""模型管理存储层（兼容入口）。

实现已拆分至 `stores/model_repository.py`；本模块仅 re-export，避免全库改 import。
新代码请使用 `from stores.model_repository import ModelStore`。
"""

from stores.model_repository import (
    MODEL_SOURCES,
    MODEL_TYPES,
    SECRET_FIELDS,
    ModelStore,
    is_model_ref,
    new_model_id,
)

# 历史脚本 import _SECRET_FIELDS
_SECRET_FIELDS = SECRET_FIELDS

__all__ = [
    "MODEL_SOURCES",
    "MODEL_TYPES",
    "ModelStore()",
    "_SECRET_FIELDS",
    "is_model_ref",
    "new_model_id",
]
