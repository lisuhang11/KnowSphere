"""xlsx 解析器（xlsx 解析器（pandas））。

每个 sheet 转成 Markdown 表格；sheet 名作为二级标题。隐藏 sheet / 空 sheet 跳过。
"""

from __future__ import annotations

import logging

from ingestion.parser.base_parser import BaseParser, ParserError, ParseResult

logger = logging.getLogger(__name__)


class ExcelParser(BaseParser):
    supported_file_types = ["xlsx"]

    def parse(self, path: str) -> ParseResult:
        try:
            import pandas as pd
        except ImportError as exc:
            raise ParserError("pandas 未安装，请执行 pip install pandas openpyxl") from exc

        try:
            excel_file = pd.ExcelFile(path, engine="openpyxl")
        except Exception as exc:  # noqa: BLE001
            raise ParserError(f"openpyxl 打开失败: {exc}") from exc

        parts: list[str] = []
        for sheet_name in excel_file.sheet_names:
            try:
                df = excel_file.parse(sheet_name)
            except Exception as exc:  # noqa: BLE001
                logger.debug("xlsx sheet %s failed: %s", sheet_name, exc)
                continue
            if df is None or df.empty:
                continue
            # 统一为字符串，空单元格留空
            df = df.fillna("")
            df = df.astype(str)
            rows: list[str] = []
            header = "| " + " | ".join(str(c) for c in df.columns) + " |"
            rows.append(header)
            rows.append("| " + " | ".join("---" for _ in df.columns) + " |")
            for _, row in df.iterrows():
                rows.append("| " + " | ".join(v.strip() for v in row) + " |")
            parts.append(f"## {sheet_name}\n\n" + "\n".join(rows))

        if not parts:
            raise ParserError("xlsx 未解析出任何 sheet")
        return ParseResult(markdown="\n\n".join(parts).strip())


__all__ = ["ExcelParser"]
