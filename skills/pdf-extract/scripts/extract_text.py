#!/usr/bin/env python3
"""从 /workspace/input 的 PDF 抽取文本，写入 KNOWSPHERE_SKILL_OUTPUT_DIR。"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import zlib
from pathlib import Path


def _output_dir() -> Path:
    raw = os.environ.get("KNOWSPHERE_SKILL_OUTPUT_DIR") or "/workspace/output"
    path = Path(raw)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _input_dir() -> Path:
    return Path("/workspace/input")


def _pdf_escape(text: bytes) -> str:
    out = text.replace(b"\\n", b"\n").replace(b"\\r", b"\r").replace(b"\\t", b"\t")
    out = out.replace(b"\\(", b"(").replace(b"\\)", b")").replace(b"\\\\", b"\\")
    try:
        return out.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return out.decode("latin-1")
        except UnicodeDecodeError:
            return out.decode("utf-8", errors="replace")


def extract_pdf_stdlib(data: bytes) -> str:
    """无 pypdf 时的尽力抽取：解压 stream 后收集 Tj 字符串。"""
    chunks: list[bytes] = []
    for match in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, re.DOTALL):
        payload = match.group(1)
        try:
            payload = zlib.decompress(payload)
        except zlib.error:
            pass
        chunks.append(payload)
    blob = b"\n".join(chunks) if chunks else data
    texts: list[str] = []
    for match in re.finditer(rb"\((?:\\.|[^\\)])*\)\s*Tj", blob):
        inner = match.group(0)
        inner = inner.rsplit(b"Tj", 1)[0].strip()
        if inner.startswith(b"(") and inner.endswith(b")"):
            inner = inner[1:-1]
        piece = _pdf_escape(inner).strip()
        if piece:
            texts.append(piece)
    return "\n".join(texts).strip()


def extract_with_pypdf(path: Path) -> tuple[str, list[str]]:
    from pypdf import PdfReader  # type: ignore[import-not-found]

    reader = PdfReader(str(path))
    pages: list[str] = []
    notes: list[str] = []
    for i, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        tables_md: list[str] = []
        if hasattr(page, "extract_tables"):
            try:
                for table in page.extract_tables() or []:
                    rows = [
                        [str(c or "").replace("\n", " ").strip() for c in row]
                        for row in table
                        if row
                    ]
                    if not rows:
                        continue
                    header = rows[0]
                    lines = [
                        "| " + " | ".join(header) + " |",
                        "| " + " | ".join("---" for _ in header) + " |",
                    ]
                    for row in rows[1:]:
                        padded = row + [""] * (len(header) - len(row))
                        lines.append("| " + " | ".join(padded[: len(header)]) + " |")
                    tables_md.append("\n".join(lines))
            except Exception as exc:
                notes.append(f"第 {i} 页表格抽取失败: {exc}")
        block = f"## 第 {i} 页\n\n"
        block += text or "（本页无文本）"
        if tables_md:
            block += "\n\n### 表格\n\n" + "\n\n".join(tables_md)
        pages.append(block)
    return "\n\n".join(pages).strip(), notes


def extract_one(path: Path) -> tuple[str, list[str], str]:
    data = path.read_bytes()
    notes: list[str] = []
    try:
        body, extra = extract_with_pypdf(path)
        notes.extend(extra)
        engine = "pypdf"
        if not body:
            notes.append("pypdf 未抽出文本，已尝试简易抽取。")
            body = extract_pdf_stdlib(data)
            engine = "pypdf+stdlib"
        return body, notes, engine
    except ImportError:
        notes.append("镜像未安装 pypdf，已使用简易抽取（扫描件/CJK 可能不完整）。")
        return extract_pdf_stdlib(data), notes, "stdlib"
    except Exception as exc:
        notes.append(f"pypdf 失败: {exc}，已尝试简易抽取。")
        return extract_pdf_stdlib(data), notes, "stdlib"


def collect_pdfs(args: list[str]) -> list[Path]:
    if args:
        return [Path(item) for item in args]
    folder = _input_dir()
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".pdf")


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract text from PDF files")
    parser.add_argument("files", nargs="*", help="PDF paths under /workspace/input")
    ns = parser.parse_args()
    pdfs = collect_pdfs(ns.files)
    if not pdfs:
        print(json.dumps({"ok": False, "message": "没有找到 PDF 文件"}, ensure_ascii=False))
        return 1

    sections: list[str] = []
    files_out: list[dict[str, object]] = []
    for path in pdfs:
        if not path.is_file():
            files_out.append({"file": str(path), "ok": False, "error": "文件不存在"})
            continue
        body, notes, engine = extract_one(path)
        heading = f"# {path.name}\n\n"
        if notes:
            heading += "> " + " ".join(notes) + "\n\n"
        sections.append(heading + (body or "（未能抽出文本）"))
        files_out.append(
            {
                "file": path.name,
                "ok": bool(body),
                "engine": engine,
                "chars": len(body),
                "notes": notes,
            }
        )

    out_path = _output_dir() / "extracted.md"
    out_path.write_text("\n\n---\n\n".join(sections).strip() + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(out_path),
                "files": files_out,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
