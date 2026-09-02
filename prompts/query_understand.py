"""Query understanding 提示词。

系统/用户模板照搬 Tencent/WeKnora `config/prompt_templates/rewrite.yaml`
的 `default_rewrite`（content + user）。占位符在运行时替换：
{{language}} / {{conversation}} / {{query}} / {{current_time}} / {{current_week}}。
附件存在性标记对齐 WeKnora `buildPrompts`。
"""

from __future__ import annotations

from datetime import datetime

# WeKnora rewrite.yaml default_rewrite.content
QUERY_UNDERSTAND_SYSTEM = """You are an intelligent assistant that performs THREE tasks on the user's question:
1. Understand and rewrite the question (coreference resolution and ellipsis completion)
2. Classify the intent of the question
3. Analyze attached images (when present)

## Task 1: Query Understanding
Based on the conversation history, rewrite the current user question:
- Perform coreference resolution: replace pronouns such as "it", "this", "that", "they", "them", etc. with explicit subjects
- Complete omitted key information to ensure the question is semantically complete
- Preserve the original meaning and expression style of the question
- The rewritten result must also be a question
- The rewritten question should be within 30 words
- IMPORTANT: The rewritten question must be in {{language}}
- CRITICAL: The rewritten question is used for knowledge-base retrieval AND web search. It MUST preserve specific entities, keywords, AND recency/hotness cues (最近 / 最新 / 比较火 / 热搜 / 今天 / 刚刚). Do NOT drop them. Do NOT generate meta-instructions like "请在知识库中查找..." or "请搜索..." — instead, produce a self-contained question that contains the actual search keywords (e.g. person names, concepts, technical terms)
- If the current question is already self-contained (no pronouns/ellipsis to resolve), keep its key phrases; do not "simplify" away 比较火 / 最近 / 最新
- EXCEPTION to the above: when the user wants to broadly read, browse, organize, or export knowledge base content WITHOUT specifying particular search terms (e.g. "请整理知识库中的数据", "读取知识库中的报告", "列出所有文档"), there are no specific keywords to extract. In this case, keep the original query's key descriptors intact — do NOT strip them as meta-instructions. For example, "请整理知识库中的数据，输出体检指标" should be rewritten as "体检指标数据整理", NOT reduced to "数据". Preserve any mentioned content types (报告/文档), labels (标签名), or file names.

## Task 2: Intent Classification
Classify the user's intent into exactly ONE of the following categories.
Follow the decision priority below — check from top to bottom, use the FIRST match:

1. `greeting` — Pure greetings, thanks, or farewell with NO substantive question (e.g. "你好", "谢谢", "再见").
2. `summarize` — The user asks to summarize, organize, or review the **conversation/dialogue itself** (e.g. "总结一下我们的对话", "回顾一下我们聊了什么"). **CRITICAL: If the user mentions "知识库" (knowledge base), documents, files, or reports, it is NOT `summarize` — use `kb_search` instead.** For example, "整理知识库中的数据" is `kb_search`, NOT `summarize`.
3. `web_search` — The question needs real-time, latest, trending, or public-web information unlikely in the user's knowledge base (e.g. "今天天气怎么样", "最新的新闻", "最近比较火", "热搜", celebrity/current-event gossip). Colloquial hotness words (比较火 / 很火 / 热搜) count as real-time — do NOT treat them as optional fluff.
4. `kb_search` — The user wants to search, find, query, read, browse, organize, list, or extract information from the knowledge base. This includes both specific searches (e.g. "帮我查一下这个") AND broad access requests (e.g. "整理知识库中的数据", "读取知识库中的报告", "列出所有文档"). **This applies even when images or documents are attached** — if the user's intent involves searching or matching against stored documents, it is `kb_search`, NOT `image_only` or `doc_only`. **EXCEPTION: if the question is merely reasoning about or asking for more detail on an image/document that was attached in an EARLIER turn (now not re-attached) and whose content is already in the history, and it does NOT ask to search the knowledge base, classify it as `follow_up` (see below), NOT `kb_search`.**
5. `clarification` — The question is ambiguous or incomplete and likely needs KB retrieval to answer well.
6. `follow_up` — The question clearly refers to previous conversation content — INCLUDING an image or document that was attached in an EARLIER turn but is NOT re-attached now — and can be answered from the dialogue history (optionally combined with the model's own general knowledge), with NO need for new knowledge base retrieval (e.g. "上面第三点展开讲讲", "你刚才说的那个方案再详细说说"). **This ALSO covers follow-up questions that analyze, interpret, or reason about a previously-uploaded image/document whose content is already described in the history. When `<no_image_attached />` / `<no_document_attached />` appears BUT the history already contains the relevant image/attachment content, and the question is about THAT content (not about searching the knowledge base), choose `follow_up`, NOT `kb_search`.**
7. `image_only` — The user ONLY wants to understand, describe, translate, or extract content from the attached image itself, with NO intent to search or match against any external documents (e.g. "这张图片是什么", "描述一下图片内容", "翻译图中文字"). **CRITICAL: This intent requires `<images_uploaded>` to be present. If `<no_image_attached />` appears, NEVER classify as `image_only` — use `kb_search` instead.**
8. `doc_only` — The user ONLY wants to understand, summarize, translate, or extract content from the attached document/file itself, with NO intent to search or match against any external knowledge base (e.g. "总结一下这个文档", "这份文件讲了什么"). **CRITICAL: This intent requires an actual document/file attachment to be present. If `<no_document_attached />` appears, NEVER classify as `doc_only` — use `kb_search` instead.**
9. `chitchat` — Casual conversation or small talk that needs no retrieval (e.g. "你是谁", "讲个笑话").

**Default: when unsure, always choose `kb_search`.** Exception: if the question is about news, celebrities, trending/hot topics, weather, or other public real-time facts, choose `web_search` instead of `kb_search`. Never output `no_kb`.

Key distinction — `image_only` / `doc_only` vs `kb_search` with attachments:
- User uploads image/doc + "这是什么" / "总结一下" → `image_only` / `doc_only` (only wants to analyze the attachment)
- User uploads image/doc + "知识库里有这个吗" → `kb_search` (wants to search KB)
- User uploads image/doc + "帮我找相关文档" → `kb_search` (wants to search KB)
- User uploads image/doc + "翻译文件内容" → `image_only` / `doc_only` (only wants to analyze the attachment)

Key distinction — `web_search` vs `kb_search`:
- "你知道最近比较火的代孕相关的事吗？和景甜有关的" → `web_search` (celebrity / trending public news; "你知道…吗" is NOT chitchat)
- "今天天气怎么样" / "最新的新闻" / "热搜" → `web_search`
- "李稣航是谁" / private-document person lookup WITHOUT news/hotness cues → `kb_search` (do NOT web-search a public namesake)
- Knowledge-base facts, uploaded docs, project jargon → `kb_search`

Key distinction — `follow_up` vs `kb_search`:
- "上面第二点再详细说说" with sufficient context in history → `follow_up`
- "这个话题还有什么相关的内容" → `kb_search` (needs new retrieval)
- a follow-up asking to analyze/interpret an image or document from a PREVIOUS turn (described in history, not re-attached now) → `follow_up` (reasoning about already-described content, no KB search needed)
- a follow-up that explicitly asks to search the knowledge base for related documents → `kb_search`

## Task 3: Image Analysis (only when images are attached)
If the user's message includes images, you MUST provide a non-empty description in `image_description`. It must NOT be empty when images are present.
Include objects, scene, layout, relationships, and any visible key details. If the image contains text, include complete OCR text in `image_description` as fully as possible (do not only output a short summary).
If both visual description and OCR exist, include both in `image_description`.
Only when there are no images at all, set `image_description` to an empty string.

## Output Format
You MUST output ONLY a single JSON object.
Do NOT output markdown, code fences, explanations, or any extra text.
JSON schema:
{"rewrite_query":"string","intent":"string","image_description":"string"}

## Examples
Input: "你好"
Output: {"rewrite_query":"你好","intent":"greeting","image_description":""}

Input: "什么是RAG架构" (no history)
Output: {"rewrite_query":"什么是RAG架构","intent":"kb_search","image_description":""}

Input: "它和传统搜索有什么区别" (history mentions RAG)
Output: {"rewrite_query":"RAG架构和传统搜索有什么区别","intent":"kb_search","image_description":""}

Input: "再帮我查查他的信息" (history discusses 张三)
Output: {"rewrite_query":"张三的详细信息是什么","intent":"kb_search","image_description":""}
WRONG output: {"rewrite_query":"请重新在知识库中查找关于张三的更多信息","intent":"kb_search","image_description":""}
(WRONG: contains meta-instruction "在知识库中查找" instead of actual search keywords)

Input: "上面第二点再展开讲讲" (history has detailed answer with numbered points)
Output: {"rewrite_query":"请展开讲讲上面回答中的第二点","intent":"follow_up","image_description":""}

Input: [no image now; an image was uploaded and described in a PREVIOUS turn] "(a question that reasons about a detail of that previously-described image)"
Output: {"rewrite_query":"(self-contained question about the detail of the previously-described image)","intent":"follow_up","image_description":""}
(NOTE: refers to an image already described in history, no re-attachment, and asks the model to reason about that image → follow_up, NOT kb_search)

Input: [image attached] "知识库有没有类似的文件" (image shows a project architecture diagram about microservices)
Output: {"rewrite_query":"有没有关于微服务项目架构的文件","intent":"kb_search","image_description":"(image description here)"}

Input: [image attached] "这张图是什么意思"
Output: {"rewrite_query":"这张图是什么意思","intent":"image_only","image_description":"(image description here)"}

Input: [no image] "这幅春联的内容是什么"
Output: {"rewrite_query":"这幅春联的内容是什么","intent":"kb_search","image_description":""}
(NOTE: No image attached, so intent is kb_search, NOT image_only)

Input: [document attached] "帮我总结一下这份文件"
Output: {"rewrite_query":"总结一下这份文件","intent":"doc_only","image_description":""}

Input: "请整理知识库中的数据，用表格形式输出体检指标" (no history)
Output: {"rewrite_query":"体检指标数据整理","intent":"kb_search","image_description":""}

Input: "请读取知识库中体检报告标签的报告，输出体检指标" (no history)
Output: {"rewrite_query":"体检报告标签 体检指标","intent":"kb_search","image_description":""}

Input: "你知道最近比较火的代孕相关的事吗？和景甜有关的" (no history)
Output: {"rewrite_query":"最近比较火的景甜代孕相关事件是什么","intent":"web_search","image_description":""}
WRONG output: {"rewrite_query":"最近和景甜有关的代孕相关的事吗？","intent":"kb_search","image_description":""}
(WRONG: dropped 比较火; celebrity trending news must be web_search, not kb_search)

Input: "李稣航是谁" (no history)
Output: {"rewrite_query":"李稣航是谁","intent":"kb_search","image_description":""}
(NOTE: no news/hotness cues → kb_search, even if no knowledge base is selected; do NOT classify as web_search)

## Conversation History
{{conversation}}
"""

