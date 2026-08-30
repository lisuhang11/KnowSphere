"""StateGraph 结构回归：节点编排、kb_ids 门控、collect_sources。"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agents.agent import build_agent
from agents.nodes.agent import _tools_for_config
from agents.nodes.sources import collect_sources
from states import KnowSphereState

def test_graph_node_names():
    graph = build_agent()
    assert set(graph.nodes.keys()) >= {
        "agent",
        "tools",
        "collect_sources",
        "prepare_context",
        "query_understand",
        "prefetch_retrieval",
        "__start__",
    }

def test_tools_for_config_empty_kb():
    assert _tools_for_config({"configurable": {"kb_ids": []}}, [object]) == []
    assert _tools_for_config({"configurable": {}}, [object]) == []

def test_tools_for_config_with_kb():
    tool = object
    assert _tools_for_config({"configurable": {"kb_ids": [1]}}, [tool]) == [tool]

def test_collect_sources_merges_tool_messages():
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

def test_agent_skips_tool_bind_without_kb():
    graph = build_agent()
    fake_response = AIMessage(content="直接回答")
    mock_model = MagicMock()
    mock_model.bind_tools = MagicMock(return_value=mock_model)
    mock_model.invoke = MagicMock(return_value=fake_response)

    with patch("agents.nodes.agent.create_chat_model", return_value=mock_model):
        result = graph.invoke(
            {"messages": [HumanMessage(content="你好")]},
            config={"configurable": {"kb_ids": []}, "recursion_limit": 10},
        )

    mock_model.bind_tools.assert_not_called
    assert result["messages"][-1].content == "直接回答"
    # output_schema：对外不含 turn 临时键
    assert "current_query" not in result
    assert "image_description" not in result


def test_prepare_then_agent_clears_stale_image_description():
    """有图残留 → 下一轮纯文本不应再注入图片描述。"""
    from langgraph.checkpoint.memory import MemorySaver

    graph = build_agent(checkpointer=MemorySaver())
    config = {
        "configurable": {"thread_id": "turn-reset", "kb_ids": []},
        "recursion_limit": 10,
    }

    # 模拟上一轮 checkpoint 已写入 image_description
    graph.update_state(
        config,
        {
            "messages": [HumanMessage(content="看图")],
            "image_description": "上一轮残留描述",
            "intent": "image_only",
        },
    )

    captured: list[str] = []

    def fake_invoke(messages, _config=None):
        # SystemMessage + HumanMessage；检查用户消息是否被错误注入
        human = next(m for m in messages if getattr(m, "type", None) == "human")
        captured.append(str(human.content))
        return AIMessage(content="ok")

    mock_model = MagicMock()
    mock_model.bind_tools = MagicMock(return_value=mock_model)
    mock_model.invoke = fake_invoke

    with (
        patch("agents.nodes.agent.create_chat_model", return_value=mock_model),
        patch("agents.nodes.query_understand.settings") as mock_qu,
    ):
        mock_qu.enable_rewrite = False
        graph.invoke(
            {"messages": [HumanMessage(content="你好")]},
            config=config,
        )

    assert captured
    assert "[用户上传图片内容]" not in captured[0]
    snap = graph.get_state(config)
    assert (snap.values.get("image_description") or "") == ""

def test_prefetch_injects_retrieval_when_kb_selected():
    graph = build_agent()
    retrieval_payload = {
        "query": "李稣航是谁",
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
    fake_response = AIMessage(content="李稣航是文档中的实习生。")
    mock_model = MagicMock()
    mock_model.bind_tools = MagicMock(return_value=mock_model)
    mock_model.invoke = MagicMock(return_value=fake_response)

    with (
        patch("agents.nodes.retrieve.doc_retrieval") as mock_dr,
        patch("agents.nodes.agent.create_chat_model", return_value=mock_model),
        patch("agents.nodes.query_understand.settings") as mock_qu_settings,
    ):
        mock_qu_settings.enable_rewrite = False
        mock_dr.invoke = MagicMock(return_value=retrieval_payload)
        result = graph.invoke(
            {"messages": [HumanMessage(content="李稣航是谁")]},
            config={"configurable": {"kb_ids": [1]}, "recursion_limit": 10},
        )

    mock_dr.invoke.assert_called_once
    call_query = mock_dr.invoke.call_args[0][0]["query"]
    assert call_query == "李稣航是谁"

    tool_msgs = [m for m in result["messages"] if getattr(m, "type", None) == "tool"]
    assert any(getattr(m, "name", None) == "doc_retrieval" for m in tool_msgs)
    assert result["messages"][-1].content == "李稣航是文档中的实习生。"


def test_doc_only_agent_reads_attachment_block():
    """doc_only 跳过检索后，主模型必须看到 [会话附件内容] 才能回答。"""
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
    mock_model.bind_tools = MagicMock(return_value=mock_model)
    mock_model.invoke = fake_invoke
    parsed = QueryUnderstandOutput(
        rewrite_query="这是啥", intent="doc_only", image_description=""
    )
    graph = build_agent()
    with (
        patch("agents.nodes.agent.create_chat_model", return_value=mock_model),
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


def test_user_facing_timeout_and_reasoning_chunk():
    from api.sessions import _chunk_reasoning, _user_facing_agent_error

    assert "超时" in _user_facing_agent_error(TimeoutError("Request timed out."))
    chunk = type("C", (), {"additional_kwargs": {"reasoning_content": "先看附件"}})()
    assert _chunk_reasoning(chunk) == "先看附件"
