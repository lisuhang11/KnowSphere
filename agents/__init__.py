"""对话图包：状态、运行时配置与组图入口。

build_agent 延迟导入，避免 `from agents.context import ...` 时拉起整张图造成环。
"""

from __future__ import annotations

from typing import Any

__all__ = ["build_agent"]


def __getattr__(name: str) -> Any:
    if name == "build_agent":
        from agents.graph import build_agent

        return build_agent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
