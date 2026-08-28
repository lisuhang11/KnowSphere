"""从 LangGraph configurable 读取本轮模型选择。"""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

def kb_ids_from_config(config: RunnableConfig | None) -> list[int]:
    if not config:
        return []
    configurable = config.get("configurable") or {}
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

def chat_model_id_from_config(config: RunnableConfig | None) -> str | None:
    if not config:
        return None
    configurable = config.get("configurable") or {}
    raw = configurable.get("chat_model_id")
    if not isinstance(raw, str) or not raw.strip():
        return None
    return raw.strip()

def vlm_model_id_from_config(config: RunnableConfig | None) -> str | None:
    if not config:
        return None
    configurable = config.get("configurable") or {}
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
