"""PDF 逐页路由解析器（内嵌简化版）。

设计要点：
* 每页独立分类为 "text"（原生文本层）或 "scanned"（扫描页），以图片面积占比为
  主导信号（而非字符数），混合 PDF 正确路由；
* 文本页：提取文本层（可选用几何 XY-cut 重建阅读顺序，多栏页按栏线性化），
  跨页重复的页眉页脚去除，隐藏文本/图表残留过滤；
* 扫描页：渲染成 JPEG 收集为图片，并调用 OCR（PaddleOCR 经典版，Q6）识别文字
  回填 markdown；OCR 不可用时降级为 "[扫描页 N]" 占位；
* 内嵌图片（图/表/矢量图区域）按尺寸/重复度过滤后收集为图片引用（Q5/Q12）。

pdfium（pypdfium2）非线程安全：所有 pdfium 操作串行化在全局锁内。
（内嵌库场景下 Celery worker 单进程处理文档，此处保留锁以防未来并发调用。）
"""

from __future__ import annotations

import base64
import io
import logging
import os
import re
import statistics
import threading

from ingestion.parser.base_parser import BaseParser, ParserError, ParseResult
from ingestion.parser.ocr import ocr_image_bytes

logger = logging.getLogger(__name__)

# pdfium 进程级共享状态非线程安全：所有 pdfium 触达必须在此锁内。
_PDFIUM_LOCK = threading.Lock()

# --- 扫描页分类阈值（可环境变量覆盖） ---
SCAN_IMAGE_AREA_RATIO = float(os.environ.get("DOCREADER_PDF_SCAN_IMAGE_RATIO", 0.5))
SCAN_MIN_CHARS_PER_PAGE = int(os.environ.get("DOCREADER_PDF_SCAN_MIN_CHARS", 10))
_LOW_TEXT_IMAGE_RATIO = 0.1

# --- 内嵌图片过滤 ---
EXTRACT_EMBEDDED_IMAGES = os.environ.get("DOCREADER_PDF_EXTRACT_EMBEDDED_IMAGES", "1").lower() in {"1", "true", "yes", "on"}
EMBED_MIN_PIXELS = int(os.environ.get("DOCREADER_PDF_EMBED_MIN_PIXELS", 80))
EMBED_MIN_AREA_RATIO = float(os.environ.get("DOCREADER_PDF_EMBED_MIN_AREA_RATIO", 0.01))
EMBED_REPEAT_PAGE_FRAC = float(os.environ.get("DOCREADER_PDF_EMBED_REPEAT_PAGE_FRAC", 0.5))
EMBED_MAX_IMAGES = int(os.environ.get("DOCREADER_PDF_EMBED_MAX_IMAGES", 50))

# --- 布局感知文本提取 ---
LAYOUT_ORDERING = os.environ.get("DOCREADER_PDF_LAYOUT_ORDERING", "1").lower() in {"1", "true", "yes", "on"}
WORD_GAP_WIDTH_RATIO = float(os.environ.get("DOCREADER_PDF_WORD_GAP_WIDTH_RATIO", 0.4))
DETECT_HEADINGS = os.environ.get("DOCREADER_PDF_DETECT_HEADINGS", "1").lower() in {"1", "true", "yes", "on"}
FILTER_HIDDEN_TEXT = os.environ.get("DOCREADER_PDF_FILTER_HIDDEN_TEXT", "1").lower() in {"1", "true", "yes", "on"}
MARGIN_COL_WIDTH_RATIO = float(os.environ.get("DOCREADER_PDF_MARGIN_COL_WIDTH_RATIO", 0.12))
MIN_HEADING_LINE_CHARS = int(os.environ.get("DOCREADER_PDF_MIN_HEADING_LINE_CHARS", 8))
SANITIZE_PDF_TEXT = os.environ.get("DOCREADER_PDF_SANITIZE_TEXT", "1").lower() in {"1", "true", "yes", "on"}
STRIP_CHART_TEXT_DEBRIS = os.environ.get("DOCREADER_PDF_STRIP_CHART_DEBRIS", "1").lower() in {"1", "true", "yes", "on"}

