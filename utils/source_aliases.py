"""检索来源短句柄：cN 对齐 [[cN]]，dN 对齐文档，供 list_chunks 精读。

对齐 WeKnora llmreference：模型只接触短句柄，工具侧再还原真实 id。
KnowSphere 的 [[cN]] 已是 1-based 检索序号；list_chunks 必须能解析 c2 / 2 / d1，
而不能把它们当成 chunks.id 或 documents.document_id。
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from typing import Any

from langchain_core.messages import BaseMessage, ToolMessage

from utils.short_term_memory import turn_ranges

RETRIEVAL_SOURCE_TOOLS = frozenset(
    {
        "doc_retrieval",
        "grep_chunks",
        "list_chunks",
        "query_knowledge_graph",
        "web_search",
        "web_fetch",
    }
)

_HANDLE_RE = re.compile(
    r"^(?:\[\[)?(?P<kind>[cCdD])(?P<num>\d{1,4})(?:\]\])?$"
)


def parse_source_handle(raw: Any) -> tuple[str | None, int | None]:
    """把 c2 / [[c2]] / d1 / 2 解析为 (kind, n)。kind 为 c、d 或 None（裸数字）。"""
    if raw is None or raw == "":
        return None, None
    text = str(raw).strip()
    if not text:
        return None, None
    match = _HANDLE_RE.fullmatch(text)
    if match:
        return match.group("kind").lower(), int(match.group("num"))
    try:
        value = int(text)
    except (TypeError, ValueError):
        return None, None
    return (None, value) if value > 0 else (None, None)


def sources_from_tool_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
    if not isinstance(payload, dict):
        return []
    out: list[dict[str, Any]] = []
    for item in payload.get("sources") or []:
        if isinstance(item, dict):
            out.append(item)
    return out


def sources_in_turn(messages: Sequence[BaseMessage]) -> list[dict[str, Any]]:
    """与 collect_sources 相同：该轮所有检索 ToolMessage 的 sources 按出现序拼接。"""
    sources: list[dict[str, Any]] = []
    for msg in messages:
        if not isinstance(msg, ToolMessage):
            continue
        if str(msg.name or "") not in RETRIEVAL_SOURCE_TOOLS:
            continue
        sources.extend(sources_from_tool_payload(getattr(msg, "content", "")))
    return sources


def iter_turn_sources_newest_first(
    messages: Sequence[BaseMessage] | None,
) -> list[list[dict[str, Any]]]:
    msgs = list(messages or [])
    found: list[list[dict[str, Any]]] = []
    for start, end in reversed(turn_ranges(msgs)):
        sources = sources_in_turn(msgs[start:end])
        if sources:
            found.append(sources)
    return found


def unique_document_ids(sources: Iterable[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in sources:
        doc = str(item.get("document_id") or "").strip()
        if not doc or doc in seen:
            continue
        seen.add(doc)
        out.append(doc)
    return out


def _chunk_id_of(item: dict[str, Any]) -> int | None:
    raw = item.get("chunk_id")
    if raw is None or raw == "":
        raw = item.get("id")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def resolve_chunk_id(
    raw: Any, messages: Sequence[BaseMessage] | None
) -> int | None:
    """优先按最近一轮 [[cN]] 句柄还原；否则把裸整数当作数据库 chunks.id。"""
    kind, num = parse_source_handle(raw)
    if num is None:
        return None
    if kind in (None, "c") and num > 0:
        for sources in iter_turn_sources_newest_first(messages):
            if 1 <= num <= len(sources):
                cid = _chunk_id_of(sources[num - 1])
                if cid:
                    return cid
                break
        if kind == "c":
            return None
    return num if num > 0 else None


def resolve_document_id(
    raw: Any, messages: Sequence[BaseMessage] | None
) -> str:
    """dN / cN / 裸序号 → 最近一轮检索里的 document_id；UUID/12 位 hex 原样返回。"""
    text = str(raw or "").strip()
    if not text:
        return ""
    kind, num = parse_source_handle(text)
    if num is None:
        return text
    for sources in iter_turn_sources_newest_first(messages):
        if kind == "d":
            docs = unique_document_ids(sources)
            if 1 <= num <= len(docs):
                return docs[num - 1]
            continue
        if kind in (None, "c") and 1 <= num <= len(sources):
            doc = str(sources[num - 1].get("document_id") or "").strip()
            if doc:
                return doc
        if kind is None:
            docs = unique_document_ids(sources)
            if 1 <= num <= len(docs):
                return docs[num - 1]
    if kind is None:
        return text
    return ""


def format_read_handle_table(sources: Sequence[dict[str, Any]]) -> str:
    """给历史检索压缩用：保留精读句柄，去掉正文。"""
    rows = [s for s in sources if isinstance(s, dict)]
    if not rows:
        return ""
    lines = [
        "精读请用下列句柄（cN 与该轮 [[cN]] 相同）。"
        "不要把 [[cN]] 的数字或文件名#后的序号当成数据库 id。",
    ]
    for i, item in enumerate(rows, 1):
        cid = _chunk_id_of(item)
        doc = str(item.get("document_id") or "").strip()
        name = str(item.get("file_name") or "").strip()
        parts = [f"c{i}"]
        if cid:
            parts.append(f"chunk_id={cid}")
        if doc:
            parts.append(f"document_id={doc}")
        if name:
            parts.append(name)
        lines.append(" ".join(parts))
    for i, doc in enumerate(unique_document_ids(rows), 1):
        lines.append(f"d{i} document_id={doc}")
    return "\n".join(lines)


def annotate_source_dicts(sources: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    docs = unique_document_ids(sources)
    alias = {doc: f"d{i}" for i, doc in enumerate(docs, 1)}
    out: list[dict[str, Any]] = []
    for i, item in enumerate(sources, 1):
        row = dict(item)
        row["cite_id"] = f"c{i}"
        doc = str(row.get("document_id") or "").strip()
        if doc:
            row["doc_alias"] = alias.get(doc, "")
        out.append(row)
    return out


def messages_from_runtime(runtime: Any) -> list[BaseMessage]:
    if runtime is None:
        return []
    state = getattr(runtime, "state", None)
    if isinstance(state, dict):
        return list(state.get("messages") or [])
    getter = getattr(state, "get", None)
    if callable(getter):
        return list(getter("messages") or [])
    return []
