"""L3 对话压缩：usage 触发、安全切分、结构化交接、ref 索引。"""

from __future__ import annotations

import json
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from utils.conversation_compaction import (
    HandoverDoc,
    archive_for_split,
    compact_archive_to_handover,
    extract_stored_refs,
    format_handover,
    merge_data_refs,
    needs_compaction,
    owning_assistant_index,
    parse_data_refs_from_handover,
    plan_compaction_split,
    prompt_tokens_from_response,
)
from utils.short_term_memory import message_stable_id


def _react_chain(n_pairs: int, *, stored: bool = False) -> list:
    msgs: list = [HumanMessage(content="帮我选品", id="h0")]
    for i in range(n_pairs):
        tid = f"call-{i}"
        msgs.append(
            AIMessage(
                content="",
                id=f"ai-{i}",
                tool_calls=[{"name": "doc_retrieval", "id": tid, "args": {"query": f"q{i}"}}],
            )
        )
        if stored:
            body = json.dumps(
                {
                    "__stored": True,
                    "__refId": f"aaaaaaaa-bbbb-cccc-dddd-{i:012d}",
                    "__toolType": "doc_retrieval",
                    "__originalLength": 20000,
                    "__summary": f"{i} records",
                    "__hint": "get_stored_data",
                }
            )
        else:
            body = json.dumps({"sources": [{"chunk_id": 10 + i, "document_id": f"doc-{i}"}]})
        msgs.append(ToolMessage(content=body, name="doc_retrieval", tool_call_id=tid, id=f"t-{i}"))
    return msgs


def test_needs_compaction_at_85_percent():
    assert needs_compaction(27199, 32000) is False
    assert needs_compaction(27200, 32000) is True
    assert needs_compaction(0, 32000) is False


def test_split_never_starts_on_tool_and_respects_floors():
    msgs = _react_chain(8)
    assert len(msgs) == 17
    split = plan_compaction_split(msgs, context_window=400, target_ratio=0.3, min_keep=6, min_delete=2)
    assert split is not None
    assert not isinstance(msgs[split], ToolMessage)
    owner = owning_assistant_index(msgs, split + 1) if isinstance(msgs[split + 1], ToolMessage) else split
    assert owner is not None
    archive = archive_for_split(msgs, split)
    assert len(archive) >= 2
    assert not any(isinstance(m, HumanMessage) and m.id == "h0" for m in archive) or split <= 0
    # 当前 Human 不进归档
    assert msgs[0] not in archive or split == 0


def test_split_returns_none_when_too_short():
    msgs = _react_chain(2)
    assert plan_compaction_split(msgs, context_window=32000, min_keep=6, min_delete=2) is None


def test_owning_assistant_walks_back_from_tool():
    msgs = _react_chain(1)
    assert owning_assistant_index(msgs, 2) == 1
    assert owning_assistant_index(msgs, 1) == 1


def test_handover_schema_and_deterministic_refs():
    msgs = _react_chain(3, stored=True)
    refs = extract_stored_refs(msgs)
    assert len(refs) == 3
    assert refs[0]["refId"].startswith("aaaaaaaa-")
    payload = json.dumps(
        {
            "original_request": "帮我选品",
            "stages": [{"stage": "检索", "did": "doc_retrieval query=q0", "got": "chunk_id=10"}],
            "abandoned_paths": [{"plan": "关键词用空格", "reason": "0 条结果"}],
        },
        ensure_ascii=False,
    )
    with patch(
        "utils.conversation_compaction._invoke_handover_llm",
        return_value=payload,
    ):
        text = compact_archive_to_handover(msgs, "", original_hint="帮我选品")
    assert "## 用户原始请求" in text
    assert "帮我选品" in text
    assert "## 执行历史" in text
    assert "chunk_id=10" in text
    assert "## 已放弃的路径" in text
    assert "关键词用空格" in text
    assert "## 数据引用索引" in text
    assert refs[1]["refId"] in text
    parsed = parse_data_refs_from_handover(text)
    assert {r["refId"] for r in parsed} == {r["refId"] for r in refs}