# 渲染参数
RENDER_DPI = int(os.environ.get("DOCREADER_PDF_RENDER_DPI", 150))
JPEG_QUALITY = int(os.environ.get("DOCREADER_PDF_JPEG_QUALITY", 80))
RENDER_MAX_EDGE = int(os.environ.get("DOCREADER_PDF_RENDER_MAX_EDGE", 2400))
FORCE_SCANNED_PDF = os.environ.get("DOCREADER_PDF_FORCE_SCANNED", "0").lower() in {"1", "true", "yes", "on"}

# pdfium 常见占位字形清理
_PDF_ARTIFACT_RE = re.compile(r"[\u00ad\u200b-\u200f\ufeff\ufffe\uffff]")
_PDF_ARTIFACT_JOIN_RE = re.compile(r"(\w)[\u00ad\ufffe](\w)")
_CHART_DEBRIS_LINE_RE = re.compile(
    r"^(?:[\d\s.]+|\d{1,2}|\d+-layer|iter\.\s*\(1e4\)|(?:training|test)\s+error\s*\(%\))$",
    re.IGNORECASE,
)
_ARXIV_LINE_RE = re.compile(r"^arXiv:\s*\S+", re.IGNORECASE)
_PAGE_NUM_LINE_RE = re.compile(r"^\d{1,3}$")


def _close(resource) -> None:
    close = getattr(resource, "close", None)
    if close:
        close()


def _sanitize_pdf_text(text: str) -> str:
    if not text:
        return text
    text = _PDF_ARTIFACT_RE.sub("", text)
    return _PDF_ARTIFACT_JOIN_RE.sub(r"\1\2", text)


def _is_chart_debris_line(line: str) -> bool:
    t = line.strip()
    if not t:
        return False
    if _CHART_DEBRIS_LINE_RE.match(t):
        return True
    if re.fullmatch(r"[\d\s.()-]+", t) and len(t) <= 24 and sum(c.isdigit() for c in t) >= 3:
        return True
    return False


def _strip_chart_text_debris(text: str) -> str:
    if not text:
        return text
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        if _is_chart_debris_line(lines[i]):
            j = i
            while j < len(lines) and (_is_chart_debris_line(lines[j]) or not lines[j].strip()):
                j += 1
            if j - i >= 3:
                i = j
                continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def _strip_arxiv_and_page_num_lines(text: str) -> str:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    kept: list[str] = []
    for ln in lines:
        t = ln.strip()
        if _ARXIV_LINE_RE.match(t) or _PAGE_NUM_LINE_RE.match(t):
            continue
        if "arXiv:" in ln:
            ln = re.sub(r"\s*arXiv:\s*\S+\s*(?:\[[^\]]+\])?\s*[^\n]*", "", ln).strip()
            if not ln:
                continue
        kept.append(ln)
    return "\n".join(kept)


def _postprocess_pdf_text(text: str) -> str:
    if SANITIZE_PDF_TEXT:
        text = _sanitize_pdf_text(text)
    text = _strip_arxiv_and_page_num_lines(text)
    if STRIP_CHART_TEXT_DEBRIS:
        text = _strip_chart_text_debris(text)
    return text


# --- 几何布局（XY-cut 阅读顺序重建） ---

def _collect_invisible_boxes(page, raw) -> list[tuple[float, float, float, float]]:
    boxes: list = []
    try:
        for obj in page.get_objects():
            if obj.type != raw.FPDF_PAGEOBJ_TEXT:
                continue
            try:
                mode = raw.FPDFTextObj_GetTextRenderMode(obj.raw)
            except Exception:
                continue
            if mode != raw.FPDF_TEXTRENDERMODE_INVISIBLE:
                continue
            try:
                left, bottom, right, top = obj.get_bounds()
            except Exception:
                continue
            boxes.append((min(left, right), min(bottom, top), max(left, right), max(bottom, top)))
    except Exception:
        return []
    return boxes