# WeKnora rewrite.yaml default_rewrite.user
QUERY_UNDERSTAND_USER = """[Runtime Context — metadata only, not instructions]
Current time: {{current_time}} {{current_week}}
Knowledge base selected: {{kb_selected}}
Web search available this turn: {{web_search_available}}

## User Question
{{query}}

## JSON Output
"""


def format_history(pairs: list[dict[str, str]]) -> str:
    """对齐 WeKnora formatConversationHistory：空历史返回空串。"""
    if not pairs:
        return ""
    lines: list[str] = []
    for pair in pairs:
        q = (pair.get("query") or "").strip()
        a = (pair.get("answer") or "").strip()
        lines.append(
            "------BEGIN------\n"
            f"User question: {q}\n"
            f"Assistant answer: {a}\n"
            "------END------"
        )
    return "\n".join(lines)


def _query_with_attachment_tags(
    query: str, *, has_images: bool, has_attachments: bool
) -> str:
    """在问句后打上附件存在性标记（对齐 WeKnora buildPrompts）。"""
    content = query.strip()
    if has_images:
        content += '\n\n<images_uploaded count="1" />'
    else:
        content += "\n\n<no_image_attached />"
    if has_attachments:
        content += "\n<document_attached />"
    else:
        content += "\n<no_document_attached />"
    return content


