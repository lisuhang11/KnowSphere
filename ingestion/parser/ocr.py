"""OCR 引擎封装（Q6/Q9：PaddleOCR 经典版，CPU 推理，内嵌库）。

- 懒加载单例：首次调用才导入 paddleocr（依赖很重），进程内复用；
- ocr_image_bytes(bytes) -> str：识别单张图片返回文本；
- PaddleOCR 未安装或初始化失败时抛 ParserError，由调用方（PDF 扫描页 /
  ImageParser）降级处理（保留占位，不阻塞解析）。
"""

from __future__ import annotations

import logging

from ingestion.parser.base_parser import ParserError

logger = logging.getLogger(__name__)

_OCR = None
_OCR_LOCK = None  # 懒加载后再赋值为 threading.Lock


def _ensure_ocr():
    """懒加载并返回 PaddleOCR 实例（进程级单例）。"""
    global _OCR, _OCR_LOCK
    if _OCR is not None:
        return _OCR
    if _OCR_LOCK is None:
        import threading
        _OCR_LOCK = threading.Lock()
    with _OCR_LOCK:
        if _OCR is not None:
            return _OCR
        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:
            raise ParserError(
                "PaddleOCR 未安装，请执行 pip install paddleocr paddlepaddle"
            ) from exc
        try:
            # lang='ch' 同时支持中文与英文；use_angle_cls 提升竖排/倾斜识别
            _OCR = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
        except Exception as exc:  # noqa: BLE001 - 模型下载/初始化失败
            logger.warning("PaddleOCR 初始化失败: %s", exc)
            raise ParserError(f"PaddleOCR 初始化失败: {exc}") from exc
        logger.info("PaddleOCR 引擎就绪")
    return _OCR


def ocr_image_bytes(image_bytes: bytes) -> str:
    """识别图片字节，返回拼接文本（每行一个识别结果）。

    失败抛 ParserError（未安装 / 初始化失败 / 识别异常）。
    """
    engine = _ensure_ocr()
    try:
        results = engine.ocr(image_bytes, cls=True)
    except Exception as exc:  # noqa: BLE001
        raise ParserError(f"PaddleOCR 识别失败: {exc}") from exc
    if not results:
        return ""
    lines: list[str] = []
    for page_result in results:
        if not page_result:
            continue
        for item in page_result:
            try:
                text = item[1][0] if isinstance(item, (list, tuple)) and len(item) > 1 else ""
            except Exception:
                text = ""
            if text:
                lines.append(str(text).strip())
    return "\n".join(lines)


def ocr_available() -> bool:
    """探测 OCR 是否可用（不抛异常）。"""
    try:
        _ensure_ocr()
        return True
    except Exception:  # noqa: BLE001
        return False


__all__ = ["ocr_image_bytes", "ocr_available"]