def _point_in_boxes(x: float, y: float, boxes: list) -> bool:
    for x0, y0, x1, y1 in boxes:
        if x0 <= x <= x1 and y0 <= y <= y1:
            return True
    return False


def _page_chars(textpage, page, raw) -> tuple[list, float]:
    """返回 (chars, page_width)；过滤隐藏/页外字形。"""
    n = textpage.count_chars()
    if n <= 0:
        return [], 0.0
    width, height = page.get_size()
    invisible = _collect_invisible_boxes(page, raw) if FILTER_HIDDEN_TEXT else []
    chars: list = []
    for i in range(n):
        try:
            left, bottom, right, top = textpage.get_charbox(i)
        except Exception:
            continue
        ch = textpage.get_text_range(i, 1)
        if ch in ("\r", "\n"):
            continue
        x0, x1 = (left, right) if left <= right else (right, left)
        y0, y1 = (bottom, top) if bottom <= top else (top, bottom)
        if FILTER_HIDDEN_TEXT:
            if x1 < 0 or x0 > width or y1 < 0 or y0 > height:
                continue
            if invisible and _point_in_boxes((x0 + x1) / 2, (y0 + y1) / 2, invisible):
                continue
        chars.append({"x0": x0, "y0": y0, "x1": x1, "y1": y1, "ch": ch})
    return chars, width


def _find_split(items: list, axis: str, min_gap: float):
    lo, hi = ("x0", "x1") if axis == "x" else ("y0", "y1")
    intervals = sorted(((s[lo], s[hi]) for s in items), key=lambda iv: iv[0])
    cur_end = intervals[0][1]
    best_gap, best_cut = 0.0, None
    for a, b in intervals[1:]:
        gap = a - cur_end
        if gap >= min_gap and gap > best_gap:
            best_gap, best_cut = gap, cur_end + gap / 2
        if b > cur_end:
            cur_end = b
    return best_cut


def _split_columns(chars: list, scale: float, width: float, depth: int = 0) -> list:
    if len(chars) <= 1 or depth > 10:
        return [chars]
    min_gap = max(scale * 2.5, width * 0.04)
    cut = _find_split(chars, "x", min_gap)
    if cut is None:
        return [chars]
    left = [c for c in chars if (c["x0"] + c["x1"]) / 2 < cut]
    right = [c for c in chars if (c["x0"] + c["x1"]) / 2 >= cut]
    if not left or not right:
        return [chars]
    return _split_columns(left, scale, width, depth + 1) + _split_columns(right, scale, width, depth + 1)


def _column_x_span(chars: list) -> float:
    if not chars:
        return 0.0
    return max(c["x1"] for c in chars) - min(c["x0"] for c in chars)


def _is_artifact_column(chars: list, width: float) -> bool:
    if not chars or width <= 0:
        return True
    span = _column_x_span(chars)
    if span <= 0:
        return True
    ys = [(c["y0"] + c["y1"]) / 2 for c in chars]
    y_span = max(ys) - min(ys)
    lines = _group_lines(chars)
    single_frac = sum(1 for ln in lines if len(ln["text"]) <= 2) / len(lines) if lines else 0.0
    narrow = span / width < MARGIN_COL_WIDTH_RATIO
    if narrow and single_frac >= 0.45:
        return True
    if y_span > span * 3.5 and len(chars) >= 8 and single_frac >= 0.35:
        return True
    return False


def _filter_reading_columns(chars: list, scale: float, width: float) -> list:
    cols = _split_columns(chars, scale, width)
    kept = [c for c in cols if not _is_artifact_column(c, width)]
    if kept:
        return kept
    if len(cols) > 1:
        return [max(cols, key=_column_x_span)]
    return cols


