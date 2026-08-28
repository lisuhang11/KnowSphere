"""回归：LangGraph config 注入与 kb_ids 解析（P0 核心修复）。

覆盖历史 bug：langchain-core 新版移除 get_config 后，doc_retrieval 拿不到
会话选定的知识库 → 永远"未选择"→ 不检索 → 幻觉回答。
"""

from __future__ import annotations

from unittest.mock import patch

from tools.retrieval.doc_retrieval import _kb_ids_from_config, doc_retrieval, _retrieval

def test_no_config_returns_empty_without_external_calls():
    """无 config（直接调用/无知识库会话）：空 sources + 说明，且不触发检索/LLM/embedding。"""
    result = doc_retrieval.invoke({"query": "你好"})
    assert result["sources"] == []
    assert "未选择知识库" in result["note"]

def test_config_injection_reaches_tool():
    """config 注入生效：指定 kb_ids 后走 KB 校验路径（而非"未选择"）。"""
    with patch.object(
        _retrieval.store,
        "get_knowledge_base_configs",
        return_value={},
    ):
        result = doc_retrieval.invoke(
            {"query": "你好"}, config={"configurable": {"kb_ids": [999999999]}}
        )
    assert result["sources"] == []
    assert "所选知识库不存在" in result["note"]

def test_kb_ids_from_config_none():
    assert _kb_ids_from_config(None) is None

def test_kb_ids_from_config_missing_keys():
    assert _kb_ids_from_config({}) is None
    assert _kb_ids_from_config({"configurable": {}}) is None
    assert _kb_ids_from_config({"configurable": {"kb_ids": None}}) is None
    assert _kb_ids_from_config({"configurable": {"kb_ids": []}}) is None

def test_kb_ids_from_config_parses_ints():
    assert _kb_ids_from_config({"configurable": {"kb_ids": ["1", 2, "3"]}}) == [1, 2, 3]

def test_kb_ids_from_config_skips_garbage():
    assert _kb_ids_from_config({"configurable": {"kb_ids": ["abc", None, "7", {}, 2.5]}}) == [7]

def test_kb_ids_from_config_empty_after_filter():
    assert _kb_ids_from_config({"configurable": {"kb_ids": ["x", None, {}]}}) is None
