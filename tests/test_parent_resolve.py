"""parent resolve 单元测试。"""

from tools.retrieval.parent_resolve import (
    join_chunk_content,
    resolve_parent_chunks,
    snippet_from_resolved,
)

class _FakeStore:
    def __init__(self, parents: dict[int, dict]):
        self._parents = parents

    def get_chunks_by_ids(self, ids, owner=None):
        return [self._parents[i] for i in ids if i in self._parents]

def test_join_chunk_content_child_in_parent():
    parent = "父块全文包含子块内容"
    child = "子块内容"
    assert join_chunk_content(parent, child) == parent

def test_join_chunk_content_concat():
    merged = join_chunk_content("父块", "子块")
    assert merged == "父块\n\n子块"

def test_snippet_from_resolved_prefers_child_region():
    full = "AAAA" * 50 + "TARGET" + "BBBB" * 50
    snip = snippet_from_resolved(full, "TARGET", max_len=100)
    assert "TARGET" in snip

def test_resolve_parent_chunks_expands_content():
    rows = [
        {
            "content": "子块命中",
            "chunk_index": 3,
            "parent_chunk_id": 42,
            "snippet": "子块命中",
        }
    ]
    store = _FakeStore(
        {
            42: {
                "id": 42,
                "content": "父块上下文很长",
                "chunk_type": "parent_text",
            }
        }
    )
    out = resolve_parent_chunks(rows, store)
    assert out[0]["parent_resolved"] is True
    assert "父块上下文很长" in out[0]["content"]
    assert out[0]["sub_chunk_index"] == 3
