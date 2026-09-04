"""提示词模板（事实源，随 git 版本管理；不上 Prompt Hub）。

Agent / RAG 系统提示照搬 Tencent/WeKnora `config/prompt_templates/`，
产品名改为 KnowSphere；引用协议为 KnowSphere 本地扩展。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from prompts.agent_system import (
    PPTX_TOOL_GUIDELINE,
    PROGRESSIVE_RAG_SYSTEM_PROMPT,
    PURE_AGENT_SYSTEM_PROMPT,
)
from prompts.rag_system import (
    PURE_CHAT_SYSTEM_PROMPT,
    RAG_SYSTEM_PROMPT,
    build_rag_system_prompt,
    format_rag_user_message,
)
from skills.catalog import SkillInfo
from tools.catalog import ordered_tool_names

CITATION_PROTOCOL = """[Citation output protocol]
1. When citing knowledge-base or tool-retrieved content, append a citation handle immediately after the supported fact, in the form [[cN]], where N is the 1-based index of this turn's retrieval results (e.g. [[c1]], [[c2]]).
2. The handle MUST follow the fact it supports. Do not dump handles at the end of the answer.
3. Do not cite non-existent indices (e.g. [[c99]]). Do not use any citation form other than [[cN]] (no [1], 【1】, or "参考来源：").
4. [[cN]] handles in earlier messages belong to previous retrieval turns; ignore them and cite only this turn's results."""


def build_system_prompt(
    enable_citation: bool = True,
    tool_names: Iterable[str] | None = None,
    skills: Sequence[SkillInfo] | None = None,
    *,
    web_search_status: str | None = None,
) -> str:
    """组装智能推理系统提示词；有知识库工具时用 WeKnora Progressive RAG 模板。"""
    names = ordered_tool_names(tool_names)
    name_set = set(names)
    has_kb = bool(
        name_set
        & {"doc_retrieval", "grep_chunks", "list_chunks", "get_document_info", "query_knowledge_graph"}
    )
    status = web_search_status or "see this turn's settings"
    if has_kb:
        text = PROGRESSIVE_RAG_SYSTEM_PROMPT.replace("{{web_search_status}}", status)
    else:
        text = PURE_AGENT_SYSTEM_PROMPT.replace("{{web_search_status}}", status)
    if "generate_pptx" in name_set:
        text = text.rstrip() + "\n" + PPTX_TOOL_GUIDELINE
    prompt = text.strip()
    if enable_citation:
        prompt = prompt + "\n\n" + CITATION_PROTOCOL
    if skills:
        from skills.prompt import append_skills_prompt

        prompt = append_skills_prompt(prompt, skills)
    return prompt


SYSTEM_PROMPT = build_system_prompt

__all__ = [
    "CITATION_PROTOCOL",
    "PURE_CHAT_SYSTEM_PROMPT",
    "RAG_SYSTEM_PROMPT",
    "SYSTEM_PROMPT",
    "build_rag_system_prompt",
    "build_system_prompt",
    "format_rag_user_message",
]
