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


def test_row_to_dict_requires_full_column_set():
    from datetime import datetime, timezone

    from utils.temporary_attachments import TemporaryAttachmentStore

    now = datetime.now(timezone.utc)
    full = (
        "aid",
        "sid",
        "owner/chat-attachments/sid/aid/a.png",
        "a.png",
        "png",
        "image/png",
        12,
        "uploaded",
        None,
        None,
        [],
        None,
        None,
        now,
        now,
        now,
    )
    d = TemporaryAttachmentStore._row_to_dict(full)
    assert d["file_name"] == "a.png"
    assert d["storage_key"].endswith("a.png")
    assert d["chunks"] is None
    assert d["image_refs"] == []

    short = (
        "aid",
        "sid",
        "a.png",
        "png",
        "image/png",
        12,
        "uploaded",
        None,
        None,
        None,
        now,
        now,
        now,
    )
    try:
        TemporaryAttachmentStore._row_to_dict(short)
        raise AssertionError("expected ValueError for 13-column row")
    except ValueError as exc:
        assert "got 13" in str(exc)


def test_rewrite_markdown_image_refs_uses_serving_urls():
    from utils.attachment_images import rewrite_markdown_image_refs

    md = "见图\n\n![图1](images/p1.png)\n\n正文"
    out = rewrite_markdown_image_refs(
        md,
        {"images/p1.png": "/api/sessions/s/attachments/a/images/p1.png"},
    )
    assert "(images/p1.png)" not in out
    assert "/api/sessions/s/attachments/a/images/p1.png" in out


def test_persist_attachment_parse_uploads_and_rewrites():
    from ingestion.parser.base_parser import ImageRef, ParseResult
    from utils.attachment_images import persist_attachment_parse

    parsed = ParseResult(
        markdown="![截图](images/fig.png)\n\n章节一",
        image_refs=[
            ImageRef(filename="fig.png", original_ref="fig.png", mime_type="image/png", data=b"\x89PNG"),
        ],
    )
    store = MagicMock()
    with patch("utils.attachment_images.require_object_store", return_value=store):
        md, refs = persist_attachment_parse("sess-1", "att-1", parsed, file_name="notes.pdf")
    assert refs[0]["filename"] == "fig.png"
    assert refs[0]["storage_key"].endswith("/images/fig.png")
    assert "/attachments/att-1/images/fig.png" in md
    assert "data:image" not in md
    store.put_bytes.assert_called_once()


def test_persist_image_file_reuses_original_preview():
    from ingestion.parser.base_parser import ImageRef, ParseResult
    from utils.attachment_images import persist_attachment_parse

    parsed = ParseResult(
        markdown="![a.png](images/a.png)",
        image_refs=[
            ImageRef(filename="a.png", original_ref="a.png", mime_type="image/png", data=b"\x89PNG"),
        ],
    )
    store = MagicMock()
    with patch("utils.attachment_images.require_object_store", return_value=store):
        md, refs = persist_attachment_parse("sess-1", "att-1", parsed, file_name="a.png")
    assert refs[0]["url"].endswith("/attachments/att-1/preview")
    assert "/preview" in md
    store.put_bytes.assert_not_called()


def test_visual_query_injects_extracted_images():
    from utils.attachment_resolve import build_human_message_with_attachments

    rows = [
        {
            "id": "att-1",
            "file_name": "report.pdf",
            "file_type": "pdf",
            "content": "![图](/api/sessions/s/attachments/att-1/images/p1.png)\n正文",
            "image_refs": [
                {
                    "filename": "p1.png",
                    "url": "/api/sessions/s/attachments/att-1/images/p1.png",
                }
            ],
        }
    ]
    msg = build_human_message_with_attachments("这张图是什么", rows, "s")
    images = msg.additional_kwargs.get("ks_images") or []
    assert images
    assert images[0]["url"].endswith("/images/p1.png")


def test_is_image_upload_by_name_and_mime():
    from utils.temporary_attachments import is_image_upload

    assert is_image_upload("a.png", "")
    assert is_image_upload("photo.JPG", "application/octet-stream")
    assert is_image_upload("a.pdf", "image/png")
    assert not is_image_upload("a.pdf", "application/pdf")
    assert not is_image_upload("notes.txt", "")


