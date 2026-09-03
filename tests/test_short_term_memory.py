"""短期记忆：LLM 视图窗口、历史检索压缩、工作记忆。"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from utils.citation import citation_payload_from_source_dicts
from utils.short_term_memory import (
    COMPACT_RETRIEVAL,
    build_memory_view,
    extract_working_memory,
    format_memory_system_block,
    memory_view_from_state,
)


def _turn(q: str, a: str, *, retrieve: bool = False) -> list:
    msgs = [HumanMessage(content=q, id=f"h-{q}")]
    if retrieve:
        msgs.append(
            AIMessage(
                content="",
                id=f"ai-call-{q}",
                tool_calls=[{"name": "doc_retrieval", "id": f"c-{q}", "args": {"query": q}}],
            )
        )
        msgs.append(
            ToolMessage(
                content='{"sources":[{"document_id":"d1","file_name":"secret.txt","snippet":"机密正文"}]}',
                name="doc_retrieval",
                tool_call_id=f"c-{q}",
                id=f"t-{q}",
            )
        )
    msgs.append(AIMessage(content=a, id=f"a-{q}"))
    return msgs


def test_history_pairs_skip_current_and_retrieval_tools():
    from agents.nodes.prepare_context import extract_history_pairs

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


def test_window_keeps_recent_turns_and_archives_older():
    messages: list = []
    for i in range(6):
        messages.extend(_turn(f"问题{i}", f"回答{i}"))
    messages.append(HumanMessage(content="现在呢", id="h-now"))
    view = build_memory_view(messages, keep_turns=3, max_context_tokens=32000)
    texts = [getattr(m, "content", "") for m in view.window_messages if isinstance(m, HumanMessage)]
    assert texts[-1] == "现在呢"
    assert "问题5" in texts
    assert "问题4" in texts
    assert "问题0" not in texts
    assert any(getattr(m, "content", "") == "问题0" for m in view.archive_messages)
    assert view.needs_consolidation
    assert view.history_pairs[-1]["query"] == "问题5"


def test_historical_retrieval_is_redacted_current_turn_kept():
    messages = [
        *_turn("张三是谁", "项目经理", retrieve=True),
        HumanMessage(content="他负责什么", id="h-now"),
        AIMessage(
            content="",
            id="ai-now",
            tool_calls=[{"name": "doc_retrieval", "id": "c-now", "args": {"query": "张三"}}],
        ),
        ToolMessage(
            content='{"sources":[{"document_id":"d2","file_name":"now.txt","snippet":"本轮机密"}]}',
            name="doc_retrieval",
            tool_call_id="c-now",
            id="t-now",
        ),
    ]
    view = build_memory_view(messages, keep_turns=8, redact_old_retrieval=True)
    old = [m for m in view.window_messages if isinstance(m, ToolMessage) and m.tool_call_id == "c-张三是谁"]
    cur = [m for m in view.window_messages if isinstance(m, ToolMessage) and m.tool_call_id == "c-now"]
    assert old and old[0].content == COMPACT_RETRIEVAL
    assert cur and "本轮机密" in str(cur[0].content)


def test_historical_list_chunks_is_redacted():
    messages = [
        HumanMessage(content="园区几点开门", id="h1"),
        AIMessage(
            content="",
            id="ai-1",
            tool_calls=[{"name": "list_chunks", "id": "c-old", "args": {"chunk_id": 11}}],
        ),
        ToolMessage(
            content='{"sources":[{"document_id":"d1","file_name":"园区.md","content":"北门8:00"}]}',
            name="list_chunks",
            tool_call_id="c-old",
            id="t-old",
        ),
        AIMessage(content="北门八点", id="a1"),
        HumanMessage(content="南门呢", id="h-now"),
    ]
    view = build_memory_view(messages, keep_turns=8, redact_old_retrieval=True)
    old = [m for m in view.window_messages if isinstance(m, ToolMessage) and m.tool_call_id == "c-old"]
    assert old and old[0].content == COMPACT_RETRIEVAL


def test_working_memory_keeps_write_plan():
    messages = [
        HumanMessage(content="做个方案"),
        ToolMessage(content="目标：做方案\n步骤：\n1. 检索\n2. 总结", name="write_plan", tool_call_id="p1"),
        AIMessage(content="已记下计划"),
        HumanMessage(content="继续"),
    ]
    mem = extract_working_memory(messages)
    assert "做方案" in mem["last_plan"]
    assert mem["recent_facts"]
    block = format_memory_system_block(working_memory=mem, session_summary="早先谈过预算。")
    assert "【会话工作记忆】" in block
    assert "【更早对话摘要】" in block
    assert "预算" in block


def test_memory_view_from_state_uses_summary_upto_to_skip_reconsolidate():
    messages: list = []
    for i in range(4):
        messages.extend(_turn(f"Q{i}", f"A{i}"))
    messages.append(HumanMessage(content="next", id="h-next"))
    view = build_memory_view(messages, keep_turns=2, max_context_tokens=32000)
    assert view.needs_consolidation
    assert view.archive_end_id
    again = build_memory_view(
        messages,
        keep_turns=2,
        max_context_tokens=32000,
        summary_upto_id=view.archive_end_id,
        session_summary="已有摘要",
    )
    assert again.needs_consolidation is False


def test_manage_memory_writes_summary_when_archive_exists():
    from unittest.mock import MagicMock, patch

    from langchain_core.messages import AIMessage as AI
    from langchain_core.messages import HumanMessage as H

    from agents.nodes.manage_memory import manage_memory

    messages = []
    for i in range(5):
        messages.append(H(content=f"问{i}", id=f"h{i}"))
        messages.append(AI(content=f"答{i}", id=f"a{i}"))
    messages.append(H(content="现在", id="h-now"))
    state = {"messages": messages}

    mock_llm = MagicMock()
    mock_llm.invoke.return_value = AI(content="用户先后问了问0到问3。")
    with (
        patch("agents.nodes.manage_memory.create_chat_model", return_value=mock_llm),
        patch("agents.nodes.manage_memory.settings") as st,
    ):
        st.stm_max_context_tokens = 32000
        st.stm_keep_turns = 2
        st.stm_consolidate_ratio = 0.5
        st.stm_hard_trim_ratio = 0.8
        st.stm_redact_old_retrieval = True
        out = manage_memory(state, {"configurable": {}})
    assert "用户先后问了" in out["session_summary"]
    assert out["summary_upto_message_id"]
    assert out["working_memory"]["recent_facts"]
    mock_llm.invoke.assert_called_once()


def test_historical_attachment_compacted_current_kept():
    old = HumanMessage(
        content="看这个\n\n[会话附件内容]\n### 附件：old.pdf\n" + ("历史正文" * 20),
        id="h-old",
    )
    now = HumanMessage(
        content="再看这个\n\n[会话附件内容]\n### 附件：now.pdf\n本轮附件正文",
        id="h-now",
    )
    messages = [old, AIMessage(content="已看", id="a-old"), now]
    view = build_memory_view(messages, keep_turns=8)
    humans = [m for m in view.window_messages if isinstance(m, HumanMessage)]
    assert humans[0].content.endswith("（历史附件正文已省略）")
    assert "本轮附件正文" in humans[-1].content
    assert "历史正文" in old.content


def test_current_turn_never_dropped_when_over_budget():
    huge = "字" * 8000
    messages = [
        HumanMessage(content="早先问", id="h0"),
        AIMessage(content="早先答", id="a0"),
        HumanMessage(content=huge, id="h-now"),
    ]
    view = build_memory_view(
        messages,
        keep_turns=8,
        max_context_tokens=2000,
        hard_trim_ratio=0.8,
    )
    assert any(getattr(m, "id", None) == "h-now" for m in view.window_messages)
    assert view.window_messages[-1].content == huge


def test_citation_payload_keeps_web_url():
    payload = citation_payload_from_source_dicts(
        [
            {
                "document_id": "https://example.com/a",
                "file_name": "标题",
                "chunk_index": 0,
                "score": 1.0,
                "snippet": "摘要",
                "url": "https://example.com/a",
            }
        ]
    )
    assert payload[0]["url"] == "https://example.com/a"
    assert payload[0]["file_name"] == "标题"


def test_manage_memory_skips_llm_on_short_history():
    from unittest.mock import patch

    from agents.nodes.manage_memory import manage_memory

    with patch("agents.nodes.manage_memory.create_chat_model") as mock_llm:
        out = manage_memory(
            {"messages": [HumanMessage(content="你好", id="h1")]},
            {"configurable": {}},
        )
    mock_llm.assert_not_called()
    assert "session_summary" not in out
    assert out["working_memory"]["recent_facts"] == []


def test_llm_view_does_not_mutate_checkpoint_tool_content():
    original = '{"sources":[{"snippet":"机密正文"}]}'
    messages = [
        HumanMessage(content="谁", id="h1"),
        ToolMessage(content=original, name="doc_retrieval", tool_call_id="c1", id="t1"),
        AIMessage(content="张三", id="a1"),
        HumanMessage(content="还有呢", id="h2"),
    ]
    view = memory_view_from_state({"messages": messages})
    compacted = [m for m in view.window_messages if isinstance(m, ToolMessage)]
    assert compacted and compacted[0].content == COMPACT_RETRIEVAL
    assert messages[1].content == original
