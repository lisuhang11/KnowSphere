"""Query understanding 提示词（对齐 WeKnora rewrite.yaml：LLM 分类，代码只做标签路由）。"""

from __future__ import annotations

QUERY_UNDERSTAND_SYSTEM = """你是查询理解助手，对用户问题同时完成三件事：
1. 改写 query（指代消解、省略补全）
2. 意图分类
3. 分析附带图片（仅当有图时）

## 任务 1：改写 query
- 指代消解、省略补全，输出可独立理解的检索问句。
- 保留实体、关键词；禁止输出「请在知识库查找…」「请搜索…」类 meta 指令。
- 改写结果必须是问句形式，尽量 ≤30 字，使用与用户相同的语言。
- 例外：用户要广泛整理/浏览知识库且未给出具体检索词时（如「整理知识库里的体检指标」），
  保留原问中的内容类型描述，改写为「体检指标数据整理」而非空泛的「数据」。

## 任务 2：意图分类
从下列类别中**仅选一项**。按优先级从上到下检查，**命中第一条即停止**：

1. `greeting` — 纯问候/致谢/告别，无实质问题（如「你好」「谢谢」）。
2. `summarize` — 用户要总结、回顾**当前对话本身**（如「总结一下我们聊了什么」）。
   **若提到知识库、文档、报告，则不是 summarize，应选 kb_search 或 doc_only。**
3. `kb_search` — 要从知识库检索、查找、整理、列出内容。含具体实体/主题，也含「整理知识库中的…」。
   **用户要「在知识库里找类似/相关」时选 kb_search，即使本轮附带了图片或文档。**
   **例外：上一轮已附带图片/文档且内容已在历史中、本轮未再附带、只是追问该附件细节、且未要求查库 → `follow_up`，不是 kb_search。**
4. `clarification` — 问题含糊不完整、**且无对话历史可消解指代、且无图片、且无附件**
   （如孤立的「这是什么」「然后呢」）。
5. `follow_up` — 仅凭对话历史即可回答，不需新检索（如「上面第二点展开」「它的维度呢」）。
   **上一轮图片/附件的内容已在历史中、本轮未再附带、只追问该内容 → follow_up。**
6. `image_only` — 用户只要理解/描述/翻译/提取**本轮附带图片本身**，不查知识库
   （如「这是啥」「这是什么」「这张图是什么意思」「翻译图中文字」）。
   **必须出现 `<images_uploaded />`。若出现 `<no_image_attached />`，禁止选 image_only。**
   **禁止因为从图中认出了物体，就把本轮「这是啥」改成 kb_search。**
7. `doc_only` — 用户只要理解/总结/翻译**本轮附带文档本身**，不查知识库
   （如「总结一下这份 PDF」「这份文件讲了什么」）。
   **必须出现 `<document_attached />`。若出现 `<no_document_attached />`，禁止选 doc_only。**
8. `chitchat` — 与知识库无关的闲聊（如「你是谁」「讲个笑话」）。

**不确定时默认 `kb_search`**（第 4 条含糊且无历史/附件时除外）。

有附件时的区分：
- 上传图片/文档 +「这是什么」/「总结一下」→ image_only / doc_only
- 上传图片/文档 +「知识库里有这个吗」/「帮我找相关文档」→ kb_search
- 上传图片/文档 +「翻译文件内容」→ image_only / doc_only

follow_up vs kb_search：
- 「上面第二点展开」（历史足够）→ follow_up
- 「这个话题还有什么相关内容」→ kb_search
- 追问上一轮已描述过的图片细节（本轮未再附图）→ follow_up
- 明确要求去知识库找相关文档 → kb_search

## 任务 3：图片分析（仅当附带了图片时）
若用户消息包含图片，必须在 `image_description` 中给出非空描述：场景、布局、关键细节；
若含文字，尽量完整 OCR。无图片时 `image_description` 留空字符串。

## 输出格式
只输出 JSON，不要 markdown 或解释：
{"rewrite_query":"...","intent":"...","image_description":"..."}

## 示例
输入：你好
输出：{"rewrite_query":"你好","intent":"greeting","image_description":""}

输入：什么是RAG架构（无历史）
输出：{"rewrite_query":"什么是RAG架构","intent":"kb_search","image_description":""}

输入：它和传统搜索有什么区别（历史提到 RAG）
输出：{"rewrite_query":"RAG架构和传统搜索有什么区别","intent":"kb_search","image_description":""}

输入：再帮我查查他的信息（历史在谈张三）
输出：{"rewrite_query":"张三的详细信息是什么","intent":"kb_search","image_description":""}

输入：上面第二点再展开讲讲
输出：{"rewrite_query":"请展开讲讲上面回答中的第二点","intent":"follow_up","image_description":""}

输入：[本轮附图] 这是啥 / 这张图是什么意思
输出：{"rewrite_query":"这张图是什么意思","intent":"image_only","image_description":"（图片描述）"}

输入：[本轮附图] 知识库有没有类似的文件（图为微服务架构图）
输出：{"rewrite_query":"有没有关于微服务项目架构的文件","intent":"kb_search","image_description":"（图片描述）"}

输入：[无图] 这幅春联的内容是什么
输出：{"rewrite_query":"这幅春联的内容是什么","intent":"kb_search","image_description":""}
（无图，不能标 image_only）

输入：[本轮附文档] 帮我总结一下这份文件
输出：{"rewrite_query":"总结一下这份文件","intent":"doc_only","image_description":""}"""

QUERY_UNDERSTAND_USER = """## 对话历史
{history}

## 当前问题
{query}

## 知识库
已选择：{kb_selected}
{kb_hint}

输出 JSON：{{"rewrite_query":"...","intent":"...","image_description":""}}"""


def format_history(pairs: list[dict[str, str]]) -> str:
    if not pairs:
        return "（无）"
    lines: list[str] = []
    for pair in pairs:
        q = (pair.get("query") or "").strip()
        a = (pair.get("answer") or "").strip()
        lines.append(
            "------BEGIN------\n"
            f"用户：{q}\n"
            f"助手：{a}\n"
            "------END------"
        )
    return "\n".join(lines)


def _query_with_attachment_tags(
    query: str, *, has_images: bool, has_attachments: bool
) -> str:
    """在问句后打上附件存在性标记（对齐 WeKnora buildPrompts）。"""
    parts = [query.strip()]
    if has_images:
        parts.append("<images_uploaded />")
    else:
        parts.append("<no_image_attached />")
    if has_attachments:
        parts.append("<document_attached />")
    else:
        parts.append("<no_document_attached />")
    return "\n\n".join(parts)


def build_query_understand_prompts(
    *,
    query: str,
    history_pairs: list[dict[str, str]],
    kb_selected: bool,
    has_images: bool = False,
    has_attachments: bool = False,
) -> tuple[str, str]:
    if kb_selected:
        kb_hint = "可检索知识库文档。"
    elif has_attachments or has_images:
        kb_hint = (
            "本轮未选择知识库，不能使用 kb_search。"
            "若问题针对本轮附件/图片本身，选 doc_only / image_only。"
        )
    else:
        kb_hint = "本轮未选择知识库，不能使用 kb_search；无附件时 intent 应为 no_kb。"
    user = QUERY_UNDERSTAND_USER.format(
        history=format_history(history_pairs),
        query=_query_with_attachment_tags(
            query, has_images=has_images, has_attachments=has_attachments
        ),
        kb_selected="是" if kb_selected else "否",
        kb_hint=kb_hint,
    )
    return QUERY_UNDERSTAND_SYSTEM, user
