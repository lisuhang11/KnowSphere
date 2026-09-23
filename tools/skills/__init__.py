"""技能元工具：不进智能体工具勾选框，有绑定技能时由运行时注入。"""

from tools.skills.execute_skill_script import execute_skill_script
from tools.skills.read_file import read_file

SKILL_RUNTIME_TOOL_NAMES: tuple[str, ...] = ("read_file", "execute_skill_script")

SKILL_RUNTIME_TOOLS = (read_file, execute_skill_script)

__all__ = [
    "SKILL_RUNTIME_TOOLS",
    "SKILL_RUNTIME_TOOL_NAMES",
    "execute_skill_script",
    "read_file",
]
