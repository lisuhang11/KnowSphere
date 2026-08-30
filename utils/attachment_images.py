"""聊天附件解析图片：上传 MinIO，并把 markdown 占位改写成可访问 URL。

对齐 WeKnora ImageResolver：解析器产出 markdown + ImageRefs（常含 base64），
落库前换成 serving URL，不把 data URI 留在 prompt 里。
"""

from __future__ import annotations

import base64
import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

from config.settings import get_current_owner, settings
from ingestion.parser.base_parser import ImageRef, ParseResult
from utils.object_store import require_object_store
from utils.temporary_attachments import attachment_preview_url, is_image_attachment

logger = logging.getLogger(__name__)

# 与知识库 markdown 占位一致：![alt](images/foo.jpg)
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\((images/[^)\s]+)\)")
_DATA_URI_MD_RE = re.compile(
    r"!\[([^\]]*)\]\((data:image/[^;]+;base64,[^)]+)\)",
    re.IGNORECASE,
)
_ANY_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")

# 对齐 WeKnora temporaryDocumentLowTextRunes：去掉图片语法后正文过少才走 VLM OCR
ATTACHMENT_LOW_TEXT_CHARS = 200
MAX_VISION_IMAGES = 4
_VISUAL_QUERY_MARKERS = (
    "图",
    "表格",
    "截图",
    "页面",
    "排版",
    "chart",
    "figure",
    "diagram",
    "image",
    "layout",
)


def attachment_extracted_image_url(session_id: str, attachment_id: str, filename: str) -> str:
    safe = Path(filename or "image").name
    return f"/api/sessions/{session_id}/attachments/{attachment_id}/images/{quote(safe)}"


def attachment_extracted_image_key(session_id: str, attachment_id: str, filename: str) -> str:
    owner = get_current_owner() or settings.default_owner
    safe = Path(filename or "image").name or "image"
    return f"{owner}/chat-attachments/{session_id}/{attachment_id}/images/{safe}"


def approx_attachment_text_chars(markdown: str) -> int:
    """去掉 markdown 图片语法后的正文长度，判断是否需要 VLM OCR 补全。"""
    stripped = _ANY_MD_IMAGE_RE.sub("", markdown or "")
    return len(stripped.strip())


def is_visual_document_query(query: str) -> bool:
    lower = (query or "").lower()
    return any(m in lower for m in _VISUAL_QUERY_MARKERS)


def rewrite_markdown_image_refs(markdown: str, url_by_ref: dict[str, str]) -> str:
    """把 `images/foo.jpg` / 残留 data URI 换成 serving URL。"""
    if not markdown:
        return ""

    def _md_repl(match: re.Match[str]) -> str:
        alt, target = match.group(1), match.group(2)
        url = url_by_ref.get(target) or url_by_ref.get(Path(target).name)
        if not url:
            return match.group(0)
        return f"![{alt}]({url})"

    out = _MD_IMAGE_RE.sub(_md_repl, markdown)
    out = _DATA_URI_MD_RE.sub(
        lambda m: f"![{m.group(1)}]({url_by_ref.get(m.group(2), m.group(2))})",
        out,
    )
    return out


def _bytes_for_ref(ref: ImageRef, images: dict[str, str]) -> bytes:
    if ref.data:
        return ref.data
    for key in (f"images/{ref.filename}", ref.filename, ref.original_ref):
        raw = (images or {}).get(key) or ""
        if not raw:
            continue
        try:
            if raw.startswith("data:"):
                raw = raw.split(",", 1)[-1]
            return base64.b64decode(raw)
        except Exception:
            continue
    return b""


def persist_attachment_parse(
    session_id: str,
    attachment_id: str,
    parsed: ParseResult,
    *,
    file_name: str,
) -> tuple[str, list[dict[str, Any]]]:
    """上传解析出的图片并改写 markdown，返回 (markdown, image_refs dicts)。"""
    store = require_object_store()
    is_image = is_image_attachment(file_name)
    preview = attachment_preview_url(session_id, attachment_id)
    url_by_ref: dict[str, str] = {}
    out_refs: list[dict[str, Any]] = []

    for idx, ref in enumerate(parsed.image_refs):
        filename = Path(ref.filename or f"image_{idx}.jpg").name
        reuse_original = is_image and idx == 0
        if reuse_original:
            storage_key = ""
            url = preview
        else:
            data = _bytes_for_ref(ref, parsed.images)
            if not data:
                logger.warning("附件 %s 图片 %s 无字节，跳过上传", attachment_id, filename)
                continue
            storage_key = attachment_extracted_image_key(session_id, attachment_id, filename)
            store.put_bytes(data, storage_key, ref.mime_type or "image/jpeg")
            url = attachment_extracted_image_url(session_id, attachment_id, filename)

        url_by_ref[f"images/{filename}"] = url
        url_by_ref[filename] = url
        if ref.original_ref:
            url_by_ref[ref.original_ref] = url
        out_refs.append(
            {
                "filename": filename,
                "original_ref": ref.original_ref or filename,
                "mime_type": ref.mime_type or "",
                "storage_key": storage_key,
                "url": url,
            }
        )

    markdown = rewrite_markdown_image_refs(parsed.markdown or "", url_by_ref)
    return markdown.strip(), out_refs
