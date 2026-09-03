"""grep_chunks 关键词搜索。"""

from __future__ import annotations

import re
from unittest.mock import patch

from tools.retrieval.content import compile_grep_regex, extract_match_snippet
from tools.retrieval.grep_chunks import grep_chunks


def test_extract_match_snippet_around_hit():
    compiled = compile_grep_regex("ECONNRESET")
    text = "连接失败：" + ("x" * 250) + "ECONNRESET" + ("y" * 250) + "结束"
    snippet = extract_match_snippet(text, compiled)
    assert "ECONNRESET" in snippet
    assert snippet.startswith("…")
    assert "连接失败" not in snippet
    assert len(snippet) < len(text)


def test_compile_grep_rejects_invalid():
    assert compile_grep_regex("(") is None
    assert compile_grep_regex("abc|def") is not None


def test_grep_requires_kb():
    out = grep_chunks.invoke({"query": "ECONNRESET"}, config={"configurable": {"kb_ids": []}})
    assert out["sources"] == []
    assert "知识库" in (out.get("note") or "")


def test_grep_rejects_empty_and_invalid():
    cfg = {"configurable": {"kb_ids": [1]}}
    empty = grep_chunks.invoke({"query": "  "}, config=cfg)
    assert "不能为空" in (empty.get("note") or "")
    bad = grep_chunks.invoke({"query": "("}, config=cfg)
    assert "正则无效" in (bad.get("note") or "")


class _FakeStore:
    def __init__(self) -> None:
        self.rows = [
            {
                "id": 11,
                "document_id": "doc-1",
                "file_name": "运维手册.md",
                "chunk_index": 2,
                "content": "数据库超时后出现 ECONNRESET，请重试。",
                "knowledge_base_id": 1,
            },
            {
                "id": 12,
                "document_id": "doc-2",
                "file_name": "ECONNRESET 专题.md",
                "chunk_index": 0,
                "content": "本文记录网络中断。",
                "knowledge_base_id": 1,
            },
            {
                "id": 13,
                "document_id": "doc-3",
                "file_name": "其他.md",
                "chunk_index": 0,
                "content": "无关内容",
                "knowledge_base_id": 99,
            },
        ]

    def grep_chunks(self, pattern, kb_ids, owner=None, fetch_limit=200):
        compiled = re.compile(pattern, re.IGNORECASE)
        return [
            dict(r)
            for r in self.rows
            if r["knowledge_base_id"] in kb_ids
            and (compiled.search(r["content"] or "") or compiled.search(r["file_name"] or ""))
        ][:fetch_limit]


def test_grep_returns_snippet_not_full_and_prefers_filename():
    fake = _FakeStore()
    with patch("tools.retrieval.grep_chunks.ChunkStore", return_value=fake):
        out = grep_chunks.invoke(
            {"query": "ECONNRESET"},
            config={"configurable": {"kb_ids": [1]}},
        )
    assert len(out["sources"]) == 2
    assert out["sources"][0]["document_id"] == "doc-2"
    assert out["sources"][0]["chunk_id"] == 12
    assert "ECONNRESET" in out["sources"][0]["snippet"]
    assert "ECONNRESET" in out["sources"][1]["snippet"]
    assert "请重试" in out["sources"][1]["content"]
    assert "list_chunks" in (out.get("note") or "")


def test_grep_no_hit():
    fake = _FakeStore()
    with patch("tools.retrieval.grep_chunks.ChunkStore", return_value=fake):
        out = grep_chunks.invoke(
            {"query": "不存在的编码XYZ"},
            config={"configurable": {"kb_ids": [1]}},
        )
    assert out["sources"] == []
    assert "未找到" in (out.get("note") or "")
