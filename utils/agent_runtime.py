"""按会话绑定的智能体解析运行时提示词与工具名单。

未传 agent_id（测试/评测）时不做限制，沿用编译图时的 tool_list。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from langchain_core.runnables import RunnableConfig

from config.settings import settings
from utils.run_config import agent_id_from_config

logger = logging.getLogger(__name__)


def load_agent(agent_id: str | None) -> dict[str, Any] | None:
    """读取智能体；id 无效时回落到默认。数据库不可用时返回 None。"""
    try:
        from stores.agent_repository import AgentStore

        store = AgentStore()
        if agent_id:
            rec = store.get_agent(agent_id)
            if rec and rec.get("status") != "disabled":
                return rec
        return store.get_default_agent()
    except Exception:
        logger.debug("加载智能体失败 agent_id=%s", agent_id, exc_info=True)
        return None


def resolve_agent_tool_names(config: RunnableConfig | None) -> frozenset[str] | None:
    """智能体允许的工具名。None = 不额外裁剪（使用传入 tool_list）。"""
    agent_id = agent_id_from_config(config)
    if not agent_id:
        return None
    rec = load_agent(agent_id)
    if rec is None:
        return None
    return frozenset(rec.get("tool_names") or [])


def resolve_system_prompt(
    config: RunnableConfig | None,
    default: str,
    bound_tool_names: Iterable[str] | None = None,
) -> str:
    """自定义 system_prompt 优先；否则按本轮实际绑定的工具生成。"""
    agent_id = agent_id_from_config(config)
    rec = load_agent(agent_id) if agent_id else None
    if rec is not None:
        custom = (rec.get("system_prompt") or "").strip()
        if custom:
            return custom
    names: Iterable[str] | None
    if bound_tool_names is not None:
        names = bound_tool_names
    elif rec is not None:
        names = rec.get("tool_names") or []
    else:
        return default
    from prompts import build_system_prompt

    return build_system_prompt(settings.citation_enabled, tool_names=names)


def resolve_max_iterations(agent_id: str | None) -> int:
    rec = load_agent(agent_id) if agent_id else None
    if rec and rec.get("max_iterations"):
        try:
            return max(4, min(int(rec["max_iterations"]), 80))
        except (TypeError, ValueError):
            pass
    return settings.agent_max_steps
