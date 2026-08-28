"""Query understand 多模态：从用户消息加载图片供 VLM 调用。"""

from __future__ import annotations

import base64
import logging
import re
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage

from utils.chat_images import load_chat_image_bytes
from utils.message_content import message_images
from utils.object_store import require_object_store
from utils.temporary_attachments import TemporaryAttachmentStore

logger = logging.getLogger(__name__)

_CHAT_IMAGE_URL_RE = re.compile(
    r"/api/sessions/(?P<session_id>[0-9a-f-]+)/chat-images/(?P<image_id>[0-9a-f]{32})"
)
_ATTACHMENT_PREVIEW_RE = re.compile(
    r"/api/sessions/(?P<session_id>[0-9a-f-]+)/attachments/(?P<aid>[0-9a-f-]+)/preview"
)

def _bytes_to_data_uri(data: bytes, storage_key: str = "") -> str:
    mime = "image/jpeg"
    key = storage_key.lower()
    if key.endswith(".png"):
        mime = "image/png"
    elif key.endswith(".webp"):
        mime = "image/webp"
    elif key.endswith(".gif"):
        mime = "image/gif"
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"

def _load_from_url(url: str, default_session_id: str) -> str | None:
    url = (url or "").strip()
    if not url:
        return None

    m = _CHAT_IMAGE_URL_RE.search(url)
    if m:
        try:
            data, _ = load_chat_image_bytes(m.group("session_id"), m.group("image_id"))
            return _bytes_to_data_uri(data)
        except Exception as exc:
            logger.warning("加载 chat image 失败 %s: %s", url, exc)
            return None

    m = _ATTACHMENT_PREVIEW_RE.search(url)
    if m:
        sid = m.group("session_id")
        aid = m.group("aid")
        store = TemporaryAttachmentStore()
        row = store.get(aid, sid)
        if not row:
            return None
        try:
            data, _ = require_object_store().get_bytes(row["storage_key"])
            return _bytes_to_data_uri(data, row.get("storage_key") or "")
        except Exception as exc:
            logger.warning("加载附件预览失败 %s: %s", url, exc)
            return None

    if url.startswith("data:image/"):
        return url

    # 相对路径 fallback
    if default_session_id and url.startswith("/api/sessions/"):
        return _load_from_url(url, default_session_id)

    return None

def load_image_data_uris_from_message(
    msg: BaseMessage,
    *,
    session_id: str,
    max_images: int = 4,
) -> list[str]:
    """从 HumanMessage.ks_images 加载 data URI 列表。"""
    if not isinstance(msg, HumanMessage):
        return []
    uris: list[str] = []
    for meta in message_images(msg):
        uri = _load_from_url(meta.get("url") or "", session_id)
        if uri:
            uris.append(uri)
        if len(uris) >= max_images:
            break
    return uris

def build_multimodal_user_content(
    text: str,
    image_data_uris: list[str],
) -> list[dict[str, Any]] | str:
    """构建 VLM user content blocks。"""
    if not image_data_uris:
        return text
    blocks: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for uri in image_data_uris:
        blocks.append({"type": "image_url", "image_url": {"url": uri}})
    return blocks