def _join_line_glyphs(ln_sorted: list) -> str:
    if not ln_sorted:
        return ""
    widths = [c["x1"] - c["x0"] for c in ln_sorted if c["x1"] > c["x0"]]
    med_w = statistics.median(widths) if widths else 1.0
    gap_threshold = med_w * WORD_GAP_WIDTH_RATIO
    parts: list[str] = []
    for i, cur in enumerate(ln_sorted):
        ch = cur["ch"]
        if i == 0:
            parts.append(ch)
            continue
        prev = ln_sorted[i - 1]
        if ch.isspace() or prev["ch"].isspace():
            if not ch.isspace() or (parts and not parts[-1].endswith(" ")):
                parts.append(ch)
            continue
        if cur["x0"] - prev["x1"] > gap_threshold:
            parts.append(" ")
        parts.append(ch)
    return "".join(parts).strip()


def _group_lines(chars: list) -> list:
    if not chars:
        return []
    heights = [c["y1"] - c["y0"] for c in chars if c["y1"] - c["y0"] > 0]
    med_h = statistics.median(heights) if heights else 1.0
    ordered = sorted(chars, key=lambda c: -(c["y0"] + c["y1"]) / 2)
    lines: list = []
    cur: list = []
    ref = None
    for c in ordered:
        yc = (c["y0"] + c["y1"]) / 2
        if ref is None or abs(yc - ref) <= 0.5 * med_h:
            cur.append(c)
            ref = yc if ref is None else ref
        else:
            lines.append(cur)
            cur = [c]
            ref = yc
    if cur:
        lines.append(cur)
    out: list = []
    for ln in lines:
        ln_sorted = sorted(ln, key=lambda c: c["x0"])
        text = _join_line_glyphs(ln_sorted)
        if not text:
            continue
        hs = [c["y1"] - c["y0"] for c in ln_sorted if c["y1"] - c["y0"] > 0]
        out.append({"h": statistics.median(hs) if hs else med_h, "text": text})
    return out


def _segments_to_markdown(lines: list) -> str:
    if not lines:
        return ""
    body = statistics.median([ln["h"] for ln in lines])

    def level(ln) -> int:
        txt = ln["text"]
        if not DETECT_HEADINGS or body <= 0 or len(txt) > 80 or len(txt) < MIN_HEADING_LINE_CHARS:
            return 0
        if txt[-1:] in ".。!！?？,，;；:：":
            return 0
        r = ln["h"] / body
        if r >= 2.0:
            return 1
        if r >= 1.6:
            return 2
        if r >= 1.35:
            return 3
        return 0

    levels = [level(ln) for ln in lines]
    if sum(1 for x in levels if x) > max(1, int(0.4 * len(lines))):
        levels = [0] * len(lines)
    return "\n".join(
        ("#" * lv + " " + ln["text"]) if lv else ln["text"] for ln, lv in zip(lines, levels)
    )


def _chars_to_layout_markdown(chars: list, scale: float, width: float) -> str:
    blocks: list[str] = []
    for col in _filter_reading_columns(chars, scale, width):
        md = _segments_to_markdown(_group_lines(col))
        if md:
            blocks.append(md)
    return "\n".join(blocks)


def _extract_layout_text(page, raw) -> str:
    """布局感知提取：阅读顺序 + 标题 + 隐藏文本过滤；失败回退纯文本。"""
    textpage = None
    try:
        textpage = page.get_textpage()
        chars, width = _page_chars(textpage, page, raw)
        if not chars:
            return ""
        heights = [c["y1"] - c["y0"] for c in chars if c["y1"] - c["y0"] > 0]
        scale = (statistics.median(heights) if heights else 1.0) or 1.0
        return _chars_to_layout_markdown(chars, scale, width)
    except Exception:
        logger.debug("layout extraction failed; using plain text", exc_info=True)
        return _extract_page_text(page)
    finally:
        _close(textpage)


def _extract_page_text(page) -> str:
    textpage = None
    try:
        textpage = page.get_textpage()
        return textpage.get_text_range()
    finally:
        _close(textpage)


