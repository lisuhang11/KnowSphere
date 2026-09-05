"""Langfuse 观测：开关、@observe、LangGraph CallbackHandler。

未配置 LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY 时全部 no-op，
不影响对话与摄取。启动时关闭 LangSmith 自动 tracing，避免旧 .env 继续上报。
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

from config.settings import settings

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def is_langfuse_enabled() -> bool:
    return bool(
        settings.langfuse_tracing_enabled
        and settings.langfuse_public_key
        and settings.langfuse_secret_key
    )


def observe(*dargs: Any, **dkwargs: Any):
    """Langfuse @observe；未启用时直接调用原函数。支持 @observe / @observe() / @observe(name=...)。"""

    def _decorate(fn: F) -> F:
        decorated: Callable[..., Any] | None = None

        def _resolve() -> Callable[..., Any]:
            nonlocal decorated
            if decorated is not None:
                return decorated
            if not is_langfuse_enabled():
                return fn
            try:
                from langfuse import observe as _observe

                decorated = _observe(*dargs, **dkwargs)(fn)
            except Exception:
                logger.debug("Langfuse observe 不可用，跳过", exc_info=True)
                decorated = fn
            return decorated

        if inspect.iscoroutinefunction(fn):

            @wraps(fn)
            async def _awrapped(*args: Any, **kwargs: Any):
                target = _resolve()
                result = target(*args, **kwargs)
                if inspect.isawaitable(result):
                    return await result
                return result

            return _awrapped  # type: ignore[return-value]

        @wraps(fn)
        def _wrapped(*args: Any, **kwargs: Any):
            return _resolve()(*args, **kwargs)

        return _wrapped  # type: ignore[return-value]

    if dargs and callable(dargs[0]) and not dkwargs:
        fn = dargs[0]
        dargs = ()
        return _decorate(fn)
    return _decorate


def attach_langfuse(
    config: dict[str, Any] | None = None,
    *,
    name: str | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """给 LangGraph / LangChain invoke config 挂上 CallbackHandler 与 trace 属性。"""
    out = dict(config or {})
    if not is_langfuse_enabled():
        return out
    try:
        from langfuse.langchain import CallbackHandler
    except Exception:
        logger.debug("Langfuse CallbackHandler 不可用，跳过", exc_info=True)
        return out

    callbacks = list(out.get("callbacks") or [])
    callbacks.append(CallbackHandler())
    out["callbacks"] = callbacks
    if name:
        out["run_name"] = name

    meta = dict(out.get("metadata") or {})
    if user_id:
        meta["langfuse_user_id"] = user_id
    if session_id:
        meta["langfuse_session_id"] = session_id
    if tags:
        meta["langfuse_tags"] = tags
    if metadata:
        meta.update(metadata)
    if meta:
        out["metadata"] = meta
    return out


def flush_langfuse() -> None:
    """短生命周期进程（CLI / 关停）把缓冲 traces 送出。"""
    if not is_langfuse_enabled():
        return
    try:
        from langfuse import get_client

        get_client().flush()
    except Exception:
        logger.debug("Langfuse flush 失败", exc_info=True)
