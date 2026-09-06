"""Context.from_runnable_config 解析与默认值。"""

from __future__ import annotations

from unittest.mock import patch

from agents.context import Context
from utils.run_config import (
    chat_model_kwargs_from_config,
    graph_enabled_from_config,
    kb_ids_from_config,
    thread_id_from_config,
    web_search_enabled_from_config,
)


def test_from_runnable_config_parses_fields():
    ctx = Context.from_runnable_config(
        {
            "configurable": {
                "thread_id": "s1",
                "kb_ids": ["1", 2, "x"],
                "chat_model_id": "m1",
                "agent_id": "a1",
                "owner": "u1",
                "attachment_ids": ["att-1", "att-1", ""],
                "web_search_enabled": False,
                "graph_enabled": True,
            }
        }
    )
    assert ctx.thread_id == "s1"
    assert ctx.kb_ids == [1, 2]
    assert ctx.chat_model_id == "m1"
    assert ctx.agent_id == "a1"
    assert ctx.owner == "u1"
    assert ctx.attachment_ids == ["att-1"]
    assert ctx.web_search_enabled is False
    assert ctx.graph_enabled is True
    assert ctx.skill_names is None


def test_skill_names_none_vs_empty():
    assert Context.from_runnable_config({}).skill_names is None
    assert Context.from_runnable_config({"configurable": {}}).skill_names is None
    assert Context.from_runnable_config({"configurable": {"skill_names": []}}).skill_names == []
    assert Context.from_runnable_config(
        {"configurable": {"skill_names": ["pdf-extract"]}}
    ).skill_names == ["pdf-extract"]


def test_run_config_helpers_delegate():
    cfg = {"configurable": {"thread_id": "t1", "kb_ids": [3], "chat_model_id": "mid"}}
    assert thread_id_from_config(cfg) == "t1"
    assert kb_ids_from_config(cfg) == [3]
    assert chat_model_kwargs_from_config(cfg, {"temperature": 0}) == {
        "temperature": 0,
        "model": "mid",
    }


def test_web_and_graph_defaults():
    with patch("config.settings.settings") as mock_settings:
        mock_settings.web_search_enabled = True
        assert web_search_enabled_from_config({}) is True
        assert web_search_enabled_from_config({"configurable": {"web_search_enabled": False}}) is False
        mock_settings.web_search_enabled = False
        assert web_search_enabled_from_config({"configurable": {"web_search_enabled": True}}) is False

    assert graph_enabled_from_config({"configurable": {"kb_ids": []}}) is False
    assert graph_enabled_from_config({"configurable": {"kb_ids": [1]}}) is True
    assert graph_enabled_from_config({"configurable": {"kb_ids": [1], "graph_enabled": False}}) is False