def test_approx_text_ignores_image_markdown():
    from utils.attachment_images import approx_attachment_text_chars

    assert approx_attachment_text_chars("![x](images/a.png)") == 0
    body = "章节内容。" * 40
    md = f"![图](images/p1.png)\n\n{body}"
    assert approx_attachment_text_chars(md) == len(body)


def test_vlm_skipped_when_parser_already_extracted_enough_text():
    from utils.attachment_vlm import maybe_vlm_enrich_attachment

    content = "原生文本页。" * 80
    with patch("utils.attachment_vlm.vlm_ocr_images") as mock_ocr:
        out, extra = maybe_vlm_enrich_attachment(
            content=content,
            file_name="notes.pdf",
            original_storage_key="k",
            image_refs=[{"storage_key": "page.jpg", "filename": "p.jpg"}],
        )
    mock_ocr.assert_not_called()
    assert extra == ""
    assert out == content

    screenshot = ("合同条款第几条。" * 40) + "\n\n![a.png](/preview)"
    with patch("utils.attachment_vlm.vlm_ocr_images") as mock_ocr:
        maybe_vlm_enrich_attachment(
            content=screenshot,
            file_name="a.png",
            original_storage_key="orig",
            image_refs=[],
        )
    mock_ocr.assert_not_called()


def test_image_file_low_text_uses_ocr_then_caption_if_sparse():
    from utils.attachment_vlm import VLM_CAPTION_PROMPT, VLM_OCR_PROMPT, vlm_ocr_images

    llm = MagicMock()
    llm.invoke.side_effect = [
        MagicMock(content="No text content"),
        MagicMock(content="一张架构示意图"),
    ]
    with patch("utils.attachment_vlm._resolve_vlm_id", return_value="vlm-1"):
        with patch("utils.attachment_vlm.create_vlm_model", return_value=llm):
            text = vlm_ocr_images([(b"fake", "a.png")], scanned=False, caption_fallback=True)
    assert "架构示意" in text
    prompts = [c.args[0][0]["content"][0]["text"] for c in llm.invoke.call_args_list]
    assert prompts == [VLM_OCR_PROMPT, VLM_CAPTION_PROMPT]


def test_image_file_rich_vlm_ocr_skips_caption():
    from utils.attachment_vlm import vlm_ocr_images

    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content="识别出的段落。" * 10)
    with patch("utils.attachment_vlm._resolve_vlm_id", return_value="vlm-1"):
        with patch("utils.attachment_vlm.create_vlm_model", return_value=llm):
            text = vlm_ocr_images([(b"fake", "scan.png")], scanned=False, caption_fallback=True)
    assert llm.invoke.call_count == 1
    assert "识别出的段落" in text


def test_scanned_document_vlm_ocr_never_captions():
    from utils.attachment_vlm import VLM_OCR_SCANNED_PROMPT, vlm_ocr_images

    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content="No text content")
    with patch("utils.attachment_vlm._resolve_vlm_id", return_value="vlm-1"):
        with patch("utils.attachment_vlm.create_vlm_model", return_value=llm):
            text = vlm_ocr_images([(b"fake", "p.jpg")], scanned=True, caption_fallback=False)
    assert llm.invoke.call_count == 1
    assert text == ""
    prompt = llm.invoke.call_args[0][0][0]["content"][0]["text"]
    assert prompt == VLM_OCR_SCANNED_PROMPT


def test_maybe_vlm_enrich_low_text_pdf_uses_scanned_ocr():
    from utils.attachment_vlm import maybe_vlm_enrich_attachment

    store = MagicMock()
    store.get_bytes.return_value = (b"page", "image/jpeg")
    with patch("utils.attachment_vlm.require_object_store", return_value=store):
        with patch("utils.attachment_vlm.vlm_ocr_images", return_value="扫描页正文") as mock:
            out, extra = maybe_vlm_enrich_attachment(
                content="![p](/api/sessions/s/attachments/a/images/p.jpg)",
                file_name="scan.pdf",
                original_storage_key="orig",
                image_refs=[{"storage_key": "page.jpg", "filename": "p.jpg"}],
            )
    mock.assert_called_once()
    assert mock.call_args.kwargs["scanned"] is True
    assert mock.call_args.kwargs["caption_fallback"] is False
    assert extra == "扫描页正文"
    assert "扫描页正文" in out
