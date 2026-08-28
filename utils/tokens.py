"""Token 近似估算：不加载分词器，按中英字符占比估算（兼容 bge-m3 sentencepiece 量级）。

仅用于切块列表/预览的展示（"预估 token 数"），不参与计费或截断判断。
"""

from __future__ import annotations

def _is_cjk(ch: str) -> bool:
    """中文汉字与中文标点、全角符号区段。"""
    code = ord(ch)
    return (
        0x4E00 <= code <= 0x9FFF  # CJK 统一表意文字
        or 0x3400 <= code <= 0x4DBF  # 扩展 A
        or 0x3000 <= code <= 0x303F  # CJK 标点
        or 0xFF00 <= code <= 0xFFEF  # 全角/半角形式
    )

def estimate_tokens(text: str) -> int:
    """中英混合文本的 token 数近似值。

    经验比例：中文约 0.6 token/字符；拉丁字母连续文本约 4 字符/token。
    误差可接受（±20%），对展示场景足够。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if _is_cjk(ch))
    rest = len(text) - cjk
    return max(1, round(cjk * 0.6 + rest * 0.25))
