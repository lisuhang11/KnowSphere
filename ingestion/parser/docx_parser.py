"""docx 专用解析器（docx 专用解析器）。

用 python-docx 提取段落、表格、图片：
- 段落按样式级别映射为 markdown 标题/列表/引用；
- 表格转为 markdown 表格；
- 内嵌图片提取为 base64（_embed_image 收集），正文保留占位引用。
"""

from __future__ import annotations

import io
import logging

from ingestion.parser.base_parser import BaseParser, ParserError, ParseResult

logger = logging.getLogger(__name__)

# 内置标题样式名 -> markdown 层级
_HEADING_STYLES = {
    "Title": "# ",
    "Subtitle": "## ",
    "Heading 1": "# ",
    "Heading 2": "## ",
    "Heading 3": "### ",
    "Heading 4": "#### ",
    "Heading 5": "##### ",
    "Heading 6": "###### ",
    "标题": "# ",
    "标题 1": "# ",
    "标题 2": "## ",
    "标题 3": "### ",
    "标题 4": "#### ",
    "标题 5": "##### ",
    "标题 6": "###### ",
}


class DocxParser(BaseParser):
    supported_file_types = ["docx"]

    def parse(self, path: str) -> ParseResult:
        try:
            import docx  # python-docx
        except ImportError as exc:
            raise ParserError("python-docx 未安装，请执行 pip install python-docx") from exc

        try:
            document = docx.Document(path)
        except Exception as exc:  # noqa: BLE001
            raise ParserError(f"python-docx 打开失败: {exc}") from exc

        result = ParseResult()
        parts: list[str] = []
        body = document.element.body
        # 按文档流顺序遍历段落与表格（python-docx 的 iter_inner_content 在新版本可用）
        for child in body.iterchildren():
            tag = child.tag.rsplit("}", 1)[-1]
            try:
                if tag == "p":
                    self._handle_paragraph(document, child, parts, result)
                elif tag == "tbl":
                    self._handle_table(document, child, parts, result)
            except Exception as exc:  # noqa: BLE001
                logger.debug("docx block %s failed: %s", tag, exc)

        result.markdown = "\n\n".join(p for p in parts if p and p.strip()).strip()
        if not result.markdown:
            raise ParserError("docx 未解析出任何文本")
        return result

    def _handle_paragraph(self, document, p_element, parts: list[str], result: ParseResult) -> None:
        from docx.text.paragraph import Paragraph

        para = Paragraph(p_element, document)
        text = para.text.strip()
        if not text and not para.runs:
            return
        style_name = (para.style.name if para.style is not None else "") or ""
        prefix = _HEADING_STYLES.get(style_name, "")
        if not prefix and style_name.lower().startswith("heading"):
            level = style_name.split()[-1]
            if level.isdigit():
                prefix = "#" * min(int(level), 6) + " "
        if not prefix:
            if style_name.lower() in {"list bullet", "list bullet 2", "list bullet 3"}:
                indent = style_name.split()[-1]
                prefix = ("  " * (int(indent) - 1 if indent.isdigit() else 0)) + "- "
            elif style_name.lower() in {"list number", "list number 2", "list number 3"}:
                prefix = "1. "
            elif style_name.lower() == "quote":
                prefix = "> "
            elif style_name.lower() in {"caption", "figure"}:
                prefix = ""
        line = prefix + self._extract_runs_text(document, para)
        # 图片 run（inline shapes）在文本中不体现，单独收集
        line = self._extract_inline_images(document, para, line, result)
        if line:
            parts.append(line)

    def _extract_runs_text(self, document, para) -> str:
        return para.text or ""

    def _extract_inline_images(self, document, para, line: str, result: ParseResult) -> str:
        """提取段落内嵌图片，在文本末尾追加占位引用。"""
        try:
            shapes = para._element.xpath(".//w:drawing//a:blip/@r:embed")  # noqa: SLF001
            if not shapes:
                return line
            rels = document.part.rels
            for rid in shapes:
                try:
                    rel = rels[rid]
                    image_part = rel.target_part
                    data = image_part.blob
                    mime = getattr(image_part, "content_type", "image/png") or "image/png"
                    line += ("\n" if line else "") + self._embed_image(
                        result, data, mime, original_ref=rid
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("docx image %s failed: %s", rid, exc)
        except Exception as exc:  # noqa: BLE001
            logger.debug("docx inline image scan failed: %s", exc)
        return line

    def _handle_table(self, document, tbl_element, parts: list[str], result: ParseResult) -> None:
        from docx.table import Table

        try:
            table = Table(tbl_element, document)
        except Exception as exc:  # noqa: BLE001
            logger.debug("docx table open failed: %s", exc)
            return
        rows: list[str] = []
        for i, row in enumerate(table.rows):
            cells = [" ".join(c.text.split()) for c in row.cells]
            rows.append("| " + " | ".join(cells) + " |")
            if i == 0:
                rows.append("| " + " | ".join("---" for _ in cells) + " |")
        if rows:
            parts.append("\n".join(rows))


__all__ = ["DocxParser"]
