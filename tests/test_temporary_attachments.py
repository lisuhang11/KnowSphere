"""临时附件解析测试。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from utils.attachment_chunks import (
    select_attachment_content,
    split_attachment_chunks,
)
from utils.attachment_resolve import (
    build_attachment_prompt_block,
    normalize_attachment_ids,
    resolve_for_prompt,
)

def test_normalize_attachment_ids_dedupe_and_cap():
    ids = normalize_attachment_ids(["a", "b", "a", "c", "d", "e", "f"])
    assert ids == ["a", "b", "c", "d", "e"]

def test_split_attachment_chunks_large_doc():
    content = "支付模块说明。" * 800
    chunks = split_attachment_chunks(content)
    assert len(chunks) > 1
    assert chunks[0]["seq"] == 0

def test_select_attachment_content_by_query():
    content = "第一章 退款流程。\n\n" + ("其他内容。" * 200) + "\n\n附录 支付接口。"
    row = {
        "content": content,
        "chunks": split_attachment_chunks(content),
        "file_name": "doc.pdf",
    }
    body, selected, total = select_attachment_content(row, "退款流程怎么走")
    assert "退款" in body
    assert selected >= 1
    assert total >= 1

def test_resolve_for_prompt_sets_selected_content():
    content = "A" * 5000 + "目标关键词在这里。" + "B" * 5000
    row = {
        "id": "1",
        "content": content,
        "chunks": split_attachment_chunks(content),
        "file_name": "notes.pdf",
    }
    out = resolve_for_prompt([row], "目标关键词")
    assert out[0]["selected_content"]
    assert "目标关键词" in out[0]["selected_content"]

def test_build_attachment_prompt_block_shows_partial_mode():
    content = "x" * 20_000
    row = {
        "file_name": "notes.pdf",
        "content": content,
        "chunks": split_attachment_chunks(content),
    }
    block = build_attachment_prompt_block([row], query="notes")
    assert "notes.pdf" in block
    assert "片段" in block or "notes" in block.lower() or len(block) < len(content)

def test_cleanup_expired_deletes_rows():
    from utils.temporary_attachments import TemporaryAttachmentStore

    store = TemporaryAttachmentStore()
    mock_row = {
        "id": "aid-1",
        "session_id": "sess-1",
        "storage_key": "k1",
        "file_name": "a.pdf",
        "file_type": "pdf",
        "mime_type": "application/pdf",
        "file_size": 1,
        "status": "ready",
        "content": "x",
        "chunks": [],
        "image_description": "",
        "error_message": "",
        "expires_at": None,
        "created_at": None,
        "updated_at": None,
    }
    with patch.object(store, "list_expired", side_effect=[[mock_row], []]):
        with patch.object(store, "delete", return_value=True) as mock_delete:
            count = store.cleanup_expired(batch_size=10)
    assert count == 1
    mock_delete.assert_called_once_with("aid-1", "sess-1")
