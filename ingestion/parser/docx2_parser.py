"""docx 链式解析器（docx 链式解析器）。

FirstParser([MarkItDownParser, DocxParser])：
- markitdown_first=True（引擎="markitdown"）：MarkItDown 优先，失败回退专用 DocxParser；
- markitdown_first=False（引擎="builtin"）：专用 DocxParser 优先（自带表格/图片提取），
  失败才回退 MarkItDown。

两者都失败时抛 ParserError。
"""

from __future__ import annotations

from ingestion.parser.base_parser import ParseResult
from ingestion.parser.chain_parser import FirstParser
from ingestion.parser.docx_parser import DocxParser
from ingestion.parser.markitdown_parser import MarkItDownParser


class Docx2Parser(FirstParser):
    def __init__(self, parse_options: dict | None = None, *, markitdown_first: bool = True):
        if parse_options:
            self.parse_options = parse_options
        md_parser = MarkItDownParser(parse_options)
        docx_parser = DocxParser(parse_options)
        parsers = [md_parser, docx_parser] if markitdown_first else [docx_parser, md_parser]
        super().__init__(parsers, parse_options)


__all__ = ["Docx2Parser"]
