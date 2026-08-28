"""解析器抽象基类与解析结果数据模型。

- ParseResult：解析输出（markdown + images + image_refs + error_type），替代
  Document/ParseResult 两级模型，内嵌库形态下直接返回给摄取链路。
- ImageRef：图片引用元数据（文件名/原始引用/存储 key），供主链路上传 MinIO 后
  记录到 documents 元数据（markdown 留引用、元数据存 storage_key）。
- ParserError：解析失败统一异常（含"不支持的文件类型"）。
"""

from __future__ import annotations

import base64
import logging
import re
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ParserError(Exception):
    """解析失败统一异常。"""


@dataclass
class ImageRef:
    """单张图片的引用元数据。

    filename:     图片文件名（如 "image_1.jpg"）。
    original_ref: 源文档中的原始引用（如 "Figure 1" / "images/pic.png"）。
    mime_type:    MIME 类型（image/jpeg 等）。
    storage_key:  MinIO 存储 key（由主链路填充；解析阶段为空串）。
    data:         base64 解码后的原始字节（供上传/OCR，不入库）。
    """

    filename: str
    original_ref: str = ""
    mime_type: str = ""
    storage_key: str = ""
    data: bytes = b""

    def to_dict(self) -> dict[str, Any]:
        """序列化为可入库的 dict（不含 data 字节）。"""
        return {
            "filename": self.filename,
            "original_ref": self.original_ref,
            "mime_type": self.mime_type,
            "storage_key": self.storage_key,
        }


@dataclass
class ParseResult:
    """一次文档解析的输出。

    markdown:   最终 Markdown 文本（图片位置保留 `![name](images/xxx.jpg)` 占位引用）。
    images:     {ref_path: base64}，与 markdown 中占位引用的文件名一一对应。
    image_refs: 图片引用元数据列表（上传 MinIO 后主链路填充 storage_key）。
    error_type: 部分解析器（PDF 扫描页等）未达 100% 成功时记录原因；None 表示完整成功。
    """

    markdown: str = ""
    images: dict[str, str] = field(default_factory=dict)
    image_refs: list[ImageRef] = field(default_factory=list)
    error_type: str | None = None

    def merge(self, other: "ParseResult") -> None:
        """合并另一份 ParseResult（供 PipelineParser 逐段拼接）。"""
        if other.markdown:
            self.markdown = (self.markdown + "\n\n" + other.markdown).strip() if self.markdown else other.markdown
        self.images.update(other.images)
        self.image_refs.extend(other.image_refs)
        if other.error_type and not self.error_type:
            self.error_type = other.error_type


def decode_base64_image(data_uri: str) -> tuple[bytes, str]:
    """解码 data URI（data:image/png;base64,...）为 (bytes, mime_type)。

    兼容裸 base64 字符串（无 data: 前缀）。
    """
    mime = "image/png"
    payload = data_uri
    if data_uri.startswith("data:"):
        header, _, payload = data_uri.partition(",")
        m = re.match(r"data:([^;]+)", header)
        if m:
            mime = m.group(1).strip()
    return base64.b64decode(payload), mime


def make_image_filename(original: str, mime_type: str, idx: int) -> str:
    """生成图片文件名：优先保留原始名（去路径），否则用 {uuid}.{ext}。

    ext 从 mime_type 推断（image/png -> png），未知回退 jpg。
    """
    name = Path(original).name if isinstance(original, str) and original else ""
    if name and "." in name:
        return name
    ext_map = {
        "image/jpeg": "jpg", "image/png": "png", "image/gif": "gif",
        "image/webp": "webp", "image/bmp": "bmp", "image/tiff": "tiff",
    }
    ext = ext_map.get(mime_type, "jpg")
    return f"image_{idx}_{uuid.uuid4().hex[:8]}.{ext}"


class BaseParser(ABC):
    """解析器抽象基类。

    子类需实现 parse()，返回 ParseResult。解析中提取的图片以 base64 收集到
    ParseResult.images（ref_path -> base64），markdown 内以
    `![name](images/xxx.jpg)` 形式占位，主链路统一上传 MinIO（Q5/Q12）。

    supported_file_types: 支持的扩展名列表（如 ["docx", "pdf"]，不带点）。
    parse_options: 可选解析选项（如 ocr_enabled、ocr_lang）。
    """

    supported_file_types: list[str] = []
    parse_options: dict[str, Any] = {}

    def __init__(self, parse_options: dict[str, Any] | None = None):
        if parse_options:
            self.parse_options = parse_options

    @abstractmethod
    def parse(self, path: str) -> ParseResult:
        """解析 path 指向的文档，返回 ParseResult。失败抛 ParserError。"""

    # ---------- 图片收集工具（子类复用） ----------

    def _add_image(
        self,
        result: ParseResult,
        data: bytes,
        mime_type: str,
        original_ref: str = "",
        ref_name: str | None = None,
    ) -> str:
        """将图片字节加入 ParseResult，返回 markdown 占位引用名（含 images/ 前缀）。

        参考名重复时自动加序号（_1/_2/...），避免覆盖。
        """
        idx = len(result.images) + 1
        filename = ref_name or make_image_filename(original_ref, mime_type, idx)
        base_name, dot, ext = filename.rpartition(".")
        if not dot:
            ext_map = {
                "image/jpeg": "jpg", "image/png": "png", "image/gif": "gif",
                "image/webp": "webp", "image/bmp": "bmp", "image/tiff": "tiff",
            }
            ext = ext_map.get(mime_type, "jpg")
            base_name = filename
        unique = filename
        n = 2
        while f"images/{unique}" in result.images:
            unique = f"{base_name}_{n}.{ext}"
            n += 1
        ref_path = f"images/{unique}"
        result.images[ref_path] = base64.b64encode(data).decode("ascii")
        result.image_refs.append(
            ImageRef(filename=unique, original_ref=original_ref or unique, mime_type=mime_type, data=data)
        )
        return ref_path

    def _embed_image(
        self,
        result: ParseResult,
        data: bytes,
        mime_type: str,
        original_ref: str = "",
        ref_name: str | None = None,
        alt: str = "",
    ) -> str:
        """收集图片并返回 markdown 图片语法 `![alt](images/xxx.jpg)`。"""
        ref_path = self._add_image(result, data, mime_type, original_ref, ref_name)
        return f"![{alt or ref_path}]({ref_path})"
