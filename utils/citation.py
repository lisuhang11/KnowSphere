"""引用输出控制：[[cN]] 句柄 → 前端角标 HTML 的流式安全展开。

引用输出协议（句柄不泄露、fail-closed、流式安全）：
- 模型只输出私有句柄 [[cN]]（N = 本轮检索结果序号，1-based），接触不到真实文档 ID；
- 后端在 SSE 出口做 fail-closed 展开：合法句柄 → <sup class="cite"> 角标，
  非法/越界句柄 → 整段剥离（防幻觉引用）；
- 流式半截句柄（如 "[[c1"）缓存到闭合 "]]" 再输出，杜绝残片泄漏到前端。
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

# [[cN]]：N 为 1-based 检索结果序号（最多 3 位，防超长回溯）
_CITE_RE = re.compile(r"\[\[c(?P<index>\d{1,3})\]\]")
# 尾部未闭合句柄前缀（[[cN / [[c / [[c），用于流式截留
_OPEN_RE = re.compile(r"\[\[c\d{0,3}\Z")

@dataclass(frozen=True)
class Citation:
    """单个可点击引用的元数据（前端角标 / 来源面板的数据源）。"""

    index: int
    document_id: str
    file_name: str
    chunk_index: int
    score: float = 0.0
    snippet: str = ""

def citations_from_sources(sources) -> dict[int, Citation]:
    """把 doc_retrieval 的 Source 列表映射为 {1-based 序号: Citation}。"""
    return {
        i + 1: Citation(
            index=i + 1,
            document_id=s.document_id,
            file_name=s.file_name,
            chunk_index=s.chunk_index,
            score=getattr(s, "score", 0.0) or 0.0,
            snippet=getattr(s, "snippet", "") or "",
        )
        for i, s in enumerate(sources)
    }

def unique_sources_by_document(citations: dict[int, Citation]) -> list[Citation]:
    """来源面板：按 document_id 去重，每文档保留检索序最靠前的一条（最小 cite index）。

    展示语义：正文内联引用可指向具体 chunk，底部来源列表只列文档，
    不重复罗列同一文件的多个分块。
    """
    seen: set[str] = set()
    unique: list[Citation] = []
    for idx in sorted(citations):
        c = citations[idx]
        key = c.document_id or c.file_name
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(c)
    return unique

def _citation_dict(c: Citation) -> dict:
    payload = {
        "index": c.index,
        "document_id": c.document_id,
        "file_name": c.file_name,
        "chunk_index": c.chunk_index,
        "score": c.score,
        "snippet": c.snippet,
    }
    doc = (c.document_id or "").strip()
    if doc.lower().startswith(("http://", "https://")):
        payload["url"] = doc
    return payload

def merge_citation_maps(
    existing: dict[int, Citation], incoming: dict[int, Citation]
) -> dict[int, Citation]:
    """合并多次检索的 cite 表：后续批次 index 续编，避免 [[c1]] 冲突。"""
    if not incoming:
        return dict(existing)
    merged = dict(existing)
    offset = len(merged)
    for i, idx in enumerate(sorted(incoming)):
        c = incoming[idx]
        new_idx = offset + i + 1
        merged[new_idx] = Citation(
            index=new_idx,
            document_id=c.document_id,
            file_name=c.file_name,
            chunk_index=c.chunk_index,
            score=c.score,
            snippet=c.snippet,
        )
    return merged

def citation_meta_payload(citations: dict[int, Citation]) -> dict:
    """构造下发给前端的 citation_meta 帧数据（index 从 1 开始）。

    citations：全量 chunk 级引用，供 [[cN]] 角标映射；
    sources：按 document_id 去重后的文档列表，供底部「来源」面板展示。
    """
    return {
        "type": "citation_meta",
        "citations": [_citation_dict(c) for c in citations.values()],
        "sources": [_citation_dict(c) for c in unique_sources_by_document(citations)],
    }

class CitationStreamExpander:
    """流式安全地展开 [[cN]] 句柄为引用角标 HTML。

    用法：逐个 feed LLM 输出块，把返回值拼进 SSE answer 帧；流结束调 flush。

    安全边界：
    - 合法句柄（1 <= N <= citations 总数）→ <sup class="cite" ...>N</sup>
    - 非法 / 越界句柄（[[c0]]、[[c99]]、[[cx]]）→ 整段剥离（fail-closed），记入 dropped_count
    - 流结束时残留未闭合的 "[[cN" → 剥离
    """

    def __init__(self, citations: dict[int, Citation] | list[Citation] | None = None):
        self._citations: dict[int, Citation] = {}
        if citations is not None:
            if isinstance(citations, dict):
                self._citations = dict(citations)
            else:
                self._citations = {c.index: c for c in citations}
        self._buf = ""  # 尾部待判定缓冲（未闭合句柄前缀）
        self._dropped = 0
        self._used: set[int] = set()

    @property
    def dropped_count(self) -> int:
        """被剥离的非法/越界/未闭合句柄数（幻觉引用统计）。"""
        return self._dropped

    @property
    def used_indexes(self) -> list[int]:
        """实际被模型引用过的句柄序号（引用命中率统计）。"""
        return sorted(self._used)

    def feed(self, chunk: str) -> str:
        """处理一个输出块；返回可安全进入 SSE 的文本。"""
        if not chunk:
            return ""
        text = self._buf + chunk
        self._buf = ""
        # 尾部是未闭合句柄前缀则截留进缓冲，等下一块闭合
        m = _OPEN_RE.search(text)
        if m:
            cut = m.start
            self._buf = text[cut:]
            text = text[:cut]
        if not text:
            return ""
        return self._expand(text)

    def flush(self) -> str:
        """流结束时调用：剥离未闭合句柄残留，返回剩余可输出文本（通常为空）。"""
        buf = self._buf
        self._buf = ""
        if not buf:
            return ""
        if re.fullmatch(r"\[\[c\d{0,3}", buf):
            self._dropped += 1  # 未闭合句柄 → fail-closed 剥离
            return ""
        return buf

    def _expand(self, text: str) -> str:
        def repl(m: re.Match) -> str:
            idx = int(m.group("index"))
            c = self._citations.get(idx)
            if c is None:
                self._dropped += 1  # 越界 / 未提供 → 剥离
                return ""
            self._used.add(idx)
            return _render_sup(c)

        return _CITE_RE.sub(repl, text)

def _render_sup(c: Citation) -> str:
    """渲染引用角标 HTML；data-* 承载前端跳转所需元数据（属性值转义防注入）。"""
    return (
        f'<sup class="cite" data-cite-index="{c.index}" '
        f'data-doc-id="{html.escape(c.document_id, quote=True)}" '
        f'data-file-name="{html.escape(c.file_name, quote=True)}" '
        f'data-chunk-index="{c.chunk_index}">{c.index}</sup>'
    )
