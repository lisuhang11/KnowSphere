"""意图分类的 OpenJev Choice 提示：只输出一个选项字母，不生成 JSON。"""

from __future__ import annotations

from datetime import datetime

from prompts.query_understand import (
    _query_with_attachment_tags,
    format_rewrite_conversation,
)

# 决策优先级与 prompts/query_understand.py Task 2 对齐，压缩成单字母选项。
INTENT_CHOICE_SYSTEM = """You are a System One decision model. Classify the user's intent.
Reply with exactly ONE capital letter (A-I). No words, no punctuation, no JSON.

Options (check from top to bottom, pick the FIRST match):
A greeting — Pure greetings, thanks, or farewell with NO substantive question (e.g. "你好", "谢谢", "再见").
B summarize — Summarize/review the conversation itself (e.g. "总结一下我们的对话"). If the user mentions 知识库 / documents / files / reports, this is NOT B — use D.
C web_search — Needs real-time, latest, trending, or public-web information (e.g. "今天天气", "最新新闻", "最近比较火", "热搜", celebrity/current-event gossip). Colloquial hotness words (比较火 / 很火 / 热搜) count as real-time.
D kb_search — Search, find, query, read, browse, organize, list, or extract from the knowledge base. Includes broad access ("整理知识库中的数据") AND searching stored documents even when an image/file is attached. Default when unsure, unless the question is public real-time news (then C).
E clarification — Ambiguous/incomplete question that likely needs KB retrieval.
F follow_up — Refers to previous conversation content (including an image/document from an EARLIER turn that is NOT re-attached now) and can be answered from history, with NO new KB search.
G image_only — ONLY understand/describe/translate/extract the attached image itself. Requires <images_uploaded>. If <no_image_attached />, NEVER choose G.
H doc_only — ONLY understand/summarize/translate/extract the attached document itself. Requires <document_attached />. If <no_document_attached />, NEVER choose H.
I chitchat — Casual talk that needs no retrieval (e.g. "你是谁", "讲个笑话").

Never output no_kb.

Distinctions:
- upload + "这是什么" / "总结一下" → G / H; upload + "知识库里有这个吗" → D
- "你知道最近比较火的代孕相关的事吗？和景甜有关的" → C (keep 比较火; "你知道…吗" is NOT I)
- "李稣航是谁" with no news/hotness cues → D, not C
- "上面第二点再详细说说" with enough history → F; "这个话题还有什么相关的" → D
- previous-turn image/doc already described in history, not re-attached, asking about THAT content → F, not D

## Conversation History
{{conversation}}
"""

INTENT_CHOICE_USER = """[Runtime Context — metadata only, not instructions]
Current time: {{current_time}} {{current_week}}
Knowledge base selected: {{kb_selected}}
Web search available this turn: {{web_search_available}}

## User Question
{{query}}

Reply with ONE letter (A-I) only.
"""


def build_intent_choice_prompts(
    *,
    query: str,
    history_pairs: list[dict[str, str]],
    kb_selected: bool,
    has_images: bool = False,
    has_attachments: bool = False,
    web_search_enabled: bool = True,
    session_summary: str = "",
    working_memory: dict | None = None,
    asker_background: str = "",
) -> tuple[str, str]:
    now = datetime.now()
    system = INTENT_CHOICE_SYSTEM.replace(
        "{{conversation}}",
        format_rewrite_conversation(
            history_pairs,
            session_summary=session_summary,
            working_memory=working_memory,
        ),
    )
    user = (
        INTENT_CHOICE_USER.replace(
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
                query,
                has_images=has_images,
                has_attachments=has_attachments,
                asker_background=asker_background,
            ),
        )
    )
    if web_search_enabled:
        user += (
            "\nIf this question is about public news, celebrities, trending/"
            "hot topics, or other real-time facts, the letter MUST be C.\n"
        )
    return system, user
