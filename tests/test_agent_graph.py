"""StateGraph 结构回归：智能推理 ReAct（agent ↔ tools）与 generate 分流。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from agents.agent import build_agent
from agents.nodes.agent import tools_for_state
from agents.nodes.sources import collect_sources
from config.settings import settings
from states import KnowSphereState


def test_graph_node_names():
    graph = build_agent()
    assert set(graph.nodes.keys()) >= {
        "prepare_context",
        "manage_memory",
        "query_understand",
        "agent",
        "tools",
        "collect_sources",
        "generate",
        "__start__",
    }
    assert "retrieve" not in graph.nodes
    assert "into_chat_message" not in graph.nodes
    assert "prefetch_retrieval" not in graph.nodes


def test_tools_for_state_no_kb_keeps_web_and_plan():
    @tool
    def doc_retrieval(query: str) -> str:
        """kb search"""
        return query

    @tool
    def web_search(query: str) -> str:
        """web search"""
        return query

    @tool
    def write_plan(goal: str, steps: list[str]) -> str:
        """plan"""
        return goal

    tools = [doc_retrieval, web_search, write_plan]
    with patch.object(settings, "web_search_enabled", True):
        names = {t.name for t in tools_for_state({"configurable": {"kb_ids": []}}, tools)}
    assert names == {"web_search", "write_plan"}


def test_tools_for_state_request_web_off_keeps_kb():
    @tool
    def doc_retrieval(query: str) -> str:
        """kb search"""
        return query

    @tool
    def web_search(query: str) -> str:
        """web search"""
        return query

    @tool
    def write_plan(goal: str, steps: list[str]) -> str:
        """plan"""
        return goal

    tools = [doc_retrieval, web_search, write_plan]
    with patch.object(settings, "web_search_enabled", True):
        names = {
            t.name
            for t in tools_for_state(
                {"configurable": {"kb_ids": [1], "web_search_enabled": False}},
                tools,
            )
        }
    assert names == {"doc_retrieval", "write_plan"}


def test_tools_for_state_with_kb_includes_retrieval():
    @tool
    def doc_retrieval(query: str) -> str:
        """kb search"""
        return query

    @tool
    def query_knowledge_graph(query: str) -> str:
        """graph search"""
        return query

    @tool
    def write_plan(goal: str, steps: list[str]) -> str:
        """plan"""
        return goal

    tools = [doc_retrieval, query_knowledge_graph, write_plan]
    with patch.object(settings, "web_search_enabled", False):
        names = {t.name for t in tools_for_state({"configurable": {"kb_ids": [1]}}, tools)}
    assert names == {"doc_retrieval", "query_knowledge_graph", "write_plan"}


def test_collect_sources_merges_tool_messages():
    import json

    payload = {
        "sources": [
            {
                "document_id": "d1",
                "file_name": "a.md",
                "chunk_index": 0,
                "score": 0.9,
                "snippet": "hello",
            }
        ]
    }
    state: KnowSphereState = {
        "messages": [
            HumanMessage(content="q"),
            ToolMessage(content=json.dumps(payload), name="doc_retrieval", tool_call_id="1"),
        ]
    }
    out = collect_sources(state)
    assert len(out["last_sources"]) == 1
    assert out["last_sources"][0]["file_name"] == "a.md"


def test_collect_sources_includes_list_chunks():
    import json

    payload = {
        "sources": [
            {
                "document_id": "d1",
                "file_name": "a.md",
                "chunk_index": 1,
                "score": 1.0,
                "snippet": "全文",
                "chunk_id": 9,
                "content": "全文更长",
            }
        ]
    }
    state: KnowSphereState = {
        "messages": [
            HumanMessage(content="q"),
            ToolMessage(content=json.dumps(payload), name="list_chunks", tool_call_id="2"),
        ]
    }
    out = collect_sources(state)
    assert out["last_sources"][0]["chunk_id"] == 9


def test_collect_sources_ignores_previous_turn_retrieval():
    import json

    old = {
        "sources": [
            {"document_id": "old", "file_name": "old.md", "chunk_index": 0, "snippet": "旧"}
        ]
    }
    now = {
        "sources": [
            {"document_id": "now", "file_name": "now.md", "chunk_index": 0, "snippet": "新"}
        ]
    }
    state: KnowSphereState = {
        "messages": [
            HumanMessage(content="上一问"),
            ToolMessage(content=json.dumps(old), name="doc_retrieval", tool_call_id="old"),
            AIMessage(content="旧答"),
            HumanMessage(content="这一问"),
            ToolMessage(content=json.dumps(now), name="doc_retrieval", tool_call_id="now"),
        ]
    }
    out = collect_sources(state)
    assert [s["file_name"] for s in out["last_sources"]] == ["now.md"]


def test_greeting_goes_to_generate_without_tools():
    graph = build_agent()
    fake_response = AIMessage(content="你好，我是 KnowSphere。")
    mock_model = MagicMock()
    mock_model.bind_tools = MagicMock(return_value=mock_model)
    mock_model.invoke = MagicMock(return_value=fake_response)

    with (
        patch("agents.nodes.generate.create_chat_model", return_value=mock_model),
        patch("agents.nodes.agent.create_chat_model", return_value=mock_model),
    ):
        result = graph.invoke(
            {"messages": [HumanMessage(content="你好")]},
            config={"configurable": {"kb_ids": []}, "recursion_limit": 10},
        )

    mock_model.bind_tools.assert_not_called()
    assert result["messages"][-1].content == "你好，我是 KnowSphere。"
    assert "current_query" not in result


def test_kb_search_react_calls_doc_retrieval():
    @tool
    def doc_retrieval(query: str) -> dict:
        """kb search"""
        return {
            "query": query,
            "sources": [
                {
                    "document_id": "d1",
                    "file_name": "自我介绍.txt",
                    "chunk_index": 0,
                    "score": 0.9,
                    "snippet": "李稣航 河北地质大学 实习生",
                }
            ],
        }

    calls: list[int] = []

    def fake_invoke(messages, _config=None):
        calls.append(1)
        if len(calls) == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "doc_retrieval",
                        "args": {"query": "李稣航是谁"},
                        "id": "call-1",
                        "type": "tool_call",
                    }
                ],
            )
        return AIMessage(content="李稣航是文档中的实习生。")

    mock_model = MagicMock()
    mock_bound = MagicMock()
    mock_bound.invoke = fake_invoke
    mock_model.bind_tools = MagicMock(return_value=mock_bound)
    mock_model.invoke = fake_invoke

    graph = build_agent(tools=[doc_retrieval])
    with (
        patch("agents.nodes.agent.create_chat_model", return_value=mock_model),
        patch("agents.nodes.query_understand.settings") as mock_qu,
        patch.object(settings, "web_search_enabled", False),
    ):
        mock_qu.enable_rewrite = False
        result = graph.invoke(
            {"messages": [HumanMessage(content="李稣航是谁")]},
            config={"configurable": {"kb_ids": [1]}, "recursion_limit": 15},
        )

    mock_model.bind_tools.assert_called()
    tool_msgs = [m for m in result["messages"] if getattr(m, "type", None) == "tool"]
    assert any(getattr(m, "name", None) == "doc_retrieval" for m in tool_msgs)
    assert result.get("last_sources")
    assert result["last_sources"][0]["file_name"] == "自我介绍.txt"
    assert result["messages"][-1].content == "李稣航是文档中的实习生。"
    cites = (result["messages"][-1].additional_kwargs or {}).get("ks_citations") or []
    assert cites and cites[0]["file_name"] == "自我介绍.txt"


def test_doc_only_generate_reads_attachment_block():
    from schemas.query import QueryUnderstandOutput

    msg = HumanMessage(
        content="这是啥\n\n[会话附件内容]\n### 附件：简历.pdf\n齐浩哲 应聘 C++ 开发工程师"
    )
    msg.additional_kwargs["ks_attachments"] = [
        {"id": "a1", "file_name": "简历.pdf", "file_type": "pdf"}
    ]
    captured: list[str] = []

    def fake_invoke(messages, _config=None):
        captured.append("\n".join(str(getattr(m, "content", "")) for m in messages))
        return AIMessage(content="这是一份 C++ 简历")

    mock_model = MagicMock()
    mock_model.invoke = fake_invoke
    parsed = QueryUnderstandOutput(
        rewrite_query="这是啥", intent="doc_only", image_description=""
    )
    graph = build_agent()
    with (
        patch("agents.nodes.generate.create_chat_model", return_value=mock_model),
        patch(
            "agents.nodes.query_understand._invoke_text_query_understand",
            return_value=parsed,
        ),
    ):
        result = graph.invoke(
            {"messages": [msg]},
            config={"configurable": {"kb_ids": []}, "recursion_limit": 10},
        )

    assert captured
    assert "[会话附件内容]" in captured[0]
    assert "齐浩哲" in captured[0]
    assert result["messages"][-1].content == "这是一份 C++ 简历"


def test_web_fetch_blocks_localhost():
    from tools.web.fetch import web_fetch

    out = web_fetch.invoke({"url": "http://127.0.0.1:8080/secret"})
    assert out["sources"] == []
    assert "内网" in (out.get("note") or "")


def test_user_facing_timeout_and_reasoning_chunk():
    from api.sessions import _chunk_reasoning, _user_facing_agent_error

    assert "超时" in _user_facing_agent_error(TimeoutError("Request timed out."))
    chunk = type("C", (), {"additional_kwargs": {"reasoning_content": "先看附件"}})()
    assert _chunk_reasoning(chunk) == "先看附件"
