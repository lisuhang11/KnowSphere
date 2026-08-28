"""Query understanding 提示词（简化版）。"""

from __future__ import annotations

QUERY_UNDERSTAND_SYSTEM = """你是查询理解助手。基于对话历史，完成下列任务：

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
3. `kb_search` — 需要从知识库检索新内容（默认）。含明确实体/主题，或「整理知识库中的…」类请求。
   **用户要「在知识库里找类似/相关」时选 kb_search，即使附带了图片或文档。**
4. `image_only` — **仅当本轮附带了图片**时可选：用户只想理解/描述/翻译图片本身，
   不需要检索知识库（如「这张图是什么意思」「翻译图中文字」）。
   **无图片时禁止选 image_only。**
5. `doc_only` — **仅当本轮附带了文档/文件附件**时可选：用户只想理解/总结/翻译附件本身，
   不需要检索知识库（如「总结一下这份 PDF」「这份文件讲了什么」）。
   **无附件时禁止选 doc_only。**
6. `clarification` — 问题过于含糊、**且无对话历史可消解指代、且无图片、且无附件**，
   无法形成有效检索词（如孤立的「这是什么」「然后呢」）。
7. `follow_up` — 仅凭对话历史即可回答，不需新检索（如「上面第二点展开」「它的维度呢」）。
8. `chitchat` — 与知识库无关的闲聊（如「你是谁」「讲个笑话」）。

**不确定时默认选 `kb_search`**（但第 6 条含糊且无历史/附件时除外）。

## 任务 3：图片分析（仅当附带了图片时）
若用户消息包含图片，必须在 `image_description` 中给出非空描述：包含场景、布局、关键细节；
若图片含文字，尽量完整 OCR。无图片时 `image_description` 留空字符串。

## 关键区分
- 「总结一下我们的对话」→ summarize；「整理知识库中的报告」→ kb_search。
- 有历史时「它的维度呢」→ follow_up；无历史孤立「这是什么」且无附件/图片 → clarification。
- **有图片时**「这是什么」→ image_only；「知识库里有类似的吗」→ kb_search。
- **有 PDF 附件时**「总结一下这份文件」→ doc_only；「知识库里有类似文档吗」→ kb_search。
- 「上面第二点展开」→ follow_up；「这个话题还有什么相关内容」→ kb_search。

## 输出格式
只输出 JSON，不要 markdown 或解释：
{"rewrite_query":"...", "intent":"...", "image_description":"..."}"""

QUERY_UNDERSTAND_USER = """## 对话历史
{history}

## 当前问题
{query}

## 知识库
已选择：{kb_selected}
{kb_hint}

## 图片
{image_hint}

## 文档附件
{attachment_hint}

输出 JSON：{{"rewrite_query":"...", "intent":"...", "image_description":""}}"""

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

def build_query_understand_prompts(
    *,
    query: str,
    history_pairs: list[dict[str, str]],
    kb_selected: bool,
    has_images: bool = False,
    has_attachments: bool = False,
) -> tuple[str, str]:
    kb_hint = "可检索用户上传的文档。" if kb_selected else "本轮未选择知识库，intent 应为 no_kb。"
    image_hint = (
        "本轮用户附带了图片（<images_uploaded />）。"
        "若仅分析图片选 image_only；若要查知识库选 kb_search。"
        if has_images
        else "本轮无图片（<no_image_attached />）。禁止选 image_only。"
    )
    attachment_hint = (
        "本轮用户附带了文档/文件附件（<document_attached />）。"
        "若仅分析附件选 doc_only；若要查知识库选 kb_search。"
        if has_attachments
        else "本轮无文档附件（<no_document_attached />）。禁止选 doc_only。"
    )
    user = QUERY_UNDERSTAND_USER.format(
        history=format_history(history_pairs),
        query=query.strip(),
        kb_selected="是" if kb_selected else "否",
        kb_hint=kb_hint,
        image_hint=image_hint,
        attachment_hint=attachment_hint,
    )
    return QUERY_UNDERSTAND_SYSTEM, user