def _plain_is_well_formed(plain: str) -> bool:
    plain = (plain or "").strip()
    if not plain:
        return False
    if re.search(r"\[\w+,\s", plain):
        return True
    if plain.count(" . . ") >= 2:
        return True
    words = re.findall(r"\S+", plain)
    if len(words) < 30:
        return False
    avg_len = sum(len(w) for w in words) / len(words)
    return avg_len >= 5.0


def _layout_garbled_line_fraction(text: str) -> float:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return 0.0
    garbled = 0
    for ln in lines:
        words = ln.split()
        if len(words) >= 6 and sum(1 for w in words if len(w) <= 2) / len(words) > 0.45:
            garbled += 1
    return garbled / len(lines)


def _should_prefer_plain(plain: str, layout: str) -> bool:
    layout = (layout or "").strip()
    plain = (plain or "").strip()
    if not layout:
        return True
    if not plain:
        return False
    lines = [ln.strip() for ln in layout.splitlines() if ln.strip()]
    n = len(lines)
    if n == 0:
        return True
    single = sum(1 for ln in lines if len(ln) <= 2)
    if single / n >= 0.18:
        return True
    if _layout_garbled_line_fraction(layout) >= 0.20 and _layout_garbled_line_fraction(plain) < 0.08:
        return True
    if re.search(r"\[\w+,\s", plain) and re.search(r"\[\w+\s+\w+\s+\d", layout):
        return True
    for ln in plain.splitlines():
        probe = ln.strip()
        if len(probe) < 24:
            continue
        alnum = "".join(c for c in probe if c.isalnum())[:16]
        if len(alnum) < 12:
            continue
        if alnum not in "".join(c for c in layout if c.isalnum()):
            return True
        break
    return False


# --- 扫描页渲染 ---

def _effective_scale(page, scale: float, max_edge: int) -> float:
    if max_edge <= 0:
        return scale
    width, height = page.get_size()
    longest_pt = max(float(width), float(height))
    if longest_pt <= 0:
        return scale
    return min(scale, max_edge / longest_pt)


def _render_page_to_jpeg(page, scale: float, quality: int, max_edge: int = 0) -> bytes:
    bitmap = None
    try:
        bitmap = page.render(scale=_effective_scale(page, scale, max_edge))
        pil = bitmap.to_pil()
        if pil.mode != "RGB":
            pil = pil.convert("RGB")
        buf = io.BytesIO()
        pil.save(buf, format="JPEG", quality=quality, optimize=True)
        return buf.getvalue()
    finally:
        _close(bitmap)


# --- 内嵌图片提取（文本页内的图/表） ---

def _decode_embedded_image_pil(obj):
    """解码内嵌图片（处理 /SMask 软蒙版：合成到白底）。"""
    from PIL import Image

    bitmap = None
    try:
        try:
            bitmap = obj.get_bitmap(render=True)
        except Exception:
            _close(bitmap)
            bitmap = obj.get_bitmap()
        pil = bitmap.to_pil()
        if pil.mode in ("RGBA", "LA", "PA"):
            rgba = pil.convert("RGBA")
            if rgba.getchannel("A").getextrema()[0] < 255:
                background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
                return Image.alpha_composite(background, rgba).convert("RGB")
            return rgba.convert("RGB")
        if pil.mode in ("RGB", "L"):
            return pil.copy()
        return pil.convert("RGB")
    finally:
        _close(bitmap)


