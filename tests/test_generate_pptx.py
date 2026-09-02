"""generate_pptx 大纲渲染。"""

from __future__ import annotations

from tools.creation.generate_pptx import _safe_pptx_name, build_pptx_bytes


def test_build_pptx_bytes_is_zip():
    data = build_pptx_bytes(
        "园区介绍",
        [
            {"title": "概况", "bullets": ["占地 120 亩", "2003 年投用"]},
            {"title": "配套", "bullets": ["食堂", "班车"]},
        ],
    )
    assert data[:2] == b"PK"
    assert len(data) > 1000


def test_safe_pptx_name():
    assert _safe_pptx_name("", "园区介绍").endswith(".pptx")
    assert "/" not in _safe_pptx_name("../x.pptx", "t")
    assert _safe_pptx_name("汇报", "t").endswith(".pptx")
