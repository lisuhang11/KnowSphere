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
        prompt_line=(
            "- doc_retrieval：在用户知识库中语义+词法检索。"
            "返回 chunk_id、document_id 和正文。"
            "库内事实必须先走它；没命中或问法不对可换检索词再搜。"
        ),
        requires_kb=True,
        produces="citations",
    ),
    "grep_chunks": ToolSpec(
        name="grep_chunks",
        display_name="关键词搜索",
        description="在知识库分块中按正则精确查找（工号、错误码、产品名）。",
        category="knowledge",
        prompt_line=(
            "- grep_chunks：库内 POSIX 正则（忽略大小写），适合必须出现的专名、编号、错误码。"
            "多个词用 | 写进一条。只返回匹配附近片段；随后用 list_chunks 精读。"
            "不能代替 doc_retrieval。"
        ),
        requires_kb=True,
        produces="citations",
    ),
    "list_chunks": ToolSpec(
        name="list_chunks",
        display_name="精读文档",
        description="按 chunk_id 读一块全文，或按 document_id 分页列出分块正文。",
        category="knowledge",
        prompt_line=(
            "- list_chunks：doc_retrieval 或 grep_chunks 之后精读。"
            "用返回的 chunk_id / document_id，或句柄 cN / dN（与 [[cN]] 相同）。"
            "不要把引用序号或文件名#后的数字当成数据库 id；不能代替语义检索。"
        ),
        requires_kb=True,
        produces="citations",
    ),
    "get_document_info": ToolSpec(
        name="get_document_info",
        display_name="文档信息",
        description="查看文档文件名、解析状态、分块数；不含正文。",
        category="knowledge",
        prompt_line=(
            "- get_document_info：文档元数据（文件名、解析状态、分块数），没有正文。"
            "可传 document_ids，或不传以列出本轮知识库中的文档。"
            "要读内容请用 list_chunks。"
        ),
        requires_kb=True,
        produces="text",
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
        description="公开网页搜索（Bing HTML，DuckDuckGo 短超时回退），无需 API Key。",
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
    "grep_chunks",
    "list_chunks",
    "get_document_info",
    "query_knowledge_graph",
    "web_search",
    "web_fetch",
)
PPT_AGENT_TOOL_NAMES: tuple[str, ...] = (
    "write_plan",
    "doc_retrieval",
    "list_chunks",
    "generate_pptx",
)
# 内置 PPT 助手的默认技能白名单；用户仍可在编辑页增删，seed 只在名单为空时补上。
PPT_AGENT_SKILL_NAMES: tuple[str, ...] = (
    "ppt-structure",
    "ppt-from-material",
)
CATALOG_TOOL_NAMES: tuple[str, ...] = tuple(TOOL_SPECS.keys())

BUILTIN_PPT_AGENT_NAME = "PPT 助手"
BUILTIN_PPT_AGENT_DESCRIPTION = "把主题做成演示文稿，可先检索知识库再生成 PPT。"
BUILTIN_PPT_AGENT_PROMPT = """你是 KnowSphere 的 PPT 助手，专门把用户需求做成演示文稿。

工作方式：
1. 系统提示里若列出了 PPT 相关技能，先 `read_skill` 再按说明书组织大纲；不要跳过技能直接堆要点。
2. 先确认主题、受众、页数；用户没说就按 6–10 页、面向内部汇报来做。
3. 需要知识库中的事实时先调用 doc_retrieval；专名/编号可用 grep_chunks；摘要不够再用 list_chunks 按 chunk_id 或 document_id 精读。禁止编造库内人物、项目、数据。
4. 材料足够后调用 generate_pptx：传入 title 和 slides（每页 title + bullets 要点列表）。
5. 不要在工具成功返回之前声称已经生成文件。
6. 用户要改某一页或整体风格时，基于上一轮大纲重新调用 generate_pptx，生成完整新文件。

不要使用未绑定的工具。最终用中文简要说明生成了什么，不要大段复述每页正文。"""

# 升级前内置提示词；seed 只在库里仍是这段时才改写，避免覆盖用户自定义。
LEGACY_PPT_AGENT_PROMPT = """你是 KnowSphere 的 PPT 助手，专门把用户需求做成演示文稿。

工作方式：
1. 先确认主题、受众、页数；用户没说就按 6–10 页、面向内部汇报来做。
2. 需要知识库中的事实时先调用 doc_retrieval；专名/编号可用 grep_chunks；摘要不够再用 list_chunks 按 chunk_id 或 document_id 精读。禁止编造库内人物、项目、数据。
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
