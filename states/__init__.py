"""兼容再导出。新代码请从 ``agents.state`` / ``agents.context`` 导入。"""

from agents.context import Context
from agents.state import (
    InputState,
    KnowSphereState,
    OutputState,
    OverallState,
    TurnState,
)

# 旧名：曾作为 StateGraph context_schema 的 TypedDict
AgentConfig = Context

__all__ = [
    "AgentConfig",
    "Context",
    "InputState",
    "KnowSphereState",
    "OutputState",
    "OverallState",
    "TurnState",
]
