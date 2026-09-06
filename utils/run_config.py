"""从 RunnableConfig 读取本轮配置（委托 agents.context.Context）。"""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from agents.context import Context, _configurable, context_from_config


def kb_ids_from_config(config: RunnableConfig | None) -> list[int]:
    return context_from_config(config).kb_ids


def web_search_enabled_from_config(config: RunnableConfig | None) -> bool:
    """本轮是否绑定联网工具。管理员 WEB_SEARCH_ENABLED=false 为总开关。"""
    return context_from_config(config).resolved_web_search_enabled()


def graph_enabled_from_config(config: RunnableConfig | None) -> bool:
    """本轮是否绑定知识图谱工具。"""
    return context_from_config(config).resolved_graph_enabled()


def thread_id_from_config(config: RunnableConfig | None) -> str | None:
    ident = context_from_config(config).thread_id
    return ident or None


def agent_id_from_config(config: RunnableConfig | None) -> str | None:
    ident = context_from_config(config).agent_id
    return ident or None


def attachment_ids_from_config(config: RunnableConfig | None) -> list[str]:
    return context_from_config(config).attachment_ids


def pinned_skill_names_from_config(config: RunnableConfig | None) -> list[str]:
    return context_from_config(config).pinned_skill_names


def chat_model_id_from_config(config: RunnableConfig | None) -> str | None:
    ident = context_from_config(config).chat_model_id
    return ident or None


def vlm_model_id_from_config(config: RunnableConfig | None) -> str | None:
    ident = context_from_config(config).vlm_model_id
    return ident or None


def chat_model_kwargs_from_config(
    config: RunnableConfig | None,
    base: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return context_from_config(config).chat_model_kwargs(base)


__all__ = [
    "Context",
    "_configurable",
    "agent_id_from_config",
    "attachment_ids_from_config",
    "chat_model_id_from_config",
    "chat_model_kwargs_from_config",
    "context_from_config",
    "graph_enabled_from_config",
    "kb_ids_from_config",
    "pinned_skill_names_from_config",
    "thread_id_from_config",
    "vlm_model_id_from_config",
    "web_search_enabled_from_config",
]
