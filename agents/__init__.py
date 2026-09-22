"""对话图包：状态、运行时配置与组图入口。

``build_agent`` 惰性导出，避免 ``utils.long_term_memory → agents.context →
agents.graph → nodes → long_term_memory`` 循环导入。
"""

from __future__ import annotations

from typing import Any

__all__ = ["build_agent"]


def __getattr__(name: str) -> Any:
    if name == "build_agent":
        from agents.graph import build_agent

        return build_agent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
