"""从 LangGraph configurable 读取本轮模型选择。"""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig


def _configurable(config: RunnableConfig | None) -> dict[str, Any]:
    if not config:
        return {}
    raw = config.get("configurable") or {}
    return raw if isinstance(raw, dict) else {}


def kb_ids_from_config(config: RunnableConfig | None) -> list[int]:
    configurable = _configurable(config)
    raw = configurable.get("kb_ids")
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[int] = []
    for v in raw:
        if isinstance(v, bool):
            continue
        if isinstance(v, int):
            out.append(v)
            continue
        if isinstance(v, str):
            s = v.strip()
            if s.isdigit():
                out.append(int(s))
    return out

def web_search_enabled_from_config(config: RunnableConfig | None) -> bool:
    """本轮是否绑定联网工具。管理员 WEB_SEARCH_ENABLED=false 为总开关。

    configurable 未传 web_search_enabled 时默认开启（兼容测试/评测）。
    """
    from config.settings import settings

    if not settings.web_search_enabled:
        return False
    configurable = _configurable(config)
    if "web_search_enabled" not in configurable:
        return True
    return bool(configurable["web_search_enabled"])


def graph_enabled_from_config(config: RunnableConfig | None) -> bool:
    """本轮是否绑定知识图谱工具。

    生产会话会显式写入 graph_enabled；测试只传 kb_ids 时视为可用。
    """
    if not kb_ids_from_config(config):
        return False
    configurable = _configurable(config)
    if "graph_enabled" not in configurable:
        return True
    return bool(configurable["graph_enabled"])


def thread_id_from_config(config: RunnableConfig | None) -> str | None:
    configurable = _configurable(config)
    raw = configurable.get("thread_id")
    if raw is None:
        return None
    ident = str(raw).strip()
    return ident or None


def agent_id_from_config(config: RunnableConfig | None) -> str | None:
    configurable = _configurable(config)
    raw = configurable.get("agent_id")
    if not isinstance(raw, str) or not raw.strip():
        return None
    return raw.strip()


def attachment_ids_from_config(config: RunnableConfig | None) -> list[str]:
    configurable = _configurable(config)
    raw = configurable.get("attachment_ids")
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        ident = str(item or "").strip()
        if not ident or ident in seen:
            continue
        seen.add(ident)
        out.append(ident)
    return out


def pinned_skill_names_from_config(config: RunnableConfig | None) -> list[str]:
    configurable = _configurable(config)
    raw = configurable.get("pinned_skill_names")
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        name = str(item or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def chat_model_id_from_config(config: RunnableConfig | None) -> str | None:
    configurable = _configurable(config)
    raw = configurable.get("chat_model_id")
    if not isinstance(raw, str) or not raw.strip():
        return None
    return raw.strip()

def vlm_model_id_from_config(config: RunnableConfig | None) -> str | None:
    configurable = _configurable(config)
    raw = configurable.get("vlm_model_id")
    if not isinstance(raw, str) or not raw.strip():
        return None
    return raw.strip()

def chat_model_kwargs_from_config(
    config: RunnableConfig | None,
    base: dict[str, Any] | None = None,
) -> dict[str, Any]:
    kw = dict(base or {})
    cid = chat_model_id_from_config(config)
    if cid:
        kw["model"] = cid
    return kw
