"""Tier3 legacy splitter：递归字符切分（与既有摄取行为完全一致）。

作为自适应切块链的兜底策略：heading / heuristic 输出未通过校验时降级到这里。
"""

from __future__ import annotations

from langchain_text_splitters import RecursiveCharacterTextSplitter

# 中文友好的切分分隔符（按标点层级切，避免硬切语义）
CHINESE_SEPARATORS = ["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""]

def split_recursive(
    text: str, chunk_size: int, chunk_overlap: int
) -> list[str]:
    """递归字符切分，返回文本块列表。"""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=CHINESE_SEPARATORS,
        length_function=len,
    )
    return splitter.split_text(text)
