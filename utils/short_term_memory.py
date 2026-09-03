"""会话短期记忆：LLM 视图压缩，不改写 checkpoint 原文。

三层：
- 最近完整轮原文（本轮永不丢、AI+Tool 不拆开）
- 更早轮次的滚动摘要（session_summary）
- 本会话工作记忆（计划 / 近期要点）

历史检索 ToolMessage 在视图里压成一行，避免过期 snippet 冒充新证据。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from utils.message_content import message_query_text, message_text
from utils.tokens import estimate_tokens

RETRIEVAL_TOOL_NAMES = frozenset(
    {
        "doc_retrieval",
        "grep_chunks",
        "list_chunks",
        "query_knowledge_graph",
        "web_search",
        "web_fetch",
    }
)

COMPACT_RETRIEVAL = (
    "上一轮检索结果已省略（知识库或网页可能已更新）。如需事实请重新检索。"
)
COMPACT_ATTACHMENT = "（历史附件正文已省略）"
COMPACT_IMAGE = "（历史图片描述已省略）"

_PER_MESSAGE_OVERHEAD = 8
_SUMMARY_RESERVE_TOKENS = 500

DEFAULT_MAX_CONTEXT_TOKENS = 32000
DEFAULT_KEEP_TURNS = 8
DEFAULT_CONSOLIDATE_RATIO = 0.5
DEFAULT_HARD_TRIM_RATIO = 0.8
DEFAULT_WORKING_MEMORY_MAX_CHARS = 1200


def message_stable_id(msg: BaseMessage) -> str:
    mid = getattr(msg, "id", None)
    if mid:
        return str(mid)
    body = message_text(getattr(msg, "content", ""))[:240]
    name = str(getattr(msg, "name", "") or "")
    tcid = str(getattr(msg, "tool_call_id", "") or "")
    raw = f"{getattr(msg, 'type', '')}|{name}|{tcid}|{body}"
    return hashlib.sha1(raw.encode("utf-8", errors="replace")).hexdigest()[:16]


def _is_human(msg: BaseMessage) -> bool:
    if isinstance(msg, HumanMessage):
        return True
    return getattr(msg, "type", None) in ("human", "user")


def _is_ai(msg: BaseMessage) -> bool:
    if isinstance(msg, AIMessage):
        return True
    return getattr(msg, "type", None) in ("ai", "assistant")


def estimate_message_tokens(msg: BaseMessage) -> int:
    text = message_text(getattr(msg, "content", ""))
    extra = 0
    tool_calls = getattr(msg, "tool_calls", None) or []
    if tool_calls:
        extra = estimate_tokens(str(tool_calls))
    return max(1, estimate_tokens(text) + extra + _PER_MESSAGE_OVERHEAD)


def estimate_messages_tokens(messages: list[BaseMessage]) -> int:
    return sum(estimate_message_tokens(m) for m in messages)


def turn_ranges(messages: list[BaseMessage]) -> list[tuple[int, int]]:
    """每轮 [start, end)，从 Human 起到下一 Human 前。"""
    human_idx = [i for i, m in enumerate(messages) if _is_human(m)]
    if not human_idx:
        return []
    ranges: list[tuple[int, int]] = []
    for k, start in enumerate(human_idx):
        end = human_idx[k + 1] if k + 1 < len(human_idx) else len(messages)
        ranges.append((start, end))
    return ranges


def _copy_tool(msg: ToolMessage, content: str) -> ToolMessage:
    copied = ToolMessage(
        content=content,
        name=msg.name,
        tool_call_id=msg.tool_call_id,
        id=getattr(msg, "id", None),
    )
    kwargs = dict(getattr(msg, "additional_kwargs", None) or {})
    if kwargs:
        copied.additional_kwargs = kwargs
    return copied


def _copy_human(msg: BaseMessage, content: str) -> HumanMessage:
    copied = HumanMessage(content=content, id=getattr(msg, "id", None))
    kwargs = dict(getattr(msg, "additional_kwargs", None) or {})
    if kwargs:
        copied.additional_kwargs = kwargs
    return copied


def compact_historical_human(msg: BaseMessage) -> BaseMessage:
    """历史轮次里的附件/图片注入块过长则换成占位，当前轮不要走这里。"""
    if not _is_human(msg):
        return msg
    text = message_text(getattr(msg, "content", ""))
    if not text:
        return msg
    changed = False
    if "\n\n[会话附件内容]\n" in text:
        head, _rest = text.split("\n\n[会话附件内容]\n", 1)
        text = f"{head.strip()}\n\n[会话附件内容]\n{COMPACT_ATTACHMENT}"
        changed = True
    if "\n\n[用户上传图片内容]\n" in text:
        head, _rest = text.split("\n\n[用户上传图片内容]\n", 1)
        text = f"{head.strip()}\n\n[用户上传图片内容]\n{COMPACT_IMAGE}"
        changed = True
    if not changed:
        return msg
    return _copy_human(msg, text)


def compact_historical_tool(msg: ToolMessage, *, redact_retrieval: bool) -> ToolMessage:
    name = str(getattr(msg, "name", "") or "")
    if redact_retrieval and name in RETRIEVAL_TOOL_NAMES:
        from utils.source_aliases import format_read_handle_table, sources_from_tool_payload

        table = format_read_handle_table(sources_from_tool_payload(getattr(msg, "content", "")))
        content = COMPACT_RETRIEVAL if not table else f"{COMPACT_RETRIEVAL}\n{table}"
        return _copy_tool(msg, content)
    body = message_text(getattr(msg, "content", ""))
    if name == "generate_pptx" and len(body) > 240:
        return _copy_tool(msg, body[:240].rstrip() + "…")
    if len(body) > 4000:
        return _copy_tool(msg, body[:400] + "…\n（历史工具输出已截断）")
    return msg


def compact_turn_messages(
    messages: list[BaseMessage],
    *,
    redact_retrieval: bool,
    compact_human: bool,
) -> list[BaseMessage]:
    out: list[BaseMessage] = []
    for msg in messages:
        if isinstance(msg, ToolMessage):
            out.append(compact_historical_tool(msg, redact_retrieval=redact_retrieval))
        elif compact_human and _is_human(msg):
            out.append(compact_historical_human(msg))
        else:
            out.append(msg)
    return out


def extract_history_pairs_from_messages(
    messages: list[BaseMessage],
    max_rounds: int,
) -> tuple[str | None, list[dict[str, str]]]:
    """(current_query, history_pairs)。history 不含当前轮，且跳过检索 ToolMessage。"""
    human_indices = [i for i, m in enumerate(messages) if _is_human(m) and message_text(getattr(m, "content", ""))]
    if not human_indices:
        return None, []

    last_idx = human_indices[-1]
    last_msg = messages[last_idx]
    current_query = message_query_text(last_msg) or message_text(getattr(last_msg, "content", ""))

    pairs: list[dict[str, str]] = []
    for hi in human_indices[:-1]:
        q = message_query_text(messages[hi]) or message_text(getattr(messages[hi], "content", ""))
        answer = ""
        for j in range(hi + 1, len(messages)):
            if _is_human(messages[j]):
                break
            if isinstance(messages[j], ToolMessage) and str(messages[j].name or "") in RETRIEVAL_TOOL_NAMES:
                continue
            if _is_ai(messages[j]):
                text = message_text(getattr(messages[j], "content", ""))
                if text:
                    answer = text
                    break
        if q:
            pairs.append({"query": q, "answer": answer})

    if max_rounds > 0:
        pairs = pairs[-max_rounds:]
    return current_query, pairs


@dataclass
class MemoryView:
    window_messages: list[BaseMessage]
    archive_messages: list[BaseMessage]
    history_pairs: list[dict[str, str]]
    current_query: str | None
    estimated_window_tokens: int
    needs_consolidation: bool
    archive_end_id: str | None = None
    kept_turn_count: int = 0


def build_memory_view(
    messages: list[BaseMessage],
    *,
    session_summary: str = "",
    summary_upto_id: str = "",
    max_context_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS,
    keep_turns: int = DEFAULT_KEEP_TURNS,
    consolidate_ratio: float = DEFAULT_CONSOLIDATE_RATIO,
    hard_trim_ratio: float = DEFAULT_HARD_TRIM_RATIO,
    redact_old_retrieval: bool = True,
) -> MemoryView:
    """选出送进 LLM 的对话窗口，以及待摘要的归档轮次。"""
    msgs = list(messages or [])
    ranges = turn_ranges(msgs)
    if not ranges:
        return MemoryView(
            window_messages=[],
            archive_messages=[],
            history_pairs=[],
            current_query=None,
            estimated_window_tokens=0,
            needs_consolidation=False,
        )

    current_start, current_end = ranges[-1]
    current = msgs[current_start:current_end]
    prior_ranges = ranges[:-1]
    keep_n = max(1, int(keep_turns))
    max_tokens = max(2000, int(max_context_tokens))
    summary_tokens = estimate_tokens(session_summary) if session_summary else 0
    current_tokens = estimate_messages_tokens(current)

    target = max(800, int(max_tokens * consolidate_ratio * 0.6) - _SUMMARY_RESERVE_TOKENS)
    hard_cap = max(target, int(max_tokens * hard_trim_ratio) - summary_tokens)

    kept_ranges: list[tuple[int, int]] = []
    running = current_tokens + summary_tokens
    for start, end in reversed(prior_ranges):
        if len(kept_ranges) >= keep_n:
            break
        piece = msgs[start:end]
        cost = estimate_messages_tokens(piece)
        if kept_ranges and running + cost > hard_cap:
            break
        kept_ranges.append((start, end))
        running += cost
        if running > target and len(kept_ranges) >= 1:
            # 超过整合目标后不再往更早扩窗口（仍保留至少一轮近期）
            break
    kept_ranges.reverse()

    archive_ranges = prior_ranges[: len(prior_ranges) - len(kept_ranges)]
    archive = [m for s, e in archive_ranges for m in msgs[s:e]]
    kept_flat: list[BaseMessage] = []
    for s, e in kept_ranges:
        kept_flat.extend(
            compact_turn_messages(
                msgs[s:e],
                redact_retrieval=redact_old_retrieval,
                compact_human=True,
            )
        )
    window = kept_flat + current
    _cur_q, pairs = extract_history_pairs_from_messages(kept_flat + current, max_rounds=keep_n)

    archive_end_id = message_stable_id(archive[-1]) if archive else None
    uncovered = bool(archive) and archive_end_id != (summary_upto_id or "")

    return MemoryView(
        window_messages=window,
        archive_messages=archive,
        history_pairs=pairs,
        current_query=_cur_q,
        estimated_window_tokens=estimate_messages_tokens(window) + summary_tokens,
        needs_consolidation=uncovered,
        archive_end_id=archive_end_id,
        kept_turn_count=len(kept_ranges),
    )


def format_working_memory(memory: dict[str, Any] | None, max_chars: int = DEFAULT_WORKING_MEMORY_MAX_CHARS) -> str:
    if not memory:
        return ""
    parts: list[str] = []
    plan = str(memory.get("last_plan") or "").strip()
    if plan:
        parts.append(plan)
    facts = memory.get("recent_facts") or []
    if isinstance(facts, list):
        lines = [str(x).strip() for x in facts if str(x).strip()]
        if lines:
            parts.append("近期要点：\n" + "\n".join(f"- {x}" for x in lines[:6]))
    text = "\n\n".join(parts).strip()
    if len(text) > max_chars:
        return text[: max_chars - 1].rstrip() + "…"
    return text


def extract_working_memory(messages: list[BaseMessage], keep_facts: int = 4) -> dict[str, Any]:
    last_plan = ""
    for msg in reversed(messages):
        if isinstance(msg, ToolMessage) and str(msg.name or "") == "write_plan":
            last_plan = message_text(getattr(msg, "content", ""))
            break
    _q, pairs = extract_history_pairs_from_messages(messages, max_rounds=keep_facts)
    facts: list[str] = []
    for pair in pairs[-keep_facts:]:
        q = (pair.get("query") or "").strip()
        a = (pair.get("answer") or "").strip().replace("\n", " ")
        if not q:
            continue
        if a:
            facts.append(f"{q[:80]} → {a[:100]}")
        else:
            facts.append(q[:80])
    return {"last_plan": last_plan, "recent_facts": facts}


def memory_view_from_state(state: dict) -> MemoryView:
    from config.settings import settings

    return build_memory_view(
        list(state.get("messages") or []),
        session_summary=str(state.get("session_summary") or ""),
        summary_upto_id=str(state.get("summary_upto_message_id") or ""),
        max_context_tokens=settings.stm_max_context_tokens,
        keep_turns=settings.stm_keep_turns,
        consolidate_ratio=settings.stm_consolidate_ratio,
        hard_trim_ratio=settings.stm_hard_trim_ratio,
        redact_old_retrieval=settings.stm_redact_old_retrieval,
    )


def memory_system_suffix_from_state(state: dict) -> str:
    from config.settings import settings

    return format_memory_system_block(
        session_summary=str(state.get("session_summary") or ""),
        working_memory=state.get("working_memory") if isinstance(state.get("working_memory"), dict) else None,
        max_chars=settings.stm_working_memory_max_chars,
    )


def format_memory_system_block(
    *,
    session_summary: str = "",
    working_memory: dict[str, Any] | None = None,
    max_chars: int = DEFAULT_WORKING_MEMORY_MAX_CHARS,
) -> str:
    parts: list[str] = []
    wm = format_working_memory(working_memory, max_chars=max_chars)
    if wm:
        parts.append("【会话工作记忆】\n" + wm)
    summary = (session_summary or "").strip()
    if summary:
        parts.append("【更早对话摘要】\n" + summary)
    return "\n\n".join(parts)


def format_archive_for_summary(messages: list[BaseMessage]) -> str:
    lines: list[str] = []
    for msg in messages:
        if _is_human(msg):
            q = message_query_text(msg) or message_text(getattr(msg, "content", ""))
            if q:
                lines.append(f"用户：{q[:400]}")
        elif _is_ai(msg):
            text = message_text(getattr(msg, "content", ""))
            if text:
                lines.append(f"助手：{text[:400]}")
            elif getattr(msg, "tool_calls", None):
                names = [str(c.get("name") or "") for c in (msg.tool_calls or [])]
                lines.append("助手：调用 " + ", ".join(n for n in names if n))
        elif isinstance(msg, ToolMessage):
            name = str(msg.name or "tool")
            if name in RETRIEVAL_TOOL_NAMES:
                lines.append(f"工具 {name}：已检索")
            elif name == "write_plan":
                lines.append("计划：" + message_text(getattr(msg, "content", ""))[:300])
            else:
                lines.append(f"工具 {name}：已执行")
    return "\n".join(lines).strip()


SUMMARY_SYSTEM_PROMPT = """你把较早的对话压缩成一份中文会话摘要，供后续轮次接着聊。
保留：用户目标、已确认事实、未完成事项、关键实体与决定、工具失败原因。
不要编造。控制在 400 字以内。若已有旧摘要，把新归档内容合并进去，不要重复罗列。
只输出摘要正文。"""


def build_summary_user_prompt(archive_text: str, previous_summary: str) -> str:
    prev = (previous_summary or "").strip()
    archive = (archive_text or "").strip()
    if prev:
        return f"【已有摘要】\n{prev}\n\n【新归档的对话】\n{archive}"
    return f"【需要压缩的对话】\n{archive}"


def fallback_archive_summary(archive_text: str, previous_summary: str, max_chars: int = 1600) -> str:
    prev = (previous_summary or "").strip()
    body = (archive_text or "").strip()
    if len(body) > max_chars:
        body = body[: max_chars - 1].rstrip() + "…"
    if prev:
        return f"{prev}\n\n（后续摘录）\n{body}"
    return body
