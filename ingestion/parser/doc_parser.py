"""doc 老格式解析器（LibreOffice 转换，Q4：仅 LibreOffice 依赖）。

doc -> docx 转换后复用 Docx2Parser 解析：
- 依赖系统安装的 LibreOffice（soffice 命令），Dockerfile 已安装。
- 转换输出到临时目录，解析后清理。
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from ingestion.parser.base_parser import BaseParser, ParserError, ParseResult

logger = logging.getLogger(__name__)


class DocParser(BaseParser):
    supported_file_types = ["doc"]

    def __init__(self, parse_options: dict | None = None):
        super().__init__(parse_options)
        self._soffice: str | None = None

    def _find_soffice(self) -> str:
        if self._soffice:
            return self._soffice
        candidate = shutil.which("soffice") or shutil.which("libreoffice")
        if not candidate:
            raise ParserError("未找到 LibreOffice（soffice），doc 格式需要 LibreOffice 支持")
        self._soffice = candidate
        return candidate

    def _convert_doc_to_docx(self, path: str, out_dir: str) -> str:
        soffice = self._find_soffice()
        cmd = [
            soffice, "--headless", "--convert-to", "docx", "--outdir", out_dir, path,
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ParserError("LibreOffice 转换 doc 超时") from exc
        except Exception as exc:  # noqa: BLE001
            raise ParserError(f"LibreOffice 调用失败: {exc}") from exc
        if proc.returncode != 0:
            raise ParserError(f"LibreOffice 转换失败: {proc.stderr[-500:]}")
        converted = Path(out_dir) / (Path(path).stem + ".docx")
        if not converted.exists():
            raise ParserError("LibreOffice 转换 doc 未生成 docx")
        return str(converted)

    def parse(self, path: str) -> ParseResult:
        from ingestion.parser.docx2_parser import Docx2Parser

        with tempfile.TemporaryDirectory(prefix="docparser_") as tmp:
            docx_path = self._convert_doc_to_docx(path, tmp)
            parser = Docx2Parser(parse_options=self.parse_options)
            return parser.parse(docx_path)


__all__ = ["DocParser"]
