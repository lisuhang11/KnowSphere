"""get_document_info 文档元数据。"""

from __future__ import annotations

from unittest.mock import patch

from tools.retrieval.get_document_info import get_document_info


class _FakeStore:
    def __init__(self) -> None:
        self.docs = [
            {
                "document_id": "doc-1",
                "file_name": "园区.md",
                "status": "completed",
                "error_message": None,
                "stage": "done",
                "updated_at": "2026-09-01T00:00:00",
                "knowledge_base_id": 1,
                "chunk_count": 4,
            },
            {
                "document_id": "doc-2",
                "file_name": "制度.pdf",
                "status": "processing",
                "error_message": None,
                "stage": "embed",
                "updated_at": "2026-09-02T00:00:00",
                "knowledge_base_id": 1,
                "chunk_count": 0,
            },
        ]

    def list_document_infos(self, kb_ids, document_ids=None, owner=None, limit=50):
        rows = [d for d in self.docs if d["knowledge_base_id"] in kb_ids]
        if document_ids:
            wanted = set(document_ids)
            rows = [d for d in rows if d["document_id"] in wanted]
        return {"total": len(rows), "documents": rows[:limit]}


def test_get_document_info_requires_kb():
    out = get_document_info.invoke({}, config={"configurable": {"kb_ids": []}})
    assert out["documents"] == []
    assert "知识库" in (out.get("note") or "")


def test_get_document_info_lists_kb():
    fake = _FakeStore()
    with patch("tools.retrieval.get_document_info.ChunkStore", return_value=fake):
        out = get_document_info.invoke({}, config={"configurable": {"kb_ids": [1]}})
    assert len(out["documents"]) == 2
    assert out["documents"][0]["file_name"] == "园区.md"
    assert "不含正文" in (out.get("note") or "")
    assert "content" not in out["documents"][0]


def test_get_document_info_by_ids():
    fake = _FakeStore()
    with patch("tools.retrieval.get_document_info.ChunkStore", return_value=fake):
        out = get_document_info.invoke(
            {"document_ids": ["doc-2", "missing"]},
            config={"configurable": {"kb_ids": [1]}},
        )
    assert [d["document_id"] for d in out["documents"]] == ["doc-2"]
    assert "missing" in (out.get("note") or "")


def test_get_document_info_single_alias():
    fake = _FakeStore()
    with patch("tools.retrieval.get_document_info.ChunkStore", return_value=fake):
        out = get_document_info.invoke(
            {"document_id": "doc-1"},
            config={"configurable": {"kb_ids": [1]}},
        )
    assert out["documents"][0]["chunk_count"] == 4


def test_get_document_info_missing_all():
    fake = _FakeStore()
    with patch("tools.retrieval.get_document_info.ChunkStore", return_value=fake):
        out = get_document_info.invoke(
            {"document_ids": ["nope"]},
            config={"configurable": {"kb_ids": [1]}},
        )
    assert out["documents"] == []
    assert "未找到" in (out.get("note") or "")
