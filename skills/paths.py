"""技能目录路径：拒绝穿越，隐藏缓存目录。"""

from __future__ import annotations

import os
from pathlib import Path

SKILL_MD = "SKILL.md"
MAX_SKILL_NAME_LEN = 64
MAX_DESCRIPTION_LEN = 1024
MAX_COMPATIBILITY_LEN = 500
MAX_READ_CHARS = 80_000
MAX_FILE_BYTES = 2 * 1024 * 1024
IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".bmp"})

SKIP_DIR_NAMES = frozenset(
    {".venv", "__pycache__", ".git", "node_modules", ".mypy_cache", ".ruff_cache"}
)
CODE_FILENAMES = frozenset(
    {"__init__.py", "catalog.py", "paths.py", "must_use.py", "prompt.py"}
)


def skills_root() -> Path:
    override = (os.environ.get("KNOWSPHERE_SKILLS_DIR") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parent


def is_valid_skill_name(name: str) -> bool:
    """Agent Skills：1–64 字符，Unicode 小写字母、数字、单个连字符，且与目录名同形。"""
    raw = (name or "").strip()
    if not raw or len(raw) > MAX_SKILL_NAME_LEN:
        return False
    if raw.startswith("-") or raw.endswith("-") or "--" in raw:
        return False
    for char in raw:
        if char == "-":
            continue
        if (char.isalpha() and char.islower()) or char.isdigit():
            continue
        return False
    return True


def _is_skipped_part(part: str) -> bool:
    return (not part) or part in SKIP_DIR_NAMES or part.startswith(".")


def is_hidden_relative(rel: Path) -> bool:
    return any(_is_skipped_part(p) for p in rel.parts)


def skill_virtual_path(name: str, rel: str = SKILL_MD) -> str:
    """模型可见的技能路径，根是 `/skills/<name>/`。"""
    suffix = (rel or SKILL_MD).replace("\\", "/").lstrip("/")
    return f"/skills/{name}/{suffix}"


def parse_skill_virtual_path(file_path: str) -> tuple[str, str] | None:
    """解析 `/skills/<name>/<rel>`。目录本身视为 `SKILL.md`。非法路径返回 None。"""
    raw = (file_path or "").strip().replace("\\", "/")
    if not raw or raw.startswith("~"):
        return None
    if raw.startswith("/"):
        raw = raw[1:]
    parts = [part for part in raw.split("/") if part]
    if len(parts) < 2 or parts[0] != "skills":
        return None
    if any(part in {".", ".."} for part in parts):
        return None
    name = parts[1]
    if not is_valid_skill_name(name):
        return None
    rel = "/".join(parts[2:]) or SKILL_MD
    return name, rel


def resolve_skill_dir(name: str, *, root: Path | None = None) -> Path | None:
    if not is_valid_skill_name(name):
        return None
    base = (root or skills_root()).resolve()
    candidate = (base / name).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        return None
    if not candidate.is_dir():
        return None
    if not (candidate / SKILL_MD).is_file():
        return None
    return candidate


def resolve_skill_file(name: str, file_path: str, *, root: Path | None = None) -> Path | None:
    """把技能内相对路径解析为绝对文件。拒绝绝对路径、`..` 与隐藏目录。"""
    rel_raw = (file_path or "").strip().replace("\\", "/")
    if not rel_raw or rel_raw.startswith(("/", "~")):
        return None
    rel = Path(rel_raw)
    if rel.is_absolute() or ".." in rel.parts:
        return None
    if is_hidden_relative(rel):
        return None
    skill_dir = resolve_skill_dir(name, root=root)
    if skill_dir is None:
        return None
    candidate = (skill_dir / rel).resolve()
    try:
        candidate.relative_to(skill_dir)
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return candidate


def list_skill_files(name: str, *, root: Path | None = None) -> list[str]:
    skill_dir = resolve_skill_dir(name, root=root)
    if skill_dir is None:
        return []
    out: list[str] = []
    for path in sorted(skill_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(skill_dir)
        if is_hidden_relative(rel):
            continue
        out.append(rel.as_posix())
    return out
