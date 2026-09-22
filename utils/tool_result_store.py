"""L1 工具结果压缩：大结果外置，消息里只留引用。

preview 只写库、永不进入 LLM prompt。取回走 get_stored_data，
不要把原文和 summary 同时塞进同一条 tool 消息。
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig

from config.settings import get_current_owner, settings
from utils.message_content import message_text

logger = logging.getLogger(__name__)

SKIP_OFFLOAD_TOOLS = frozenset({"get_stored_data"})
REF_KEYS = ("__stored", "__refId", "__toolType", "__originalLength", "__summary", "__hint")


@dataclass(frozen=True)
class StoredToolResult:
    ref_id: str
    tool_name: str
    original_length: int
    summary: str
    preview: str
    payload: str
    thread_id: str = ""
    owner: str = ""
    expires_at: datetime | None = None


_CACHE: dict[str, StoredToolResult] = {}


def reset_tool_result_cache() -> None:
    """单测隔离进程内缓存。"""
    _CACHE.clear()


def parse_tool_payload(content: Any) -> Any:
    if isinstance(content, (dict, list)):
        return content
    text = message_text(content) if not isinstance(content, str) else content
    if not text:
        return ""
    try:
        return json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return text


def serialize_tool_payload(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    if payload is None:
        return ""
    return json.dumps(payload, ensure_ascii=False)


def is_stored_ref(payload: Any) -> bool:
    return isinstance(payload, dict) and payload.get("__stored") is True and bool(
        payload.get("__refId")
    )


def stored_ref_id(payload: Any) -> str:
    if not is_stored_ref(payload):
        return ""
    return str(payload.get("__refId") or "").strip()


def largest_array_len(payload: Any) -> int:
    if isinstance(payload, list):
        return len(payload)
    if not isinstance(payload, dict):
        return 0
    best = 0
    for value in payload.values():
        if isinstance(value, list):
            best = max(best, len(value))
    return best


def should_store(
    payload: Any,
    *,
    content_len: int,
    always_store: bool = False,
    char_limit: int | None = None,
    array_limit: int | None = None,
) -> bool:
    if always_store:
        return content_len > 0
    limit = settings.tool_result_char_limit if char_limit is None else char_limit
    arr_limit = settings.tool_result_array_limit if array_limit is None else array_limit
    if content_len > limit:
        return True
    return largest_array_len(payload) > arr_limit


def _clip(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def build_preview(payload_text: str, max_chars: int | None = None) -> str:
    """审计/回放用前缀。禁止写入 LLM 消息。"""
    n = settings.tool_result_preview_chars if max_chars is None else max_chars
    return (payload_text or "")[: max(0, n)]


def build_summary(payload: Any, tool_name: str, *, original_length: int) -> str:
    """确定性摘要：结构 + 句柄/短 snippet，不含 preview、不改字段名。"""
    cap = max(200, int(settings.tool_result_summary_chars))
    name = tool_name or "tool"
    if isinstance(payload, dict) and isinstance(payload.get("sources"), list):
        sources = [s for s in payload["sources"] if isinstance(s, dict)]
        lines = [f"Array, {len(sources)} items total, tool={name}"]
        note = str(payload.get("note") or "").strip()
        if note:
            lines.append(f"note: {_clip(note, 200)}")
        show = min(len(sources), 10)
        for i, item in enumerate(sources[:show]):
            cid = item.get("chunk_id") if item.get("chunk_id") not in (None, "") else item.get("id")
            doc = str(item.get("document_id") or "").strip()
            file_name = str(item.get("file_name") or "").strip()
            snippet = str(item.get("snippet") or "").strip()
            if not snippet:
                snippet = str(item.get("content") or "").strip()
            parts = [f"[{i}]", f"c{i + 1}"]
            if cid not in (None, ""):
                parts.append(f"chunk_id={cid}")
            if doc:
                parts.append(f"document_id={doc}")
            if file_name:
                parts.append(file_name)
            if snippet:
                parts.append(_clip(snippet, 120))
            lines.append(" ".join(parts))
        if len(sources) > show:
            lines.append(f"... and {len(sources) - show} more items")
        return _clip("\n".join(lines), cap)

    if isinstance(payload, list):
        lines = [f"Array, {len(payload)} items total, tool={name}"]
        show = min(len(payload), 10)
        for i, item in enumerate(payload[:show]):
            if isinstance(item, dict):
                keys = ", ".join(list(item.keys())[:8])
                lines.append(f"[{i}]: {{{keys}}}")
            else:
                lines.append(f"[{i}]: {_clip(str(item), 80)}")
        if len(payload) > show:
            lines.append(f"... and {len(payload) - show} more items")
        return _clip("\n".join(lines), cap)

    if isinstance(payload, dict):
        keys = ", ".join(list(payload.keys())[:16])
        return _clip(
            f"Object, tool={name}, {original_length} chars, keys=[{keys}]",
            cap,
        )
    return _clip(f"{name}, {original_length} chars: {payload!s}", cap)


def build_ref_object(
    *,
    ref_id: str,
    tool_name: str,
    original_length: int,
    summary: str,
) -> dict[str, Any]:
    return {
        "__stored": True,
        "__refId": ref_id,
        "__toolType": tool_name,
        "__originalLength": original_length,
        "__summary": summary,
        "__hint": f'Call get_stored_data(ref_id="{ref_id}") for full data',
    }


def _owner() -> str:
    return (get_current_owner() or settings.default_owner or "default").strip() or "default"


def _is_expired(record: StoredToolResult) -> bool:
    if record.expires_at is None:
        return False
    expiry = record.expires_at
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=UTC)
    return datetime.now(UTC) >= expiry


def _expires_at(ttl_sec: int | None) -> datetime | None:
    if ttl_sec is None or ttl_sec <= 0:
        return None
    return datetime.now(UTC) + timedelta(seconds=int(ttl_sec))


def put_tool_result(
    *,
    tool_name: str,
    payload_text: str,
    payload: Any,
    thread_id: str = "",
    owner: str = "",
    always_store: bool = False,
    force: bool = False,
    summary: str | None = None,
    ttl_sec: int | None = None,
) -> StoredToolResult | None:
    text = payload_text or ""
    if not force and not should_store(
        payload, content_len=len(text), always_store=always_store
    ):
        return None
    ref_id = str(uuid.uuid4())
    digest = summary if summary is not None else build_summary(
        payload, tool_name, original_length=len(text)
    )
    record = StoredToolResult(
        ref_id=ref_id,
        tool_name=tool_name,
        original_length=len(text),
        summary=digest,
        preview=build_preview(text),
        payload=text,
        thread_id=thread_id or "",
        owner=owner or _owner(),
        expires_at=_expires_at(ttl_sec),
    )
    _CACHE[ref_id] = record
    try:
        from stores.tool_result_repository import ToolResultRepository

        ToolResultRepository().insert(
            ref_id=record.ref_id,
            thread_id=record.thread_id,
            owner=record.owner,
            tool_name=record.tool_name,
            original_length=record.original_length,
            summary=record.summary,
            preview=record.preview,
            payload=record.payload,
            expires_at=record.expires_at,
        )
    except Exception:
        logger.warning("工具结果落库失败，仅保留进程缓存 ref_id=%s", ref_id, exc_info=True)
    return record


def get_stored_result(
    ref_id: str,
    *,
    thread_id: str = "",
    owner: str = "",
) -> StoredToolResult | None:
    key = (ref_id or "").strip()
    if not key:
        return None
    record = _CACHE.get(key)
    if record is None:
        try:
            from stores.tool_result_repository import ToolResultRepository

            row = ToolResultRepository().get(key)
        except Exception:
            logger.debug("读取外置工具结果失败 ref_id=%s", key, exc_info=True)
            row = None
        if not row:
            return None
        record = StoredToolResult(
            ref_id=str(row.get("ref_id") or key),
            tool_name=str(row.get("tool_name") or ""),
            original_length=int(row.get("original_length") or 0),
            summary=str(row.get("summary") or ""),
            preview=str(row.get("preview") or ""),
            payload=str(row.get("payload") or ""),
            thread_id=str(row.get("thread_id") or ""),
            owner=str(row.get("owner") or ""),
            expires_at=row.get("expires_at"),
        )
        _CACHE[key] = record
    if _is_expired(record):
        _CACHE.pop(key, None)
        return None
    if thread_id and record.thread_id and record.thread_id != thread_id:
        return None
    current_owner = owner or _owner()
    if record.owner and current_owner and record.owner != current_owner:
        return None
    return record


def resolve_tool_payload(content: Any) -> Any:
    """解析工具 JSON；若是引用则取回原文（供 collect_sources / 句柄解析，不进 prompt）。"""
    payload = parse_tool_payload(content)
    ref_id = stored_ref_id(payload)
    if not ref_id:
        return payload
    record = get_stored_result(ref_id)
    if record is None:
        return payload
    return parse_tool_payload(record.payload)


def _always_store_tool(tool_name: str) -> bool:
    from tools.catalog import get_tool_spec

    spec = get_tool_spec(tool_name)
    return bool(spec and spec.always_store)


def offload_tool_message(
    msg: ToolMessage,
    *,
    config: RunnableConfig | None = None,
) -> ToolMessage:
    """L1 外置 + L2 语义压缩；preview 永不进入消息。"""
    name = str(getattr(msg, "name", "") or "")
    if name in SKIP_OFFLOAD_TOOLS:
        return msg
    raw = getattr(msg, "content", "")
    payload = parse_tool_payload(raw)
    if is_stored_ref(payload):
        return msg
    text = raw if isinstance(raw, str) else serialize_tool_payload(payload)
    from utils.run_config import thread_id_from_config
    from utils.semantic_compressor import (
        is_fallback_truncated,
        semantic_compress,
        should_semantic_compress,
    )

    need_l2 = should_semantic_compress(len(text))
    need_l1 = settings.tool_result_store_enabled and should_store(
        payload, content_len=len(text), always_store=_always_store_tool(name)
    )
    if not need_l1 and not need_l2:
        return msg
    digest = build_summary(payload, name, original_length=len(text))
    ttl: int | None = None
    if need_l2:
        digest = semantic_compress(
            text, tool_name=name, original_length=len(text), config=config
        )
        ttl = int(settings.tool_result_ttl_sec)
    record = put_tool_result(
        tool_name=name,
        payload_text=text,
        payload=payload,
        thread_id=thread_id_from_config(config) or "",
        force=True,
        summary=digest,
        ttl_sec=ttl,
    )
    if record is None:
        return msg
    ref = build_ref_object(
        ref_id=record.ref_id,
        tool_name=name,
        original_length=record.original_length,
        summary=record.summary,
    )
    if need_l2:
        if is_fallback_truncated(record.summary):
            ref["__fallbackTruncated"] = True
        else:
            ref["__compressed"] = True
    kwargs = dict(getattr(msg, "additional_kwargs", None) or {})
    kwargs["ks_ref_id"] = record.ref_id
    copied = ToolMessage(
        content=json.dumps(ref, ensure_ascii=False),
        name=name,
        tool_call_id=msg.tool_call_id,
        id=getattr(msg, "id", None),
    )
    copied.additional_kwargs = kwargs
    return copied
