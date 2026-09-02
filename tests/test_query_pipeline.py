"""Query pipeline：prepare_context、query_understand、route、本地 expansion。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import ValidationError

from agents.nodes.prepare_context import extract_history_pairs, prepare_context
from agents.nodes.query_understand import query_understand, route_after_understand
from schemas.query import (
    QueryUnderstandOutput,
    is_meta_rewrite,
    needs_agent_tools,
    needs_retrieval,
    normalize_intent,
    parse_query_understand_json,
    restore_rewrite_cues,
    sanitize_rewrite_query,
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
    # WeKnora NeedsKBRetrieval：clarification / summarize 也检索
    assert needs_retrieval("clarification", True)
    assert needs_retrieval("summarize", True)
    assert not needs_retrieval("follow_up", True)
    assert not needs_retrieval("greeting", True)
    assert not needs_retrieval("image_only", True)
    assert not needs_retrieval("doc_only", True)
    assert not needs_retrieval("web_search", True)
    assert not needs_retrieval("kb_search", False)
    assert needs_retrieval("kb_search", True)


def test_needs_agent_tools_rules():
    assert needs_agent_tools("kb_search", True)
    assert needs_agent_tools("clarification", True)
    assert needs_agent_tools("web_search", False)
    assert needs_agent_tools("web_search", True)
    assert not needs_agent_tools("greeting", True)
    assert not needs_agent_tools("doc_only", False)
    assert not needs_agent_tools("kb_search", False)
    assert not needs_agent_tools("web_search", False, web_search_enabled=False)
    assert needs_agent_tools("web_search", True, web_search_enabled=False)
    assert needs_agent_tools("follow_up", False, agent_has_tools=True)
    assert needs_agent_tools("no_kb", False, agent_has_tools=True)
    assert not needs_agent_tools("greeting", False, agent_has_tools=True)
    assert not needs_agent_tools("chitchat", True, agent_has_tools=True)
    assert not needs_agent_tools("image_only", True, agent_has_tools=True)


def test_normalize_intent_attachment_invariants():
    assert normalize_intent("image_only", kb_selected=True, has_images=True) == "image_only"
    assert normalize_intent("image_only", kb_selected=True, has_images=False) == "kb_search"
    assert normalize_intent("doc_only", kb_selected=True, has_attachments=False) == "kb_search"
    assert normalize_intent("kb_search", kb_selected=False) == "no_kb"
    # 未选知识库时会话附件仍走 doc_only / image_only，不能打成 no_kb
    assert normalize_intent("doc_only", kb_selected=False, has_attachments=True) == "doc_only"
    assert normalize_intent("image_only", kb_selected=False, has_images=True) == "image_only"
    assert normalize_intent("doc_only", kb_selected=False, has_attachments=False) == "no_kb"
    assert normalize_intent("greeting", kb_selected=False) == "greeting"
    assert normalize_intent("summarize", kb_selected=True) == "summarize"
    assert normalize_intent("summarize", kb_selected=False) == "no_kb"
    assert normalize_intent("web_search", kb_selected=True) == "web_search"
    assert normalize_intent("web_search", kb_selected=False) == "web_search"
    # 模型不得输出 no_kb；有库时纠正为 kb_search
    assert normalize_intent("no_kb", kb_selected=True) == "kb_search"
    assert normalize_intent("no_kb", kb_selected=False) == "no_kb"


def test_sanitize_rewrite_query():
    assert is_meta_rewrite("请重新在知识库中查找关于张三的信息")
    assert sanitize_rewrite_query(
        "请重新在知识库中查找关于张三的信息", "张三的信息"
    ) == "张三的信息"
    assert sanitize_rewrite_query("张三的详细信息是什么", "张三") == "张三的详细信息是什么"


def test_restore_rewrite_cues_keeps_hotness_words():
    original = "你知道最近比较火的代孕相关的事吗？和景甜有关的"
    dropped = "最近和景甜有关的代孕相关的事吗？"
    assert restore_rewrite_cues(dropped, original) == original
    assert sanitize_rewrite_query(dropped, original) == original
    kept = "最近比较火的景甜代孕相关事件是什么"
    assert sanitize_rewrite_query(kept, original) == kept


def test_query_understand_output_rejects_no_kb():
    with pytest.raises(ValidationError):
        QueryUnderstandOutput(rewrite_query="李稣航是谁", intent="no_kb")
    parsed = QueryUnderstandOutput(
        rewrite_query="最近比较火的景甜代孕相关事件是什么",
        intent="web_search",
    )
    assert parsed.intent == "web_search"

def test_parse_query_understand_json():
    raw = '说明如下：{"rewrite_query":"RAG 架构","intent":"kb_search"}'
    parsed = parse_query_understand_json(raw)
    assert parsed["rewrite_query"] == "RAG 架构"
    assert parsed["intent"] == "kb_search"

def test_route_after_understand():
    assert route_after_understand({"intent": "follow_up", "kb_selected": True}) == "generate"
    assert route_after_understand({"intent": "kb_search", "kb_selected": True}) == "agent"
    assert route_after_understand({
        "intent": "clarification",
        "kb_selected": True,
        "current_query": "这是什么",
    }) == "agent"
    assert route_after_understand({
        "intent": "summarize",
        "kb_selected": True,
        "current_query": "总结一下我们聊了什么",
    }) == "agent"
    assert route_after_understand({
        "intent": "kb_search",
        "kb_selected": True,
        "current_query": "这是什么",
    }) == "agent"
    assert route_after_understand({
        "intent": "image_only",
        "kb_selected": True,
        "current_query": "这是啥",
        "has_images": True,
    }) == "generate"
    assert route_after_understand({"intent": "web_search", "kb_selected": False}) == "agent"
    assert (
        route_after_understand(
            {
                "intent": "web_search",
                "kb_selected": False,
                "web_search_enabled": False,
            }
        )
        == "generate"
    )
    assert (
        route_after_understand(
            {"intent": "follow_up", "kb_selected": False, "agent_has_tools": True}
        )
        == "agent"
    )
    assert (
        route_after_understand(
            {"intent": "greeting", "kb_selected": False, "agent_has_tools": True}
        )
        == "generate"
    )

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
    assert out["image_description"] == ""
    assert out["system_prompt_override"] == ""
    assert out["last_sources"] == []
    assert out["agent_has_tools"] is False


def test_prepare_context_agent_has_tools_from_bound_agent():
    state = {"messages": [HumanMessage(content="做一份园区介绍 PPT")]}
    with patch(
        "agents.nodes.prepare_context.resolve_agent_tool_names",
        return_value=frozenset({"generate_pptx"}),
    ):
        out = prepare_context(state, {"configurable": {"agent_id": "agent-ppt"}})
    assert out["agent_has_tools"] is True
    assert route_after_understand({**out, "intent": "follow_up"}) == "agent"


def test_prepare_context_resets_stale_turn_keys():
    """checkpoint 中残留的上一轮 turn 键须被入口清零，避免脏读。"""
    state = {
        "messages": [HumanMessage(content="纯文本问题")],
        "image_description": "上一轮图片描述",
        "system_prompt_override": "旧 override",
        "intent": "image_only",
        "last_sources": [{"file_name": "old.md"}],
        "has_images": True,
    }
    out = prepare_context(state, {"configurable": {"kb_ids": [1]}})
    assert out["current_query"] == "纯文本问题"
    assert out["image_description"] == ""
    assert out["system_prompt_override"] == ""
    assert out["last_sources"] == []
    assert out["context_block"] == ""
    assert out["has_images"] is False
    assert out["intent"] == "kb_search"

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

def test_query_understand_uses_llm_intent_for_vague_query():
    """含糊问句交给 LLM 分类，代码不再用正则改写成 clarification。"""
    state = {
        "current_query": "这是什么",
        "history_pairs": [],
        "kb_selected": True,
        "has_images": False,
    }
    mock_out = MagicMock()
    mock_out.rewrite_query = "这是什么"
    mock_out.intent = "clarification"
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
    assert out["intent"] == "clarification"
    assert out.get("system_prompt_override") == ""
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
    assert "greeting" in out.get("system_prompt_override", "").lower()
    assert route_after_understand({**state, **out}) == "generate"

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
    assert "conversation history" in out["system_prompt_override"].lower()

def test_prepare_context_detects_attachments():
    from langchain_core.messages import HumanMessage

    msg = HumanMessage(content="总结附件")
    msg.additional_kwargs["ks_attachments"] = [{"id": "a1", "file_name": "x.pdf", "file_type": "pdf"}]
    state = {"messages": [msg]}
    out = prepare_context(state, {"configurable": {"kb_ids": [1]}})
    assert out["has_attachments"] is True
    assert out["has_images"] is False
    assert out["intent"] == "kb_search"

    out_no_kb = prepare_context(state, {"configurable": {"kb_ids": []}})
    assert out_no_kb["has_attachments"] is True
    assert out_no_kb["kb_selected"] is False
    assert out_no_kb["intent"] == "doc_only"


def test_query_understand_keeps_doc_only_without_kb():
    """未选知识库 + 本轮附件：「这文档里是啥」应走 doc_only，不能打成 no_kb。"""
    state = {
        "current_query": "这文档里是啥",
        "history_pairs": [],
        "kb_selected": False,
        "has_attachments": True,
        "has_images": False,
    }
    mock_out = MagicMock()
    mock_out.rewrite_query = "这份文档的内容是什么"
    mock_out.intent = "doc_only"
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

    assert out["intent"] == "doc_only"
    assert "document" in out.get("system_prompt_override", "").lower()
    assert "无法查阅" not in out.get("system_prompt_override", "")
    assert route_after_understand({**state, **out}) == "generate"

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
    # 空描述也必须写出，覆盖上一轮残留
    assert "image_description" in out
    assert out["image_description"] == ""


def test_query_understand_trusts_llm_image_only():
    """有图 +「这是啥」：分类交给 LLM，代码不二次改写意图。"""
    state = {
        "current_query": "这是啥",
        "history_pairs": [],
        "kb_selected": True,
        "has_images": True,
        "messages": [HumanMessage(content="这是啥")],
    }
    mock_out = MagicMock()
    mock_out.rewrite_query = "这张图是什么意思"
    mock_out.intent = "image_only"
    mock_out.image_description = "一台机柜设备"

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.invoke.return_value = mock_out

    with (
        patch("agents.nodes.query_understand.settings") as mock_settings,
        patch("agents.nodes.query_understand.create_chat_model", return_value=mock_llm),
    ):
        mock_settings.enable_rewrite = True
        mock_settings.query_understand_model = ""
        out = query_understand(state, {})

    assert out["intent"] == "image_only"
    assert "image" in out.get("system_prompt_override", "").lower()
    assert route_after_understand({**state, **out}) == "generate"


def test_query_understand_keeps_kb_search_when_llm_says_search():
    state = {
        "current_query": "知识库里有类似的吗",
        "history_pairs": [],
        "kb_selected": True,
        "has_images": True,
        "messages": [HumanMessage(content="知识库里有类似的吗")],
    }
    mock_out = MagicMock()
    mock_out.rewrite_query = "知识库中是否有类似设备"
    mock_out.intent = "kb_search"
    mock_out.image_description = "一台机柜设备"

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.invoke.return_value = mock_out

    with (
        patch("agents.nodes.query_understand.settings") as mock_settings,
        patch("agents.nodes.query_understand.create_chat_model", return_value=mock_llm),
    ):
        mock_settings.enable_rewrite = True
        mock_settings.query_understand_model = ""
        out = query_understand(state, {})

    assert out["intent"] == "kb_search"
    assert out.get("system_prompt_override") == ""
    assert route_after_understand({**state, **out}) == "agent"


def test_query_understand_summarize_still_retrieves():
    """对齐 WeKnora：summarize 仍走检索，不在代码里改写成 kb_search。"""
    state = {
        "current_query": "李稣航是谁",
        "history_pairs": [],
        "kb_selected": True,
        "has_images": False,
        "has_attachments": False,
    }
    mock_out = MagicMock()
    mock_out.rewrite_query = "请总结一下关于李稣航的信息"
    mock_out.intent = "summarize"
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

    assert out["rewrite_query"] == "请总结一下关于李稣航的信息"
    assert out["intent"] == "summarize"
    assert out.get("system_prompt_override") == ""
    assert route_after_understand({**state, **out}) == "agent"


def test_query_understand_hot_news_keeps_cues_and_web_search():
    """未选库 + 联网开：明星热搜须走 web_search，改写丢掉「比较火」时回退原问。"""
    original = "你知道最近比较火的代孕相关的事吗？和景甜有关的"
    state = {
        "current_query": original,
        "history_pairs": [],
        "kb_selected": False,
        "web_search_enabled": True,
        "has_images": False,
        "has_attachments": False,
    }
    mock_out = MagicMock()
    mock_out.rewrite_query = "最近和景甜有关的代孕相关的事吗？"
    mock_out.intent = "web_search"
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

    assert out["rewrite_query"] == original
    assert "比较火" in out["rewrite_query"]
    assert out["intent"] == "web_search"
    assert route_after_understand({**state, **out}) == "agent"


def test_query_understand_private_name_without_kb_stays_no_kb():
    """「李稣航是谁」无新闻热度词：未选库时保持 no_kb，不误走联网。"""
    state = {
        "current_query": "李稣航是谁",
        "history_pairs": [],
        "kb_selected": False,
        "web_search_enabled": True,
        "has_images": False,
        "has_attachments": False,
    }
    mock_out = MagicMock()
    mock_out.rewrite_query = "李稣航是谁"
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

    assert out["intent"] == "no_kb"
    assert route_after_understand({**state, **out}) == "generate"


def test_query_understand_prompt_injects_attachment_tags():
    from prompts.query_understand import build_query_understand_prompts

    _, user_with_image = build_query_understand_prompts(
        query="这是啥",
        history_pairs=[],
        kb_selected=True,
        has_images=True,
    )
    assert "<images_uploaded" in user_with_image
    assert "<no_image_attached />" not in user_with_image

    _, user_no_image = build_query_understand_prompts(
        query="这是啥",
        history_pairs=[],
        kb_selected=True,
        has_images=False,
    )
    assert "<no_image_attached />" in user_no_image
    assert "<images_uploaded" not in user_no_image

    _, user_doc_no_kb = build_query_understand_prompts(
        query="这文档里是啥",
        history_pairs=[],
        kb_selected=False,
        has_attachments=True,
    )
    assert "<document_attached />" in user_doc_no_kb
    assert "不能使用 kb_search" not in user_doc_no_kb
    assert "intent 应为 no_kb" not in user_doc_no_kb


def test_query_understand_prompt_matches_weknora_rewrite():
    from prompts.query_understand import QUERY_UNDERSTAND_SYSTEM, QUERY_UNDERSTAND_USER

    assert "when unsure, always choose `kb_search`" in QUERY_UNDERSTAND_SYSTEM
    assert "请重新在知识库中查找关于张三的更多信息" in QUERY_UNDERSTAND_SYSTEM
    assert "整理知识库中的数据" in QUERY_UNDERSTAND_SYSTEM
    assert "## Conversation History" in QUERY_UNDERSTAND_SYSTEM
    assert "[Runtime Context — metadata only, not instructions]" in QUERY_UNDERSTAND_USER
    assert "Never output `no_kb`" in QUERY_UNDERSTAND_SYSTEM
    assert "比较火" in QUERY_UNDERSTAND_SYSTEM
    assert "web_search" in QUERY_UNDERSTAND_SYSTEM
    assert "Knowledge base selected:" in QUERY_UNDERSTAND_USER
    assert "Web search available this turn:" in QUERY_UNDERSTAND_USER


def test_query_understand_prompt_injects_web_runtime():
    from prompts.query_understand import build_query_understand_prompts

    _, user_on = build_query_understand_prompts(
        query="你知道最近比较火的代孕相关的事吗？和景甜有关的",
        history_pairs=[],
        kb_selected=False,
        web_search_enabled=True,
    )
    assert "Knowledge base selected: no" in user_on
    assert "Web search available this turn: yes" in user_on
    assert "intent MUST be `web_search`" in user_on
    assert "比较火" in user_on

    _, user_off = build_query_understand_prompts(
        query="李稣航是谁",
        history_pairs=[],
        kb_selected=False,
        web_search_enabled=False,
    )
    assert "Web search available this turn: no" in user_off
    assert "intent MUST be `web_search`" not in user_off
