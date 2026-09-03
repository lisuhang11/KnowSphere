"""智能体 / 工具：目录、提示词与运行时裁剪。"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from langchain_core.tools import tool

from agents.nodes.agent import tools_for_state
from config.settings import settings
from prompts import build_system_prompt
from tools import get_tools
from tools.catalog import (
    BUILTIN_AGENT_ID,
    BUILTIN_PPT_AGENT_ID,
    CATALOG_TOOL_NAMES,
    PPT_AGENT_TOOL_NAMES,
    REASONING_TOOL_NAMES,
    ordered_tool_names,
    tools_to_public,
)


def test_catalog_covers_runtime_tools():
    names = {t.name for t in get_tools()}
    assert names == set(CATALOG_TOOL_NAMES)
    public = tools_to_public()
    assert [t["name"] for t in public] == list(CATALOG_TOOL_NAMES)
    assert all(t["display_name"] and t["category"] for t in public)
    pptx = next(t for t in public if t["name"] == "generate_pptx")
    assert pptx["category"] == "creation"
    assert pptx["produces"] == "file"
    assert pptx["requires_kb"] is False
    assert pptx["requires_web"] is False


def test_ordered_tool_names_keeps_catalog_order():
    assert ordered_tool_names(["web_fetch", "write_plan", "unknown", "doc_retrieval"]) == [
        "write_plan",
        "doc_retrieval",
        "web_fetch",
    ]


def test_system_prompt_lists_bound_tools_only():
    prompt = build_system_prompt(tool_names=["web_search", "web_fetch"])
    assert "web_search" in prompt
    assert "web_fetch" in prompt
    assert "doc_retrieval" not in prompt
    assert "generate_pptx" not in prompt
    full = build_system_prompt()
    assert "doc_retrieval" in full
    assert "list_chunks" in full
    assert "write_plan" in full
    assert "query_knowledge_graph" in full
    assert "知识库检索之后" in full
    assert "换更具体的检索词" in full
    ppt = build_system_prompt(tool_names=["generate_pptx", "doc_retrieval"])
    assert "generate_pptx" in ppt
    assert "不要在工具成功返回前声称已经生成文件" in ppt


def test_tools_for_state_respects_request_web_toggle():
    @tool
    def web_search(query: str) -> str:
        """web search"""
        return query

    @tool
    def write_plan(goal: str, steps: list[str]) -> str:
        """plan"""
        return goal

    tools = [web_search, write_plan]
    with patch.object(settings, "web_search_enabled", True):
        off = tools_for_state(
            {"configurable": {"kb_ids": [], "web_search_enabled": False}}, tools
        )
        on = tools_for_state(
            {"configurable": {"kb_ids": [], "web_search_enabled": True}}, tools
        )
    assert {t.name for t in off} == {"write_plan"}
    assert {t.name for t in on} == {"web_search", "write_plan"}


def test_tools_for_state_hides_graph_when_disabled():
    @tool
    def doc_retrieval(query: str) -> str:
        """kb search"""
        return query

    @tool
    def query_knowledge_graph(query: str) -> str:
        """graph search"""
        return query

    tools = [doc_retrieval, query_knowledge_graph]
    names = {
        t.name
        for t in tools_for_state(
            {"configurable": {"kb_ids": [1], "graph_enabled": False}}, tools
        )
    }
    assert names == {"doc_retrieval"}


def test_tools_for_state_respects_agent_allowlist():
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
    config = {"configurable": {"kb_ids": [1], "agent_id": BUILTIN_AGENT_ID}}
    with (
        patch("agents.nodes.agent.resolve_agent_tool_names", return_value=frozenset({"web_search", "write_plan"})),
        patch.object(settings, "web_search_enabled", True),
    ):
        names = {t.name for t in tools_for_state(config, tools)}
    assert names == {"web_search", "write_plan"}


def test_tools_for_state_keeps_creation_tools_without_kb():
    @tool
    def generate_pptx(title: str, slides: list) -> str:
        """pptx"""
        return title

    @tool
    def doc_retrieval(query: str) -> str:
        """kb search"""
        return query

    tools = [generate_pptx, doc_retrieval]
    names = {t.name for t in tools_for_state({"configurable": {"kb_ids": []}}, tools)}
    assert names == {"generate_pptx"}


def test_seed_builtin_agent_binds_catalog_tools(pg_available):
    if not pg_available:
        pytest.skip("postgres unavailable")

    from stores.agent_repository import AgentStore

    store = AgentStore()
    store.init_schema()
    store.seed_builtins()
    agent = store.get_agent(BUILTIN_AGENT_ID)
    assert agent is not None
    assert agent["is_builtin"]
    assert "toolkit_ids" not in agent
    assert set(agent["tool_names"]) == set(REASONING_TOOL_NAMES)
    assert "generate_pptx" not in agent["tool_names"]
    ppt = store.get_agent(BUILTIN_PPT_AGENT_ID)
    assert ppt is not None
    assert ppt["is_builtin"]
    assert not ppt["is_default"]
    assert set(ppt["tool_names"]) == set(PPT_AGENT_TOOL_NAMES)
    assert "generate_pptx" in ppt["system_prompt"]
    default = store.get_default_agent()
    assert default is not None


def test_create_agent_binds_tools_directly(pg_available):
    if not pg_available:
        pytest.skip("postgres unavailable")

    from stores.agent_repository import AgentStore, new_agent_id

    store = AgentStore()
    store.init_schema()
    aid = new_agent_id()
    rec = store.create_agent(
        name="资料整理",
        description="只检索知识库",
        tool_names=["doc_retrieval", "write_plan"],
        agent_id=aid,
    )
    try:
        assert rec["tool_names"] == ["write_plan", "doc_retrieval"]
        assert {t["name"] for t in rec["tools"]} == {"write_plan", "doc_retrieval"}
        updated = store.update_agent(aid, tool_names=["web_search"])
        assert updated["tool_names"] == ["web_search"]
    finally:
        store.delete_agent(aid)


def test_reasoning_agent_tools_are_immutable(pg_available):
    if not pg_available:
        pytest.skip("postgres unavailable")

    from stores.agent_repository import AgentStore

    store = AgentStore()
    store.init_schema()
    store.seed_builtins()
    before = store.get_agent(BUILTIN_AGENT_ID)
    assert before is not None
    with pytest.raises(ValueError, match="智能推理"):
        store.update_agent(BUILTIN_AGENT_ID, tool_names=["generate_pptx"])
    after = store.get_agent(BUILTIN_AGENT_ID)
    assert after is not None
    assert after["tool_names"] == before["tool_names"] == list(REASONING_TOOL_NAMES)


def test_ppt_agent_tools_can_be_changed(pg_available):
    if not pg_available:
        pytest.skip("postgres unavailable")

    from stores.agent_repository import AgentStore

    store = AgentStore()
    store.init_schema()
    store.seed_builtins()
    original = store.get_agent(BUILTIN_PPT_AGENT_ID)
    assert original is not None
    try:
        updated = store.update_agent(BUILTIN_PPT_AGENT_ID, tool_names=["generate_pptx"])
        assert updated["tool_names"] == ["generate_pptx"]
        store.seed_builtins()
        again = store.get_agent(BUILTIN_PPT_AGENT_ID)
        assert again is not None
        assert again["tool_names"] == ["generate_pptx"]
        restored = store.update_agent(
            BUILTIN_PPT_AGENT_ID, tool_names=["generate_pptx", "doc_retrieval"]
        )
        assert "generate_pptx" in restored["tool_names"]
        assert "doc_retrieval" in restored["tool_names"]
    finally:
        store.update_agent(BUILTIN_PPT_AGENT_ID, tool_names=list(PPT_AGENT_TOOL_NAMES))


def test_seed_migrates_legacy_ppt_tools(pg_available):
    if not pg_available:
        pytest.skip("postgres unavailable")

    from stores.agent_repository import AgentStore

    store = AgentStore()
    store.init_schema()
    store.seed_builtins()
    try:
        store.update_agent(
            BUILTIN_PPT_AGENT_ID,
            tool_names=["write_plan", "doc_retrieval", "generate_pptx"],
        )
        store.seed_builtins()
        after = store.get_agent(BUILTIN_PPT_AGENT_ID)
        assert after is not None
        assert after["tool_names"] == list(PPT_AGENT_TOOL_NAMES)
    finally:
        store.update_agent(BUILTIN_PPT_AGENT_ID, tool_names=list(PPT_AGENT_TOOL_NAMES))
