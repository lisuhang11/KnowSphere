"""扫描 skills/<name>/SKILL.md，读取 YAML frontmatter。"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from skills.paths import (
    CODE_FILENAMES,
    MAX_DESCRIPTION_LEN,
    SKILL_MD,
    is_valid_skill_name,
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
    """按目录扫描顺序去重；未知 name 丢弃。"""
    catalog = [s.name for s in list_skills(root=root)]
    if names is None:
        return list(catalog)
    wanted = {str(n).strip() for n in names if str(n).strip()}
    return [n for n in catalog if n in wanted]


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
    return [{"name": s.name, "description": s.description} for s in records]


def get_skill(name: str, *, root: Path | None = None) -> SkillInfo | None:
    key = (name or "").strip()
    if not key:
        return None
    for rec in list_skills(root=root):
        if rec.name == key:
            return rec
    return None
