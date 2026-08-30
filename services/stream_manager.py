"""会话生成与 SSE 解耦：后台 run 写事件，任意连接按 offset 重放/续推。

一个会话同时只允许一轮未完成的生成。客户端断开只停止推送，不取消 run。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_TTL_SEC = 60.0
DEFAULT_POLL_SEC = 0.1


class SessionRunBusy(Exception):
    """该会话已有进行中的生成。"""


@dataclass
class StreamFrame:
    event: str
    data: dict[str, Any]


@dataclass
class SessionRun:
    session_id: str
    run_id: str
    user_preview: dict[str, Any]
    frames: list[StreamFrame] = field(default_factory=list)
    done: bool = False
    stopped: bool = False
    task: asyncio.Task[None] | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "user_preview": self.user_preview,
            "done": self.done,
            "stopped": self.stopped,
        }

    def answer_text(self) -> str:
        parts: list[str] = []
        for frame in self.frames:
            if frame.event == "messages" and frame.data.get("type") == "answer":
                parts.append(str(frame.data.get("content") or ""))
        return "".join(parts).strip()


class StreamManager:
    def __init__(self, *, ttl_sec: float = DEFAULT_TTL_SEC, poll_sec: float = DEFAULT_POLL_SEC) -> None:
        self._ttl_sec = ttl_sec
        self._poll_sec = poll_sec
        self._runs: dict[str, SessionRun] = {}

    def get(self, session_id: str) -> SessionRun | None:
        return self._runs.get(session_id)

    def active(self, session_id: str) -> SessionRun | None:
        run = self._runs.get(session_id)
        if run is None or run.done:
            return None
        return run

    def begin(self, session_id: str, user_preview: dict[str, Any] | None = None) -> SessionRun:
        existing = self._runs.get(session_id)
        if existing is not None and not existing.done:
            raise SessionRunBusy(session_id)
        run = SessionRun(
            session_id=session_id,
            run_id=str(uuid.uuid4()),
            user_preview=dict(user_preview or {}),
        )
        self._runs[session_id] = run
        return run

    def append(self, session_id: str, event: str, data: dict[str, Any]) -> None:
        run = self._runs.get(session_id)
        if run is None or run.done:
            return
        run.frames.append(StreamFrame(event=event, data=data))

    def set_user_preview(self, session_id: str, preview: dict[str, Any]) -> None:
        run = self._runs.get(session_id)
        if run is None:
            return
        run.user_preview = dict(preview)

    def finish(self, run: SessionRun) -> None:
        run.done = True
        current = self._runs.get(run.session_id)
        if current is run:
            self._schedule_expire(run.session_id, run.run_id)

    def request_stop(self, session_id: str) -> SessionRun | None:
        run = self.active(session_id)
        if run is None:
            return None
        run.stopped = True
        task = run.task
        if task is not None and not task.done():
            task.cancel()
        return run

    def discard(self, session_id: str) -> SessionRun | None:
        run = self._runs.pop(session_id, None)
        if run is None:
            return None
        run.done = True
        run.stopped = True
        task = run.task
        if task is not None and not task.done():
            task.cancel()
        return run

    async def iter_frames(self, run: SessionRun, start: int = 0) -> AsyncIterator[StreamFrame]:
        """从 offset 重放，再轮询直到 run.done。订阅方取消不影响 run。"""
        offset = max(0, start)
        try:
            while True:
                chunk = run.frames[offset:]
                done = run.done
                for frame in chunk:
                    yield frame
                offset += len(chunk)
                if done:
                    return
                await asyncio.sleep(self._poll_sec)
        except asyncio.CancelledError:
            return

    def _schedule_expire(self, session_id: str, run_id: str) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._expire(session_id, run_id), name=f"ks-run-ttl-{session_id}")

    async def _expire(self, session_id: str, run_id: str) -> None:
        await asyncio.sleep(self._ttl_sec)
        current = self._runs.get(session_id)
        if current is not None and current.run_id == run_id and current.done:
            self._runs.pop(session_id, None)


_manager: StreamManager | None = None


def get_stream_manager() -> StreamManager:
    global _manager
    if _manager is None:
        _manager = StreamManager()
    return _manager


def reset_stream_manager(manager: StreamManager | None = None) -> StreamManager:
    global _manager
    _manager = manager if manager is not None else StreamManager()
    return _manager
