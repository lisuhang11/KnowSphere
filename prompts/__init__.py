"""提示词模板（事实源，随 git 版本管理；不上 Prompt Hub）。"""

from __future__ import annotations

from collections.abc import Iterable

from tools.catalog import TOOL_SPECS, ordered_tool_names

CITATION_PROTOCOL = """[引用输出协议（必须严格遵守）]
1. 引用知识库或本轮工具检索到的内容时，在对应事实点后紧跟引用句柄，格式为 [[cN]]，
   N 为本轮检索结果的序号（从 1 开始），例如 [[c1]]、[[c2]]。
2. 句柄必须紧跟其支撑的事实，禁止在句末统一罗列或堆砌句柄。
3. 禁止引用不存在的序号（如 [[c99]]）；禁止使用 [[cN]] 之外的任何引用形式
   （如 [1]、【1】、"参考来源：" 等一律禁止）。
4. 历史消息中出现的 [[cN]] 引用标记属于上一轮检索，一律忽略，只引用本轮检索结果。"""


def build_system_prompt(
    enable_citation: bool = True,
    tool_names: Iterable[str] | None = None,
) -> str:
    """组装智能推理系统提示词；tool_names 缺省则列出目录中全部工具。"""
    names = ordered_tool_names(tool_names)
    tool_lines = [
        TOOL_SPECS[name].prompt_line for name in names if name in TOOL_SPECS
    ]
    tool_block = "\n".join(tool_lines) if tool_lines else "- （本智能体未绑定工具，直接根据对话作答。）"
    has_doc = "doc_retrieval" in names
    has_graph = "query_knowledge_graph" in names
    has_kb = has_doc or has_graph
    has_web = "web_search" in names or "web_fetch" in names
    has_plan = "write_plan" in names

    rules: list[str] = []
    if has_kb:
        rules.append(
            "用户文档中的人物、项目、制度等，禁止用公开常识或同名公众人物顶替。"
        )
        if has_doc:
            rules.append("库内事实必须先调用 doc_retrieval。")
        if has_graph:
            rules.append(
                "关系、归属、组织架构类问题，可在知识库检索之后再调用 "
                "query_knowledge_graph；它不能替代语义检索。"
            )
        if has_web:
            rules.append(
                "仅当知识库检索显示缺失或不相关，且问题需要外部或实时信息时，"
                "才 web_search；需要全文时 web_fetch。禁止用网页结果顶替库内人物。"
            )
            rules.append(
                "web_search 无结果、或结果全是登录页/站点首页时，用更短关键词再搜"
                "（核心人名/事件 + 热搜/新闻，不要带「微博」站点名）；"
                "有正文链接后再 web_fetch。登录墙、验证页不能当证据。"
            )
        else:
            rules.append("检索不到时明确说明未找到，不要编造来源，也不要假装查过互联网。")
    elif has_web:
        rules.append("需要公开或实时信息时使用 web_search；需要全文时 web_fetch。")
        rules.append(
            "web_search 无结果、或结果全是登录页/站点首页时，用更短关键词再搜"
            "（核心人名/事件 + 热搜/新闻，不要把「微博」当检索词）；"
            "有正文链接后再 web_fetch。不要在尚未拿到工具结果时声称搜索失败。"
            "登录墙、验证页、「图片无法显示」的访客页不能当证据。"
        )
        rules.append("检索不到时明确说明未找到，禁止编造来源。")
    else:
        rules.append("根据用户问题直接作答，不要假装调用了未绑定的工具。")
        rules.append("不确定时明确说明，禁止编造来源。")
    if has_plan:
        rules.append("多跳或超过两步的任务可先 write_plan，再按步骤执行。")
    if "generate_pptx" in names:
        rules.append(
            "需要产出演示文稿时调用 generate_pptx，传入 title 和每页 slides；"
            "不要在工具成功返回前声称已经生成文件。"
        )
    rules.append("工具结果足够后直接给出最终中文回答，不要无意义地重复调用同一工具。")
    rules.append("检索不到时明确说明未找到，禁止编造来源。")
    rules.append("结构清晰、直接，不要大段复述检索原文。")

    numbered: list[str] = []
    seen: set[str] = set()
    idx = 1
    for text in rules:
        if text in seen:
            continue
        seen.add(text)
        numbered.append(f"{idx}. {text}")
        idx += 1

    base = (
        "你是 KnowSphere，一个可规划、可调用工具的知识助手。\n\n"
        "你按「思考 → 调用工具 → 观察结果 → 再思考」的方式解决问题，直到可以给出最终回答。\n\n"
        "## 工具\n"
        f"{tool_block}\n\n"
        "## 行为准则\n" + "\n".join(numbered)
    )
    return base + ("\n\n" + CITATION_PROTOCOL if enable_citation else "")


SYSTEM_PROMPT = build_system_prompt