def test_llm_failure_keeps_structure_and_refs():
    msgs = _react_chain(2, stored=True)
    with patch(
        "utils.conversation_compaction._invoke_handover_llm",
        side_effect=TimeoutError("slow"),
    ):
        text = compact_archive_to_handover(msgs, "", original_hint="帮我选品")
    assert "## 用户原始请求" in text
    assert "## 已放弃的路径" in text
    assert extract_stored_refs(msgs)[0]["refId"] in text


def test_format_handover_keeps_refs_when_clipped():
    refs = [
        {
            "refId": f"aaaaaaaa-bbbb-cccc-dddd-{i:012d}",
            "tool": "doc_retrieval",
            "summary": f"{i} records",
        }
        for i in range(3)
    ]
    text = format_handover(
        HandoverDoc(
            original_request="帮我选品",
            stages=[
                {
                    "stage": "检索",
                    "did": "x" * 1800,
                    "got": "y" * 1800,
                }
            ],
            abandoned_paths=[{"plan": "空格关键词", "reason": "0 条"}],
            data_refs=refs,
        )
    )
    assert "## 数据引用索引" in text
    for item in refs:
        assert item["refId"] in text
    assert "## 已放弃的路径" in text
    assert "~~空格关键词~~" in text
    assert "帮我选品" in text


def test_trim_current_keeps_human_drops_compacted_prefix():
    from utils.conversation_compaction import trim_current_after_compaction

    current = [
        HumanMessage(content="继续", id="h-now"),
        AIMessage(content="", id="ai-old", tool_calls=[{"name": "doc_retrieval", "id": "c1", "args": {}}]),
        ToolMessage(content="{}", name="doc_retrieval", tool_call_id="c1", id="t-old"),
        AIMessage(content="新一步", id="ai-new"),
    ]
    out = trim_current_after_compaction(current, "t-old")
    assert out[0].id == "h-now"
    assert [m.id for m in out] == ["h-now", "ai-new"]


def test_merge_refs_from_previous_handover():
    prev = format_handover(
        HandoverDoc(
            original_request="旧目标",
            data_refs=[{"refId": "aaaaaaaa-bbbb-cccc-dddd-000000000001", "tool": "web_fetch", "summary": "page"}],
        )
    )
    merged = merge_data_refs(
        parse_data_refs_from_handover(prev),
        [{"refId": "aaaaaaaa-bbbb-cccc-dddd-000000000002", "tool": "list_chunks", "summary": "8 items"}],
    )
    assert [r["refId"] for r in merged] == [
        "aaaaaaaa-bbbb-cccc-dddd-000000000001",
        "aaaaaaaa-bbbb-cccc-dddd-000000000002",
    ]


def test_prompt_tokens_from_response():
    first = type("R", (), {"usage_metadata": {"input_tokens": 9000}, "response_metadata": {}})()
    assert prompt_tokens_from_response(first) == 9000
    second = type(
        "R2",
        (),
        {"usage_metadata": {}, "response_metadata": {"token_usage": {"prompt_tokens": 1200}}},
    )()
    assert prompt_tokens_from_response(second) == 1200


def test_maybe_compact_state_writes_handover_on_usage():
    from config.settings import settings
    from utils.conversation_compaction import maybe_compact_state

    msgs = _react_chain(8, stored=True)
    with (
        patch.object(settings, "stm_max_context_tokens", 500),
        patch.object(settings, "stm_compact_trigger_ratio", 0.1),
        patch.object(settings, "stm_compact_target_ratio", 0.3),
        patch.object(settings, "stm_compact_min_keep", 6),
        patch.object(settings, "stm_compact_min_delete", 2),
        patch(
            "utils.conversation_compaction._invoke_handover_llm",
            return_value=json.dumps(
                {
                    "original_request": "帮我选品",
                    "stages": [{"stage": "检索", "did": "搜了", "got": "chunk_id=10"}],
                    "abandoned_paths": [],
                }
            ),
        ),
    ):
        out = maybe_compact_state({"messages": msgs, "last_prompt_tokens": 400})
    assert "## 用户原始请求" in out["session_summary"]
    assert out["summary_upto_message_id"]
    assert any(
        message_stable_id(m) == out["summary_upto_message_id"] for m in msgs
    )
