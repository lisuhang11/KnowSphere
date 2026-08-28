"""html 解析器（html 解析器（BeautifulSoup））。

- 提取 title 为一级标题；
- 表格转 markdown 表格；
- 图片提取 base64；链接保留 markdown 语法；脚本/样式剔除。
"""

from __future__ import annotations

import logging
import re

from ingestion.parser.base_parser import BaseParser, ParserError, ParseResult

logger = logging.getLogger(__name__)


class HTMLParser(BaseParser):
    supported_file_types = ["html", "htm"]

    def parse(self, path: str) -> ParseResult:
        try:
            from bs4 import BeautifulSoup
        except ImportError as exc:
            raise ParserError("beautifulsoup4 未安装，请执行 pip install beautifulsoup4") from exc

        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError as exc:
            raise ParserError(f"读取文件失败: {exc}") from exc

        soup = BeautifulSoup(content, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()

        result = ParseResult()
        parts: list[str] = []

        if soup.title and soup.title.string and soup.title.string.strip():
            parts.append(f"# {soup.title.string.strip()}")

        body = soup.body or soup
        for el in body.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "table", "img", "li", "pre", "blockquote"], recursive=True):
            if el.name in ("h1", "h2", "h3", "h4", "h5", "h6"):
                level = int(el.name[1])
                text = el.get_text(" ", strip=True)
                if text:
                    parts.append("#" * level + " " + text)
            elif el.name == "p":
                text = self._clean_text(el.get_text(" ", strip=True))
                if text:
                    parts.append(text)
            elif el.name == "table":
                rows: list[str] = []
                for i, tr in enumerate(el.find_all("tr")):
                    cells = [self._clean_text(c.get_text(" ", strip=True)) for c in tr.find_all(["td", "th"])]
                    if cells:
                        rows.append("| " + " | ".join(cells) + " |")
                        if i == 0:
                            rows.append("| " + " | ".join("---" for _ in cells) + " |")
                if rows:
                    parts.append("\n".join(rows))
            elif el.name == "img":
                src = el.get("src", "") or ""
                alt = el.get("alt", "") or ""
                md_img = self._handle_img(result, src, alt)
                if md_img:
                    parts.append(md_img)
            elif el.name == "li":
                text = self._clean_text(el.get_text(" ", strip=True))
                if text:
                    parts.append("- " + text)
            elif el.name == "pre":
                text = el.get_text("\n").strip()
                if text:
                    parts.append("```\n" + text + "\n```")
            elif el.name == "blockquote":
                text = self._clean_text(el.get_text(" ", strip=True))
                if text:
                    parts.append("> " + text)

        if not parts:
            raise ParserError("html 未解析出任何文本")
        result.markdown = "\n\n".join(p for p in parts if p).strip()
        return result

    def _handle_img(self, result: ParseResult, src: str, alt: str) -> str:
        if src.startswith("data:image/"):
            try:
                import base64
                payload = src.split(",", 1)[1]
                data = base64.b64decode(payload)
                mime = re.match(r"data:([^;]+)", src)
                mime_type = mime.group(1) if mime else "image/png"
                return self._embed_image(result, data, mime_type, original_ref=alt or "image")
            except Exception as exc:  # noqa: BLE001
                logger.debug("html base64 img failed: %s", exc)
                return f"![{alt}]({src})"
        # 外部图片：保留链接，不下载
        return f"![{alt}]({src})"

    @staticmethod
    def _clean_text(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()


__all__ = ["HTMLParser"]
