"""LangGraph 自定义流事件：thinking / tool_call / tool_result。"""

from __future__ import annotations

from typing import Any


def emit_thinking(text: str, writer: Any = None) -> None:
    _write({"type": "thinking", "content": text}, writer)


def emit_tool_call(tool_name: str, content: str, writer: Any = None) -> None:
    _write({"type": "tool_call", "tool_name": tool_name, "content": content}, writer)


def emit_tool_result(tool_name: str, content: str, *, success: bool = True, writer: Any = None) -> None:
    _write(
        {
            "type": "tool_result",
            "tool_name": tool_name,
            "success": success,
            "content": content,
        },
        writer,
    )


def emit_file_artifact(
    *,
    attachment_id: str,
    file_name: str,
    file_type: str = "",
    file_size: int = 0,
    mime_type: str = "",
    writer: Any = None,
) -> None:
    """下发助手生成的文件产物，供对话卡片预览。"""
    aid = (attachment_id or "").strip()
    name = (file_name or "").strip()
    if not aid or not name:
        return
    _write(
        {
            "type": "file_artifact",
            "id": aid,
            "file_name": name,
            "file_type": (file_type or "").strip(),
            "file_size": int(file_size or 0),
            "mime_type": (mime_type or "").strip(),
        },
        writer,
    )


def emit_citation_sources(sources: Any, writer: Any = None) -> None:
    """把本轮检索命中写成 citation_meta，供 [[cN]] 展开。"""
    from config.settings import settings
    from utils.citation import citation_meta_payload, citations_from_sources

    if not settings.citation_enabled or not sources:
        return
    _write(citation_meta_payload(citations_from_sources(sources)), writer)


def _write(payload: dict[str, Any], writer: Any = None) -> None:
    try:
        if writer is None:
            from langgraph.config import get_stream_writer

            writer = get_stream_writer()
        if writer is not None:
            writer(payload)
    except Exception:
        pass
