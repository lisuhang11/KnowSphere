"""内置工具目录：代码即事实源，供智能体勾选与运行时裁剪。

新增工具时：实现 @tool 函数 → 在此登记 → 加入 get_tools()。
智能体只引用 name，不把可执行体存进数据库。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class ToolSpec:
    name: str
    display_name: str
    description: str
    category: str  # planning | knowledge | web | creation
    prompt_line: str
    requires_kb: bool = False
    requires_web: bool = False
    requires_graph: bool = False
    produces: str = "text"  # text | citations | file


TOOL_SPECS: dict[str, ToolSpec] = {
    "write_plan": ToolSpec(
        name="write_plan",
        display_name="任务规划",
        description="多步任务先列出计划，再按步骤调用其它工具。",
        category="planning",
        prompt_line="- write_plan：多跳或超过两步的任务，先列出计划再执行。简单单跳事实题可跳过。",
    ),
    "doc_retrieval": ToolSpec(
        name="doc_retrieval",
        display_name="知识库检索",
        description="在用户知识库中混合检索文档片段（选了库才生效）。",
        category="knowledge",
        prompt_line="- doc_retrieval：在用户知识库中检索文档片段。库内事实必须先走它。",
        requires_kb=True,
        produces="citations",
    ),
    "query_knowledge_graph": ToolSpec(
        name="query_knowledge_graph",
        display_name="知识图谱",
        description="查询实体关系，并回捞相关文档片段（需开启图谱的知识库）。",
        category="knowledge",
        prompt_line=(
            "- query_knowledge_graph：知识库检索之后可选，"
            "适合「A 和 B 的关系」、组织架构等；不能替代 doc_retrieval。"
        ),
        requires_kb=True,
        requires_graph=True,
        produces="citations",
    ),
    "web_search": ToolSpec(
        name="web_search",
        display_name="联网搜索",
        description="公开网页搜索（DuckDuckGo，失败时回退 Bing），无需 API Key。",
        category="web",
        prompt_line=(
            "- web_search：仅当本轮联网已开启，且知识库缺失或不相关"
            "（或未选知识库）时，搜索公开网页。"
        ),
        requires_web=True,
        produces="citations",
    ),
    "web_fetch": ToolSpec(
        name="web_fetch",
        display_name="读取网页",
        description="抓取公开网页正文；拦截内网与 localhost。",
        category="web",
        prompt_line="- web_fetch：读取 web_search 得到的某个 URL 的正文。",
        requires_web=True,
        produces="citations",
    ),
    "generate_pptx": ToolSpec(
        name="generate_pptx",
        display_name="生成 PPT",
        description="根据标题和每页要点生成 .pptx，出现在对话的文件卡片中。",
        category="creation",
        prompt_line=(
            "- generate_pptx：材料足够后调用，传入 title 和 slides"
            "（每页 title + bullets）。不要在工具成功返回前声称已生成文件。"
        ),
        produces="file",
    ),
}

TOOL_CATEGORIES: dict[str, str] = {
    "planning": "规划",
    "knowledge": "知识库",
    "web": "联网",
    "creation": "生成",
}

BUILTIN_AGENT_ID = "agent-smart-reasoning"
BUILTIN_PPT_AGENT_ID = "agent-ppt"
REASONING_TOOL_NAMES: tuple[str, ...] = (
    "write_plan",
    "doc_retrieval",
    "query_knowledge_graph",
    "web_search",
    "web_fetch",
)
PPT_AGENT_TOOL_NAMES: tuple[str, ...] = (
    "write_plan",
    "doc_retrieval",
    "generate_pptx",
)
CATALOG_TOOL_NAMES: tuple[str, ...] = tuple(TOOL_SPECS.keys())

BUILTIN_PPT_AGENT_NAME = "PPT 助手"
BUILTIN_PPT_AGENT_DESCRIPTION = "把主题做成演示文稿，可先检索知识库再生成 PPT。"
BUILTIN_PPT_AGENT_PROMPT = """你是 KnowSphere 的 PPT 助手，专门把用户需求做成演示文稿。

工作方式：
1. 先确认主题、受众、页数；用户没说就按 6–10 页、面向内部汇报来做。
2. 需要知识库中的事实时先调用 doc_retrieval，禁止编造库内人物、项目、数据。
3. 材料足够后调用 generate_pptx：传入 title 和 slides（每页 title + bullets 要点列表）。
4. 不要在工具成功返回之前声称已经生成文件。
5. 用户要改某一页或整体风格时，基于上一轮大纲重新调用 generate_pptx，生成完整新文件。

不要使用未绑定的工具。最终用中文简要说明生成了什么，不要大段复述每页正文。"""


def get_tool_spec(name: str) -> ToolSpec | None:
    return TOOL_SPECS.get(name)


def known_tool_names() -> frozenset[str]:
    return frozenset(TOOL_SPECS)


def ordered_tool_names(names: Iterable[str] | None) -> list[str]:
    """按目录登记顺序去重；未知 name 丢弃。"""
    if names is None:
        return list(TOOL_SPECS)
    wanted = {str(n).strip() for n in names if str(n).strip()}
    return [n for n in TOOL_SPECS if n in wanted]


def spec_to_public(spec: ToolSpec) -> dict:
    return {
        "name": spec.name,
        "display_name": spec.display_name,
        "description": spec.description,
        "category": spec.category,
        "category_label": TOOL_CATEGORIES.get(spec.category, spec.category),
        "requires_kb": spec.requires_kb,
        "requires_web": spec.requires_web,
        "requires_graph": spec.requires_graph,
        "produces": spec.produces,
    }


def tools_to_public(names: Sequence[str] | None = None) -> list[dict]:
    ordered = ordered_tool_names(names)
    return [spec_to_public(TOOL_SPECS[n]) for n in ordered]
