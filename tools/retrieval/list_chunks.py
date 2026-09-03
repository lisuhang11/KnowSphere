"""list_chunks：按文档翻页或按 chunk_id 精读全文。"""

from __future__ import annotations

from typing import Annotated, Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import InjectedToolArg, tool
from langgraph.prebuilt import ToolRuntime

from schemas import RetrievalResult, Source
from stores.facade import ChunkStore
from tools.retrieval.content import (
    LIST_CHUNKS_DEFAULT_LIMIT,
    LIST_CHUNKS_MAX_LIMIT,
    READ_CONTENT_MAX,
    clip_content,
    snippet_of,
)
from tools.retrieval.doc_retrieval import _emit_citation_meta, _emit_thinking
from tools.retrieval.parent_resolve import resolve_parent_chunks
from utils.run_config import kb_ids_from_config
from utils.source_aliases import (
    messages_from_runtime,
    resolve_chunk_id,
    resolve_document_id,
)


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _empty(query: str, note: str) -> dict:
    return RetrievalResult(query=query, sources=[], note=note).model_dump()


def _missing_id_hint(requested: Any, *, kind: str) -> str:
    return (
        f"「{requested}」不是数据库里的{('分块' if kind == 'chunk' else '文档')} id。"
        "请使用检索结果中的 chunk_id / document_id，或句柄 cN / dN（与 [[cN]] 相同）。"
        "不要把引用序号或文件名#后的数字当成 id。"
    )


def _kb_ok(kb_id: Any, kb_ids: list[int]) -> bool:
    got = _as_int(kb_id)
    return got is not None and got in kb_ids


def _source_from_row(row: dict[str, Any], *, file_name: str, score: float = 1.0) -> Source:
    content = clip_content(row.get("content") or "", READ_CONTENT_MAX)
    return Source(
        document_id=str(row.get("document_id") or ""),
        file_name=file_name or str(row.get("file_name") or ""),
        chunk_index=int(row.get("chunk_index") or 0),
        score=score,
        snippet=snippet_of(content),
        parent_resolved=bool(row.get("parent_resolved")),
        sub_chunk_index=row.get("sub_chunk_index"),
        chunk_id=_as_int(row.get("id")),
        content=content,
    )


@tool
def list_chunks(
    document_id: str = "",
    chunk_id: str | int = "",
    offset: int = 0,
    limit: int = 0,
    config: Annotated[RunnableConfig, InjectedToolArg] = None,  # noqa: B008
    runtime: Annotated[ToolRuntime | None, InjectedToolArg] = None,
) -> dict:
    """精读知识库文档：按 chunk_id 读取一块全文，或按 document_id 分页列出分块正文。

    在 doc_retrieval / grep_chunks 之后，若摘要不够，用工具返回的 chunk_id、document_id，
    或短句柄 cN / dN（与 [[cN]] 相同，例如 c2、d1）。
    不要把 [[c2]] 里的数字 2、或文件名#后的序号当成数据库 id。
    不能代替语义检索。必须提供 chunk_id 或 document_id 之一。
    """
    store = ChunkStore()
    kb_ids = kb_ids_from_config(config)
    messages = messages_from_runtime(runtime)
    resolved_chunk = resolve_chunk_id(chunk_id, messages)
    resolved_doc = resolve_document_id(document_id, messages)
    query = (
        f"chunk_id={chunk_id}"
        if str(chunk_id).strip() not in ("", "0")
        else f"document_id={document_id} offset={offset}"
    )
    writer = getattr(runtime, "stream_writer", None) if runtime is not None else None

    if not kb_ids:
        return _empty(
            query,
            "未选择知识库：无法精读用户文档。请提示用户选择知识库后再问。",
        )

    cid = resolved_chunk or 0
    doc_id = (resolved_doc or "").strip()
    if cid <= 0 and not doc_id:
        return _empty(
            query,
            "必须提供 chunk_id 或 document_id 之一。"
            "请使用检索结果里的 chunk_id / document_id，或句柄 cN / dN（与 [[cN]] 相同）。"
            "不要把引用序号或文件名#后的数字当成数据库 id。",
        )

    if cid > 0:
        result = _read_one_chunk(store, cid, kb_ids, query, requested=chunk_id)
    else:
        result = _list_document(
            store, doc_id, kb_ids, offset, limit, query, requested=document_id
        )

    sources = [Source.model_validate(s) if isinstance(s, dict) else s for s in result.get("sources") or []]
    if sources:
        _emit_citation_meta(sources, writer)
        _emit_thinking(
            f"【精读】{result.get('note') or f'返回 {len(sources)} 条分块'}",
            writer,
        )
    elif result.get("note"):
        _emit_thinking(f"【精读】{result['note']}", writer)
    return result


