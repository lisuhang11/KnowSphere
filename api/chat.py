"""嵌入式 LangGraph 运行时：graph 在 FastAPI 进程内编译，供 sessions 路由调用。"""

from __future__ import annotations

import asyncio
import logging

import psycopg
from fastapi import HTTPException

from config.settings import settings

logger = logging.getLogger(__name__)

_agent = None
_checkpointer = None
_pool = None

async def init_agent_runtime() -> None:
    """初始化 checkpointer 并编译 graph。幂等。"""
    global _agent, _checkpointer, _pool
    if _agent is not None:
        return

    postgres_ok = False
    try:
        from api.sessions import ensure_session_table

        await asyncio.to_thread(ensure_session_table)
        postgres_ok = True
    except Exception as e:
        logger.warning("Postgres 不可用（会话表创建失败），降级 MemorySaver: %s", e)

    if postgres_ok:
        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
            from psycopg_pool import AsyncConnectionPool
            from langgraph.checkpoint.postgres import PostgresSaver

            with psycopg.connect(settings.postgres_dsn, autocommit=True) as conn:
                PostgresSaver(conn).setup()

            _pool = AsyncConnectionPool(
                conninfo=settings.postgres_dsn, open=False, min_size=1, max_size=8
            )
            await _pool.open()
            _checkpointer = AsyncPostgresSaver(_pool)
            logger.info("LangGraph AsyncPostgresSaver 就绪（postgres 持久化）")
        except Exception as e:
            logger.warning("Postgres checkpointer 初始化失败，降级 MemorySaver: %s", e)
            from langgraph.checkpoint.memory import MemorySaver

            _checkpointer = MemorySaver()
    else:
        from langgraph.checkpoint.memory import MemorySaver

        _checkpointer = MemorySaver()

    from agents.agent import build_agent

    _agent = build_agent(checkpointer=_checkpointer)
    logger.info("嵌入式 LangGraph agent 已编译（FastAPI 进程内运行）")

async def close_agent_runtime() -> None:
    global _agent, _checkpointer, _pool
    _agent = None
    _checkpointer = None
    if _pool is not None:
        try:
            await _pool.close()
        except Exception as e:  # pragma: no cover
            logger.warning("关闭 checkpointer 连接池失败: %s", e)
        _pool = None

def get_agent():
    if _agent is None:
        raise HTTPException(status_code=503, detail="agent 未初始化，请稍后重试")
    return _agent

def get_checkpointer():
    return _checkpointer
