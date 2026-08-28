"""MarkItDown 解析器（Q7：docx/pptx 首选引擎，微软 MarkItDown）。

用 markitdown 库把文档转成 Markdown。
MarkItDown 对 docx/pptx/pdf/md/html 都有转换能力；若未安装 markitdown，
解析时抛 ParserError 由链式解析器回退到专用解析器。

注意：markitdown 默认不导出内嵌图片，图片交给链上专用解析器（DocxParser 等）提取。
"""

from __future__ import annotations

import logging

from ingestion.parser.base_parser import BaseParser, ParserError, ParseResult

logger = logging.getLogger(__name__)


class MarkItDownParser(BaseParser):
    supported_file_types = ["docx", "pptx", "pdf", "md", "markdown", "txt", "html", "htm"]

    def __init__(self, parse_options: dict | None = None):
        super().__init__(parse_options)
        self._converter = None

    def _get_converter(self):
        """懒加载 markitdown 转换器（依赖较重，首次使用时初始化）。"""
        if self._converter is not None:
            return self._converter
        try:
            from markitdown import MarkItDown
        except ImportError as exc:
            raise ParserError("markitdown 未安装，请执行 pip install markitdown") from exc
        self._converter = MarkItDown(enable_plugins=False)
        return self._converter

    def parse(self, path: str) -> ParseResult:
        converter = self._get_converter()
        try:
            md = converter.convert(path)
        except Exception as exc:  # noqa: BLE001 - 转换失败由链式解析器回退
            raise ParserError(f"MarkItDown 转换失败: {exc}") from exc
        text = (md.text_content or "").strip()
        if not text:
            raise ParserError("MarkItDown 输出为空")
        return ParseResult(markdown=text)


__all__ = ["MarkItDownParser"]
