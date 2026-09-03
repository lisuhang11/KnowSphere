"""list_chunks 精读工具。"""

from __future__ import annotations

from unittest.mock import patch

from tools.catalog import PPT_AGENT_TOOL_NAMES, REASONING_TOOL_NAMES
from tools.retrieval.content import clip_content
from tools.retrieval.list_chunks import list_chunks


def test_clip_content():
    assert clip_content("abc", 10) == "abc"
    assert clip_content("abcdef", 3) == "abc…"


def test_tools_for_state_includes_list_chunks_with_kb():
    from agents.nodes.agent import tools_for_state
    from tools import get_tools

    names = {
        t.name for t in tools_for_state({"configurable": {"kb_ids": [1]}}, get_tools())
    }
    assert "list_chunks" in names
    assert "doc_retrieval" in names
    names_off = {
        t.name for t in tools_for_state({"configurable": {"kb_ids": []}}, get_tools())
    }
    assert "list_chunks" not in names_off
    assert "doc_retrieval" not in names_off


def test_reasoning_and_ppt_include_list_chunks():
    assert "list_chunks" in REASONING_TOOL_NAMES
    assert "list_chunks" in PPT_AGENT_TOOL_NAMES
    assert REASONING_TOOL_NAMES.index("list_chunks") == REASONING_TOOL_NAMES.index(
        "doc_retrieval"
    ) + 1


def test_list_chunks_requires_kb():
    out = list_chunks.invoke(
        {"document_id": "doc-1"},
        config={"configurable": {"kb_ids": []}},
    )
    assert out["sources"] == []
    assert "知识库" in (out.get("note") or "")


def test_list_chunks_requires_id():
    out = list_chunks.invoke({}, config={"configurable": {"kb_ids": [1]}})
    assert out["sources"] == []
    assert "chunk_id" in (out.get("note") or "")


class _FakeStore:
    def __init__(self) -> None:
        self.chunks = {
            11: {
                "id": 11,
                "document_id": "doc-1",
                "file_name": "园区.md",
                "chunk_index": 2,
                "content": "子块：北门开放时间 8:00",
                "parent_chunk_id": 10,
                "knowledge_base_id": 1,
                "chunk_type": "text",
            },
            10: {
                "id": 10,
                "document_id": "doc-1",
                "file_name": "园区.md",
                "chunk_index": 0,
                "content": "园区导览。子块：北门开放时间 8:00。南门 9:00。",
                "parent_chunk_id": None,
                "knowledge_base_id": 1,
                "chunk_type": "parent_text",
            },
        }

    def get_chunks_by_ids(self, ids, owner=None):
        return [dict(self.chunks[i]) for i in ids if i in self.chunks]

    def get_document_config(self, document_id, owner=None):
        if document_id != "doc-1":
            return None
        return {"file_name": "园区.md", "kb_id": 1}

    def list_chunks(self, document_id, owner=None, page=1, page_size=20, include_parent_text=False, offset=None):
        kids = [self.chunks[11]]
        skip = 0 if offset is None else int(offset)
        sliced = kids[skip : skip + page_size]
        return {"total": len(kids), "page": 1, "page_size": page_size, "offset": skip, "chunks": sliced}


def test_list_chunks_reads_one_with_parent_resolve():
    fake = _FakeStore()
    with patch("tools.retrieval.list_chunks.ChunkStore", return_value=fake):
        out = list_chunks.invoke(
            {"chunk_id": 11},
            config={"configurable": {"kb_ids": [1]}},
        )
    assert len(out["sources"]) == 1
    src = out["sources"][0]
    assert src["chunk_id"] == 11
    assert src["parent_resolved"] is True
    assert "南门 9:00" in src["content"]
    assert src["snippet"]


def test_list_chunks_denies_other_kb():
    fake = _FakeStore()
    with patch("tools.retrieval.list_chunks.ChunkStore", return_value=fake):
        out = list_chunks.invoke(
            {"chunk_id": 11},
            config={"configurable": {"kb_ids": [99]}},
        )
    assert out["sources"] == []
    assert "范围" in (out.get("note") or "")


def test_list_chunks_pages_document():
    fake = _FakeStore()
    with patch("tools.retrieval.list_chunks.ChunkStore", return_value=fake):
        out = list_chunks.invoke(
            {"document_id": "doc-1", "offset": 0, "limit": 8},
            config={"configurable": {"kb_ids": [1]}},
        )
    assert len(out["sources"]) == 1
    assert out["sources"][0]["document_id"] == "doc-1"
    assert "共 1 块" in (out.get("note") or "")


def test_list_chunks_offset_out_of_range():
    fake = _FakeStore()
    with patch("tools.retrieval.list_chunks.ChunkStore", return_value=fake):
        out = list_chunks.invoke(
            {"document_id": "doc-1", "offset": 5},
            config={"configurable": {"kb_ids": [1]}},
        )
    assert out["sources"] == []
    assert "超出范围" in (out.get("note") or "")
