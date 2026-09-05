"""长期记忆：asker_background 注入与显式「记住」解析（对齐 WeKnora）。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from prompts.query_understand import build_query_understand_prompts
from utils.long_term_memory import (
    MEMORY_KIND_INTEREST,
    MEMORY_KIND_PROFILE,
    RetrievalContext,
    build_retrieval_context,
    detect_explicit_memory,
    format_asker_background,
    infer_explicit_kind,
    sanitize_memory_content,
)


def test_detect_explicit_memory():
    assert detect_explicit_memory("记住：我们的生产库是 PostgreSQL 17") == "我们的生产库是 PostgreSQL 17"
    assert detect_explicit_memory("请记住我每周五要交周报") == "我每周五要交周报"
    assert detect_explicit_memory("帮我记住，接口超时统一设 30 秒") == "接口超时统一设 30 秒"
    assert detect_explicit_memory("记住") is None
    assert detect_explicit_memory("什么是 RAG") is None


def test_infer_explicit_kind():
    assert infer_explicit_kind("在做医学影像的后端") == MEMORY_KIND_PROFILE
    assert infer_explicit_kind("长期关注医学影像分割") == MEMORY_KIND_INTEREST


def test_sanitize_memory_content_collapses_controls():
    text = sanitize_memory_content("第一行\n第二行\t还有\x00控制符")
    assert "\n" not in text
    assert "\x00" not in text
    assert "第一行" in text


def test_format_asker_background_matches_weknora():
    block = format_asker_background(
        RetrievalContext(
            background="在做医学影像的后端",
            interests=["医学影像分割"],
            documents=["分割模型调参手册"],
        )
    )
    assert '<asker_background note="背景仅用于消解指代和补全检索词，不要当作问题的一部分">' in block
    assert "在做医学影像的后端" in block
    assert "长期关注：医学影像分割" in block
    assert "常查资料：分割模型调参手册" in block
    assert block.endswith("</asker_background>")
    assert format_asker_background(RetrievalContext()) == ""


def test_build_retrieval_context_rune_budget():
    items = [
        {"kind": MEMORY_KIND_PROFILE, "content": "在做医学影像的后端"},
        {"kind": MEMORY_KIND_INTEREST, "content": "医学影像分割"},
        {"kind": MEMORY_KIND_PROFILE, "content": "x" * 300},
    ]
    ctx = build_retrieval_context(items, ["分割模型调参手册"], rune_budget=40)
    assert "医学影像" in ctx.background
    assert ctx.interests == ["医学影像分割"]
    assert ctx.documents == ["分割模型调参手册"]
    assert "xxx" not in ctx.background


def test_query_understand_prompt_injects_asker_background():
    _, user = build_query_understand_prompts(
        query="分割怎么调参",
        history_pairs=[],
        kb_selected=True,
        asker_background=format_asker_background(
            RetrievalContext(
                background="在做医学影像的后端",
                interests=["医学影像分割"],
                documents=["分割模型调参手册"],
            )
        ),
    )
    assert "分割怎么调参" in user
    assert "在做医学影像的后端" in user
    assert "医学影像分割" in user
    assert "分割模型调参手册" in user
    assert "<no_image_attached />" in user


def test_query_understand_prompt_unchanged_without_memory():
    _, user = build_query_understand_prompts(
        query="分割怎么调参",
        history_pairs=[],
        kb_selected=True,
    )
    assert "asker_background" not in user
    assert "分割怎么调参" in user


def test_manage_memory_writes_then_loads_asker_background():
    from langchain_core.messages import HumanMessage

    from agents.nodes.manage_memory import manage_memory

    ctx = RetrievalContext(background="在做医学影像的后端")
    with (
        patch("agents.nodes.manage_memory.remember_explicit", return_value={"id": "m1"}),
        patch(
            "agents.nodes.manage_memory.retrieval_context_for",
            return_value=ctx,
        ) as recall,
        patch("agents.nodes.manage_memory.emit_thinking"),
        patch(
            "agents.nodes.manage_memory.build_memory_view",
        ) as view,
    ):
        view.return_value = MagicMock(
            history_pairs=[],
            needs_consolidation=False,
            archive_messages=[],
            archive_end_id="",
        )
        out = manage_memory(
            {
                "messages": [HumanMessage(content="记住：在做医学影像的后端")],
                "current_query": "记住：在做医学影像的后端",
            },
            {"configurable": {"owner": "u1", "thread_id": "s1"}},
        )
    assert "asker_background" in out
    assert "在做医学影像的后端" in out["asker_background"]
    recall.assert_called_once()
