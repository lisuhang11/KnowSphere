"""L3 对话压缩：usage 触发的结构化交接，不改写 checkpoint 原文。

分割点不能落在 tool 消息上（回溯到对应 assistant）。
最少保留 6 条、最少删除 2 条。数据引用索引由代码提取，不经 LLM。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import BaseMessage, ToolMessage
from langchain_core.runnables import RunnableConfig

from utils.message_content import message_query_text, message_text
from utils.short_term_memory import (
    _is_ai,
    _is_human,
    estimate_messages_tokens,
    message_stable_id,
)

logger = logging.getLogger(__name__)

DEFAULT_TRIGGER_RATIO = 0.85
DEFAULT_TARGET_RATIO = 0.30
DEFAULT_MIN_KEEP = 6
DEFAULT_MIN_DELETE = 2
HANDOVER_MAX_CHARS = 2500

_REF_LINE = re.compile(
    r"^[-*]\s*(?P<ref>[0-9a-fA-F-]{8,36})\s*\|\s*(?P<tool>[^|]+)\|\s*(?P<summary>.*)$"
)

HANDOVER_SYSTEM_PROMPT = """你把较早的对话整理成一份结构化交接文档，供后续步骤接着做。
必须输出 JSON 对象，不要 markdown 围栏，字段如下：
{
  "original_request": "用户最初要做什么",
  "stages": [{"stage": "阶段名", "did": "做了什么", "got": "得到什么"}],
  "abandoned_paths": [{"plan": "放弃的方案", "reason": "原因"}]
}
硬性约束：
- original_request 保留用户原话要点，防止忘掉目标。
- stages 按逻辑阶段分组：阶段 → 做了什么 → 得到什么。
- 保留具体的值、ID、名称、数字，不要概括成「数据已检索」「已处理」。
- abandoned_paths 列出已尝试但放弃的路径，避免重蹈覆辙；没有则空数组。
- 不要编造。不要输出数据引用表（系统会单独提取 __refId）。
若已有旧交接，把新归档合并进去，不要重复罗列。"""


@dataclass
class HandoverDoc:
    original_request: str = ""
    stages: list[dict[str, str]] = field(default_factory=list)
    abandoned_paths: list[dict[str, str]] = field(default_factory=list)
    data_refs: list[dict[str, str]] = field(default_factory=list)


def usage_ratio(prompt_tokens: int, context_window: int) -> float:
    if context_window <= 0:
        return 0.0
    return max(0.0, int(prompt_tokens)) / int(context_window)


def needs_compaction(
    prompt_tokens: int,
    context_window: int,
    *,
    trigger_ratio: float = DEFAULT_TRIGGER_RATIO,
) -> bool:
    return usage_ratio(prompt_tokens, context_window) >= trigger_ratio


def prompt_tokens_from_response(response: Any) -> int:
    meta = getattr(response, "usage_metadata", None) or {}
    for key in ("input_tokens", "prompt_tokens"):
        if meta.get(key):
            return int(meta[key])
    rm = getattr(response, "response_metadata", None) or {}
    usage = rm.get("token_usage") or rm.get("usage") or {}
    for key in ("prompt_tokens", "input_tokens"):
        if usage.get(key):
            return int(usage[key])
    return 0


def owning_assistant_index(messages: list[BaseMessage], idx: int) -> int | None:
    """tool 必须回溯到发出 tool_calls 的 assistant；找不到则 None。"""
    if idx < 0 or idx >= len(messages):
        return None
    msg = messages[idx]
    if not isinstance(msg, ToolMessage):
        return idx
    tid = str(getattr(msg, "tool_call_id", "") or "")
    for j in range(idx - 1, -1, -1):
        other = messages[j]
        if not _is_ai(other):
            if _is_human(other):
                return None
            continue
        calls = getattr(other, "tool_calls", None) or []
        ids = {str(c.get("id") or "") for c in calls}
        if tid and tid in ids:
            return j
        if not tid and calls:
            return j
    return None


def plan_compaction_split(
    messages: list[BaseMessage],
    *,
    context_window: int,
    target_ratio: float = DEFAULT_TARGET_RATIO,
    min_keep: int = DEFAULT_MIN_KEEP,
    min_delete: int = DEFAULT_MIN_DELETE,
) -> int | None:
    """返回 KEEP 起点下标。不能从 tool 起切；不足最小删除/保留则 None。"""
    msgs = list(messages or [])
    n = len(msgs)
    if n < min_keep + min_delete:
        return None
    target_tokens = max(200, int(context_window * target_ratio))
    last_human = next((i for i in range(n - 1, -1, -1) if _is_human(msgs[i])), 0)

    keep_start = n
    kept_tokens = 0
    i = n - 1
    while i >= 0:
        start = owning_assistant_index(msgs, i)
        if start is None:
            return None
        if keep_start < n:
            tentative_kept = n - start + (1 if start > last_human else 0)
            if tentative_kept >= min_keep and kept_tokens >= target_tokens:
                break
        keep_start = start
        kept_tokens += estimate_messages_tokens(msgs[start : i + 1])
        i = start - 1

    if keep_start >= n:
        return None
    if isinstance(msgs[keep_start], ToolMessage):
        back = owning_assistant_index(msgs, keep_start)
        if back is None:
            return None
        keep_start = back
    if keep_start <= last_human:
        deleted, kept = keep_start, n - keep_start
    else:
        deleted = keep_start - 1
        kept = 1 + (n - keep_start)
    if deleted < min_delete or kept < min_keep:
        return None
    return keep_start


def archive_for_split(messages: list[BaseMessage], split: int) -> list[BaseMessage]:
    """分割点之前的消息进入交接；当前轮 Human 不归档。"""
    msgs = list(messages or [])
    if split <= 0:
        return []
    last_human = next((i for i in range(len(msgs) - 1, -1, -1) if _is_human(msgs[i])), 0)
    if split <= last_human:
        return msgs[:split]
    return msgs[:last_human] + msgs[last_human + 1 : split]


def extract_stored_refs(messages: list[BaseMessage]) -> list[dict[str, str]]:
    from utils.tool_result_store import is_stored_ref, parse_tool_payload, stored_ref_id

    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for msg in messages:
        if not isinstance(msg, ToolMessage):
            continue
        payload = parse_tool_payload(getattr(msg, "content", ""))
        ref_id = stored_ref_id(payload)
        if not ref_id or not is_stored_ref(payload) or ref_id in seen:
            continue
        seen.add(ref_id)
        out.append(
            {
                "refId": ref_id,
                "tool": str(payload.get("__toolType") or msg.name or "tool"),
                "summary": str(payload.get("__summary") or "")[:180],
            }
        )
    return out


def parse_data_refs_from_handover(text: str) -> list[dict[str, str]]:
    body = (text or "")
    if "## 数据引用索引" not in body:
        return []
    section = body.split("## 数据引用索引", 1)[1]
    refs: list[dict[str, str]] = []
    seen: set[str] = set()
    for line in section.splitlines():
        match = _REF_LINE.match(line.strip())
        if not match:
            continue
        ref_id = match.group("ref").strip()
        if not ref_id or ref_id in seen:
            continue
        seen.add(ref_id)
        refs.append(
            {
                "refId": ref_id,
                "tool": match.group("tool").strip(),
                "summary": match.group("summary").strip()[:180],
            }
        )
    return refs


def merge_data_refs(*groups: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for group in groups:
        for item in group:
            ref_id = str(item.get("refId") or "").strip()
            if not ref_id or ref_id in seen:
                continue
            seen.add(ref_id)
            out.append(
                {
                    "refId": ref_id,
                    "tool": str(item.get("tool") or "tool"),
                    "summary": str(item.get("summary") or "")[:180],
                }
            )
    return out


def _format_ref_index(data_refs: list[dict[str, str]]) -> str:
    lines = ["## 数据引用索引"]
    if data_refs:
        for item in data_refs:
            lines.append(
                f"- {item['refId']} | {item.get('tool') or 'tool'} | {item.get('summary') or ''}"
            )
    else:
        lines.append("（无）")
    return "\n".join(lines)


def _format_abandoned(abandoned_paths: list[dict[str, str]]) -> str:
    lines = ["## 已放弃的路径"]
    if abandoned_paths:
        for item in abandoned_paths:
            plan = (item.get("plan") or "").strip()
            reason = (item.get("reason") or "").strip()
            if plan:
                lines.append(f"- ~~{plan}~~：{reason or '未说明'}")
    else:
        lines.append("（无）")
    return "\n".join(lines)


def _format_stages(stages: list[dict[str, str]]) -> str:
    lines = ["## 执行历史"]
    if stages:
        for i, stage in enumerate(stages, 1):
            title = (stage.get("stage") or f"阶段{i}").strip()
            did = (stage.get("did") or "").strip()
            got = (stage.get("got") or "").strip()
            lines.append(f"### [{title}]")
            if did:
                lines.append(f"- 做了什么：{did}")
            if got:
                lines.append(f"- 得到什么：{got}")
    else:
        lines.append("（尚无）")
    return "\n".join(lines)


def format_handover(doc: HandoverDoc) -> str:
    request = "\n".join(
        ["【交接文档】", "## 用户原始请求", (doc.original_request or "（未记录）").strip()]
    )
    stages = _format_stages(doc.stages)
    abandoned = _format_abandoned(doc.abandoned_paths)
    refs = _format_ref_index(doc.data_refs)
    text = f"{request}\n{stages}\n{abandoned}\n{refs}"
    if len(text) <= HANDOVER_MAX_CHARS:
        return text
    pinned = f"{request}\n{abandoned}\n{refs}"
    if len(pinned) <= HANDOVER_MAX_CHARS:
        budget = HANDOVER_MAX_CHARS - len(request) - len(abandoned) - len(refs) - 3
        clipped = stages[: max(20, budget)].rstrip() + "…"
        return f"{request}\n{clipped}\n{abandoned}\n{refs}"
    fallback = f"{request}\n{refs}"
    if len(fallback) <= HANDOVER_MAX_CHARS:
        budget = HANDOVER_MAX_CHARS - len(request) - len(refs) - 2
        clipped = abandoned[: max(20, budget)].rstrip() + "…"
        return f"{request}\n{clipped}\n{refs}"
    budget = HANDOVER_MAX_CHARS - len(refs) - 1
    return f"{request[: max(40, budget)].rstrip()}…\n{refs}"


def _clip_json_text(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def parse_handover_payload(raw: str) -> HandoverDoc:
    text = _clip_json_text(raw)
    data = json.loads(text)
    if not isinstance(data, dict):
        raise TypeError("handover is not an object")
    stages: list[dict[str, str]] = []
    for item in data.get("stages") or []:
        if isinstance(item, dict):
            stages.append(
                {
                    "stage": str(item.get("stage") or "").strip(),
                    "did": str(item.get("did") or "").strip(),
                    "got": str(item.get("got") or "").strip(),
                }
            )
    abandoned: list[dict[str, str]] = []
    for item in data.get("abandoned_paths") or []:
        if isinstance(item, dict):
            abandoned.append(
                {
                    "plan": str(item.get("plan") or "").strip(),
                    "reason": str(item.get("reason") or "").strip(),
                }
            )
    return HandoverDoc(
        original_request=str(data.get("original_request") or "").strip(),
        stages=stages,
        abandoned_paths=abandoned,
    )


def format_messages_for_handover(messages: list[BaseMessage]) -> str:
    from utils.tool_result_store import is_stored_ref, parse_tool_payload, stored_ref_id

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
            calls = getattr(msg, "tool_calls", None) or []
            if calls:
                bits = []
                for call in calls:
                    name = str(call.get("name") or "tool")
                    args = call.get("args") if isinstance(call.get("args"), dict) else {}
                    hint = ""
                    for key in ("query", "chunk_id", "document_id", "ref_id", "url"):
                        if args.get(key) not in (None, ""):
                            hint = f"{key}={args[key]}"
                            break
                    bits.append(f"{name}({hint})" if hint else name)
                lines.append("助手：调用 " + ", ".join(bits))
        elif isinstance(msg, ToolMessage):
            name = str(msg.name or "tool")
            payload = parse_tool_payload(getattr(msg, "content", ""))
            if is_stored_ref(payload):
                lines.append(
                    f"工具 {name}：__refId={stored_ref_id(payload)} "
                    f"{str(payload.get('__summary') or '')[:180]}"
                )
                continue
            if isinstance(payload, dict) and payload.get("sources"):
                src = payload["sources"][0] if payload["sources"] else {}
                extra = []
                if isinstance(src, dict):
                    if src.get("chunk_id") not in (None, ""):
                        extra.append(f"chunk_id={src['chunk_id']}")
                    if src.get("document_id"):
                        extra.append(f"document_id={src['document_id']}")
                    if src.get("file_name"):
                        extra.append(str(src["file_name"]))
                lines.append(
                    f"工具 {name}：{len(payload['sources'])} 条 "
                    + " ".join(extra)
                )
                continue
            body = message_text(getattr(msg, "content", ""))
            lines.append(f"工具 {name}：{body[:200]}")
    return "\n".join(lines).strip()


def fallback_handover(
    archive_text: str,
    previous: str,
    *,
    original_hint: str = "",
    data_refs: list[dict[str, str]] | None = None,
) -> HandoverDoc:
    request = original_hint
    if not request and previous and "## 用户原始请求" in previous:
        request = previous.split("## 用户原始请求", 1)[1]
        request = request.split("##", 1)[0].strip()
    body = (archive_text or "").strip()
    if len(body) > 800:
        body = body[:799].rstrip() + "…"
    return HandoverDoc(
        original_request=request or "（见归档对话）",
        stages=[{"stage": "归档", "did": "压缩较早对话", "got": body or "（空）"}],
        abandoned_paths=[],
        data_refs=list(data_refs or []),
    )


def first_user_request(messages: list[BaseMessage]) -> str:
    for msg in messages:
        if _is_human(msg):
            return (message_query_text(msg) or message_text(getattr(msg, "content", ""))).strip()
    return ""


def _invoke_handover_llm(archive_text: str, previous: str, config: RunnableConfig | None) -> str:
    from models import create_chat_model
    from utils.run_config import chat_model_kwargs_from_config

    llm = create_chat_model(
        **chat_model_kwargs_from_config(
            config,
            {
                "temperature": 0.3,
                "max_tokens": 1200,
                "timeout": 60,
                "max_retries": 0,
                "extra_body": {"enable_thinking": False},
            },
        )
    )
    prev = (previous or "").strip()
    user = f"【需要交接的对话】\n{archive_text}"
    if prev:
        user = f"【已有交接】\n{prev}\n\n{user}"
    resp = llm.invoke(
        [
            {"role": "system", "content": HANDOVER_SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        config=config,
    )
    text = message_text(getattr(resp, "content", ""))
    if not text:
        raise ValueError("empty handover")
    return text


def compact_archive_to_handover(
    archive: list[BaseMessage],
    previous: str,
    *,
    config: RunnableConfig | None = None,
    original_hint: str = "",
) -> str:
    refs = merge_data_refs(
        parse_data_refs_from_handover(previous),
        extract_stored_refs(archive),
    )
    archive_text = format_messages_for_handover(archive)
    if not archive_text and not previous:
        return ""
    hint = original_hint or first_user_request(archive)
    try:
        parsed = parse_handover_payload(_invoke_handover_llm(archive_text, previous, config))
        if hint and not parsed.original_request:
            parsed.original_request = hint
        parsed.data_refs = refs
        return format_handover(parsed)
    except Exception:
        logger.warning("结构化交接失败，改用降级交接", exc_info=True)
        return format_handover(
            fallback_handover(archive_text, previous, original_hint=hint, data_refs=refs)
        )


def maybe_compact_state(state: dict, config: RunnableConfig | None = None) -> dict[str, Any]:
    """usage 达阈值时生成交接并记录 summary_upto；不改 messages。"""
    from config.settings import settings
    from tools.events import emit_thinking
    from utils.short_term_memory import memory_view_from_state, message_stable_id

    messages = list(state.get("messages") or [])
    window = settings.stm_max_context_tokens
    view = memory_view_from_state(state)
    tokens = int(state.get("last_prompt_tokens") or 0) or view.estimated_window_tokens
    if not needs_compaction(tokens, window, trigger_ratio=settings.stm_compact_trigger_ratio):
        return {}
    split = plan_compaction_split(
        messages,
        context_window=window,
        target_ratio=settings.stm_compact_target_ratio,
        min_keep=settings.stm_compact_min_keep,
        min_delete=settings.stm_compact_min_delete,
    )
    if split is None:
        return {}
    archive = archive_for_split(messages, split)
    if len(archive) < settings.stm_compact_min_delete:
        return {}
    emit_thinking("上下文接近上限，正在整理交接文档…")
    summary = compact_archive_to_handover(
        archive,
        str(state.get("session_summary") or ""),
        config=config,
        original_hint=first_user_request(messages),
    )
    if not summary:
        return {}
    return {
        "session_summary": summary,
        "summary_upto_message_id": message_stable_id(archive[-1]),
    }


def trim_current_after_compaction(
    current: list[BaseMessage],
    compacted_upto_id: str,
) -> list[BaseMessage]:
    """本轮 Human 必须保留；丢掉 Human 之后、已交接前缀（含 upto）。"""
    if not compacted_upto_id or not current:
        return current
    idx = next(
        (i for i, msg in enumerate(current) if message_stable_id(msg) == compacted_upto_id),
        -1,
    )
    if idx <= 0:
        return current
    return [current[0], *current[idx + 1 :]]
