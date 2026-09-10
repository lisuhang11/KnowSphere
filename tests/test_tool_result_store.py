"""L1 工具结果外置：触发条件、引用形态、取回与来源还原。"""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agents.nodes.sources import collect_sources
from agents.state import KnowSphereState
from tools.storage import get_stored_data
from utils.source_aliases import resolve_chunk_id
from utils.tool_result_store import (
    REF_KEYS,
    build_preview,
    build_summary,
    is_stored_ref,
    largest_array_len,
    offload_tool_message,
    parse_tool_payload,
    reset_tool_result_cache,
    should_store,
)


def setup_function() -> None:
    reset_tool_result_cache()


def test_should_store_by_char_and_array():
    small = {"sources": [{"id": i} for i in range(3)]}
    assert should_store(small, content_len=100, char_limit=8000, array_limit=10) is False
    assert should_store("x" * 8001, content_len=8001, char_limit=8000, array_limit=10) is True
    assert should_store("x" * 8000, content_len=8000, char_limit=8000, array_limit=10) is False
    eleven = {"sources": [{"id": i} for i in range(11)]}
    assert should_store(eleven, content_len=200, char_limit=8000, array_limit=10) is True
    ten = {"sources": [{"id": i} for i in range(10)]}
    assert should_store(ten, content_len=200, char_limit=8000, array_limit=10) is False
    assert should_store({"ok": True}, content_len=8, always_store=True) is True


def test_largest_array_len_reads_top_level_lists():
    assert largest_array_len({"sources": [1, 2, 3], "note": "x"}) == 3
    assert largest_array_len([1, 2]) == 2
    assert largest_array_len("plain") == 0


def test_summary_is_deterministic_and_omits_preview():
    payload = {
        "sources": [
            {
                "document_id": "doc-1",
                "file_name": "园区.md",
                "chunk_id": 11,
                "snippet": "北门 8:00",
                "content": "A" * 5000,
            }
        ]
    }
    summary = build_summary(payload, "list_chunks", original_length=6000)
    preview = build_preview(json.dumps(payload), max_chars=12000)
    assert "1 items" in summary
    assert "c1" in summary
    assert "chunk_id=11" in summary
    assert "A" * 200 not in summary
    assert preview.startswith("{")
    assert "preview" not in summary.lower()


def test_offload_replaces_large_message_and_keeps_preview_out_of_prompt():
    sources = [
        {
            "document_id": "d1",
            "file_name": "a.md",
            "chunk_id": 100 + i,
            "snippet": f"片段{i}",
            "content": "正文" * 400,
        }
        for i in range(12)
    ]
    payload = {"query": "q", "sources": sources}
    raw = json.dumps(payload, ensure_ascii=False)
    msg = ToolMessage(content=raw, name="doc_retrieval", tool_call_id="c1", id="t1")
    out = offload_tool_message(msg)
    ref = parse_tool_payload(out.content)
    assert is_stored_ref(ref)
    assert set(REF_KEYS) <= set(ref)
    assert ref["__toolType"] == "doc_retrieval"
    assert ref["__originalLength"] == len(raw)
    assert "get_stored_data" in ref["__hint"]
    assert "正文" * 20 not in str(out.content)
    assert build_preview(raw) not in str(out.content)
    assert out.additional_kwargs.get("ks_ref_id") == ref["__refId"]


def test_small_tool_message_stays_inline():
    payload = {"query": "q", "sources": [{"document_id": "d1", "snippet": "hi", "chunk_id": 1}]}
    msg = ToolMessage(
        content=json.dumps(payload),
        name="doc_retrieval",
        tool_call_id="c1",
    )
    assert offload_tool_message(msg) is msg


def test_get_stored_data_returns_original_without_preview():
    sources = [{"document_id": "d1", "chunk_id": 9, "content": "X" * 9000} for _ in range(2)]
    raw = json.dumps({"sources": sources})
    msg = ToolMessage(content=raw, name="list_chunks", tool_call_id="c1")
    out = offload_tool_message(msg)
    ref_id = parse_tool_payload(out.content)["__refId"]
    fetched = get_stored_data.invoke({"ref_id": ref_id})
    assert fetched["data"]["sources"][0]["chunk_id"] == 9
    assert fetched["data"]["sources"][0]["content"].startswith("X")
    assert "preview" not in fetched
    again = offload_tool_message(
        ToolMessage(content=json.dumps(fetched), name="get_stored_data", tool_call_id="c2")
    )
    assert again.name == "get_stored_data"
    assert "X" * 100 in str(again.content)


def test_collect_sources_and_handles_hydrate_stored_ref():
    sources = [
        {"document_id": "doc-1", "file_name": "a.md", "chunk_id": 101 + i, "snippet": f"s{i}"}
        for i in range(11)
    ]
    raw = json.dumps({"sources": sources})
    stored = offload_tool_message(
        ToolMessage(content=raw, name="grep_chunks", tool_call_id="g1")
    )
    assert is_stored_ref(parse_tool_payload(stored.content))
    state: KnowSphereState = {
        "messages": [HumanMessage(content="q"), stored],
    }
    out = collect_sources(state)
    assert len(out["last_sources"]) == 11
    assert out["last_sources"][1]["chunk_id"] == 102
    assert resolve_chunk_id("c2", state["messages"]) == 102


def test_get_stored_data_missing_ref():
    out = get_stored_data.invoke({"ref_id": "missing-id"})
    assert out["data"] is None
    assert "找不到" in out["error"]


def test_historical_stored_retrieval_keeps_handles_not_body():
    from utils.short_term_memory import COMPACT_RETRIEVAL, build_memory_view

    sources = [
        {
            "document_id": "d1",
            "file_name": "secret.txt",
            "chunk_id": 7,
            "snippet": "机密正文",
            "content": "机密" * 3000,
        }
        for _ in range(11)
    ]
    stored = offload_tool_message(
        ToolMessage(
            content=json.dumps({"sources": sources}, ensure_ascii=False),
            name="doc_retrieval",
            tool_call_id="c-old",
            id="t-old",
        )
    )
    messages = [
        HumanMessage(content="上一问", id="h1"),
        AIMessage(
            content="",
            id="ai-1",
            tool_calls=[{"name": "doc_retrieval", "id": "c-old", "args": {"query": "x"}}],
        ),
        stored,
        AIMessage(content="答", id="a1"),
        HumanMessage(content="这一问", id="h-now"),
    ]
    view = build_memory_view(messages, keep_turns=8, redact_old_retrieval=True)
    old = [m for m in view.window_messages if isinstance(m, ToolMessage)]
    assert old and str(old[0].content).startswith(COMPACT_RETRIEVAL)
    assert "机密" * 20 not in str(old[0].content)
    assert "chunk_id=7" in str(old[0].content)


def test_tools_for_state_always_injects_get_stored_data():
    from agents.nodes.agent import tools_for_state
    from tools import get_tools

    with_kb = {t.name for t in tools_for_state({"configurable": {"kb_ids": [1]}}, get_tools())}
    without = {t.name for t in tools_for_state({"configurable": {"kb_ids": []}}, get_tools())}
    assert "get_stored_data" in with_kb
    assert "get_stored_data" in without
    assert "list_chunks" not in without
