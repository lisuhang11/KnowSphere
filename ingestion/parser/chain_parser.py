"""链式解析器（链式解析器）。

- FirstParser：按顺序尝试子解析器，返回第一个成功的结果（Q7 的 MarkItDown 优先链）。
- PipelineParser：把子解析器输出依次拼接（如 doc -> LibreOffice 转 docx -> docx 解析）。
"""

from __future__ import annotations

import logging

from ingestion.parser.base_parser import BaseParser, ParserError, ParseResult

logger = logging.getLogger(__name__)


class FirstParser(BaseParser):
    """顺序尝试子解析器，返回第一个成功结果。

    子解析器抛出 ParserError 视为失败；其他异常同样捕获（    chain 逻辑：任一失败继续尝试下一个）。全部失败抛 ParserError。
    """

    def __init__(self, parsers: list[BaseParser], parse_options: dict | None = None):
        super().__init__(parse_options)
        self.parsers = parsers
        ext_set: set[str] = set()
        for p in parsers:
            ext_set.update(p.supported_file_types)
        self.supported_file_types = sorted(ext_set)

    def parse(self, path: str) -> ParseResult:
        errors: list[str] = []
        for parser in self.parsers:
            try:
                result = parser.parse(path)
                if result.markdown or result.images:
                    return result
                errors.append(f"{type(parser).__name__}: 输出为空")
            except Exception as exc:  # noqa: BLE001 - 链式解析需吞掉单个解析器异常
                errors.append(f"{type(parser).__name__}: {exc}")
                logger.debug("FirstParser %s -> %s failed: %s", type(parser).__name__, path, exc)
        raise ParserError("; ".join(errors))


class PipelineParser(BaseParser):
    """把子解析器输出按顺序拼接成一份文档。

    例如 PDF 的每页文本可以分段解析后拼接，或者 doc -> docx 转换链。
    若某子解析器失败，跳过它继续（记录 error_type）。
    """

    def __init__(self, parsers: list[BaseParser], parse_options: dict | None = None):
        super().__init__(parse_options)
        self.parsers = parsers
        ext_set: set[str] = set()
        for p in parsers:
            ext_set.update(p.supported_file_types)
        self.supported_file_types = sorted(ext_set)

    def parse(self, path: str) -> ParseResult:
        merged = ParseResult()
        for parser in self.parsers:
            try:
                result = parser.parse(path)
                merged.merge(result)
            except Exception as exc:  # noqa: BLE001
                merged.error_type = f"{type(parser).__name__}: {exc}"
                logger.debug("PipelineParser skip %s: %s", type(parser).__name__, exc)
        return merged


__all__ = ["FirstParser", "PipelineParser"]
