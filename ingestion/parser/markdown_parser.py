"""markdown / txt 解析器（markdown / txt 解析器）。

- 读取原始文本，保留原样（Markdown 本身即目标格式）；
- 规范化管道标记表格：
  把 `| a | b |\n| --- | --- |\n| c | d |` 中不足的分隔行补齐；
- 提取内嵌 base64 图片（data:image/...）到 ParseResult.images，正文替换为占位引用。
"""

from __future__ import annotations

import base64
import logging
import re

from ingestion.parser.base_parser import BaseParser, ParserError, ParseResult

logger = logging.getLogger(__name__)

_BASE64_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\((data:image/[^;]+;base64,[^)]+)\)", re.IGNORECASE)


class MarkdownParser(BaseParser):
    supported_file_types = ["md", "markdown", "txt"]

    def parse(self, path: str) -> ParseResult:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError as exc:
            raise ParserError(f"读取文件失败: {exc}") from exc

        if not content.strip():
            raise ParserError("文件内容为空")

        result = ParseResult()
        content = self._extract_base64_images(content, result)
        content = self._normalize_pipe_tables(content)
        result.markdown = content.strip()
        return result

    def _extract_base64_images(self, content: str, result: ParseResult) -> str:
        """把 markdown 内联 base64 图片提取为文件引用，避免正文携带巨型 base64。"""

        def repl(match: re.Match) -> str:
            alt = match.group(1)
            data_uri = match.group(2)
            try:
                payload = data_uri.split(",", 1)[1]
                data = base64.b64decode(payload)
                mime = re.match(r"data:([^;]+)", data_uri)
                mime_type = mime.group(1) if mime else "image/png"
                ref_path = self._add_image(result, data, mime_type, original_ref=alt)
                return f"![{alt or ref_path}]({ref_path})"
            except Exception as exc:  # noqa: BLE001
                logger.debug("base64 image decode failed: %s", exc)
                return match.group(0)

        return _BASE64_IMAGE_RE.sub(repl, content)

    @staticmethod
    def _normalize_pipe_tables(content: str) -> str:
        """规范化管道表格：分隔行缺失时补齐 `| --- | --- |`。"""
        lines = content.splitlines()
        out: list[str] = []
        for i, line in enumerate(lines):
            stripped = line.strip()
            # 表格行：以 | 开头/结尾且含至少一个 |
            is_table_row = stripped.startswith("|") or stripped.endswith("|") or (
                stripped.count("|") >= 2 and "|" in stripped
            )
            out.append(line)
            if not is_table_row or not stripped.startswith("|"):
                continue
            cell_count = stripped.count("|") - 1
            if cell_count < 1:
                continue
            # 下一行是否为分隔行或数据行
            nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
            is_separator = bool(re.fullmatch(r"\|?[\s:|-]+\|?", nxt)) and "|" in nxt
            is_data = nxt.startswith("|") or nxt.endswith("|")
            if is_separator or is_data:
                continue
            # 插入分隔行
            out.append("| " + " | ".join("---" for _ in range(cell_count)) + " |")
        return "\n".join(out)


__all__ = ["MarkdownParser"]
