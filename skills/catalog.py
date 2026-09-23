"""扫描 skills/<name>/SKILL.md，按 Agent Skills 规范读取 YAML 前言。"""

from __future__ import annotations

import base64
import logging
import mimetypes
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from skills.paths import (
    CODE_FILENAMES,
    IMAGE_EXTS,
    MAX_COMPATIBILITY_LEN,
    MAX_DESCRIPTION_LEN,
    MAX_FILE_BYTES,
    MAX_READ_CHARS,
    SKILL_MD,
    is_valid_skill_name,
    list_skill_files,
    resolve_skill_file,
    skills_root,
)

logger = logging.getLogger(__name__)

_FRONTMATTER_RE = re.compile(r"^---\s*\r?\n(.*?)\r?\n---\s*(?:\r?\n|$)(.*)\Z", re.DOTALL)


@dataclass(frozen=True)
class SkillInfo:
    name: str
    description: str
    root: Path
    instructions: str
    license: str | None = None
    compatibility: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    allowed_tools: tuple[str, ...] = ()


def parse_skill_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """解析 SKILL.md 顶部 YAML。失败时返回空映射，调用方据此丢弃该技能。"""
    raw = text.lstrip("\ufeff")
    match = _FRONTMATTER_RE.match(raw)
    if match is None:
        return {}, raw.strip()
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        logger.warning("SKILL.md 前言不是合法 YAML: %s", exc)
        return {}, raw.strip()
    if not isinstance(data, dict):
        return {}, match.group(2).strip()
    return data, match.group(2).strip()


def _scalar(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _parse_allowed_tools(raw: object) -> list[str]:
    """`allowed-tools`：空格或逗号分隔的字符串，或字符串列表。"""
    if isinstance(raw, str):
        return [tool for tool in re.split(r"[\s,]+", raw) if tool]
    if isinstance(raw, list):
        return [item.strip() for item in raw if isinstance(item, str) and item.strip()]
    return []


def _parse_metadata(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items()}


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
    name = _scalar(meta.get("name"))
    description = _scalar(meta.get("description"))
    if name != dirname or not is_valid_skill_name(name) or not description:
        return None
    if len(description) > MAX_DESCRIPTION_LEN:
        description = description[:MAX_DESCRIPTION_LEN].rstrip()
    compatibility = _scalar(meta.get("compatibility")) or None
    if compatibility and len(compatibility) > MAX_COMPATIBILITY_LEN:
        compatibility = compatibility[:MAX_COMPATIBILITY_LEN].rstrip()
    license_name = _scalar(meta.get("license")) or None
    return SkillInfo(
        name=name,
        description=description,
        root=path,
        instructions=body,
        license=license_name,
        compatibility=compatibility,
        metadata=_parse_metadata(meta.get("metadata")),
        allowed_tools=tuple(_parse_allowed_tools(meta.get("allowed-tools"))),
    )


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


def skill_script_paths(name: str, *, root: Path | None = None) -> list[str]:
    """技能包 `scripts/` 下的相对路径。没有脚本的技能返回空列表。"""
    return [p for p in list_skill_files(name, root=root) if p.startswith("scripts/")]


def any_skill_has_scripts(names: Iterable[str] | None, *, root: Path | None = None) -> bool:
    return any(skill_script_paths(str(n).strip(), root=root) for n in (names or []) if str(n).strip())


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
