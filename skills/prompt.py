"""系统提示词 Level 1：技能名 + 短描述（渐进披露）。"""

from __future__ import annotations

from collections.abc import Sequence

from skills.catalog import SkillInfo


def format_skills_prompt(skills: Sequence[SkillInfo] | None) -> str:
    if not skills:
        return ""
    lines = [
        "### 可用技能（请认真阅读）",
        "",
        "处理**每一个**用户请求前，必须按下列清单考虑是否使用技能：",
        "",
        "#### 技能匹配流程（必须）",
        "",
        "1. **SCAN**：阅读下列技能的描述与触发条件",
        "2. **MATCH**：判断用户意图是否命中任一技能（关键词、场景或任务类型）",
        "3. **LOAD**：若命中，先调用 `read_skill(skill_name=\"...\")`，再生成回答",
        "4. **APPLY**：按技能说明书执行，以给出更高质量、更结构化的结果",
        "",
        "**重要**：技能适用时必须使用，不要为了省时间或 token 而跳过。",
        "",
        "#### 技能列表",
        "",
    ]
    for i, skill in enumerate(skills, start=1):
        lines.append(f"{i}. **{skill.name}**")
        lines.append(f"   {skill.description}")
        lines.append("")
    lines.extend(
        [
            "#### 沙箱工作区",
            "",
            "技能脚本在一次性 Docker 容器中运行，工作目录为 `/workspace`：",
            "- `/workspace/input`：本轮用户上传的附件（只读）。把绝对路径作为脚本参数传入",
            "- `/workspace/output`：脚本生成的、需要交给用户的文件",
            (
                "- 技能文件在 `/opt/knowsphere/skills/<name>`，只能通过 `read_skill` / "
                "`execute_skill_script` 访问，不要假设可以 ls/cat 宿主机路径"
            ),
            "",
            "#### 工具用法",
            "",
            "- `read_skill(skill_name)`：加载 SKILL.md 并列出技能内文件，从而发现 scripts/",
            (
                "- `read_skill(skill_name, file_path)`：读取技能内一个相对路径文件"
                "（例如 `scripts/extract_text.py`）"
            ),
            (
                "- `execute_skill_script(skill_name, script_path, script_args, stdin_text)`："
                "在沙箱中运行技能目录内的脚本（`script_path` 必须是技能内相对路径，"
                "例如 `scripts/extract_text.py`）"
            ),
            "  - `script_args`：命令行参数；用户附件请传 `/workspace/input/<文件名>`",
            "  - `stdin_text`：写入脚本 stdin 的文本（内存中的 JSON 等）",
            "  - 生成文件只写到 `$KNOWSPHERE_SKILL_OUTPUT_DIR`（即 `/workspace/output`）",
            "  - 脚本工作目录是 `/workspace`；要用 `$KNOWSPHERE_SKILL_DIR` 引用技能自身文件",
            (
                "  - 禁止在 `/workspace` 里现写现跑脚本；没有 Docker 时该工具会返回错误，"
                "不要改在对话里假装已经执行"
            ),
            "  - 产出文件会出现在对话的文件卡片中，不要用无法打开的本地路径引用它们",
        ]
    )
    return "\n".join(lines).strip()


def append_skills_prompt(base: str, skills: Sequence[SkillInfo] | None) -> str:
    block = format_skills_prompt(skills)
    if not block:
        return base
    text = (base or "").rstrip()
    if not text:
        return block
    return text + "\n\n" + block
