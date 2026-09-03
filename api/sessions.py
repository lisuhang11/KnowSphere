"""会话 API。

对外前缀 `/sessions`。
LangGraph checkpoint 使用与 session `id` 相同的 UUID 作为 `thread_id` 键。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

import psycopg
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from api.chat import get_agent, get_checkpointer
from config.settings import settings
from services.stream_manager import SessionRun, SessionRunBusy, get_stream_manager
from skills.catalog import ordered_skill_names
from skills.must_use import inject_must_use_messages
from utils.agent_runtime import load_agent, resolve_max_iterations
from utils.attachment_resolve import (
    build_human_message_with_attachments,
    normalize_attachment_ids,
    resolve_attachments,
)
from utils.chat_images import (
    NO_VLM_IMAGE_UPLOAD_DETAIL,
    ChatImageError,
    build_human_message_saved_images_only,
    load_chat_image_bytes,
    save_chat_images,
)
from utils.citation import Citation, CitationStreamExpander, merge_citation_maps
from utils.file_artifacts import (
    attach_outputs_to_ai_message,
    collect_turn_file_artifacts,
    last_ai_message,
)
from utils.message_content import message_attachments, message_images, message_query_text
from utils.model_credentials import ensure_knowledgeqa_model_ready
from utils.model_store import ModelStore
from utils.vector_store import ChunkStore

logger = logging.getLogger(__name__)

sessions_router = APIRouter(prefix="/sessions", tags=["sessions"])

# ---------------------------------------------------------------------------
# 会话元数据（ks_threads 表，历史表名保留）
# ---------------------------------------------------------------------------

def ensure_session_table() -> None:
    with psycopg.connect(settings.postgres_dsn) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ks_threads (
                thread_id  UUID PRIMARY KEY,
                title      TEXT,
                kb_ids     BIGINT[] NOT NULL DEFAULT '{}',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        conn.execute(
            "ALTER TABLE ks_threads ADD COLUMN IF NOT EXISTS kb_ids BIGINT[] NOT NULL DEFAULT '{}'"
        )
        conn.execute(
            "ALTER TABLE ks_threads ADD COLUMN IF NOT EXISTS is_pinned BOOLEAN NOT NULL DEFAULT false"
        )
        conn.execute(
            "ALTER TABLE ks_threads ADD COLUMN IF NOT EXISTS pinned_at TIMESTAMPTZ"
        )
        conn.execute(
            "ALTER TABLE ks_threads ADD COLUMN IF NOT EXISTS agent_id TEXT"
        )
        conn.execute(
            "ALTER TABLE ks_threads ADD COLUMN IF NOT EXISTS web_search_enabled "
            "BOOLEAN NOT NULL DEFAULT TRUE"
        )
        conn.commit()

_SESSION_SELECT = (
    "thread_id, title, kb_ids, created_at, updated_at, is_pinned, pinned_at, "
    "agent_id, web_search_enabled"
)

def _sanitize_kb_ids(raw: Any) -> list[int]:
    if not isinstance(raw, (list, tuple)):
        return []
    ids: list[int] = []
    seen: set[int] = set()
    for v in raw:
        try:
            n = int(v)
        except (TypeError, ValueError):
            continue
        if n in seen:
            continue
        seen.add(n)
        ids.append(n)
    return ids

def _sanitize_agent_id(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    return s or None


def _sanitize_optional_bool(raw: Any) -> bool | None:
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)) and raw in (0, 1):
        return bool(raw)
    if isinstance(raw, str):
        s = raw.strip().lower()
        if s in ("true", "1", "yes", "on"):
            return True
        if s in ("false", "0", "no", "off"):
            return False
    return None


def _session_graph_enabled(kb_ids: list[int]) -> bool:
    """所选知识库是否已开图谱，且服务端 Neo4j 可用。"""
    if not kb_ids or not settings.neo4j_enable:
        return False
    try:
        cfgs = ChunkStore().get_knowledge_base_configs(kb_ids)
    except Exception:
        logger.debug("读取知识库图谱开关失败", exc_info=True)
        return False
    return any(bool(cfgs.get(kid, {}).get("graph_enabled")) for kid in kb_ids)


def _effective_web_search(requested: bool | None, stored: bool | None) -> bool:
    if not settings.web_search_enabled:
        return False
    if requested is not None:
        return bool(requested)
    if stored is not None:
        return bool(stored)
    return True


def _row_to_session(row) -> dict[str, Any]:
    (
        thread_id,
        title,
        kb_ids,
        created_at,
        updated_at,
        is_pinned,
        pinned_at,
        agent_id,
        web_search_enabled,
    ) = row
    sid = str(thread_id)
    kb_list = list(kb_ids) if kb_ids else []
    agent = (agent_id or "").strip() or None
    web_on = True if web_search_enabled is None else bool(web_search_enabled)
    metadata: dict[str, Any] = {}
    if title:
        metadata["title"] = title
    if kb_list:
        metadata["kb_ids"] = kb_list
    if agent:
        metadata["agent_id"] = agent
    metadata["web_search_enabled"] = web_on
    return {
        "id": sid,
        "thread_id": sid,  # LangGraph checkpoint 键（兼容旧前端）
        "title": title or "",
        "kb_ids": kb_list,
        "agent_id": agent,
        "web_search_enabled": web_on,
        "metadata": metadata,
        "created_at": created_at.isoformat() if created_at else None,
        "updated_at": updated_at.isoformat() if updated_at else None,
        "is_pinned": bool(is_pinned),
        "pinned_at": pinned_at.isoformat() if pinned_at else None,
    }

def _to_uuid(session_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"非法 session_id: {session_id}") from None

def _db_create_session(
    session_id: uuid.UUID,
    title: str | None,
    kb_ids: list[int],
    agent_id: str | None = None,
    web_search_enabled: bool = True,
) -> dict[str, Any]:
    web_on = bool(web_search_enabled) if settings.web_search_enabled else False
    with psycopg.connect(settings.postgres_dsn) as conn:
        row = conn.execute(
            f"INSERT INTO ks_threads (thread_id, title, kb_ids, agent_id, web_search_enabled) "
            f"VALUES (%s, %s, %s, %s, %s) "
            f"RETURNING {_SESSION_SELECT}",
            (session_id, title, kb_ids, agent_id, web_on),
        ).fetchone()
        conn.commit()
    return _row_to_session(row)

def _db_list_sessions(limit: int) -> list[dict[str, Any]]:
    with psycopg.connect(settings.postgres_dsn) as conn:
        rows = conn.execute(
            f"SELECT {_SESSION_SELECT} FROM ks_threads "
            "ORDER BY is_pinned DESC, pinned_at DESC NULLS LAST, updated_at DESC LIMIT %s",
            (limit,),
        ).fetchall()
    return [_row_to_session(r) for r in rows]

def _db_get_session(session_id: uuid.UUID) -> dict[str, Any] | None:
    with psycopg.connect(settings.postgres_dsn) as conn:
        row = conn.execute(
            f"SELECT {_SESSION_SELECT} FROM ks_threads WHERE thread_id = %s",
            (session_id,),
        ).fetchone()
    return _row_to_session(row) if row else None

def _db_update_session(
    session_id: uuid.UUID,
    title: str | None = None,
    kb_ids: list[int] | None = None,
    agent_id: str | None = None,
    *,
    update_agent: bool = False,
    web_search_enabled: bool | None = None,
    update_web_search: bool = False,
) -> dict[str, Any]:
    with psycopg.connect(settings.postgres_dsn) as conn:
        conn.execute(
            "INSERT INTO ks_threads (thread_id) VALUES (%s) "
            "ON CONFLICT (thread_id) DO UPDATE SET updated_at = now()",
            (session_id,),
        )
        if title is not None:
            conn.execute(
                "UPDATE ks_threads SET title = %s WHERE thread_id = %s",
                (title, session_id),
            )
        if kb_ids is not None:
            conn.execute(
                "UPDATE ks_threads SET kb_ids = %s WHERE thread_id = %s",
                (kb_ids, session_id),
            )
        if update_agent:
            conn.execute(
                "UPDATE ks_threads SET agent_id = %s WHERE thread_id = %s",
                (agent_id, session_id),
            )
        if update_web_search:
            web_on = bool(web_search_enabled) if settings.web_search_enabled else False
            conn.execute(
                "UPDATE ks_threads SET web_search_enabled = %s WHERE thread_id = %s",
                (web_on, session_id),
            )
        row = conn.execute(
            f"SELECT {_SESSION_SELECT} FROM ks_threads WHERE thread_id = %s",
            (session_id,),
        ).fetchone()
        conn.commit()
    if row is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return _row_to_session(row)

def _db_delete_session(session_id: uuid.UUID) -> None:
    with psycopg.connect(settings.postgres_dsn) as conn:
        conn.execute("DELETE FROM ks_threads WHERE thread_id = %s", (session_id,))
        conn.commit()

def _db_set_pinned(session_id: uuid.UUID, pinned: bool) -> dict[str, Any]:
    with psycopg.connect(settings.postgres_dsn) as conn:
        conn.execute(
            "INSERT INTO ks_threads (thread_id) VALUES (%s) "
            "ON CONFLICT (thread_id) DO NOTHING",
            (session_id,),
        )
        if pinned:
            conn.execute(
                "UPDATE ks_threads SET is_pinned = TRUE, pinned_at = now() WHERE thread_id = %s",
                (session_id,),
            )
        else:
            conn.execute(
                "UPDATE ks_threads SET is_pinned = FALSE, pinned_at = NULL WHERE thread_id = %s",
                (session_id,),
            )
        row = conn.execute(
            f"SELECT {_SESSION_SELECT} FROM ks_threads WHERE thread_id = %s",
            (session_id,),
        ).fetchone()
        conn.commit()
    if row is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return _row_to_session(row)

def _db_touch_session(session_id: uuid.UUID) -> None:
    with psycopg.connect(settings.postgres_dsn) as conn:
        conn.execute(
            "INSERT INTO ks_threads (thread_id) VALUES (%s) "
            "ON CONFLICT (thread_id) DO UPDATE SET updated_at = now()",
            (session_id,),
        )
        conn.commit()

def _db_get_session_kb_ids(session_id: uuid.UUID) -> list[int]:
    with psycopg.connect(settings.postgres_dsn) as conn:
        row = conn.execute(
            "SELECT kb_ids FROM ks_threads WHERE thread_id = %s", (session_id,)
        ).fetchone()
    return list(row[0]) if row and row[0] else []


def _db_get_session_agent_id(session_id: uuid.UUID) -> str | None:
    with psycopg.connect(settings.postgres_dsn) as conn:
        row = conn.execute(
            "SELECT agent_id FROM ks_threads WHERE thread_id = %s", (session_id,)
        ).fetchone()
    if not row or not row[0]:
        return None
    return str(row[0]).strip() or None


def _db_get_session_web_search_enabled(session_id: uuid.UUID) -> bool | None:
    with psycopg.connect(settings.postgres_dsn) as conn:
        row = conn.execute(
            "SELECT web_search_enabled FROM ks_threads WHERE thread_id = %s",
            (session_id,),
        ).fetchone()
    if not row:
        return None
    return None if row[0] is None else bool(row[0])

# ---------------------------------------------------------------------------
# 请求体
# ---------------------------------------------------------------------------

class SessionCreateRequest(BaseModel):
    title: str | None = None
    kb_ids: list[int] | None = None
    agent_id: str | None = None
    web_search_enabled: bool | None = None
    metadata: dict[str, Any] | None = None  # LangGraph 兼容：{title, kb_ids}


class SessionUpdateRequest(BaseModel):
    title: str | None = None
    kb_ids: list[int] | None = None
    agent_id: str | None = None
    web_search_enabled: bool | None = None
    metadata: dict[str, Any] | None = None  # LangGraph PATCH 兼容

class ChatImageAttachment(BaseModel):
    """聊天 inline 图片（前端 base64 data URI）。"""

    data: str = Field(..., description="data:image/png;base64,...")
    url: str | None = Field(default=None, description="客户端忽略，服务端填充")
    caption: str | None = Field(default=None, description="客户端忽略，服务端填充")

class SessionStreamRequest(BaseModel):
    message: str | None = Field(default=None, description="用户消息（推荐）")
    input: dict[str, Any] | None = Field(default=None, description="LangGraph 兼容 input.messages")
    kb_ids: list[int] | None = Field(default=None, description="本轮检索范围（knowledge_base_ids）")
    chat_model_id: str | None = Field(
        default=None,
        description="本轮问答模型 ID（model-...）；缺省则用知识库 summary_model_id 或默认问答模型",
    )
    vlm_model_id: str | None = Field(
        default=None,
        description="本轮视觉理解模型 ID（VLLM）；缺省则用 CHAT_VLM_MODEL_ID 或默认 VLLM",
    )
    images: list[ChatImageAttachment] | None = Field(
        default=None,
        description="本轮附带的图片（base64 data URI，Embed 降级路径）",
    )
    attachment_ids: list[str] | None = Field(
        default=None,
        description="预上传的会话临时附件 ID（Web 推荐路径，最多 5 个）",
    )
    stream_mode: list[str] | None = None
    assistant_id: str | None = None
    agent_id: str | None = Field(default=None, description="本轮智能体 ID（agent-...）")
    web_search_enabled: bool | None = Field(
        default=None,
        description="本轮是否开启联网搜索（输入框开关；管理员 WEB_SEARCH_ENABLED=false 时无效）",
    )
    skill_names: list[str] | None = Field(
        default=None,
        description="本轮 @Skill 点名（须为当前智能体已绑定技能；不收回其它已绑定技能）",
    )

def _parse_create(
    body: SessionCreateRequest,
) -> tuple[str | None, list[int], str | None, bool]:
    meta = body.metadata or {}
    title = body.title or meta.get("title")
    if title is not None and not isinstance(title, str):
        title = str(title)
    kb_raw = body.kb_ids if body.kb_ids is not None else meta.get("kb_ids")
    agent_raw = body.agent_id if body.agent_id is not None else meta.get("agent_id")
    web_raw = (
        body.web_search_enabled
        if body.web_search_enabled is not None
        else meta.get("web_search_enabled")
    )
    web_on = _sanitize_optional_bool(web_raw)
    return title, _sanitize_kb_ids(kb_raw), _sanitize_agent_id(agent_raw), web_on is not False


def _parse_update(
    body: SessionUpdateRequest,
) -> tuple[str | None, list[int] | None, str | None, bool, bool | None, bool]:
    meta = body.metadata or {}
    title = body.title if body.title is not None else meta.get("title")
    if title is not None and not isinstance(title, str):
        title = str(title)
    kb_raw = body.kb_ids if body.kb_ids is not None else meta.get("kb_ids")
    kb_ids = _sanitize_kb_ids(kb_raw) if kb_raw is not None else None
    update_agent = body.agent_id is not None or "agent_id" in meta
    agent_raw = body.agent_id if body.agent_id is not None else meta.get("agent_id")
    agent_id = _sanitize_agent_id(agent_raw) if update_agent else None
    update_web = body.web_search_enabled is not None or "web_search_enabled" in meta
    web_raw = (
        body.web_search_enabled
        if body.web_search_enabled is not None
        else meta.get("web_search_enabled")
    )
    web_on = _sanitize_optional_bool(web_raw) if update_web else None
    return title, kb_ids, agent_id, update_agent, web_on, update_web

# ---------------------------------------------------------------------------
# 标准 /sessions 路由
# ---------------------------------------------------------------------------

@sessions_router.post("")
async def create_session(body: SessionCreateRequest) -> dict[str, Any]:
    """CreateSession"""
    title, kb_ids, agent_id, web_on = _parse_create(body)
    return await asyncio.to_thread(
        _db_create_session, uuid.uuid4(), title, kb_ids, agent_id, web_on
    )

@sessions_router.get("")
async def list_sessions(limit: int = Query(100, ge=1, le=500)) -> list[dict[str, Any]]:
    """ListSessions"""
    return await asyncio.to_thread(_db_list_sessions, limit)

@sessions_router.get("/{session_id}")
async def get_session(session_id: str) -> dict[str, Any]:
    """GetSession"""
    sid = _to_uuid(session_id)
    row = await asyncio.to_thread(_db_get_session, sid)
    if row is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return row

@sessions_router.put("/{session_id}")
async def update_session(session_id: str, body: SessionUpdateRequest) -> dict[str, Any]:
    """UpdateSession"""
    sid = _to_uuid(session_id)
    title, kb_ids, agent_id, update_agent, web_on, update_web = _parse_update(body)
    return await asyncio.to_thread(
        _db_update_session,
        sid,
        title,
        kb_ids,
        agent_id,
        update_agent=update_agent,
        web_search_enabled=web_on,
        update_web_search=update_web,
    )

@sessions_router.post("/{session_id}/pin")
async def pin_session(session_id: str) -> dict[str, Any]:
    """PinSession"""
    sid = _to_uuid(session_id)
    row = await asyncio.to_thread(_db_set_pinned, sid, True)
    return {"success": True, "is_pinned": row["is_pinned"], "session": row}

@sessions_router.delete("/{session_id}/pin")
async def unpin_session(session_id: str) -> dict[str, Any]:
    """UnpinSession"""
    sid = _to_uuid(session_id)
    row = await asyncio.to_thread(_db_set_pinned, sid, False)
    return {"success": True, "is_pinned": row["is_pinned"], "session": row}

@sessions_router.delete("/{session_id}")
async def delete_session(session_id: str) -> dict[str, Any]:
    """DeleteSession"""
    sid = _to_uuid(session_id)
    get_stream_manager().discard(str(sid))
    await asyncio.to_thread(_db_delete_session, sid)
    checkpointer = get_checkpointer()
    if checkpointer is not None:
        try:
            await checkpointer.adelete_thread(str(sid))
        except Exception as e:  # noqa: BLE001
            logger.warning("删除 checkpoint 失败（元数据已删）: %s", e)
    return {"deleted": True, "id": str(sid)}

@sessions_router.delete("/{session_id}/messages")
async def clear_session_messages(session_id: str) -> dict[str, Any]:
    """ClearSessionMessages"""
    _to_uuid(session_id)  # 校验格式
    get_stream_manager().discard(session_id)
    agent = get_agent()
    config = {"configurable": {"thread_id": session_id}}
    await agent.aupdate_state(config, {"messages": []})
    return {"cleared": True, "id": session_id}

@sessions_router.get("/{session_id}/state")
async def get_session_state(session_id: str) -> dict[str, Any]:
    """GetSessionState（LangGraph checkpoint 消息历史 + 进行中的生成）"""
    _to_uuid(session_id)
    agent = get_agent()
    snap = await agent.aget_state({"configurable": {"thread_id": session_id}})
    run = get_stream_manager().active(session_id)
    active = None
    if run is not None:
        active = {"run_id": run.run_id, "user_preview": run.user_preview}
    if not snap or snap.values is None:
        return {"values": {}, "active_run": active}
    messages = [m.model_dump(mode="json") for m in snap.values.get("messages", [])]
    return {"values": {"messages": messages}, "active_run": active}

# ---------------------------------------------------------------------------
# 流式对话
# ---------------------------------------------------------------------------

_ROLE_MAP = {"user": HumanMessage, "assistant": AIMessage, "system": SystemMessage}
_TYPE_MAP = {"human": HumanMessage, "ai": AIMessage, "system": SystemMessage}

def _parse_input_messages(body: SessionStreamRequest) -> list[Any]:
    if body.message and body.message.strip():
        return [HumanMessage(content=body.message.strip())]
    msgs: list[Any] = []
    for m in (body.input or {}).get("messages") or []:
        if not isinstance(m, dict):
            continue
        content = m.get("content")
        if content is None:
            continue
        cls = _ROLE_MAP.get(str(m.get("role", ""))) or _TYPE_MAP.get(str(m.get("type", "")))
        if cls is not None:
            msgs.append(cls(content=content))
    return msgs

async def _build_input_messages(body: SessionStreamRequest, session_id: uuid.UUID) -> list[Any]:
    """解析 inline 图片或纯文本消息（附件在 stream 内异步 resolve）。"""
    query = (body.message or "").strip()
    sid = str(session_id)

    if body.images:
        if not settings.chat_images_enabled:
            raise HTTPException(status_code=400, detail="图片上传未启用")
        query = query or "请分析这张图片"
        data_uris: list[str] = []
        for img in body.images:
            raw = (img.data or "").strip()
            if raw:
                data_uris.append(raw)
        if not data_uris:
            raise HTTPException(status_code=400, detail="images 缺少有效的 data 字段")
        try:
            saved = await asyncio.to_thread(save_chat_images, sid, data_uris)
            return [build_human_message_saved_images_only(query, saved)]
        except ChatImageError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    msgs = _parse_input_messages(body)
    if msgs:
        return msgs
    return []

def _sse_frame(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


_background_runs: set[asyncio.Task[None]] = set()


def _spawn_background(coro, name: str) -> asyncio.Task[None]:
    """独立于请求取消作用域的后台任务，避免客户端断开时被 GC / 连带取消。"""
    task = asyncio.create_task(coro, name=name)
    _background_runs.add(task)
    task.add_done_callback(_background_runs.discard)
    return task


def _preview_skills(msg: Any | None) -> list[dict[str, str]]:
    kwargs = getattr(msg, "additional_kwargs", None) or {}
    raw = kwargs.get("ks_skills")
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw:
        name = ""
        if isinstance(item, str):
            name = item.strip()
        elif isinstance(item, dict):
            name = str(item.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append({"name": name})
    return out


def _preview_from_human(msg: Any | None, fallback_text: str) -> dict[str, Any]:
    if msg is None:
        return {"content": fallback_text, "images": [], "attachments": []}
    preview: dict[str, Any] = {
        "content": message_query_text(msg) or fallback_text,
        "images": message_images(msg),
        "attachments": message_attachments(msg),
    }
    skills = _preview_skills(msg)
    if skills:
        preview["skills"] = skills
    return preview


async def _persist_partial_answer(agent: Any, sid_str: str, run: SessionRun) -> None:
    answer = run.answer_text()
    if not answer:
        return
    config = {"configurable": {"thread_id": sid_str}}
    try:
        snap = await agent.aget_state(config)
        msgs = (snap.values or {}).get("messages") or [] if snap else []
        last = msgs[-1] if msgs else None
        if last is not None and getattr(last, "type", None) == "ai":
            return
        await agent.aupdate_state(config, {"messages": [AIMessage(content=answer)]})
    except Exception:
        logger.warning("停止后回写部分回答失败", exc_info=True)


async def _persist_file_artifacts(agent: Any, config: dict[str, Any]) -> None:
    """把本轮工具生成的文件挂到最后一条 AI 消息，刷新后仍能打开。"""
    try:
        snap = await agent.aget_state(config)
        msgs = list((snap.values or {}).get("messages") or []) if snap else []
        artifacts = collect_turn_file_artifacts(msgs)
        if not artifacts:
            return
        last = last_ai_message(msgs)
        if last is None:
            return
        updated = attach_outputs_to_ai_message(last, artifacts)
        if updated is None:
            return
        await agent.aupdate_state(config, {"messages": [updated]})
    except Exception:
        logger.warning("回写文件产物失败", exc_info=True)


async def _execute_session_run(
    run: SessionRun,
    *,
    agent: Any,
    sid: uuid.UUID,
    body: SessionStreamRequest,
    attachment_ids: list[str],
    query_text: str,
    config: dict[str, Any],
) -> None:
    mgr = get_stream_manager()
    sid_str = run.session_id
    expander: CitationStreamExpander | None = None
    merged_citations: dict[int, Citation] = {}
    streamed_answer = False
    try:
        if attachment_ids:
            mgr.append(
                sid_str,
                "messages",
                {
                    "type": "tool_call",
                    "tool_name": "attachment_parsing",
                    "content": "正在解析附件…",
                },
            )
            resolved = await resolve_attachments(
                sid_str,
                attachment_ids,
                query_text or "请根据附件回答",
            )
            output = f"已解析 {resolved.parsed_count} 个附件"
            if resolved.skipped_count:
                output += f"，{resolved.skipped_count} 个未完成已跳过"
            mgr.append(
                sid_str,
                "messages",
                {
                    "type": "tool_result",
                    "tool_name": "attachment_parsing",
                    "success": resolved.parsed_count > 0,
                    "content": output,
                    "parsed_count": resolved.parsed_count,
                    "skipped_count": resolved.skipped_count,
                },
            )
            if not resolved.ready_rows:
                detail = "附件未就绪或不存在"
                if resolved.skipped_ids:
                    detail += f"（跳过: {', '.join(resolved.skipped_ids[:3])}）"
                mgr.append(sid_str, "error", {"message": detail})
                return
            input_messages = [
                build_human_message_with_attachments(
                    query_text or "请根据附件回答",
                    resolved.ready_rows,
                    sid_str,
                    skipped_ids=resolved.skipped_ids,
                )
            ]
        else:
            input_messages = await _build_input_messages(body, sid)
            if not input_messages:
                mgr.append(sid_str, "error", {"message": "message、attachment_ids 或 images 不能为空"})
                return

        human = input_messages[0] if input_messages else None
        pinned = (config.get("configurable") or {}).get("pinned_skill_names") or []
        if pinned:
            input_messages = inject_must_use_messages(input_messages, pinned)
            human = input_messages[0] if input_messages else human
        mgr.set_user_preview(sid_str, _preview_from_human(human, query_text))

        async for mode, payload in agent.astream(
            {"messages": input_messages},
            config,
            stream_mode=["messages", "custom"],
        ):
            if mode == "custom":
                if isinstance(payload, dict) and payload.get("type") in (
                    "thinking",
                    "tool_call",
                    "tool_result",
                    "file_artifact",
                ):
                    mgr.append(sid_str, "messages", payload)
                elif isinstance(payload, dict) and payload.get("type") == "citation_meta":
                    if settings.citation_enabled:
                        batch = _citations_from_payload(payload)
                        merged_citations = merge_citation_maps(merged_citations, batch)
                        expander = CitationStreamExpander(merged_citations)
                    mgr.append(sid_str, "messages", payload)
                continue

            chunk, meta = payload
            if not _is_answer_stream_node(meta):
                continue
            if getattr(chunk, "tool_call_chunks", None):
                continue
            reasoning = _chunk_reasoning(chunk)
            if reasoning:
                mgr.append(sid_str, "messages", {"type": "thinking", "content": reasoning})
            text = _chunk_text(chunk)
            if not text:
                continue
            if expander is not None:
                text = expander.feed(text)
            if text:
                streamed_answer = True
                mgr.append(sid_str, "messages", {"type": "answer", "content": text})
        if expander is not None:
            rest = expander.flush()
            if rest:
                streamed_answer = True
                mgr.append(sid_str, "messages", {"type": "answer", "content": rest})
            if expander.dropped_count:
                logger.warning(
                    "本轮剥离 %d 个非法/越界引用句柄，实际引用 %s",
                    expander.dropped_count,
                    expander.used_indexes,
                )
        if not streamed_answer:
            await _backfill_answer_from_state(agent, sid_str, mgr, expander)
        await _persist_file_artifacts(agent, config)
    except asyncio.CancelledError:
        mgr.append(sid_str, "messages", {"type": "stop", "content": ""})
        raise
    except Exception as e:
        logger.exception("agent 运行失败")
        detail = _user_facing_agent_error(e)
        mgr.append(sid_str, "messages", {"type": "answer", "content": detail})
        mgr.append(sid_str, "error", {"message": detail})


async def _sse_from_run(run: SessionRun, start: int = 0):
    mgr = get_stream_manager()
    async for frame in mgr.iter_frames(run, start):
        yield _sse_frame(frame.event, frame.data)

async def _backfill_answer_from_state(
    agent: Any,
    sid_str: str,
    mgr: Any,
    expander: CitationStreamExpander | None,
) -> None:
    """messages 流漏掉 generate 节点时，把 checkpoint 里的完整回答补进 SSE。"""
    try:
        snap = await agent.aget_state({"configurable": {"thread_id": sid_str}})
        msgs = (snap.values or {}).get("messages") or [] if snap else []
        last = msgs[-1] if msgs else None
        if last is None or getattr(last, "type", None) != "ai":
            return
        text = _chunk_text(last)
        if not text:
            return
        if expander is not None:
            text = (expander.feed(text) or "") + (expander.flush() or "")
        if text:
            mgr.append(sid_str, "messages", {"type": "answer", "content": text})
    except Exception:
        logger.warning("回补未流式回答失败", exc_info=True)


def _chunk_text(chunk: Any) -> str:
    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "".join(parts)
    return ""


def _chunk_reasoning(chunk: Any) -> str:
    """Qwen 等模型把思考写在 additional_kwargs.reasoning_content，不是 content。"""
    kwargs = getattr(chunk, "additional_kwargs", None) or {}
    for key in ("reasoning_content", "reasoning"):
        val = kwargs.get(key)
        if isinstance(val, str) and val:
            return val
    return ""


def _user_facing_agent_error(exc: BaseException) -> str:
    raw = str(exc) or type(exc).__name__
    lower = raw.lower()
    if "timeout" in lower or "timed out" in lower:
        return "模型响应超时。附件若已解析成功，请稍后重试，或把问题问得更具体一些。"
    return f"生成回答失败：{raw[:240]}"

def _citations_from_payload(payload: dict[str, Any]) -> dict[int, Citation]:
    citations: dict[int, Citation] = {}
    for c in payload.get("citations") or []:
        idx = int(c["index"])
        citations[idx] = Citation(
            index=idx,
            document_id=str(c.get("document_id", "")),
            file_name=str(c.get("file_name", "")),
            chunk_index=int(c.get("chunk_index") or 0),
            score=float(c.get("score") or 0),
            snippet=str(c.get("snippet") or ""),
        )
    return citations

def _resolve_chat_model_id(body: SessionStreamRequest, kb_ids: list[int]) -> str | None:
    """请求指定 > 知识库 summary_model_id > 默认问答模型。"""
    if body.chat_model_id and body.chat_model_id.strip():
        cid = body.chat_model_id.strip()
        try:
            ensure_knowledgeqa_model_ready(cid, label="问答")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return cid
    if kb_ids:
        configs = ChunkStore().get_knowledge_base_configs(kb_ids)
        for kb_id in kb_ids:
            kb = configs.get(kb_id)
            sid = (kb or {}).get("summary_model_id")
            if isinstance(sid, str) and sid.strip():
                return sid.strip()
    rec = ModelStore().get_default_model("KnowledgeQA")
    return rec["id"] if rec else None

@sessions_router.get("/{session_id}/chat-images/{image_id}")
async def get_chat_image(session_id: str, image_id: str) -> Response:
    """返回会话聊天图片（MinIO 代理）。"""
    sid = _to_uuid(session_id)
    if await asyncio.to_thread(_db_get_session, sid) is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    try:
        data, content_type = await asyncio.to_thread(load_chat_image_bytes, str(sid), image_id)
    except ChatImageError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(content=data, media_type=content_type)

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}

# 线性 RAG 的生成节点叫 generate；旧 ReAct 图叫 agent。query_understand 的 token 不得混进答案。
_ANSWER_STREAM_NODES = frozenset({"generate", "agent"})


def _is_answer_stream_node(meta: Any) -> bool:
    return isinstance(meta, dict) and meta.get("langgraph_node") in _ANSWER_STREAM_NODES


def _start_session_run(
    *,
    agent: Any,
    sid: uuid.UUID,
    body: SessionStreamRequest,
    attachment_ids: list[str],
    query_text: str,
    config: dict[str, Any],
    user_preview: dict[str, Any],
) -> SessionRun:
    mgr = get_stream_manager()
    sid_str = str(sid)
    try:
        run = mgr.begin(sid_str, user_preview)
    except SessionRunBusy:
        raise HTTPException(status_code=409, detail="该会话已有进行中的生成") from None

    async def _task() -> None:
        try:
            await _execute_session_run(
                run,
                agent=agent,
                sid=sid,
                body=body,
                attachment_ids=attachment_ids,
                query_text=query_text,
                config=config,
            )
        finally:
            if run.stopped and mgr.owns(run):
                await _persist_partial_answer(agent, sid_str, run)
            if mgr.owns(run) and mgr.store_holds(run):
                mgr.finish(run)
            else:
                run.done = True
                mgr.drop_local(run)
            try:
                await asyncio.to_thread(_db_touch_session, sid)
            except Exception as e:  # noqa: BLE001
                logger.warning("刷新会话 updated_at 失败: %s", e)

    run.task = _spawn_background(_task(), name=f"ks-run-{sid_str}")
    return run


@sessions_router.post("/{session_id}/runs/stream")
async def stream_session_run(session_id: str, body: SessionStreamRequest) -> StreamingResponse:
    """StreamSessionRun（SSE）。生成在后台运行，断开连接不取消。"""
    agent = get_agent()
    sid = _to_uuid(session_id)
    sid_str = str(sid)
    attachment_ids = normalize_attachment_ids(body.attachment_ids)
    query_text = (body.message or "").strip()

    if not attachment_ids and not query_text and not body.images and not body.input:
        raise HTTPException(status_code=400, detail="message、attachment_ids 或 images 不能为空")

    if attachment_ids and not settings.chat_images_enabled:
        raise HTTPException(status_code=400, detail="附件上传未启用")

    if body.images:
        if not settings.chat_images_enabled:
            raise HTTPException(status_code=400, detail="图片上传未启用")
        if not ModelStore().has_usable_vlm():
            raise HTTPException(status_code=400, detail=NO_VLM_IMAGE_UPLOAD_DETAIL)

    kb_ids = await asyncio.to_thread(_db_get_session_kb_ids, sid)
    if body.kb_ids is not None:
        # 本轮请求为权威来源（含空列表=清除），并回写会话避免与 UI 脱节
        kb_ids = _sanitize_kb_ids(body.kb_ids)
        await asyncio.to_thread(_db_update_session, sid, None, kb_ids)
    session_agent_id = await asyncio.to_thread(_db_get_session_agent_id, sid)
    requested_agent = _sanitize_agent_id(body.agent_id)
    agent_rec = load_agent(requested_agent or session_agent_id)
    agent_id = agent_rec["id"] if agent_rec else (requested_agent or session_agent_id)
    if requested_agent is not None and agent_id != session_agent_id:
        await asyncio.to_thread(
            _db_update_session, sid, None, None, agent_id, update_agent=True
        )
    stored_web = await asyncio.to_thread(_db_get_session_web_search_enabled, sid)
    web_on = _effective_web_search(body.web_search_enabled, stored_web)
    if body.web_search_enabled is not None and web_on != bool(stored_web):
        await asyncio.to_thread(
            _db_update_session,
            sid,
            None,
            None,
            None,
            web_search_enabled=web_on,
            update_web_search=True,
        )
    graph_on = await asyncio.to_thread(_session_graph_enabled, kb_ids)
    chat_model_id = await asyncio.to_thread(_resolve_chat_model_id, body, kb_ids)
    vlm_model_id: str | None = None
    if body.vlm_model_id and body.vlm_model_id.strip():
        vid = body.vlm_model_id.strip()
        if not ModelStore().is_vllm_model_id_valid(vid):
            raise HTTPException(status_code=400, detail=f"无效或已禁用的 VLLM 模型: {vid}")
        vlm_model_id = vid
    configurable: dict[str, Any] = {
        "thread_id": sid_str,
        "kb_ids": kb_ids,
        "web_search_enabled": web_on,
        "graph_enabled": graph_on,
    }
    if chat_model_id:
        configurable["chat_model_id"] = chat_model_id
    if vlm_model_id:
        configurable["vlm_model_id"] = vlm_model_id
    if agent_id:
        configurable["agent_id"] = agent_id
    if attachment_ids:
        configurable["attachment_ids"] = attachment_ids
    bound_skills = ordered_skill_names((agent_rec or {}).get("skill_names") or [])
    pinned = ordered_skill_names(body.skill_names or [])
    pinned = [n for n in pinned if n in set(bound_skills)]
    if pinned:
        configurable["pinned_skill_names"] = pinned
    config = {
        "configurable": configurable,
        "recursion_limit": resolve_max_iterations(agent_id),
    }

    preview: dict[str, Any] = {
        "content": query_text,
        "images": [],
        "attachments": [{"id": aid, "file_name": ""} for aid in attachment_ids],
    }
    if pinned:
        preview["skills"] = [{"name": n} for n in pinned]
    run = _start_session_run(
        agent=agent,
        sid=sid,
        body=body,
        attachment_ids=attachment_ids,
        query_text=query_text,
        config=config,
        user_preview=preview,
    )
    return StreamingResponse(
        _sse_from_run(run),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@sessions_router.get("/{session_id}/runs/continue")
async def continue_session_run(session_id: str) -> StreamingResponse:
    """重放已产生的事件并续推，直到本轮生成结束。"""
    sid = _to_uuid(session_id)
    run = get_stream_manager().get(str(sid))
    if run is None:
        raise HTTPException(status_code=404, detail="没有可续接的生成")
    return StreamingResponse(
        _sse_from_run(run),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@sessions_router.post("/{session_id}/runs/stop")
async def stop_session_run(session_id: str) -> dict[str, Any]:
    """显式停止本轮生成（离开页面不会走这里）。"""
    sid = _to_uuid(session_id)
    run = get_stream_manager().request_stop(str(sid))
    if run is None:
        return {"stopped": True, "already_done": True}
    return {"stopped": True, "run_id": run.run_id}