def _select_embedded_images(
    meta: list, num_text_pages: int,
    *, min_pixels: int = EMBED_MIN_PIXELS, min_area_ratio: float = EMBED_MIN_AREA_RATIO,
    repeat_frac: float = EMBED_REPEAT_PAGE_FRAC, max_images: int = EMBED_MAX_IMAGES,
) -> list:
    from collections import defaultdict

    hash_pages = defaultdict(set)
    for m in meta:
        hash_pages[m["hash"]].add(m["page"])
    repeat_threshold = max(2, int(num_text_pages * repeat_frac)) if num_text_pages else 2
    banned = {h for h, pages in hash_pages.items() if len(pages) >= repeat_threshold}
    kept: list = []
    seen = set()
    for idx, m in enumerate(meta):
        if m["area_ratio"] < min_area_ratio:
            continue
        if m["width"] < min_pixels or m["height"] < min_pixels:
            continue
        if m["hash"] in banned:
            continue
        key = (m["page"], m["hash"])
        if key in seen:
            continue
        seen.add(key)
        kept.append(idx)
        if len(kept) >= max_images:
            break
    return kept


def _extract_embedded_images(pdf, classes, raw, base_name: str, quality: int) -> dict:
    """返回 {page_index: [(ref_path, base64_jpeg, y_top), ...]}。"""
    import hashlib
    from collections import defaultdict

    text_indices = [i for i, c in enumerate(classes) if c == "text"]
    if not text_indices:
        return {}

    candidates: list = []
    meta: list = []
    for i in text_indices:
        page = pdf[i]
        try:
            width, height = page.get_size()
            page_area = float(width) * float(height)
            if page_area <= 0:
                continue
            for obj in page.get_objects():
                if obj.type != raw.FPDF_PAGEOBJ_IMAGE:
                    continue
                try:
                    left, bottom, right, top = obj.get_bounds()
                except Exception:
                    continue
                area_ratio = abs((right - left) * (top - bottom)) / page_area
                if area_ratio < EMBED_MIN_AREA_RATIO:
                    continue
                try:
                    pil = _decode_embedded_image_pil(obj)
                except Exception:
                    continue
                content_hash = hashlib.md5(pil.tobytes()).hexdigest()
                candidates.append((i, top, pil))
                meta.append({
                    "page": i, "width": pil.width, "height": pil.height,
                    "area_ratio": area_ratio, "hash": content_hash,
                })
        finally:
            _close(page)

    kept_idx = _select_embedded_images(meta, len(text_indices))
    if not kept_idx:
        return {}

    result: dict = defaultdict(list)
    per_page_count: dict = defaultdict(int)
    for idx in kept_idx:
        page_i, y_top, pil = candidates[idx]
        if pil.mode not in ("RGB", "L"):
            pil = pil.convert("RGB")
        if RENDER_MAX_EDGE > 0 and max(pil.size) > RENDER_MAX_EDGE:
            ratio = RENDER_MAX_EDGE / max(pil.size)
            pil = pil.resize((max(1, int(pil.width * ratio)), max(1, int(pil.height * ratio))))
        buf = io.BytesIO()
        pil.save(buf, format="JPEG", quality=quality, optimize=True)
        per_page_count[page_i] += 1
        fname = f"{base_name}_p{page_i+1}_img{per_page_count[page_i]}.jpg"
        ref_path = f"images/{fname}"
        result[page_i].append((ref_path, base64.b64encode(buf.getvalue()).decode("utf-8"), y_top))
    for page_i in result:
        result[page_i].sort(key=lambda item: item[2], reverse=True)
    return result


# --- 跨页重复页眉页脚去除 ---

def _strip_repeating_lines(texts: list, classes: list) -> list:
    from collections import Counter

    text_indices = [i for i, c in enumerate(classes) if c == "text"]
    if len(text_indices) < 4:
        return list(texts)
    counter: Counter = Counter()
    for i in text_indices:
        lines = [ln.strip() for ln in texts[i].splitlines() if ln.strip()]
        if not lines:
            continue
        for edge in {lines[0], lines[-1]}:
            if len(edge) <= 80:
                counter[edge] += 1
    threshold = max(2, int(len(text_indices) * 0.6))
    repeating = {line for line, count in counter.items() if count >= threshold}
    if not repeating:
        return list(texts)
    cleaned = []
    for i, text in enumerate(texts):
        if classes[i] != "text":
            cleaned.append(text)
            continue
        kept = [ln for ln in text.splitlines() if ln.strip() not in repeating]
        cleaned.append("\n".join(kept))
    return cleaned


