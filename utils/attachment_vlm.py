"""聊天附件解析阶段的 VLM OCR 补全（对齐 WeKnora applyImageUnderstanding）。

分层：
1. 解析器 / PaddleOCR 先跑（知识库摄取也只用这一层，不用 VLM）。
2. 去掉图片 markdown 后正文仍 < 200 字，才考虑 VLM。
3. 独立图片：VLM 先 OCR；OCR 仍 < 32 字才再打一句配图说明。
4. 扫描 PDF 等文档：仅 VLM OCR（不配图），且受 chat_attachment_vlm_ocr 开关控制。
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from config.settings import settings
from models import create_vlm_model
from utils.attachment_images import ATTACHMENT_LOW_TEXT_CHARS, approx_attachment_text_chars
from utils.message_content import message_text
from utils.object_store import require_object_store
from utils.temporary_attachments import is_image_attachment

logger = logging.getLogger(__name__)

ATTACHMENT_OCR_SUFFICIENT_CHARS = 32
ATTACHMENT_VLM_OCR_MAX_PAGES = 8

VLM_OCR_PROMPT = (
    "你是 OCR 助手。请从这张文档图片中提取全部正文，并输出纯 Markdown。\n"
    "要求：忽略页眉页脚；表格用 Markdown 表格；公式用 $ 或 $$；按阅读顺序组织；"
    "只输出提取的文本，不要 HTML、推理或无关评论。"
    "若完全没有可识别文字，只回复：No text content."
)

VLM_OCR_SCANNED_PROMPT = (
    "你是 OCR 与版面提取助手。输入是扫描 PDF 的一页。"
    "请仔细提取全部文字和结构，输出纯 Markdown。\n"
    "要求：忽略页眉、页脚、页码；尽量保留段落与层级；表格用 Markdown 表格；"
    "公式用 $ 或 $$；只输出提取的文本。"
    "若完全没有可识别文字，只回复：No text content."
)

VLM_CAPTION_PROMPT = "请用简洁中文描述这张图片的主要内容。"


def _empty_ocr(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    lower = t.lower()
    return lower in {"no text content", "无文字", "无文本内容"} or lower.startswith("no text content")


def _resolve_vlm_id() -> str | None:
    model_id = (settings.chat_vlm_model_id or "").strip() or None
    if model_id:
        return model_id
    try:
        from utils.model_store import ModelStore

        rec = ModelStore().get_default_model("VLLM")
        return rec["id"] if rec else None
    except Exception:
        return None


def _bytes_to_data_uri(data: bytes, hint: str = "") -> str:
    mime = "image/jpeg"
    key = (hint or "").lower()
    if key.endswith(".png") or "png" in key:
        mime = "image/png"
    elif key.endswith(".webp") or "webp" in key:
        mime = "image/webp"
    elif key.endswith(".gif") or "gif" in key:
        mime = "image/gif"
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _vlm_predict(llm: Any, data: bytes, prompt: str, hint: str = "") -> str:
    data_uri = _bytes_to_data_uri(data, hint)
    resp = llm.invoke(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            }
        ]
    )
    return message_text(getattr(resp, "content", "")).strip()


def vlm_ocr_images(
    images: list[tuple[bytes, str]],
    *,
    scanned: bool,
    caption_fallback: bool,
) -> str:
    """对图片列表做 VLM OCR；caption_fallback 仅用于独立图片且 OCR 过稀。"""
    if not images:
        return ""
    model_id = _resolve_vlm_id()
    if not model_id:
        logger.info("未配置 VLLM，跳过附件 VLM OCR")
        return ""
    try:
        llm = create_vlm_model(model=model_id, temperature=0.2)
    except Exception as exc:
        logger.warning("创建 VLLM 失败，跳过附件 OCR: %s", exc)
        return ""

    ocr_prompt = VLM_OCR_SCANNED_PROMPT if scanned else VLM_OCR_PROMPT
    ocr_parts: list[str] = []
    ocr_chars = 0
    for data, hint in images:
        if not data:
            continue
        try:
            text = _vlm_predict(llm, data, ocr_prompt, hint)
        except Exception as exc:
            logger.warning("附件 VLM OCR 失败: %s", exc)
            continue
        if _empty_ocr(text):
            continue
        ocr_parts.append(text)
        ocr_chars += len(text)

    if (
        caption_fallback
        and images
        and images[0][0]
        and ocr_chars < ATTACHMENT_OCR_SUFFICIENT_CHARS
    ):
        try:
            caption = _vlm_predict(llm, images[0][0], VLM_CAPTION_PROMPT, images[0][1])
            if caption:
                ocr_parts.insert(0, caption)
        except Exception as exc:
            logger.warning("附件 VLM 配图说明失败: %s", exc)

    return "\n\n".join(ocr_parts).strip()


def maybe_vlm_enrich_attachment(
    *,
    content: str,
    file_name: str,
    original_storage_key: str,
    image_refs: list[dict[str, Any]],
) -> tuple[str, str]:
    """解析器 OCR 之后：仅在正文仍很少时用 VLM 补全。返回 (content, extra_text)。"""
    if approx_attachment_text_chars(content) >= ATTACHMENT_LOW_TEXT_CHARS:
        return content, ""

    store = require_object_store()
    images: list[tuple[bytes, str]] = []
    is_image = is_image_attachment(file_name)

    if is_image:
        try:
            data, _ = store.get_bytes(original_storage_key)
            if data:
                images.append((data, original_storage_key))
        except Exception as exc:
            logger.warning("读取图片附件失败，跳过 VLM: %s", exc)
            return content, ""
        extra = vlm_ocr_images(images, scanned=False, caption_fallback=True)
    else:
        if not getattr(settings, "chat_attachment_vlm_ocr", True):
            return content, ""
        for ref in image_refs[:ATTACHMENT_VLM_OCR_MAX_PAGES]:
            key = (ref.get("storage_key") or "").strip()
            if not key:
                continue
            try:
                data, _ = store.get_bytes(key)
            except Exception as exc:
                logger.warning("读取抽出图失败 %s: %s", key, exc)
                continue
            if data:
                images.append((data, key or str(ref.get("filename") or "")))
        extra = vlm_ocr_images(images, scanned=True, caption_fallback=False)

    extra = extra.strip()
    if not extra:
        return content, ""
    if not (content or "").strip():
        return extra, extra
    return f"{content.rstrip()}\n\n{extra}", extra
