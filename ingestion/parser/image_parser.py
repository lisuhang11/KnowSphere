"""图片直传解析器（Q11：jpg/png/tiff 等作为独立文档上传）。

- 读取图片字节收集到 ParseResult.images；
- 若启用 OCR（ocr_enabled=True 且 PaddleOCR 可用），对图片 OCR 出文本作为
  markdown 正文（Q6/Q9）；OCR 不可用时正文只有图片占位引用。
"""

from __future__ import annotations

import logging
from pathlib import Path

from ingestion.parser.base_parser import BaseParser, ParserError, ParseResult
from ingestion.parser.ocr import ocr_image_bytes

logger = logging.getLogger(__name__)

_MIME_MAP = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".bmp": "image/bmp", ".tiff": "image/tiff",
    ".webp": "image/webp",
}


class ImageParser(BaseParser):
    supported_file_types = ["jpg", "jpeg", "png", "gif", "bmp", "tiff", "webp"]

    def parse(self, path: str) -> ParseResult:
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as exc:
            raise ParserError(f"读取图片失败: {exc}") from exc
        if not data:
            raise ParserError("图片内容为空")

        ext = Path(path).suffix.lower()
        mime_type = _MIME_MAP.get(ext, "image/jpeg")

        result = ParseResult()
        ref_path = self._add_image(result, data, mime_type, original_ref=Path(path).name)

        parts: list[str] = []
        # OCR 文本（可选）
        if self.parse_options.get("ocr_enabled", True):
            try:
                text = ocr_image_bytes(data)
                if text.strip():
                    parts.append(text.strip())
            except Exception as exc:  # noqa: BLE001 - OCR 失败不阻塞，仅保留占位
                logger.debug("image OCR failed: %s", exc)
        parts.append(f"![{Path(path).name}]({ref_path})")
        result.markdown = "\n\n".join(parts).strip()
        return result


__all__ = ["ImageParser"]
