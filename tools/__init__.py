"""工具统一注册表：新增工具后加入 catalog 与 get_tools。"""

from tools.catalog import (
    BUILTIN_AGENT_ID,
    TOOL_SPECS,
    get_tool_spec,
    ordered_tool_names,
)
from tools.creation.generate_pptx import generate_pptx
from tools.planning.write_plan import write_plan
from tools.retrieval.doc_retrieval import doc_retrieval
from tools.retrieval.get_document_info import get_document_info
from tools.retrieval.grep_chunks import grep_chunks
from tools.retrieval.list_chunks import list_chunks
from tools.retrieval.query_knowledge_graph import query_knowledge_graph
from tools.skills import SKILL_RUNTIME_TOOL_NAMES, SKILL_RUNTIME_TOOLS
from tools.storage import STORAGE_RUNTIME_TOOL_NAMES, STORAGE_RUNTIME_TOOLS
from tools.web.fetch import web_fetch
from tools.web.search import web_search

_TOOLS_BY_NAME = {
    write_plan.name: write_plan,
    doc_retrieval.name: doc_retrieval,
    grep_chunks.name: grep_chunks,
    list_chunks.name: list_chunks,
    get_document_info.name: get_document_info,
    query_knowledge_graph.name: query_knowledge_graph,
    web_search.name: web_search,
    web_fetch.name: web_fetch,
    generate_pptx.name: generate_pptx,
}
for _tool in (*SKILL_RUNTIME_TOOLS, *STORAGE_RUNTIME_TOOLS):
    _TOOLS_BY_NAME[_tool.name] = _tool


def ensure_storage_tools(tool_list: list) -> list:
    """评测/自定义 tool_list 也挂上 get_stored_data，否则外置后无法取回。"""
    names = {getattr(t, "name", None) for t in tool_list}
    extra = [t for t in STORAGE_RUNTIME_TOOLS if t.name not in names]
    return list(tool_list) + extra


def get_tools() -> list:
    """返回可执行工具全集（目录工具 + 运行时元工具）。智能体再按绑定名单裁剪。"""
    catalog = [_TOOLS_BY_NAME[name] for name in TOOL_SPECS if name in _TOOLS_BY_NAME]
    runtime = [
        t
        for t in (*SKILL_RUNTIME_TOOLS, *STORAGE_RUNTIME_TOOLS)
        if t.name in _TOOLS_BY_NAME
    ]
    return catalog + runtime


def get_tools_by_names(names: list[str] | None) -> list:
    selected = ordered_tool_names(names)
    extra = [
        n
        for n in (names or [])
        if n in SKILL_RUNTIME_TOOL_NAMES or n in STORAGE_RUNTIME_TOOL_NAMES
    ]
    ordered = selected + [n for n in extra if n not in selected]
    for n in STORAGE_RUNTIME_TOOL_NAMES:
        if n not in ordered:
            ordered.append(n)
    return [_TOOLS_BY_NAME[name] for name in ordered if name in _TOOLS_BY_NAME]


__all__ = [
    "BUILTIN_AGENT_ID",
    "SKILL_RUNTIME_TOOL_NAMES",
    "STORAGE_RUNTIME_TOOL_NAMES",
    "ensure_storage_tools",
    "get_tool_spec",
    "get_tools",
    "get_tools_by_names",
]
