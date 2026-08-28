"""pgvector 存储层（兼容入口）。

实现已拆分至 `stores/` 包；本模块仅 re-export，避免全库改 import。
新代码请使用 `from stores import ChunkStore` 或按域引用 Repository。
"""

from stores.common import embedding_column as _embedding_column
from stores.common import kb_cols_prefixed as _kb_cols_prefixed
from stores.facade import ChunkStore
from stores.rrf import rrf_fuse

__all__ = ["ChunkStore", "rrf_fuse", "_embedding_column", "_kb_cols_prefixed"]
