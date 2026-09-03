"""扫描 skills/<name>/SKILL.md，读取 YAML frontmatter。"""

from __future__ import annotations

import base64
import mimetypes
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from skills.paths import (
    CODE_FILENAMES,
    IMAGE_EXTS,
    MAX_DESCRIPTION_LEN,
    MAX_FILE_BYTES,
    MAX_READ_CHARS,
    SKILL_MD,
    is_valid_skill_name,
    list_skill_files,
    resolve_skill_file,
    skills_root,
)


@dataclass(frozen=True)
class SkillInfo:
    name: str
    description: str
    root: Path
    instructions: str


def _unquote(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        return text[1:-1].strip()
    return text


def parse_skill_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """解析 SKILL.md 顶部 `---` YAML。只取标量 name / description。"""
    raw = text.lstrip("\ufeff")
    if not raw.startswith("---"):
        return {}, raw.strip()
    rest = raw[3:]
    if rest.startswith("\r\n"):
        rest = rest[2:]
    elif rest.startswith("\n"):
        rest = rest[1:]
    end = rest.find("\n---")
    if end < 0:
        return {}, raw.strip()
    fm = rest[:end]
    body = rest[end + 4 :].lstrip("\r\n")
    meta: dict[str, str] = {}
    pending_key: str | None = None
    pending_lines: list[str] = []

    def flush_pending() -> None:
        nonlocal pending_key, pending_lines
        if pending_key is None:
            return
        meta[pending_key] = " ".join(pending_lines).strip()
        pending_key = None
        pending_lines = []

    for line in fm.splitlines():
        if pending_key is not None:
            if line.startswith((" ", "\t")) and line.strip():
                pending_lines.append(line.strip())
                continue
            flush_pending()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if value in (">", "|", ">-", "|-"):
            pending_key = key
            pending_lines = []
            continue
        meta[key] = _unquote(value)
    flush_pending()
    return meta, body.strip()


def _load_skill_dir(path: Path) -> SkillInfo | None:
    md = path / SKILL_MD
    if not md.is_file():
        return None
    dirname = path.name
    if dirname in CODE_FILENAMES or not is_valid_skill_name(dirname):
        return None
    try:
        text = md.read_text(encoding="utf-8")
    except OSError:
        return None
    meta, body = parse_skill_frontmatter(text)
    name = (meta.get("name") or "").strip()
    description = (meta.get("description") or "").strip()
    if name != dirname:
        return None
    if not is_valid_skill_name(name) or not description:
        return None
    if len(description) > MAX_DESCRIPTION_LEN:
        description = description[:MAX_DESCRIPTION_LEN].rstrip()
    return SkillInfo(name=name, description=description, root=path, instructions=body)


def list_skills(*, root: Path | None = None) -> list[SkillInfo]:
    base = (root or skills_root()).resolve()
    if not base.is_dir():
        return []
    skills: list[SkillInfo] = []
    for child in sorted(base.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir():
            continue
        if child.name.startswith(".") or child.name == "__pycache__":
            continue
        rec = _load_skill_dir(child)
        if rec is not None:
            skills.append(rec)
    return skills


def known_skill_names(*, root: Path | None = None) -> frozenset[str]:
    return frozenset(s.name for s in list_skills(root=root))


def ordered_skill_names(names: Iterable[str] | None, *, root: Path | None = None) -> list[str]:
    """去重并丢掉未知 name。传入名单时保持原有顺序；未传则按目录扫描顺序。"""
    catalog = [s.name for s in list_skills(root=root)]
    if names is None:
        return list(catalog)
    known = set(catalog)
    seen: set[str] = set()
    out: list[str] = []
    for raw in names:
        name = str(raw).strip()
        if not name or name in seen or name not in known:
            continue
        seen.add(name)
        out.append(name)
    return out


def skill_metadata_for_names(
    names: Sequence[str] | None, *, root: Path | None = None
) -> list[SkillInfo]:
    wanted = ordered_skill_names(names, root=root)
    by_name = {s.name: s for s in list_skills(root=root)}
    return [by_name[n] for n in wanted if n in by_name]


def skills_to_public(
    names: Sequence[str] | None = None, *, root: Path | None = None
) -> list[dict[str, Any]]:
    records = (
        list_skills(root=root)
        if names is None
        else skill_metadata_for_names(names, root=root)
    )
    return [
        {
            "name": s.name,
            "description": s.description,
            "file_count": len(list_skill_files(s.name, root=root)),
        }
        for s in records
    ]


def skill_to_detail(name: str, *, root: Path | None = None) -> dict[str, Any] | None:
    rec = get_skill(name, root=root)
    if rec is None:
        return None
    files = list_skill_files(name, root=root)
    return {
        "name": rec.name,
        "description": rec.description,
        "files": files,
        "file_count": len(files),
    }


def read_skill_file_for_api(
    name: str, rel: str, *, root: Path | None = None
) -> dict[str, Any] | None:
    """给前端文件预览：UTF-8 文本、图片 base64，或标记为 binary。"""
    path = resolve_skill_file(name, rel, root=root)
    if path is None:
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    truncated = False
    if len(data) > MAX_FILE_BYTES:
        data = data[:MAX_FILE_BYTES]
        truncated = True
    public_path = (rel or "").replace("\\", "/").strip()
    ext = path.suffix.lower()
    if ext in IMAGE_EXTS:
        media = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return {
            "path": public_path,
            "encoding": "base64",
            "media_type": media,
            "content": base64.b64encode(data).decode("ascii"),
            "truncated": truncated,
        }
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return {
            "path": public_path,
            "encoding": "binary",
            "media_type": None,
            "content": None,
            "truncated": False,
        }
    if len(text) > MAX_READ_CHARS:
        text = text[:MAX_READ_CHARS]
        truncated = True
    return {
        "path": public_path,
        "encoding": "utf-8",
        "media_type": "text/plain",
        "content": text,
        "truncated": truncated,
    }


def get_skill(name: str, *, root: Path | None = None) -> SkillInfo | None:
    key = (name or "").strip()
    if not key:
        return None
    for rec in list_skills(root=root):
        if rec.name == key:
            return rec
    return None
