"""文件产物解析与合并。"""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from utils.file_artifacts import (
    attach_outputs_to_ai_message,
    collect_turn_file_artifacts,
    parse_tool_file_artifact,
)


def test_parse_tool_file_artifact():
    payload = {
        "ok": True,
        "artifact": {
            "id": "att-1",
            "file_name": "介绍.pptx",
            "file_type": "pptx",
            "file_size": 12,
        },
    }
    art = parse_tool_file_artifact(json.dumps(payload, ensure_ascii=False))
    assert art == {
        "id": "att-1",
        "file_name": "介绍.pptx",
        "file_type": "pptx",
        "file_size": 12,
    }
    assert parse_tool_file_artifact("not json") is None


def test_collect_turn_file_artifacts_uses_last_human():
    msgs = [
        HumanMessage(content="旧问题"),
        ToolMessage(
            content=json.dumps(
                {"ok": True, "artifact": {"id": "old", "file_name": "old.pptx"}}
            ),
            tool_call_id="t0",
            name="generate_pptx",
        ),
        AIMessage(content="旧回答"),
        HumanMessage(content="做个新 PPT"),
        ToolMessage(
            content=json.dumps(
                {"ok": True, "artifact": {"id": "new", "file_name": "new.pptx"}}
            ),
            tool_call_id="t1",
            name="generate_pptx",
        ),
        AIMessage(content="好了"),
    ]
    arts = collect_turn_file_artifacts(msgs)
    assert [a["id"] for a in arts] == ["new"]


def test_attach_outputs_to_ai_message():
    msg = AIMessage(content="已生成", id="ai-1")
    updated = attach_outputs_to_ai_message(
        msg, [{"id": "a1", "file_name": "deck.pptx", "file_size": 10}]
    )
    assert updated is not None
    assert updated.id == "ai-1"
    assert updated.additional_kwargs["ks_outputs"][0]["id"] == "a1"
    again = attach_outputs_to_ai_message(
        updated, [{"id": "a1", "file_name": "deck.pptx", "file_size": 10}]
    )
    assert again is None
