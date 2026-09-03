"""get_document_info：文档元数据，不含正文（对齐 WeKnora get_document_info）。"""

from __future__ import annotations

from typing import Annotated, Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import InjectedToolArg, tool
from langgraph.prebuilt import ToolRuntime

from stores.facade import ChunkStore
from tools.retrieval.content import LIST_DOCS_MAX
from tools.retrieval.doc_retrieval import _emit_thinking
from utils.run_config import kb_ids_from_config
from utils.source_aliases import messages_from_runtime, resolve_document_id


def _normalize_ids(document_ids: list[str] | str | None, document_id: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    raw: list[Any] = []
    if isinstance(document_ids, str):
        raw.append(document_ids)
    elif document_ids:
        raw.extend(document_ids)
    extra = (document_id or "").strip()
    if extra:
        raw.append(extra)
    for item in raw:
        name = str(item or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def _empty(note: str, *, documents: list[dict] | None = None) -> dict:
    return {
        "documents": documents or [],
        "total": len(documents or []),
        "note": note,
    }


@tool
def get_document_info(
    document_ids: list[str] | None = None,
    document_id: str = "",
    config: Annotated[RunnableConfig, InjectedToolArg] = None,
    runtime: Annotated[ToolRuntime | None, InjectedToolArg] = None,
) -> dict:
    """查看知识库文档的元数据：文件名、解析状态、分块数。不含正文。

    传入 document_ids（或单个 document_id）查指定文档；都不传则列出本轮选定知识库中的文档。
    需要正文时用 list_chunks；需要语义相关段落时用 doc_retrieval。
    """
    kb_ids = kb_ids_from_config(config)
    writer = getattr(runtime, "stream_writer", None) if runtime is not None else None
    wanted = _normalize_ids(document_ids, document_id)
    messages = messages_from_runtime(runtime)
    wanted = [resolve_document_id(item, messages) or item for item in wanted]

    if not kb_ids:
        return _empty("未选择知识库：无法查看文档信息。请提示用户选择知识库后再问。")

    store = ChunkStore()
    packed = store.list_document_infos(
        kb_ids,
        document_ids=wanted or None,
        limit=LIST_DOCS_MAX,
    )
    docs = list(packed.get("documents") or [])
    total = int(packed.get("total") or 0)

    if wanted:
        found = {str(d.get("document_id") or "") for d in docs}
        missing = [i for i in wanted if i not in found]
        if not docs:
            note = (
                "未找到这些文档，或不在本轮选定的知识库范围内："
                + "、".join(wanted)
            )
            _emit_thinking(f"【文档信息】{note}", writer)
            return _empty(note)
        note = f"已返回 {len(docs)} / {len(wanted)} 份文档元数据，不含正文。"
        if missing:
            note += "未找到或不在范围内：" + "、".join(missing) + "。"
        note += "需要正文时用 list_chunks（document_id 或 chunk_id）。"
    elif not docs:
        note = "所选知识库中还没有文档。"
    else:
        note = f"知识库中共 {total} 份文档，本列表 {len(docs)} 份（不含正文）。"
        if total > len(docs):
            note += "已截断，可用 document_ids 查询其余文档。"
        note += "需要正文时用 list_chunks；按关键词定位用 grep_chunks。"

    _emit_thinking(f"【文档信息】{note}", writer)
    return {
        "documents": docs,
        "total": total if not wanted else len(docs),
        "note": note,
    }
