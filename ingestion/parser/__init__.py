"""文档解析引擎（内嵌库形态）。

统一把各类文档解析为 Markdown，并提取内嵌图片（base64 字典）。
支持格式（Q2 核心办公格式 + Q11 图片直传）：
    .pdf .docx .doc .pptx .xlsx .md .txt .html
    .jpg .jpeg .png .gif .bmp .tiff .webp

架构：引擎注册表 + 链式解析器（FirstParser/PipelineParser），
扫描 PDF 逐页路由（文本页直出 / 扫描页渲染后走 OCR），doc/ppt 老格式经
LibreOffice 转新格式后复用对应解析器。图片不直接写回 markdown，统一收集到
Document.images（ref_path -> base64），由摄取主链路上传 MinIO。

OCR（可选）：PaddleOCR 经典版，CPU 推理；未安装或 ocr_enabled=False 时
扫描页降级为 "[扫描页 N]" 占位文本。
"""

from __future__ import annotations

import logging
from pathlib import Path

from ingestion.parser.base_parser import BaseParser, ParserError, ParseResult
from ingestion.parser.image_parser import ImageParser
from ingestion.parser.registry import ParserEngineRegistry, get_parser_engine

logger = logging.getLogger(__name__)

# 支持的扩展名（后端 ALLOWED_EXTENSIONS 与其保持一致，见 api/main.py）
ALLOWED_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".pptx", ".xlsx",
    ".md", ".txt", ".html",
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp",
}
# markdown 图片行正则（切块前剔除，防止 embedding 被图片路径污染）
MARKDOWN_IMAGE_LINE_RE = r"^!\[[^\]]*\]\([^)]*\)\s*$"


def parse_document(
    path: str,
    file_type: str | None = None,
    engine: str = "builtin",
    parse_options: dict | None = None,
) -> ParseResult:
    """解析单个文档为 Markdown + 提取图片，返回 ParseResult。

    Args:
        path: 文档磁盘路径。
        file_type: 文件类型（扩展名）。缺省由路径后缀推断。
        engine: 解析引擎名（"builtin" 或 "markitdown"；缺省 builtin）。
        parse_options: 解析选项透传（如 ocr_enabled）。

    Returns:
        ParseResult(markdown, images, image_refs, error_type)。

    Raises:
        ParserError: 无匹配解析器 / 解析器抛出未捕获异常。
    """
    ext = (file_type or Path(path).suffix).lower()
    registry: ParserEngineRegistry = get_parser_engine(engine)
    parser = registry.get_parser(ext, parse_options or {"file_name": Path(path).name})
    if parser is None:
        raise ParserError(f"不支持的文件类型: {ext}（可选: {sorted(ALLOWED_EXTENSIONS)}）")
    try:
        result = parser.parse(path)
    except ParserError:
        raise
    except Exception as exc:  # 解析器内部异常统一包装，保留可读错误信息
        logger.warning("parse %s failed with %s: %s", path, type(exc).__name__, exc)
        raise ParserError(f"解析失败: {exc}") from exc
    return result


__all__ = [
    "ALLOWED_EXTENSIONS",
    "MARKDOWN_IMAGE_LINE_RE",
    "BaseParser",
    "ParserError",
    "ParseResult",
    "ImageParser",
    "ParserEngineRegistry",
    "get_parser_engine",
    "parse_document",
]
