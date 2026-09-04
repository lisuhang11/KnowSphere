"""评测取消：协作式中断（runner 在灌库/跑题间隙检查）。"""

from __future__ import annotations

from collections.abc import Callable


class EvalCancelled(Exception):
    """用户取消排队中的任务，或中断正在运行的评测。"""


def check_stop(should_stop: Callable[[], bool] | None) -> None:
    if should_stop and should_stop():
        raise EvalCancelled()
