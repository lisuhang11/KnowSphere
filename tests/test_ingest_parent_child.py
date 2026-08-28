"""resolve_chunk_config 父子分块配置合并。"""

from ingestion.ingest import resolve_chunk_config

def test_resolve_chunk_config_parent_child_from_kb():
    kb = {
        "chunk_strategy": "auto",
        "chunk_size": 600,
        "chunk_overlap": 90,
        "enable_parent_child": True,
        "parent_chunk_size": 4096,
        "child_chunk_size": 384,
    }
    cfg = resolve_chunk_config(kb, None)
    assert cfg["enable_parent_child"] is True
    assert cfg["parent_chunk_size"] == 4096
    assert cfg["child_chunk_size"] == 384

def test_resolve_chunk_config_document_override():
    kb = {
        "chunk_strategy": "auto",
        "chunk_size": 600,
        "chunk_overlap": 90,
        "enable_parent_child": False,
        "parent_chunk_size": 4096,
        "child_chunk_size": 384,
    }
    cfg = resolve_chunk_config(
        kb,
        {"chunking_config": {"enable_parent_child": True, "child_chunk_size": 256}},
    )
    assert cfg["enable_parent_child"] is True
    assert cfg["child_chunk_size"] == 256
    assert cfg["parent_chunk_size"] == 4096
