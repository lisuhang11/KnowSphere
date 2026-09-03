"""技能元工具：不进智能体工具勾选框，有绑定技能时由运行时注入。"""

from tools.skills.execute_skill_script import execute_skill_script
from tools.skills.read_skill import read_skill

SKILL_RUNTIME_TOOL_NAMES: tuple[str, ...] = ("read_skill", "execute_skill_script")

SKILL_RUNTIME_TOOLS = (read_skill, execute_skill_script)

__all__ = [
    "SKILL_RUNTIME_TOOLS",
    "SKILL_RUNTIME_TOOL_NAMES",
    "execute_skill_script",
    "read_skill",
]
