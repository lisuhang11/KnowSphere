"""检索短句柄 cN / dN 解析。"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from utils.source_aliases import (
    format_read_handle_table,
    parse_source_handle,
    resolve_chunk_id,
    resolve_document_id,
)


def _retrieval_turn(chunk_ids: list[int], document_id: str = "doc-uuid") -> list:
    sources = [
        {
            "document_id": document_id,
            "file_name": "简历.pdf",
            "chunk_id": cid,
            "chunk_index": i,
            "snippet": f"片段{i}",
        }
        for i, cid in enumerate(chunk_ids)
    ]
    import json

    return [
        HumanMessage(content="介绍一下项目"),
        AIMessage(
            content="",
            tool_calls=[{"name": "doc_retrieval", "id": "t1", "args": {"query": "项目"}}],
        ),
        ToolMessage(
            content=json.dumps({"sources": sources}),
            name="doc_retrieval",
            tool_call_id="t1",
        ),
        AIMessage(content="见 [[c2]]"),
    ]


def test_parse_source_handle():
    assert parse_source_handle("c2") == ("c", 2)
    assert parse_source_handle("[[c2]]") == ("c", 2)
    assert parse_source_handle("d1") == ("d", 1)
    assert parse_source_handle(2) == (None, 2)
    assert parse_source_handle("2") == (None, 2)
    assert parse_source_handle("abc123def456") == (None, None)


def test_resolve_chunk_id_from_citation_index():
    messages = _retrieval_turn([101, 202, 303, 404, 505, 606])
    assert resolve_chunk_id(2, messages) == 202
    assert resolve_chunk_id("c2", messages) == 202
    assert resolve_chunk_id("[[c4]]", messages) == 404
    assert resolve_chunk_id(202, messages) == 202


def test_resolve_document_id_from_citation_or_alias():
    messages = _retrieval_turn([101, 202], document_id="ab12cd34ef56")
    assert resolve_document_id(2, messages) == "ab12cd34ef56"
    assert resolve_document_id("d1", messages) == "ab12cd34ef56"
    assert resolve_document_id("c2", messages) == "ab12cd34ef56"
    assert resolve_document_id("ab12cd34ef56", messages) == "ab12cd34ef56"


def test_handle_table_lists_real_ids():
    table = format_read_handle_table(
        [{"chunk_id": 202, "document_id": "doc-1", "file_name": "简历.pdf"}]
    )
    assert "c1 chunk_id=202 document_id=doc-1" in table
    assert "d1 document_id=doc-1" in table
    assert "[[cN]]" in table
