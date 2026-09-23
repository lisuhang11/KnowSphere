"""系统提示词只放技能元数据；正文和 scripts/references/assets 按需再读。"""

from __future__ import annotations

from collections.abc import Sequence

from skills.catalog import SkillInfo
from skills.paths import skill_virtual_path


def _annotations(skill: SkillInfo) -> str:
    parts: list[str] = []
    if skill.license:
        parts.append(f"License: {skill.license}")
    if skill.compatibility:
        parts.append(f"Compatibility: {skill.compatibility}")
    return ", ".join(parts)


def format_skills_prompt(skills: Sequence[SkillInfo] | None) -> str:
    if not skills:
        return ""
    lines = [
        "### 可用技能",
        "",
        "下面只有名称、说明和可选的环境要求。技能适用时必须使用，不要跳过。",
        "",
        "1. 用 description 判断用户任务是否命中",
        "2. 命中后调用 `read_file(file_path=\"/skills/<name>/SKILL.md\")` 加载全文",
        "3. 按正文执行。正文里的相对路径拼到同一技能根下再 `read_file`，例如 `/skills/<name>/scripts/extract.py`；不要猜测未出现的路径",
        "4. Allowed tools 是该技能预先点名的工具，且必须已经绑定到本智能体",
        "",
        "**Available Skills:**",
        "",
    ]
    for skill in skills:
        desc = f"- **{skill.name}**: {skill.description}"
        notes = _annotations(skill)
        if notes:
            desc += f" ({notes})"
        lines.append(desc)
        if skill.allowed_tools:
            lines.append(f"  -> Allowed tools: {', '.join(skill.allowed_tools)}")
        lines.append(f"  -> Read `{skill_virtual_path(skill.name)}` with read_file")
        lines.append("")
    return "\n".join(lines).strip()


def append_skills_prompt(base: str, skills: Sequence[SkillInfo] | None) -> str:
    block = format_skills_prompt(skills)
    if not block:
        return base
    text = (base or "").rstrip()
    if not text:
        return block
    return text + "\n\n" + block
