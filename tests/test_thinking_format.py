"""thinking 文案单元测试。"""

from tools.retrieval.thinking_format import (
    format_expansion_result,
    format_multi_query_result,
    format_multi_query_trigger,
    format_recall_result,
    format_recall_start,
    format_source_preview,
)


def test_format_recall_start_single_path():
    text = format_recall_start("李稣航是谁", ["李稣航是谁"], [], 30, 15)
    assert "单路" in text
    assert "李稣航是谁" in text
    assert "扩展阈值 15" in text


def test_format_recall_start_multi_query_compat():
    """旧接口仍可展示多跳模式文案（兼容）。"""
    subs = ["李稣航的个人背景是什么", "李稣航的主要成就有哪些"]
    queries = ["李稣航是谁"] + subs
    text = format_recall_start("李稣航是谁", queries, subs, 30, 15)
    assert "LLM 多跳" in text
    assert "实际检索 3 路" in text


def test_format_recall_result_triggers_expansion():
    text = format_recall_result(8, 30, 15)
    assert "8 条候选" in text
    assert "命中偏少" in text
    assert "多跳" in text


def test_format_multi_query_trigger():
    text = format_multi_query_trigger(
        6,
        15,
        ["李稣航的个人背景是什么", "李稣航的主要成就有哪些"],
    )
    assert "首轮单路仅 6 条" in text
    assert "因此触发 LLM 多跳" in text
    assert "李稣航的个人背景是什么" in text


def test_format_multi_query_result():
    text = format_multi_query_result(4, 6, 8)
    assert "raw 命中 4 条" in text
    assert "候选池 8 条" in text


def test_format_expansion_result():
    text = format_expansion_result(["李 稣 航 谁"], 4, 8, 12)
    assert "本地扩展" in text
    assert "李 稣 航 谁" in text
    assert "候选池 12 条" in text


def test_format_source_preview():
    rows = [
        {
            "file_name": "自我介绍.txt",
            "chunk_index": 0,
            "score": 0.91,
            "snippet": "李稣航，河北地质大学",
        }
    ]
    text = format_source_preview(rows)
    assert "自我介绍.txt#0" in text
    assert "0.910" in text
