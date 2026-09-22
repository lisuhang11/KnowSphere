"""主对话上下文：系统级 / 任务级 / 摘要 / N 轮原文 / 本轮动态消息。"""

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from utils.context_layout import assemble_model_messages, llm_history_messages
from utils.language import ANSWER_LANGUAGE_EN


def test_layout_puts_summary_above_history_and_turn_context_before_latest_user():
    messages = [
        HumanMessage(content="上一问", id="h1"),
        AIMessage(content="上一答", id="a1"),
        ToolMessage(content="检索正文不要省略", name="doc_retrieval", tool_call_id="t1"),
        HumanMessage(content="这一问", id="h2"),
    ]
    out = assemble_model_messages(
        "You are KnowSphere.\nALWAYS respond in {{language}}",
        messages,
        {"configurable": {"pinned_skill_names": ["ppt-structure"]}},
        answer_language=ANSWER_LANGUAGE_EN,
        session_summary="【交接文档】\n## 用户原始请求\n查预算",
        rewrite_query="预算明细",
        image_description="一张表",
        asker_background="<asker_background>关注预算</asker_background>",
        bound_tool_names=["get_stored_data", "doc_retrieval"],
    )
    assert isinstance(out[0], SystemMessage)
    system = str(out[0].content)
    assert system.index("You are KnowSphere.") < system.index("### Task configuration")
    assert "ALWAYS respond in the language in Task configuration" in system
    assert "ALWAYS respond in English" in system
    assert "User Language: English" in system
    assert "{{language}}" not in system
    assert "【交接文档】" not in system
    assert "预算明细" not in system
    assert "关注预算" not in system
    assert "### Stored tool results" in system
    assert system.index("### Stored tool results") < system.index("### Task configuration")

    assert out[1].additional_kwargs["ks_view"] == "handover"
    assert "查预算" in out[1].content
    assert out[2].content == "上一问"
    assert out[3].content == "上一答"
    assert "检索正文不要省略" in out[4].content
    assert out[5].additional_kwargs["ks_view"] == "turn_context"
    turn = out[5].content
    assert "预算明细" in turn
    assert "一张表" in turn
    assert "ppt-structure" in turn
    assert "关注预算" in turn
    assert "工作记忆" not in turn
    assert out[6].content == "这一问"


def test_llm_history_keeps_retrieval_text():
    messages = [
        HumanMessage(content="早", id="h1"),
        ToolMessage(content="完整片段", name="doc_retrieval", tool_call_id="t1", id="tool1"),
        AIMessage(content="答", id="a1"),
        HumanMessage(content="今", id="h2"),
    ]
    window = llm_history_messages({"messages": messages, "session_summary": ""})
    tool = next(msg for msg in window if isinstance(msg, ToolMessage))
    assert tool.content == "完整片段"
