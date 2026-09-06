"""解析引擎注册表（引擎注册表）。

- 每个引擎是一个"扩展名 -> 解析器类"映射；get_parser() 构造带 parse_options 的实例。
- builtin 引擎：注册表内全部内置解析器（pdf/docx/doc/pptx/xlsx/md/txt/html/图片）。
- markitdown 引擎：docx/pptx 仅注册 MarkItDown 优先的 Docx2Parser / PptxParser，
  其余类型回退到 builtin 引擎（Q7：MarkItDown 作为 docx/pptx 首选引擎）。
- get_parser_engine(name) 返回引擎单例；未知引擎回退 builtin 并告警。
"""

from __future__ import annotations

import logging
from typing import Type

from ingestion.parser.base_parser import BaseParser

logger = logging.getLogger(__name__)

ENGINE_BUILTIN = "builtin"
ENGINE_MARKITDOWN = "markitdown"


class ParserEngineRegistry:
    """扩展名（不带点）-> 解析器类 的注册表。"""

    def __init__(self, name: str = ENGINE_BUILTIN):
        self.name = name
        self._parsers: dict[str, Type[BaseParser]] = {}

    def register(self, parser_cls: Type[BaseParser], file_types: list[str] | None = None) -> None:
        for ext in (file_types or parser_cls.supported_file_types):
            ext = ext.lower().lstrip(".")
            if not ext:
                continue
            self._parsers[ext] = parser_cls

    def get_parser(self, file_type: str, parse_options: dict | None = None) -> BaseParser | None:
        """按扩展名构造解析器实例（带可选 parse_options）；支持 ".pdf" 与 "pdf"。"""
        ext = file_type.lower().lstrip(".")
        cls = self._parsers.get(ext)
        if cls is None:
            return None
        return cls(parse_options) if parse_options else cls()

    @property
    def supported_file_types(self) -> list[str]:
        return sorted(self._parsers.keys())


def _build_builtin_engine() -> ParserEngineRegistry:
    """构造 builtin 引擎（全部内置解析器）。"""
    from ingestion.parser.doc_parser import DocParser
    from ingestion.parser.docx2_parser import Docx2Parser
    from ingestion.parser.excel_parser import ExcelParser
    from ingestion.parser.html_parser import HTMLParser
    from ingestion.parser.audio_parser import AudioParser
    from ingestion.parser.image_parser import ImageParser
    from ingestion.parser.markdown_parser import MarkdownParser
    from ingestion.parser.pdf_parser import PDFParser
    from ingestion.parser.pptx_parser import Pptx2Parser

    registry = ParserEngineRegistry(ENGINE_BUILTIN)
    registry.register(PDFParser, ["pdf"])
    registry.register(Docx2Parser, ["docx"])
    registry.register(DocParser, ["doc"])
    registry.register(Pptx2Parser, ["pptx"])
    registry.register(ExcelParser, ["xlsx"])
    registry.register(MarkdownParser, ["md", "markdown", "txt"])
    registry.register(HTMLParser, ["html", "htm"])
    registry.register(ImageParser, ["jpg", "jpeg", "png", "gif", "bmp", "tiff", "webp"])
    registry.register(AudioParser, ["mp3", "wav", "m4a", "flac", "ogg", "aac"])
    return registry


def _build_markitdown_engine() -> ParserEngineRegistry:
    """构造 markitdown 引擎：docx/pptx 走 MarkItDown 优先链，其余回退 builtin。"""
    from ingestion.parser.docx2_parser import Docx2Parser
    from ingestion.parser.pptx_parser import Pptx2Parser

    registry = _build_builtin_engine()
    # 覆盖注册：docx/pptx 使用 MarkItDown 优先的链式解析器
    registry.register(Docx2Parser, ["docx"])
    registry.register(Pptx2Parser, ["pptx"])
    registry.name = ENGINE_MARKITDOWN
    return registry


_ENGINES: dict[str, ParserEngineRegistry] = {}


def get_parser_engine(name: str = ENGINE_BUILTIN) -> ParserEngineRegistry:
    """按名字取引擎单例；未知引擎回退 builtin 并告警。"""
    name = (name or ENGINE_BUILTIN).lower()
    if name not in _ENGINES:
        if name == ENGINE_MARKITDOWN:
            _ENGINES[name] = _build_markitdown_engine()
        else:
            _ENGINES[name] = _build_builtin_engine()
    return _ENGINES[name]


__all__ = [
    "ENGINE_BUILTIN",
    "ENGINE_MARKITDOWN",
    "ParserEngineRegistry",
    "get_parser_engine",
]
