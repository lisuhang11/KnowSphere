"""会话聊天图片：解码、MinIO 存储、VLM 分析。"""

from __future__ import annotations

import base64
import logging
import re
import uuid
from dataclasses import dataclass

from langchain_core.messages import HumanMessage

from config.settings import get_current_owner, settings
from models import create_vlm_model
from utils.message_content import message_text
from utils.object_store import require_object_store

logger = logging.getLogger(__name__)

MAX_IMAGES = 5
MAX_IMAGE_BYTES = 10 << 20  # 10MB
NO_VLM_IMAGE_UPLOAD_DETAIL = "未配置视觉理解（VLLM）模型，无法上传图片。请先在模型管理中添加。"

_DATA_URI_RE = re.compile(r"^data:([^;]+);base64,(.+)$", re.DOTALL)

_EXT_BY_MIME = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
}

@dataclass
class SavedChatImage:
    image_id: str
    storage_key: str
    public_url: str
    caption: str = ""

class ChatImageError(ValueError):
    """聊天图片处理错误。"""

def decode_data_uri(data_uri: str) -> tuple[bytes, str]:
    text = (data_uri or "").strip()
    match = _DATA_URI_RE.match(text)
    if not match:
        raise ChatImageError("图片必须是 data:image/...;base64,... 格式")
    mime = match.group(1).lower()
    if not mime.startswith("image/"):
        raise ChatImageError(f"不支持的 MIME 类型: {mime}")
    try:
        raw = base64.b64decode(match.group(2), validate=True)
    except Exception as exc:
        raise ChatImageError("base64 解码失败") from exc
    if not raw:
        raise ChatImageError("图片内容为空")
    if len(raw) > MAX_IMAGE_BYTES:
        raise ChatImageError(f"单张图片不能超过 {MAX_IMAGE_BYTES // (1 << 20)}MB")
    return raw, mime

def build_chat_image_storage_key(session_id: str, image_id: str, ext: str) -> str:
    owner = get_current_owner() or settings.default_owner
    safe_ext = ext if ext.startswith(".") else f".{ext}"
    return f"{owner}/chat/{session_id}/{image_id}{safe_ext}"

def chat_image_public_url(session_id: str, image_id: str) -> str:
    return f"/api/sessions/{session_id}/chat-images/{image_id}"

def save_chat_images(session_id: str, data_uris: list[str]) -> list[SavedChatImage]:
    """解码 base64 图片并写入 MinIO。"""
    if not data_uris:
        return []
    if len(data_uris) > MAX_IMAGES:
        raise ChatImageError(f"最多上传 {MAX_IMAGES} 张图片")

    store = require_object_store()
    saved: list[SavedChatImage] = []
    for idx, data_uri in enumerate(data_uris):
        img_bytes, mime = decode_data_uri(data_uri)
        ext = _EXT_BY_MIME.get(mime, ".jpg")
        image_id = uuid.uuid4().hex
        storage_key = build_chat_image_storage_key(session_id, image_id, ext)
        store.put_bytes(img_bytes, storage_key, content_type=mime)
        saved.append(
            SavedChatImage(
                image_id=image_id,
                storage_key=storage_key,
                public_url=chat_image_public_url(session_id, image_id),
            )
        )
        logger.debug("chat image saved idx=%d key=%s", idx, storage_key)
    return saved

def build_image_analysis_prompt(user_query: str) -> str:
    q = (user_query or "").strip()
    if not q:
        return (
            "请分析这张图片的内容。若包含文字请提取关键信息；"
            "若是自然图片请描述主要内容。用简洁中文回答。"
        )
    return (
        f"用户的问题是：{q}\n\n"
        "请分析图片中与用户问题相关的内容。"
        "若包含文字/文档/表格，请提取与问题相关的关键信息。"
        "若是截图/图表/自然图片，请描述与问题相关的视觉内容。"
        "用简洁中文回答，只输出分析结果。"
    )

def analyze_chat_images(
    saved: list[SavedChatImage],
    user_query: str,
    *,
    vlm_model_id: str | None = None,
) -> str:
    """用 VLLM 模型分析图片，回填 SavedChatImage.caption。"""
    if not saved:
        return ""
    model_id = (vlm_model_id or settings.chat_vlm_model_id or "").strip() or None
    if not model_id:
        from utils.model_store import ModelStore

        rec = ModelStore().get_default_model("VLLM")
        model_id = rec["id"] if rec else None
    if not model_id:
        logger.warning("未配置 VLLM 模型，跳过图片分析")
        return ""

    store = require_object_store()
    prompt = build_image_analysis_prompt(user_query)
    descriptions: list[str] = []

    try:
        llm = create_vlm_model(model=model_id, temperature=0.2)
    except Exception as exc:
        logger.warning("创建 VLLM 模型失败: %s", exc)
        return ""

    for img in saved:
        try:
            data, _ = store.get_bytes(img.storage_key)
            data_uri = f"data:image/jpeg;base64,{base64.b64encode(data).decode('ascii')}"
            # 保留真实 MIME
            if img.storage_key.endswith(".png"):
                data_uri = data_uri.replace("image/jpeg", "image/png", 1)
            elif img.storage_key.endswith(".webp"):
                data_uri = data_uri.replace("image/jpeg", "image/webp", 1)
            elif img.storage_key.endswith(".gif"):
                data_uri = data_uri.replace("image/jpeg", "image/gif", 1)

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
            caption = message_text(getattr(resp, "content", ""))
            img.caption = caption
            if caption:
                descriptions.append(caption)
        except Exception as exc:
            logger.warning("VLM 分析图片 %s 失败: %s", img.image_id, exc)

    return "\n\n".join(descriptions).strip()

def build_human_message_with_images(
    query: str,
    saved: list[SavedChatImage],
    image_description: str = "",
) -> HumanMessage:
    """构建带图片元数据的用户消息。image_description 为空时由 query_understand VLM 填充。"""
    text = query.strip()
    desc = (image_description or "").strip()
    if not desc and saved:
        desc = "\n\n".join(i.caption for i in saved if i.caption).strip()
    if desc:
        text = f"{text}\n\n[用户上传图片内容]\n{desc}".strip()

    msg = HumanMessage(content=text or "请分析上传的图片")
    if saved:
        msg.additional_kwargs["ks_images"] = [
            {
                "url": img.public_url,
                **({"caption": img.caption} if img.caption else {}),
            }
            for img in saved
        ]
    return msg

def build_human_message_saved_images_only(
    query: str,
    saved: list[SavedChatImage],
) -> HumanMessage:
    """仅保存图片元数据，VLM 分析留给 query_understand 多模态一步完成。"""
    text = (query.strip() or "请分析上传的图片")
    msg = HumanMessage(content=text)
    if saved:
        msg.additional_kwargs["ks_images"] = [
            {"url": img.public_url} for img in saved
        ]
    return msg

def load_chat_image_bytes(session_id: str, image_id: str) -> tuple[bytes, str]:
    """按 image_id 读取 MinIO 对象（校验 session 前缀）。"""
    if not re.fullmatch(r"[0-9a-f]{32}", image_id or ""):
        raise ChatImageError("非法 image_id")
    owner = get_current_owner() or settings.default_owner
    prefix = f"{owner}/chat/{session_id}/"
    store = require_object_store()
    for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
        key = f"{prefix}{image_id}{ext}"
        try:
            return store.get_bytes(key)
        except Exception:
            continue
    raise ChatImageError("图片不存在")
