"""引用展开器（utils.citation.CitationStreamExpander）状态机单测。

覆盖：完整句柄展开、半截句柄跨块缓存、未闭合残留剥离、
越界/非法句柄 fail-closed、引用命中/丢弃统计、HTML 转义。
"""

from __future__ import annotations

from utils.citation import Citation, CitationStreamExpander, citation_meta_payload, citations_from_sources, unique_sources_by_document

def _mk() -> dict[int, Citation]:
    return {
        1: Citation(index=1, document_id="doc-1", file_name="a.txt", chunk_index=0, score=0.9, snippet="片段A"),
        2: Citation(index=2, document_id="doc-2", file_name="b.pdf", chunk_index=3, score=0.7, snippet="片段B"),
    }

def _sup(index: int, doc_id: str, file_name: str, chunk_index: int) -> str:
    return (
        f'<sup class="cite" data-cite-index="{index}" data-doc-id="{doc_id}" '
        f'data-file-name="{file_name}" data-chunk-index="{chunk_index}">{index}</sup>'
    )

def test_expand_simple():
    ex = CitationStreamExpander(_mk())
    out = ex.feed("根据文档[[c1]]可知。")
    assert out == f"根据文档{_sup(1, 'doc-1', 'a.txt', 0)}可知。"
    assert ex.used_indexes == [1]
    assert ex.dropped_count == 0

def test_expand_multiple_in_one_chunk():
    ex = CitationStreamExpander(_mk())
    out = ex.feed("A[[c1]]与B[[c2]]均成立。")
    assert out == f"A{_sup(1, 'doc-1', 'a.txt', 0)}与B{_sup(2, 'doc-2', 'b.pdf', 3)}均成立。"

def test_expand_across_chunks():
    ex = CitationStreamExpander(_mk())
    # 半截句柄进入缓冲，不泄漏
    assert ex.feed("前半句[[c1") == "前半句"
    # 闭合后完整输出
    assert ex.feed("]]后半句") == f"{_sup(1, 'doc-1', 'a.txt', 0)}后半句"
    assert ex.flush == ""
    assert ex.used_indexes == [1]

def test_trailing_half_open_flushed():
    ex = CitationStreamExpander(_mk())
    assert ex.feed("正文[[c2") == "正文"
    # 流结束，未闭合句柄被剥离
    assert ex.flush == ""
    assert ex.dropped_count == 1

def test_dropped_out_of_range():
    ex = CitationStreamExpander(_mk())
    out = ex.feed("幻觉引用[[c99]]被剥离")
    assert out == "幻觉引用被剥离"
    assert ex.dropped_count == 1

def test_dropped_invalid_index():
    ex = CitationStreamExpander(_mk())
    assert ex.feed("非法[[c0]]") == "非法"
    assert ex.feed("负数[[c-1]]") == "负数"  # 不匹配句柄正则，原样输出
    assert ex.dropped_count == 1

def test_markdown_brackets_untouched():
    ex = CitationStreamExpander(_mk())
    assert ex.feed("链接[text](url)与[[c1]]") == f"链接[text](url)与{_sup(1, 'doc-1', 'a.txt', 0)}"

def test_used_stats():
    ex = CitationStreamExpander(_mk())
    ex.feed("只用[[c2]]")
    assert ex.used_indexes == [2]
    assert ex.dropped_count == 0

def test_html_escape_in_attrs():
    c = {1: Citation(index=1, document_id='doc"1', file_name='a&b.txt', chunk_index=1)}
    ex = CitationStreamExpander(c)
    out = ex.feed("[[c1]]")
    assert 'data-doc-id="doc&quot;1"' in out
    assert 'data-file-name="a&amp;b.txt"' in out

def test_empty_and_noop():
    ex = CitationStreamExpander(_mk())
    assert ex.feed("") == ""
    assert ex.feed("纯文本无引用") == "纯文本无引用"
    assert ex.flush == ""

def test_citations_from_sources():
    from schemas import Source

    sources = [Source(document_id="d1", file_name="x.md", chunk_index=2, score=0.8, snippet="s")]
    cite = citations_from_sources(sources)
    assert cite[1].document_id == "d1"
    assert cite[1].chunk_index == 2
    payload = citation_meta_payload(cite)
    assert payload["type"] == "citation_meta"
    assert payload["citations"][0]["index"] == 1
    assert payload["citations"][0]["file_name"] == "x.md"
    assert payload["sources"] == payload["citations"]

def test_unique_sources_by_document():
    cites = {
        1: Citation(index=1, document_id="a", file_name="自我介绍.txt", chunk_index=0),
        2: Citation(index=2, document_id="b", file_name="简历.pdf", chunk_index=1),
        3: Citation(index=3, document_id="a", file_name="自我介绍.txt", chunk_index=2),
        4: Citation(index=4, document_id="b", file_name="简历.pdf", chunk_index=3),
    }
    unique = unique_sources_by_document(cites)
    assert len(unique) == 2
    assert unique[0].file_name == "自我介绍.txt"
    assert unique[0].chunk_index == 0
    assert unique[1].file_name == "简历.pdf"
    payload = citation_meta_payload(cites)
    assert len(payload["citations"]) == 4
    assert len(payload["sources"]) == 2