def _read_one_chunk(
    store: ChunkStore,
    chunk_id: int,
    kb_ids: list[int],
    query: str,
    *,
    requested: Any = None,
) -> dict:
    rows = store.get_chunks_by_ids([chunk_id])
    if not rows:
        hint = _missing_id_hint(requested, kind="chunk")
        return _empty(
            query,
            f"分块不存在：chunk_id={requested if requested not in (None, '') else chunk_id}。"
            f"{hint}",
        )
    row = dict(rows[0])
    if not _kb_ok(row.get("knowledge_base_id"), kb_ids):
        return _empty(query, "该分块不在本轮选定的知识库范围内。")
    file_name = str(row.get("file_name") or "")
    doc_id = str(row.get("document_id") or "")
    if not file_name and doc_id:
        meta = store.get_document_config(doc_id)
        if meta:
            file_name = str(meta.get("file_name") or "")
    resolve_parent_chunks([row], store)
    source = _source_from_row(row, file_name=file_name)
    if not source.document_id:
        source = source.model_copy(update={"document_id": doc_id})
    return RetrievalResult(
        query=query,
        sources=[source],
        note=f"已精读 {file_name or doc_id}#chunk{source.chunk_index}（chunk_id={chunk_id}）。",
    ).model_dump()


def _list_document(
    store: ChunkStore,
    document_id: str,
    kb_ids: list[int],
    offset: int,
    limit: int,
    query: str,
    *,
    requested: Any = None,
) -> dict:
    page_size = limit if limit and limit > 0 else LIST_CHUNKS_DEFAULT_LIMIT
    page_size = max(1, min(int(page_size), LIST_CHUNKS_MAX_LIMIT))
    skip = max(0, int(offset or 0))

    meta = store.get_document_config(document_id)
    file_name = str((meta or {}).get("file_name") or "")
    kb_id = (meta or {}).get("kb_id")

    packed = store.list_chunks(
        document_id,
        page_size=page_size,
        offset=skip,
        include_parent_text=False,
    )
    chunks = list(packed.get("chunks") or [])
    total = int(packed.get("total") or 0)
    if kb_id is None and chunks:
        kb_id = chunks[0].get("knowledge_base_id")
    if not _kb_ok(kb_id, kb_ids):
        if total == 0 and meta is None:
            hint = _missing_id_hint(requested or document_id, kind="document")
            return _empty(
                query,
                f"文档不存在：document_id={requested or document_id}。{hint}",
            )
        return _empty(query, "该文档不在本轮选定的知识库范围内。")
    if not file_name and chunks:
        file_name = str(chunks[0].get("file_name") or "")

    if total > 0 and skip >= total:
        suggested = max(0, total - page_size)
        return _empty(
            query,
            (
                f"offset {skip} 超出范围：文档共 {total} 个分块"
                f"（合法 offset 为 0..{total - 1}）。请改用 offset={suggested}。"
            ),
        )

    rows = [
        {
            "id": c["id"],
            "document_id": document_id,
            "file_name": c.get("file_name") or file_name,
            "chunk_index": c.get("chunk_index") or 0,
            "content": c.get("content") or "",
            "parent_chunk_id": c.get("parent_chunk_id"),
        }
        for c in chunks
    ]
    sources = [_source_from_row(r, file_name=file_name) for r in rows]
    fetched = len(sources)
    remaining = max(0, total - skip - fetched)
    note_parts = [
        f"文档 {file_name or document_id}：共 {total} 块，本页 {fetched} 块"
        f"（offset={skip}, limit={page_size}）"
    ]
    if remaining:
        note_parts.append(f"还有 {remaining} 块，下一页 offset={skip + fetched}")
    return RetrievalResult(
        query=query,
        sources=sources,
        note="。".join(note_parts) + "。",
    ).model_dump()
