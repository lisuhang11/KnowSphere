"""聊天时解析临时附件：等待就绪、构建 prompt 注入。"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage

from config.settings import settings
from utils.attachment_chunks import ATTACHMENT_PROMPT_BUDGET_CHARS, select_attachment_content
from utils.temporary_attachments import (
    MAX_ATTACHMENTS_PER_MESSAGE,
    STATUS_FAILED,
    STATUS_PROCESSING,
    STATUS_READY,
    STATUS_UPLOADED,
    TemporaryAttachmentStore,
    attachment_preview_url,
    is_image_attachment,
)

logger = logging.getLogger(__name__)

@dataclass
class AttachmentResolveResult:
    ready_rows: list[dict[str, Any]]
    skipped_ids: list[str]
    parsed_count: int
    skipped_count: int

def normalize_attachment_ids(raw: list[str] | None) -> list[str]:
    if not raw:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        aid = (item or "").strip()
        if not aid or aid in seen:
            continue
        seen.add(aid)
        out.append(aid)
        if len(out) >= MAX_ATTACHMENTS_PER_MESSAGE:
            break
    return out

def _wait_for_attachments_sync(
    session_id: str,
    attachment_ids: list[str],
    *,
    timeout_sec: float,
) -> tuple[list[dict[str, Any]], list[str]]:
    """阻塞等待附件解析完成，返回 (ready_rows, skipped_ids)。"""
    store = TemporaryAttachmentStore()
    deadline = time.monotonic() + timeout_sec
    pending = set(attachment_ids)
    ready: dict[str, dict[str, Any]] = {}
    skipped: list[str] = []

    while pending and time.monotonic() < deadline:
        done: list[str] = []
        for aid in list(pending):
            row = store.get(aid, session_id)
            if row is None:
                skipped.append(aid)
                done.append(aid)
                continue
            status = row["status"]
            if status == STATUS_READY:
                ready[aid] = row
                done.append(aid)
            elif status == STATUS_FAILED:
                skipped.append(aid)
                done.append(aid)
            elif status in (STATUS_UPLOADED, STATUS_PROCESSING):
                continue
            else:
                skipped.append(aid)
                done.append(aid)
        for aid in done:
            pending.discard(aid)
        if pending:
            time.sleep(0.5)

    for aid in pending:
        skipped.append(aid)
    ordered = [ready[aid] for aid in attachment_ids if aid in ready]
    return ordered, skipped

async def wait_for_attachments(
    session_id: str,
    attachment_ids: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    timeout = max(5.0, float(settings.chat_attachment_wait_sec))
    return await asyncio.to_thread(
        _wait_for_attachments_sync,
        session_id,
        attachment_ids,
        timeout_sec=timeout,
    )

async def resolve_attachments(
    session_id: str,
    attachment_ids: list[str],
    query: str,
) -> AttachmentResolveResult:
    """等待附件并就绪后按 query 选段。"""
    ready, skipped = await wait_for_attachments(session_id, attachment_ids)
    selected = resolve_for_prompt(ready, query)
    return AttachmentResolveResult(
        ready_rows=selected,
        skipped_ids=skipped,
        parsed_count=len(selected),
        skipped_count=len(skipped),
    )

def resolve_for_prompt(
    rows: list[dict[str, Any]],
    query: str,
) -> list[dict[str, Any]]:
    """为每条附件按 query 选取 prompt 片段（写入 selected_content）。"""
    if not rows:
        return []
    per_budget = ATTACHMENT_PROMPT_BUDGET_CHARS
    if len(rows) > 1:
        per_budget = max(2000, ATTACHMENT_PROMPT_BUDGET_CHARS // len(rows))

    out: list[dict[str, Any]] = []
    for row in rows:
        copy = dict(row)
        if is_image_attachment(copy.get("file_name") or ""):
            body = (copy.get("image_description") or copy.get("content") or "").strip()
            copy["selected_content"] = body
            copy["selected_chunks"] = 0
            copy["total_chunks"] = 0
        else:
            body, selected_n, total_n = select_attachment_content(copy, query, budget_chars=per_budget)
            copy["selected_content"] = body
            copy["selected_chunks"] = selected_n
            copy["total_chunks"] = total_n
        out.append(copy)
    return out

def build_attachment_prompt_block(
    rows: list[dict[str, Any]],
    *,
    query: str | None = None,
) -> str:
    if not rows:
        return ""
    resolved = resolve_for_prompt(rows, query or "") if query else rows
    parts: list[str] = []
    for row in resolved:
        name = row.get("file_name") or "附件"
        body = (row.get("selected_content") or row.get("content") or "").strip()
        if is_image_attachment(name) and not body:
            body = (row.get("image_description") or "").strip()
        if not body:
            body = "（未能解析出文本内容）"
        mode = ""
        total = int(row.get("total_chunks") or 0)
        selected = int(row.get("selected_chunks") or 0)
        if total > 1 and selected < total:
            mode = f"（已选 {selected}/{total} 片段）"
        parts.append(f"### 附件：{name}{mode}\n{body}")
    return "\n\n".join(parts)

def build_human_message_with_attachments(
    query: str,
    attachment_rows: list[dict[str, Any]],
    session_id: str,
    *,
    skipped_ids: list[str] | None = None,
) -> HumanMessage:
    """将临时附件内容注入用户消息；图片附件写入 ks_images。"""
    text = query.strip() or "请根据附件回答"
    block = build_attachment_prompt_block(attachment_rows, query=query)
    if block:
        text = f"{text}\n\n[会话附件内容]\n{block}".strip()

    image_metas: list[dict[str, str]] = []
    for row in attachment_rows:
        if not is_image_attachment(row.get("file_name") or ""):
            continue
        aid = row["id"]
        url = attachment_preview_url(session_id, aid)
        caption = (row.get("image_description") or row.get("selected_content") or row.get("content") or "").strip()
        image_metas.append({"url": url, **({"caption": caption} if caption else {})})

    msg = HumanMessage(content=text)
    if image_metas:
        msg.additional_kwargs["ks_images"] = image_metas
    if attachment_rows:
        msg.additional_kwargs["ks_attachments"] = [
            {
                "id": r["id"],
                "file_name": r.get("file_name") or "",
                "file_type": r.get("file_type") or "",
            }
            for r in attachment_rows
        ]
    if skipped_ids:
        msg.additional_kwargs["ks_attachment_skipped"] = skipped_ids
    return msg
