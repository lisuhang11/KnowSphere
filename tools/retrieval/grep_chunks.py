"""grep_chunks：知识库分块 POSIX 正则搜索（对齐 WeKnora grep_chunks）。"""

from __future__ import annotations

from typing import Annotated, Any

import psycopg
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import InjectedToolArg, tool
from langgraph.prebuilt import ToolRuntime

from schemas import RetrievalResult, Source
from stores.facade import ChunkStore
from tools.retrieval.content import (
    GREP_FETCH_LIMIT,
    GREP_QUERY_MAX,
    GREP_RETURN_LIMIT,
    compile_grep_regex,
    extract_match_snippet,
)
from tools.retrieval.doc_retrieval import _emit_citation_meta, _emit_thinking
from utils.run_config import kb_ids_from_config


def _empty(query: str, note: str) -> dict:
    return RetrievalResult(query=query, sources=[], note=note).model_dump()


def _score_hit(row: dict[str, Any], compiled) -> tuple[bool, float, str]:
    content = row.get("content") or ""
    file_name = str(row.get("file_name") or "")
    title_match = bool(compiled and compiled.search(file_name))
    body_match = bool(compiled and compiled.search(content))
    if body_match:
        snippet = extract_match_snippet(content, compiled)
    elif title_match:
        snippet = extract_match_snippet(file_name, compiled)
    else:
        snippet = extract_match_snippet(content or file_name, compiled)
    if compiled is None:
        return title_match, 0.0, snippet
    matches = list(compiled.finditer(content))
    if not matches and not title_match:
        return False, 0.0, snippet
    earliest = matches[0].start() if matches else 0
    score = (2.0 if title_match else 0.0) + 1.0 / (1.0 + earliest) + 0.05 * min(len(matches), 20)
    return title_match, score, snippet


@tool
def grep_chunks(
    query: str,
    config: Annotated[RunnableConfig, InjectedToolArg] = None,
    runtime: Annotated[ToolRuntime | None, InjectedToolArg] = None,
) -> dict:
    """在知识库分块正文里按正则精确查找（忽略大小写），适合工号、错误码、产品名、接口路径。

    多个词用 | 写进一条正则，不要拆成多次调用。只返回匹配附近片段，不是全文；
    命中后用 list_chunks 传入返回的 chunk_id / cN，或 document_id / dN 读全文。
    不要把 [[cN]] 的数字或文件名#后的序号当成数据库 id。
    不要用它代替语义检索。
    """
    kb_ids = kb_ids_from_config(config)
    pattern = (query or "").strip()
    writer = getattr(runtime, "stream_writer", None) if runtime is not None else None

    if not kb_ids:
        return _empty(
            pattern,
            "未选择知识库：无法搜索用户文档。请提示用户选择知识库后再问。",
        )
    if not pattern:
        return _empty(pattern, "query 不能为空，请提供一条正则或关键词。")
    if len(pattern) > GREP_QUERY_MAX:
        return _empty(
            pattern,
            f"正则过长（上限 {GREP_QUERY_MAX} 字符）。请缩短或拆成更具体的模式。",
        )

    compiled = compile_grep_regex(pattern)
    if compiled is None:
        return _empty(
            pattern,
            f"正则无效：{pattern}。请改用更简单的模式（多个词用 | 连接）。",
        )

    store = ChunkStore()
    _emit_thinking(f"【关键词搜索】{pattern}", writer)
    try:
        rows = store.grep_chunks(pattern, kb_ids, fetch_limit=GREP_FETCH_LIMIT)
    except psycopg.Error as exc:
        msg = str(exc).lower()
        if "invalid regular expression" in msg or "invalid regex" in msg:
            return _empty(pattern, f"正则无效：{pattern}。请改用更简单的模式（多个词用 | 连接）。")
        return _empty(pattern, f"关键词搜索失败：{exc}")

    ranked: list[tuple[bool, float, dict[str, Any], str]] = []
    for row in rows:
        title_match, score, snippet = _score_hit(row, compiled)
        ranked.append((title_match, score, row, snippet))
    ranked.sort(key=lambda item: (not item[0], -item[1], int(item[2].get("chunk_index") or 0)))
    top = ranked[:GREP_RETURN_LIMIT]

    sources: list[Source] = []
    docs_order: list[str] = []
    seen_docs: set[str] = set()
    for _title, score, row, snippet in top:
        doc = str(row.get("document_id") or "")
        if doc and doc not in seen_docs:
            seen_docs.add(doc)
            docs_order.append(doc)
        sources.append(
            Source(
                document_id=doc,
                file_name=str(row.get("file_name") or ""),
                chunk_index=int(row.get("chunk_index") or 0),
                score=float(score),
                snippet=snippet,
                chunk_id=int(row["id"]) if row.get("id") is not None else None,
                cite_id=f"c{len(sources) + 1}",
                doc_alias=f"d{docs_order.index(doc) + 1}" if doc else "",
                content=snippet,
            )
        )

    docs = {s.document_id for s in sources if s.document_id}
    if not sources:
        note = f"未找到匹配「{pattern}」的分块。可换关键词，或改用 doc_retrieval 做语义检索。"
    else:
        extra = ""
        if len(ranked) > len(sources):
            extra = f"已截断为 {len(sources)} 条（共命中 {len(ranked)}）。"
        note = (
            f"关键词搜索「{pattern}」命中 {len(sources)} 块、{len(docs)} 篇。"
            f"{extra}"
            "片段不是全文；需要上下文时用 list_chunks（chunk_id / cN 或 document_id / dN）。"
        )
    result = RetrievalResult(query=pattern, sources=sources, note=note).model_dump()
    if sources:
        _emit_citation_meta(sources, writer)
    _emit_thinking(f"【关键词搜索】{note}", writer)
    return result
