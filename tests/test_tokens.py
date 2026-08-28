"""回归：token 估算的确定性（供 chunk 大小与 prompt 预算校验）。"""

from __future__ import annotations

from utils.tokens import estimate_tokens

def test_empty():
    assert estimate_tokens("") == 0

def test_pure_cjk():
    assert estimate_tokens("你好世界") == 2  # round(4 * 0.6)

def test_pure_latin():
    assert estimate_tokens("hello") == 1  # round(5 * 0.25)

def test_mixed():
    assert estimate_tokens("你好 world") == 3  # round(2*0.6 + 6*0.25)

def test_min_one():
    assert estimate_tokens("a") == 1

def test_long_document_monotonic():
    short = estimate_tokens("知识库问答系统")
    long_ = estimate_tokens("知识库问答系统" * 100)
    assert long_ > short