# --- 主解析器 ---

class PDFParser(BaseParser):
    """逐页路由：原生文本页直出文本层；扫描页渲染 + OCR（可选）。"""

    supported_file_types = ["pdf"]

    def __init__(self, parse_options: dict | None = None):
        super().__init__(parse_options)
        force = self.parse_options.get("pdf_force_scanned")
        self._force_scanned = (
            str(force).strip().lower() in {"1", "true", "yes", "y", "on"}
            if force is not None else FORCE_SCANNED_PDF
        )
        self._ocr_enabled = bool(self.parse_options.get("ocr_enabled", True))

    def parse(self, path: str) -> ParseResult:
        try:
            with open(path, "rb") as f:
                content = f.read()
        except OSError as exc:
            raise ParserError(f"读取 PDF 失败: {exc}") from exc
        if not content:
            raise ParserError("PDF 内容为空")

        with _PDFIUM_LOCK:
            if self._force_scanned:
                return self._render_all_scanned(content)
            try:
                return self._route(content)
            except Exception:
                logger.exception("PDFParser 逐页路由失败，回退全页渲染")
                return self._render_all_scanned(content)

    def _render_all_scanned(self, content: bytes) -> ParseResult:
        """全页渲染为图片 + OCR（force_scanned / 兜底）。"""
        import pypdfium2 as pdfium

        result = ParseResult()
        blocks: list[str] = []
        pdf = None
        try:
            pdf = pdfium.PdfDocument(content)
            page_count = len(pdf)
            scale = max(1, RENDER_DPI) / 72
            quality = min(95, max(1, JPEG_QUALITY))
            for i in range(page_count):
                page = pdf[i]
                try:
                    jpeg = _render_page_to_jpeg(page, scale, quality, RENDER_MAX_EDGE)
                    ref_path = self._add_image(result, jpeg, "image/jpeg", original_ref=f"page_{i+1}.jpg")
                    if self._ocr_enabled:
                        try:
                            text = ocr_image_bytes(jpeg)
                            if text.strip():
                                blocks.append(f"## 第 {i+1} 页\n\n{text.strip()}")
                                continue
                        except Exception as exc:
                            logger.debug("page %d OCR failed: %s", i, exc)
                    blocks.append(f"[扫描页 {i+1}]({ref_path})")
                finally:
                    _close(page)
        finally:
            _close(pdf)
        result.markdown = "\n\n".join(blocks).strip()
        result.error_type = "scanned" if result.images else "empty"
        return result

    def _route(self, content: bytes) -> ParseResult:
        import pypdfium2 as pdfium
        import pypdfium2.raw as pdfium_r

        base_name = os.path.splitext(self._file_name_hint())[0] or "document"
        scale = max(1, RENDER_DPI) / 72
        quality = min(95, max(1, JPEG_QUALITY))

        result = ParseResult()
        pdf = None
        texts: list[str] = []
        classes: list[str] = []
        try:
            pdf = pdfium.PdfDocument(content)
            page_count = len(pdf)

            # Pass 1: 文本提取 + 扫描分类
            for i in range(page_count):
                page = pdf[i]
                try:
                    plain = _extract_page_text(page)
                    ratio = self._page_image_area_ratio(page, pdfium_r)
                    cls = "scanned" if (ratio >= SCAN_IMAGE_AREA_RATIO or
                                        (len(plain.strip()) < SCAN_MIN_CHARS_PER_PAGE and ratio >= _LOW_TEXT_IMAGE_RATIO)) else "text"
                    if cls == "text" and LAYOUT_ORDERING:
                        if _plain_is_well_formed(plain):
                            text = plain
                        else:
                            layout = _extract_layout_text(page, pdfium_r)
                            text = layout if (layout and not _should_prefer_plain(plain, layout)) else plain
                    else:
                        text = plain
                    text = _postprocess_pdf_text(text)
                finally:
                    _close(page)
                texts.append(text)
                classes.append(cls)

            texts = _strip_repeating_lines(texts, classes)
            scanned_indices = [i for i, c in enumerate(classes) if c == "scanned"]

            # Pass 2: 扫描页渲染 + OCR
            rendered: dict = {}
            for i in scanned_indices:
                page = pdf[i]
                try:
                    rendered[i] = _render_page_to_jpeg(page, scale, quality, RENDER_MAX_EDGE)
                finally:
                    _close(page)
            for i, jpeg in rendered.items():
                ref_path = self._add_image(result, jpeg, "image/jpeg", original_ref=f"{base_name}_page_{i+1}.jpg")

            # Pass 3: 文本页内嵌图片提取
            embedded: dict = {}
            if EXTRACT_EMBEDDED_IMAGES:
                embedded = _extract_embedded_images(pdf, classes, pdfium_r, base_name, quality)
                for refs in embedded.values():
                    for ref_path, b64, _y in refs:
                        result.images[ref_path] = b64
                        try:
                            raw_bytes = base64.b64decode(b64)
                        except Exception:
                            raw_bytes = b""
                        result.image_refs.append(
                            self._make_image_ref(ref_path, raw_bytes, "image/jpeg")
                        )
        finally:
            _close(pdf)

        # 组装 markdown（阅读顺序）
        blocks: list[str] = []
        scanned_img_refs: dict[int, str] = {}
        # 扫描页的图片引用：images/{base_name}_page_{i+1}.jpg
        for i, jpeg in rendered.items():
            fname = f"{base_name}_page_{i+1}.jpg"
            scanned_img_refs[i] = f"images/{fname}"
        for i in range(page_count):
            if classes[i] == "scanned":
                ref_path = scanned_img_refs.get(i, "")
                if self._ocr_enabled and rendered.get(i):
                    try:
                        text = ocr_image_bytes(rendered[i])
                        if text.strip():
                            blocks.append(f"## 第 {i+1} 页\n\n{text.strip()}")
                            continue
                    except Exception as exc:
                        logger.debug("page %d OCR failed: %s", i, exc)
                blocks.append(f"[扫描页 {i+1}]({ref_path})" if ref_path else f"[扫描页 {i+1}]")
            else:
                stripped = texts[i].strip()
                if stripped:
                    blocks.append(stripped)
                page_images = list(embedded.get(i, []))
                page_images.sort(key=lambda item: item[2], reverse=True)
                for ref_path, _b64, _y in page_images:
                    fname = os.path.basename(ref_path)
                    blocks.append(f"![{fname}]({ref_path})")

        result.markdown = "\n\n".join(blocks).strip()
        if not result.markdown and not result.images:
            raise ParserError("PDF 未解析出任何内容")
        result.error_type = "scanned" if scanned_indices else None
        return result

    def _file_name_hint(self) -> str:
        return self.parse_options.get("file_name", "document")

    def _make_image_ref(self, ref_path: str, data: bytes, mime_type: str) -> object:
        """构造 ImageRef（供内嵌图片收集）。"""
        from ingestion.parser.base_parser import ImageRef

        filename = os.path.basename(ref_path)
        return ImageRef(filename=filename, original_ref=filename, mime_type=mime_type, data=data)

    @staticmethod
    def _page_image_area_ratio(page, raw) -> float:
        width, height = page.get_size()
        page_area = float(width) * float(height)
        if page_area <= 0:
            return 0.0
        image_area = 0.0
        for obj in page.get_objects():
            try:
                if obj.type == raw.FPDF_PAGEOBJ_IMAGE:
                    left, bottom, right, top = obj.get_bounds()
                    image_area += abs((right - left) * (top - bottom))
            except Exception:
                continue
        return image_area / page_area


__all__ = ["PDFParser"]
