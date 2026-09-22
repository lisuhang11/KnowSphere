"""L2 语义压缩：蒸馏成功、失败降级、preview 不经 LLM、TTL。"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from langchain_core.messages import ToolMessage

from config.settings import settings
from utils.semantic_compressor import (
    build_fallback_truncated,
    is_fallback_truncated,
    semantic_compress,
    should_semantic_compress,
)
from utils.tool_result_store import (
    build_preview,
    get_stored_result,
    offload_tool_message,
    parse_tool_payload,
    reset_tool_result_cache,
)


def setup_function() -> None:
    reset_tool_result_cache()


def teardown_function() -> None:
    reset_tool_result_cache()


def _huge_payload(n: int = 12, body: str = "正文") -> tuple[dict, str]:
    payload = {
        "query": "q",
        "sources": [
            {
                "document_id": "d1",
                "file_name": "a.md",
                "chunk_id": 100 + i,
                "snippet": f"片段{i}",
                "content": body * 500,
            }
            for i in range(n)
        ],
    }
    return payload, json.dumps(payload, ensure_ascii=False)


def test_should_compress_only_over_limit():
    with patch.object(settings, "tool_result_compress_enabled", True):
        assert should_semantic_compress(10000, char_limit=10000) is False
        assert should_semantic_compress(10001, char_limit=10000) is True
    with patch.object(settings, "tool_result_compress_enabled", False):
        assert should_semantic_compress(50_000, char_limit=10000) is False


def test_fallback_is_json_wrapped_substring_not_raw_cut():
    original = "".join(f"{i:04d}-" for i in range(1200))
    wrapped = build_fallback_truncated(
        original, tool_name="web_fetch", original_length=len(original), max_chars=3000
    )
    parsed = json.loads(wrapped)
    assert parsed["__fallbackTruncated"] is True
    assert parsed["__toolType"] == "web_fetch"
    assert parsed["__originalLength"] == len(original)
    assert parsed["content"] == original[:3000]
    assert original[3000:3100] not in parsed["content"]
    assert is_fallback_truncated(wrapped)


def test_semantic_compress_failure_uses_fallback():
    original = "Z" * 12000
    with patch(
        "utils.semantic_compressor._invoke_compressor",
        side_effect=TimeoutError("slow"),
    ):
        out = semantic_compress(
            original, tool_name="list_chunks", original_length=len(original)
        )
    assert is_fallback_truncated(out)
    assert json.loads(out)["content"] == original[: settings.tool_result_compress_fallback_chars]


def test_offload_uses_llm_summary_and_keeps_original():
    _payload, raw = _huge_payload()
    assert len(raw) > 10000
    msg = ToolMessage(content=raw, name="doc_retrieval", tool_call_id="c1")
    with (
        patch.object(settings, "tool_result_compress_enabled", True),
        patch(
            "utils.semantic_compressor._invoke_compressor",
            return_value="共 12 条记录，状态均为 active，id 从 100 起",
        ) as invoke,
    ):
        out = offload_tool_message(msg)
    assert invoke.called
    ref = parse_tool_payload(out.content)
    assert ref["__compressed"] is True
    assert ref["__summary"] == "共 12 条记录，状态均为 active，id 从 100 起"
    assert "正文" * 20 not in str(out.content)
    assert build_preview(raw) not in str(out.content)
    record = get_stored_result(ref["__refId"])
    assert record is not None
    original = parse_tool_payload(record.payload)
    assert original["sources"][0]["chunk_id"] == 100
    assert original["sources"][0]["content"].startswith("正文")
    assert record.preview == raw[: len(record.preview)]


def test_offload_llm_failure_marks_fallback_and_preview_is_substring():
    _payload, raw = _huge_payload(body="原始字段keep_me")
    msg = ToolMessage(content=raw, name="list_chunks", tool_call_id="c1")
    with (
        patch.object(settings, "tool_result_compress_enabled", True),
        patch(
            "utils.semantic_compressor._invoke_compressor",
            side_effect=RuntimeError("boom"),
        ),
    ):
        out = offload_tool_message(msg)
    ref = parse_tool_payload(out.content)
    assert ref["__fallbackTruncated"] is True
    fallback = json.loads(ref["__summary"])
    assert fallback["__fallbackTruncated"] is True
    assert fallback["content"] == raw[: settings.tool_result_compress_fallback_chars]
    assert fallback["content"] == raw[: len(fallback["content"])]
    record = get_stored_result(ref["__refId"])
    assert record is not None
    assert record.preview == raw[: settings.tool_result_preview_chars]
    assert record.preview == raw[: len(record.preview)]
    assert "preview" not in ref


def test_midsize_result_skips_llm():
    payload = {"sources": [{"chunk_id": 1, "content": "x" * 200} for _ in range(11)]}
    raw = json.dumps(payload)
    assert len(raw) <= 10000
    msg = ToolMessage(content=raw, name="grep_chunks", tool_call_id="g1")
    with (
        patch.object(settings, "tool_result_compress_enabled", True),
        patch("utils.semantic_compressor._invoke_compressor") as invoke,
    ):
        out = offload_tool_message(msg)
    invoke.assert_not_called()
    ref = parse_tool_payload(out.content)
    assert "__compressed" not in ref
    assert "11 items" in ref["__summary"]


def test_expired_stored_result_is_unreadable():
    _payload, raw = _huge_payload()
    msg = ToolMessage(content=raw, name="web_fetch", tool_call_id="c1")
    with (
        patch.object(settings, "tool_result_compress_enabled", True),
        patch.object(settings, "tool_result_ttl_sec", 1),
        patch(
            "utils.semantic_compressor._invoke_compressor",
            return_value="网页正文要点",
        ),
    ):
        out = offload_tool_message(msg)
    ref_id = parse_tool_payload(out.content)["__refId"]
    record = get_stored_result(ref_id)
    assert record is not None
    expired = datetime.now(UTC) - timedelta(seconds=5)
    object.__setattr__(record, "expires_at", expired)
    reset_tool_result_cache()
    # 直接塞回过期记录
    from utils.tool_result_store import _CACHE

    _CACHE[ref_id] = record
    assert get_stored_result(ref_id) is None
