"""LangChain 消息 content 解析（文本 / 多模态 / 会话图片元数据）。"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import BaseMessage

from skills.must_use import strip_must_use_block


def message_text(content: Any) -> str:
    """从 HumanMessage/AIMessage content 提取纯文本。"""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text") or ""))
        return "".join(parts).strip()
    if content is None:
        return ""
    return str(content).strip()

def message_query_text(msg: BaseMessage) -> str:
    """用户可见问题文本（去掉注入块）。"""
    text = message_text(getattr(msg, "content", ""))
    text = strip_must_use_block(text)
    for marker in ("\n\n[用户上传图片内容]\n", "\n\n[会话附件内容]\n"):
        if marker in text:
            text = text.split(marker, 1)[0]
    return text.strip()

def message_images(msg: BaseMessage) -> list[dict[str, str]]:
    """读取 additional_kwargs.ks_images。"""
    kwargs = getattr(msg, "additional_kwargs", None) or {}
    raw = kwargs.get("ks_images")
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        entry: dict[str, str] = {"url": url}
        caption = item.get("caption")
        if isinstance(caption, str) and caption.strip():
            entry["caption"] = caption.strip()
        out.append(entry)
    return out

def message_attachments(msg: BaseMessage) -> list[dict[str, str]]:
    """读取 additional_kwargs.ks_attachments。"""
    kwargs = getattr(msg, "additional_kwargs", None) or {}
    raw = kwargs.get("ks_attachments")
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        aid = str(item.get("id") or "").strip()
        if not aid:
            continue
        out.append(
            {
                "id": aid,
                "file_name": str(item.get("file_name") or ""),
                "file_type": str(item.get("file_type") or ""),
            }
        )
    return out


def message_outputs(msg: BaseMessage) -> list[dict[str, Any]]:
    """读取 additional_kwargs.ks_outputs（助手生成文件）。"""
    from utils.file_artifacts import message_outputs as _outputs

    return _outputs(msg)

def message_has_images(msg: BaseMessage) -> bool:
    return bool(message_images(msg))

def message_has_attachments(msg: BaseMessage) -> bool:
    return bool(message_attachments(msg))
