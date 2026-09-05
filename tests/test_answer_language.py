"""回答语言：按提问检测，中文用中文，其余用英文。"""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from agents.nodes.agent import _prepare_messages as prepare_agent_messages
from agents.nodes.generate import _prepare_messages as prepare_generate_messages
from agents.nodes.prepare_context import prepare_context
from prompts import build_system_prompt
from prompts.intent_prompts import intent_system_prompt
from prompts.query_understand import build_query_understand_prompts
from prompts.rag_system import RAG_SYSTEM_PROMPT, build_rag_system_prompt
from utils.language import (
    ANSWER_LANGUAGE_EN,
    ANSWER_LANGUAGE_ZH,
    answer_language_for_query,
    apply_answer_language,
    ensure_answer_language,
    is_chinese_query,
)


def test_detects_chinese_and_english_queries():
    assert is_chinese_query("什么是 RAG")
    assert is_chinese_query("你好")
    assert is_chinese_query("What is RAG 架构")
    assert not is_chinese_query("What is RAG?")
    assert not is_chinese_query("hello")
    assert not is_chinese_query("")
    assert not is_chinese_query("これは日本語です")
    assert answer_language_for_query("什么是 RAG") == ANSWER_LANGUAGE_ZH
    assert answer_language_for_query("What is RAG?") == ANSWER_LANGUAGE_EN
    assert answer_language_for_query("이것은 한국어") == ANSWER_LANGUAGE_EN


def test_prepare_context_sets_answer_language_from_query():
    zh = prepare_context({"messages": [HumanMessage(content="你好")]}, {"configurable": {}})
    assert zh["answer_language"] == ANSWER_LANGUAGE_ZH

    en = prepare_context(
        {"messages": [HumanMessage(content="What is RAG?")]},
        {"configurable": {}},
    )
    assert en["answer_language"] == ANSWER_LANGUAGE_EN

    empty = prepare_context({"messages": []}, {"configurable": {}})
    assert empty["answer_language"] == ANSWER_LANGUAGE_EN


def test_query_understand_prompt_uses_query_language():
    zh_sys, _ = build_query_understand_prompts(
        query="什么是 RAG",
        history_pairs=[],
        kb_selected=True,
    )
    assert "must be in 中文" in zh_sys
    assert "{{language}}" not in zh_sys

    en_sys, _ = build_query_understand_prompts(
        query="What is RAG?",
        history_pairs=[],
        kb_selected=True,
    )
    assert "must be in English" in en_sys


def test_intent_and_rag_prompts_follow_query_language():
    zh = intent_system_prompt("greeting", language=ANSWER_LANGUAGE_ZH)
    en = intent_system_prompt("greeting", language=ANSWER_LANGUAGE_EN)
    assert zh is not None and "ALWAYS respond in 中文" in zh
    assert en is not None and "ALWAYS respond in English" in en
    assert "{{language}}" not in zh and "{{language}}" not in en

    rag_zh = build_rag_system_prompt(language=ANSWER_LANGUAGE_ZH)
    rag_en = build_rag_system_prompt(language=ANSWER_LANGUAGE_EN)
    assert "ALWAYS respond in 中文" in rag_zh
    assert "ALWAYS respond in English" in rag_en
    assert "{{language}}" in RAG_SYSTEM_PROMPT


def test_generate_and_agent_messages_inject_answer_language():
    agent_prompt = build_system_prompt(tool_names=["web_search"])
    assert "User Language: {{language}}" in agent_prompt

    gen_zh = prepare_generate_messages(
        "",
        [HumanMessage(content="你好")],
        {"configurable": {}},
        answer_language=ANSWER_LANGUAGE_ZH,
    )
    assert isinstance(gen_zh[0], SystemMessage)
    assert "ALWAYS respond in 中文" in str(gen_zh[0].content)

    gen_en = prepare_generate_messages(
        "",
        [HumanMessage(content="hi")],
        {"configurable": {}},
        answer_language=ANSWER_LANGUAGE_EN,
    )
    assert "ALWAYS respond in English" in str(gen_en[0].content)

    agent_en = prepare_agent_messages(
        agent_prompt,
        [HumanMessage(content="What is RAG?")],
        {"configurable": {}},
        answer_language=ANSWER_LANGUAGE_EN,
    )
    assert "ALWAYS respond in English" in str(agent_en[0].content)
    assert "User Language: English" in str(agent_en[0].content)
    assert "{{language}}" not in str(agent_en[0].content)


def test_ensure_answer_language_appends_rule_for_custom_prompt():
    custom = "You are a helper."
    filled = ensure_answer_language(custom, ANSWER_LANGUAGE_EN)
    assert "ALWAYS respond in English" in filled
    already = apply_answer_language("ALWAYS respond in {{language}}", ANSWER_LANGUAGE_ZH)
    assert ensure_answer_language(already, ANSWER_LANGUAGE_ZH) == already
