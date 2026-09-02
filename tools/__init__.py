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
from tools.retrieval.query_knowledge_graph import query_knowledge_graph
from tools.web.fetch import web_fetch
from tools.web.search import web_search

_TOOLS_BY_NAME = {
    write_plan.name: write_plan,
    doc_retrieval.name: doc_retrieval,
    query_knowledge_graph.name: query_knowledge_graph,
    web_search.name: web_search,
    web_fetch.name: web_fetch,
    generate_pptx.name: generate_pptx,
}


def get_tools() -> list:
    """返回目录中全部可执行工具（图的 ToolNode 挂全集；智能体再按绑定名单裁剪）。"""
    return [_TOOLS_BY_NAME[name] for name in TOOL_SPECS if name in _TOOLS_BY_NAME]


def get_tools_by_names(names: list[str] | None) -> list:
    selected = ordered_tool_names(names)
    return [_TOOLS_BY_NAME[name] for name in selected if name in _TOOLS_BY_NAME]


__all__ = [
    "BUILTIN_AGENT_ID",
    "get_tool_spec",
    "get_tools",
    "get_tools_by_names",
]