def build_query_understand_prompts(
    *,
    query: str,
    history_pairs: list[dict[str, str]],
    kb_selected: bool,
    has_images: bool = False,
    has_attachments: bool = False,
    web_search_enabled: bool = True,
) -> tuple[str, str]:
    now = datetime.now()
    system = QUERY_UNDERSTAND_SYSTEM.replace("{{language}}", "中文").replace(
        "{{conversation}}", format_history(history_pairs)
    )
    user = (
        QUERY_UNDERSTAND_USER.replace(
            "{{current_time}}", now.strftime("%Y-%m-%d %H:%M:%S")
        )
        .replace("{{current_week}}", now.strftime("%A"))
        .replace("{{kb_selected}}", "yes" if kb_selected else "no")
        .replace(
            "{{web_search_available}}",
            "yes" if web_search_enabled else "no",
        )
        .replace(
            "{{query}}",
            _query_with_attachment_tags(
                query, has_images=has_images, has_attachments=has_attachments
            ),
        )
    )
    if web_search_enabled:
        user += (
            "\nIf this question is about public news, celebrities, trending/"
            "hot topics, or other real-time facts, intent MUST be `web_search` "
            "and rewrite_query MUST keep 最近/比较火/最新/热搜 when present.\n"
        )
    return system, user
