"""聊天图片与消息 content 解析测试。"""

from __future__ import annotations

import base64
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage

from schemas.query import needs_retrieval
from utils.chat_images import (
    ChatImageError,
    SavedChatImage,
    build_human_message_with_images,
    decode_data_uri,
    save_chat_images,
)
from utils.message_content import message_has_images, message_images, message_query_text

def _png_data_uri() -> str:
    raw = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    return f"data:image/png;base64,{base64.b64encode(raw).decode('ascii')}"

def test_decode_data_uri():
    uri = _png_data_uri()
    data, mime = decode_data_uri(uri)
    assert mime == "image/png"
    assert len(data) > 0

def test_decode_data_uri_rejects_invalid():
    with pytest.raises(ChatImageError):
        decode_data_uri("not-a-data-uri")

def test_build_human_message_with_images():
    saved = [
        SavedChatImage(
            image_id="abc123456789012345678901234567890",
            storage_key="default/chat/s/abc123456789012345678901234567890.png",
            public_url="/api/sessions/s/chat-images/abc123456789012345678901234567890",
            caption="一张图表",
        )
    ]
    msg = build_human_message_with_images("这张图是什么", saved, "一张图表")
    assert "这张图是什么" in str(msg.content)
    assert "[用户上传图片内容]" in str(msg.content)
    assert message_has_images(msg)
    assert message_images(msg)[0]["url"].endswith("abc123456789012345678901234567890")

def test_message_query_text_strips_image_block():
    msg = HumanMessage(content="问题\n\n[用户上传图片内容]\n图片描述")
    assert message_query_text(msg) == "问题"

def test_needs_retrieval_image_only():
    assert not needs_retrieval("image_only", True)
    assert needs_retrieval("kb_search", True)

def test_save_chat_images():
    uri = _png_data_uri()
    store = MagicMock()
    with patch("utils.chat_images.require_object_store", return_value=store):
        saved = save_chat_images("sess-1", [uri])
    assert len(saved) == 1
    assert saved[0].public_url.startswith("/api/sessions/sess-1/chat-images/")
    store.put_bytes.assert_called_once()


def test_has_usable_vlm_false_when_no_vllm(monkeypatch):
    from config.settings import settings
    from stores.model_repository import ModelStore

    monkeypatch.setattr(settings, "chat_vlm_model_id", "")
    monkeypatch.setattr(ModelStore, "is_vllm_model_id_valid", lambda self, mid: False)
    monkeypatch.setattr(ModelStore, "get_default_model", lambda self, t: None)
    monkeypatch.setattr(ModelStore, "list_models", lambda self, type_=None, source=None: [])
    assert ModelStore().has_usable_vlm() is False


def test_has_usable_vlm_true_with_settings_id(monkeypatch):
    from config.settings import settings
    from stores.model_repository import ModelStore

    monkeypatch.setattr(settings, "chat_vlm_model_id", "model-vlm")
    monkeypatch.setattr(ModelStore, "is_vllm_model_id_valid", lambda self, mid: mid == "model-vlm")
    assert ModelStore().has_usable_vlm() is True


def test_has_usable_vlm_true_with_enabled_catalog(monkeypatch):
    from config.settings import settings
    from stores.model_repository import ModelStore

    monkeypatch.setattr(settings, "chat_vlm_model_id", "")
    monkeypatch.setattr(ModelStore, "get_default_model", lambda self, t: None)
    monkeypatch.setattr(
        ModelStore,
        "list_models",
        lambda self, type_=None, source=None: [{"id": "model-x", "type": "VLLM", "status": "active"}],
    )
    assert ModelStore().has_usable_vlm() is True


def test_has_usable_vlm_ignores_disabled(monkeypatch):
    from config.settings import settings
    from stores.model_repository import ModelStore

    monkeypatch.setattr(settings, "chat_vlm_model_id", "")
    monkeypatch.setattr(
        ModelStore,
        "get_default_model",
        lambda self, t: {"id": "model-d", "type": "VLLM", "status": "disabled"},
    )
    monkeypatch.setattr(
        ModelStore,
        "list_models",
        lambda self, type_=None, source=None: [{"id": "model-d", "type": "VLLM", "status": "disabled"}],
    )
    assert ModelStore().has_usable_vlm() is False
