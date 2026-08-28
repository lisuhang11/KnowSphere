"""pytest 公共 fixture。

- 清理 models.dimensions._detected 缓存，避免实测维度测试互相污染。
- pg 可用性探测：供需要真实 postgres 的集成测试 skipif 使用。
"""

from __future__ import annotations

import psycopg
import pytest

from config.settings import settings

@pytest.fixture(autouse=True)
def _clean_dim_detected_cache():
    """每个用例前清空实测维度缓存，保证 resolve_embedding_dim 探测路径可重复。"""
    from models import dimensions

    dimensions._detected.clear()
    yield
    dimensions._detected.clear()

@pytest.fixture(scope="session")
def pg_available() -> bool:
    """postgres 是否在运行（集成测试 skipif 依据）。"""
    try:
        with psycopg.connect(settings.postgres_dsn, connect_timeout=2):
            return True
    except Exception:
        return False
