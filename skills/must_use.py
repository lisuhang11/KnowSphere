"""本轮 @Skill 注入 <must_use>，对齐 WeKnora applyPerRequestSkillScope。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.messages import HumanMessage

MUST_USE_OPEN = "<must_use>"
MUST_USE_CLOSE = "</must_use>"


def sanitize_must_use_field(value: str) -> str:
    return (
        (value or "")
        .replace("\n", " ")
        .replace("\r", " ")
        .replace("<", " ")
        .replace(">", " ")
        .strip()
    )


def build_must_use_block(skill_names: Sequence[str] | None) -> str:
    lines: list[str] = []
    seen: set[str] = set()
    for raw in skill_names or []:
        name = sanitize_must_use_field(str(raw))
        if not name or name in seen:
            continue
        seen.add(name)
        lines.append(
            f'Must call read_skill(skill_name="{name}") for @Skill "{name}" before answering.'
        )
    if not lines:
        return ""
    return MUST_USE_OPEN + "\n" + "\n".join(lines) + "\n" + MUST_USE_CLOSE


def strip_must_use_block(text: str) -> str:
    raw = text or ""
    start = raw.find(MUST_USE_OPEN)
    if start < 0:
        return raw
    end = raw.find(MUST_USE_CLOSE, start)
    if end < 0:
        return raw
    cut = raw[:start] + raw[end + len(MUST_USE_CLOSE) :]
    return cut.strip()


def inject_must_use_messages(
    messages: list[Any], skill_names: Sequence[str] | None
) -> list[Any]:
    pinned: list[str] = []
    seen: set[str] = set()
    for raw in skill_names or []:
        name = sanitize_must_use_field(str(raw))
        if not name or name in seen:
            continue
        seen.add(name)
        pinned.append(name)
    block = build_must_use_block(pinned)
    if not block or not messages:
        return messages
    out = list(messages)
    first = out[0]
    if not isinstance(first, HumanMessage):
        return out
    content = first.content
    if isinstance(content, str):
        new_content: Any = f"{block}\n\n{content}" if content else block
    elif isinstance(content, list):
        new_content = [{"type": "text", "text": block + "\n\n"}, *content]
    else:
        new_content = f"{block}\n\n{content}"
    kwargs = dict(getattr(first, "additional_kwargs", None) or {})
    kwargs["ks_skills"] = [{"name": n} for n in pinned]
    out[0] = HumanMessage(content=new_content, additional_kwargs=kwargs, id=getattr(first, "id", None))
    return out
