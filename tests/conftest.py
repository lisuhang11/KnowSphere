"""pytest 公共 fixture。

- 清理 models.dimensions._detected 缓存，避免实测维度测试互相污染。
- pg / redis 可用性探测：供需要真实依赖的集成测试 skipif 使用。
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
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture(scope="session")
def redis_available() -> bool:
    """redis 是否在运行（续流跨进程测试 skipif 依据）。"""
    try:
        import redis

        client = redis.Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=2,
        )
        client.ping()
        return True
    except Exception:  # noqa: BLE001
        return False
