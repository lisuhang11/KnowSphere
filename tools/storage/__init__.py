"""运行时存储工具：不进智能体勾选框，有工具可用时注入。"""

from tools.storage.get_stored_data import get_stored_data

STORAGE_RUNTIME_TOOL_NAMES: tuple[str, ...] = ("get_stored_data",)
STORAGE_RUNTIME_TOOLS = (get_stored_data,)

__all__ = [
    "STORAGE_RUNTIME_TOOLS",
    "STORAGE_RUNTIME_TOOL_NAMES",
    "get_stored_data",
]
