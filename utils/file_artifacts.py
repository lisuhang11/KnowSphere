"""助手生成的文件产物：解析工具返回、挂到最终 AI 消息。"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

KS_OUTPUTS_KEY = "ks_outputs"


def normalize_file_artifact(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    aid = str(raw.get("id") or raw.get("attachment_id") or "").strip()
    name = str(raw.get("file_name") or "").strip()
    if not aid or not name:
        return None
    out: dict[str, Any] = {"id": aid, "file_name": name}
    file_type = str(raw.get("file_type") or "").strip()
    if file_type:
        out["file_type"] = file_type
    mime = str(raw.get("mime_type") or "").strip()
    if mime:
        out["mime_type"] = mime
    try:
        size = int(raw.get("file_size") or 0)
    except (TypeError, ValueError):
        size = 0
    if size > 0:
        out["file_size"] = size
    return out


def parse_tool_file_artifact(content: Any) -> dict[str, Any] | None:
    """从工具返回 JSON 中取出 artifact 对象。"""
    payload: Any = content
    if isinstance(content, str):
        text = content.strip()
        if not text:
            return None
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return None
    if not isinstance(payload, dict):
        return None
    raw = payload.get("artifact")
    if raw is None and payload.get("ok") and payload.get("id"):
        raw = payload
    return normalize_file_artifact(raw)


def merge_file_artifacts(*groups: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in groups:
        for item in group or []:
            art = normalize_file_artifact(item)
            if art is None or art["id"] in seen:
                continue
            seen.add(art["id"])
            out.append(art)
    return out


def collect_turn_file_artifacts(messages: list[BaseMessage] | None) -> list[dict[str, Any]]:
    """收集上一轮用户消息之后的工具产物。"""
    msgs = list(messages or [])
    start = 0
    for i, msg in enumerate(msgs):
        if isinstance(msg, HumanMessage) or getattr(msg, "type", None) in ("human", "user"):
            start = i
    collected: list[dict[str, Any]] = []
    for msg in msgs[start:]:
        if not isinstance(msg, ToolMessage):
            continue
        art = parse_tool_file_artifact(getattr(msg, "content", None))
        if art:
            collected.append(art)
    return merge_file_artifacts(collected)


def message_outputs(msg: BaseMessage) -> list[dict[str, Any]]:
    kwargs = getattr(msg, "additional_kwargs", None) or {}
    raw = kwargs.get(KS_OUTPUTS_KEY)
    if not isinstance(raw, list):
        return []
    return merge_file_artifacts(raw)


def attach_outputs_to_ai_message(
    msg: AIMessage, artifacts: list[dict[str, Any]]
) -> AIMessage | None:
    """把产物写入 AIMessage.additional_kwargs.ks_outputs；无变化时返回 None。"""
    merged = merge_file_artifacts(message_outputs(msg), artifacts)
    if not merged:
        return None
    existing = message_outputs(msg)
    if existing == merged:
        return None
    kwargs = dict(getattr(msg, "additional_kwargs", None) or {})
    kwargs[KS_OUTPUTS_KEY] = merged
    if hasattr(msg, "model_copy"):
        return msg.model_copy(update={"additional_kwargs": kwargs})
    return AIMessage(
        content=msg.content,
        id=getattr(msg, "id", None),
        name=getattr(msg, "name", None),
        tool_calls=list(getattr(msg, "tool_calls", None) or []),
        additional_kwargs=kwargs,
        response_metadata=dict(getattr(msg, "response_metadata", None) or {}),
    )


def last_ai_message(messages: list[BaseMessage] | None) -> AIMessage | None:
    for msg in reversed(list(messages or [])):
        if isinstance(msg, AIMessage):
            return msg
    return None
