"""父子分块单元测试（不依赖 Postgres）。"""

from chunkers.parent_child import (
    derive_parent_child_configs,
    embedding_content,
    merge_breadcrumbs,
    split_parent_child_with_diagnostics,
)

def test_derive_parent_child_configs_child_overlap():
    parent_cfg, child_cfg = derive_parent_child_configs(
        strategy="auto", chunk_overlap=90, parent_size=4096, child_size=384
    )
    assert parent_cfg["chunk_size"] == 4096
    assert parent_cfg["chunk_overlap"] == 90
    assert child_cfg["chunk_size"] == 384
    assert child_cfg["chunk_overlap"] == 384 // 5

def test_merge_breadcrumbs_dedup_first_line():
    parent = "# 第一章\n## 1.1 背景"
    child = "## 1.1 背景\n正文"
    merged = merge_breadcrumbs(parent, child)
    assert merged == "# 第一章\n## 1.1 背景\n正文"

def test_embedding_content_with_header():
    text = embedding_content("正文", "# 标题")
    assert text.startswith("# 标题")
    assert "正文" in text

def test_split_parent_child_produces_children():
    text = ("# 标题\n\n" + "这是测试段落。" * 200).strip()
    result = split_parent_child_with_diagnostics(
        text,
        strategy="recursive",
        parent_size=800,
        child_size=200,
        chunk_overlap=40,
    )
    assert result.children
    assert result.selected_tier
    assert all(c.content for c in result.children)

def test_split_parent_child_skips_redundant_parent():
    text = "短文档，无需多级切分。"
    result = split_parent_child_with_diagnostics(
        text,
        strategy="recursive",
        parent_size=4096,
        child_size=384,
        chunk_overlap=40,
    )
    assert len(result.children) >= 1
    assert all(c.parent_index == -1 for c in result.children)
    assert len(result.parents) == 0
