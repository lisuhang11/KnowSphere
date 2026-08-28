"""Query pipeline：prepare_context、query_understand、route、本地 expansion。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agents.nodes.prepare_context import extract_history_pairs, prepare_context
from agents.nodes.query_understand import query_understand, route_after_understand
from schemas.query import (
    needs_retrieval,
    is_vague_query,
    is_meta_rewrite,
    sanitize_rewrite_query,
    parse_query_understand_json,
)
from tools.retrieval.query_expansion import expand_queries_local

def test_extract_history_pairs_excludes_current_turn():
    messages = [
        HumanMessage(content="embedding 用的什么"),
        ToolMessage(content="{}", name="doc_retrieval", tool_call_id="p1"),
        AIMessage(content="bge-m3"),
        HumanMessage(content="它的维度呢"),
    ]
    current, pairs = extract_history_pairs(messages, max_rounds=5)
    assert current == "它的维度呢"
    assert len(pairs) == 1
    assert pairs[0]["query"] == "embedding 用的什么"
    assert "bge-m3" in pairs[0]["answer"]

def test_needs_retrieval_rules():
    assert needs_retrieval("kb_search", True)
    assert not needs_retrieval("clarification", True)
    assert not needs_retrieval("clarification", True, query="这是什么")
    assert not needs_retrieval("kb_search", True, query="这是什么")
    assert needs_retrieval("kb_search", True, query="SuIM 项目架构是什么")
    assert not needs_retrieval("follow_up", True)
    assert not needs_retrieval("summarize", True)
    assert not needs_retrieval("greeting", True)
    assert not needs_retrieval("image_only", True)
    assert not needs_retrieval("doc_only", True)
    assert not needs_retrieval("kb_search", False)

def test_sanitize_rewrite_query():
    assert is_meta_rewrite("请重新在知识库中查找关于张三的信息")
    assert sanitize_rewrite_query(
        "请重新在知识库中查找关于张三的信息", "张三的信息"
    ) == "张三的信息"
    assert sanitize_rewrite_query("张三的详细信息是什么", "张三") == "张三的详细信息是什么"

def test_parse_query_understand_json():
    raw = '说明如下：{"rewrite_query":"RAG 架构","intent":"kb_search"}'
    parsed = parse_query_understand_json(raw)
    assert parsed["rewrite_query"] == "RAG 架构"
    assert parsed["intent"] == "kb_search"

def test_is_vague_query():
    assert is_vague_query("这是什么")
    assert is_vague_query("  这是啥  ")
    assert not is_vague_query("这是什么", history_pairs=[{"query": "a", "answer": "b"}])
    assert not is_vague_query("SuIM 即时通讯系统是什么")

def test_route_after_understand():
    assert route_after_understand({"intent": "follow_up", "kb_selected": True}) == "agent"
    assert route_after_understand({"intent": "kb_search", "kb_selected": True}) == "prefetch_retrieval"
    assert route_after_understand({
        "intent": "clarification",
        "kb_selected": True,
        "current_query": "这是什么",
    }) == "agent"
    assert route_after_understand({
        "intent": "kb_search",
        "kb_selected": True,
        "current_query": "这是什么",
    }) == "agent"

def test_expand_queries_local_strips_question_words():
    variants = expand_queries_local("什么是支付模块退款流程", max_variants=5)
    assert variants
    assert any("支付" in v or "退款" in v for v in variants)

def test_prepare_context_sets_defaults():
    state = {"messages": [HumanMessage(content="你好")]}
    out = prepare_context(state, {"configurable": {"kb_ids": [1]}})
    assert out["current_query"] == "你好"
    assert out["kb_selected"] is True
    assert out["rewrite_query"] == "你好"

def test_query_understand_skips_llm_when_disabled():
    state = {
        "current_query": "它的维度",
        "history_pairs": [{"query": "embedding", "answer": "bge-m3"}],
        "kb_selected": True,
    }
    with patch("agents.nodes.query_understand.settings") as mock_settings:
        mock_settings.enable_rewrite = False
        out = query_understand(state, {})
    assert out["rewrite_query"] == "它的维度"

def test_query_understand_skips_retrieval_for_vague_query():
    state = {
        "current_query": "这是什么",
        "history_pairs": [],
        "kb_selected": True,
        "has_images": False,
    }
    with patch("agents.nodes.query_understand.settings") as mock_settings:
        mock_settings.enable_rewrite = True
        out = query_understand(state, {})
    assert out["intent"] == "clarification"
    assert out.get("system_prompt_override")
    assert route_after_understand({**state, **out}) == "agent"

def test_query_understand_applies_intent_override_for_greeting():
    state = {
        "current_query": "你好",
        "history_pairs": [],
        "kb_selected": True,
    }
    mock_out = MagicMock()
    mock_out.rewrite_query = "你好"
    mock_out.intent = "greeting"

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.invoke.return_value = mock_out

    with (
        patch("agents.nodes.query_understand.settings") as mock_settings,
        patch("agents.nodes.query_understand.create_chat_model", return_value=mock_llm),
    ):
        mock_settings.enable_rewrite = True
        mock_settings.query_understand_model = ""
        out = query_understand(state, {})
    assert out["intent"] == "greeting"
    assert "问候" in out.get("system_prompt_override", "")
    assert route_after_understand({**state, **out}) == "agent"

def test_query_understand_uses_llm_output():
    state = {
        "current_query": "它的维度呢",
        "history_pairs": [{"query": "embedding 是什么", "answer": "bge-m3"}],
        "kb_selected": True,
    }
    mock_out = MagicMock()
    mock_out.rewrite_query = "embedding 模型输出维度是多少"
    mock_out.intent = "follow_up"

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.invoke.return_value = mock_out

    with (
        patch("agents.nodes.query_understand.settings") as mock_settings,
        patch("agents.nodes.query_understand.create_chat_model", return_value=mock_llm),
    ):
        mock_settings.enable_rewrite = True
        mock_settings.query_understand_model = ""
        out = query_understand(state, {})
    assert out["rewrite_query"] == "embedding 模型输出维度是多少"
    assert out["intent"] == "follow_up"
    assert out.get("system_prompt_override")
    assert "对话历史" in out["system_prompt_override"]

def test_prepare_context_detects_attachments():
    from langchain_core.messages import HumanMessage

    msg = HumanMessage(content="总结附件")
    msg.additional_kwargs["ks_attachments"] = [{"id": "a1", "file_name": "x.pdf", "file_type": "pdf"}]
    state = {"messages": [msg]}
    out = prepare_context(state, {"configurable": {"kb_ids": [1]}})
    assert out["has_attachments"] is True
    assert out["has_images"] is False

def test_parse_query_understand_json_with_image_description():
    raw = '{"rewrite_query":"图意思","intent":"image_only","image_description":"一张流程图"}'
    parsed = parse_query_understand_json(raw)
    assert parsed["image_description"] == "一张流程图"

def test_query_understand_filters_meta_rewrite():
    state = {
        "current_query": "再查一下张三",
        "history_pairs": [{"query": "张三是谁", "answer": "项目负责人"}],
        "kb_selected": True,
    }
    mock_out = MagicMock()
    mock_out.rewrite_query = "请重新在知识库中查找关于张三的更多信息"
    mock_out.intent = "kb_search"
    mock_out.image_description = ""

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.invoke.return_value = mock_out

    with (
        patch("agents.nodes.query_understand.settings") as mock_settings,
        patch("agents.nodes.query_understand.create_chat_model", return_value=mock_llm),
    ):
        mock_settings.enable_rewrite = True
        mock_settings.query_understand_model = ""
        out = query_understand(state, {})
    assert out["rewrite_query"] == "再查一下张三"
    assert out["intent"] == "kb_search"
