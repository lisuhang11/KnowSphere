"""会话生成与 SSE 解耦：后台 run 写事件，任意连接按 offset 重放/续推。

一个会话同时只允许一轮未完成的生成。客户端断开只停止推送，不取消 run。

事件缓冲默认 Redis List（RPUSH / LRANGE + TTL），与 WeKnora StreamManager 相同。
LLM 仍在接到请求的进程里跑；其它实例只能续上看和发 stop。原进程挂了，生成照样停。
STREAM_MANAGER_TYPE=memory 或 Redis 不可用时降级本机内存。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger(__name__)

DEFAULT_TTL_SEC = 60.0
DEFAULT_POLL_SEC = 0.1
DEFAULT_STOP_POLL_SEC = 0.3
STOP_WATCH_MAX_SEC = 2 * 3600


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


class StreamStore(Protocol):
    def load_meta(self, session_id: str) -> dict[str, Any] | None: ...
    def try_create(self, session_id: str, meta: dict[str, Any]) -> bool: ...
    def replace(self, session_id: str, meta: dict[str, Any]) -> None: ...
    def save_meta(self, session_id: str, meta: dict[str, Any]) -> None: ...
    def append(self, session_id: str, frame: StreamFrame) -> None: ...
    def get_frames(self, session_id: str, offset: int) -> list[StreamFrame]: ...
    def delete(self, session_id: str) -> None: ...


class MemoryStreamStore:
    """本进程事件缓冲；多 worker / 滚动发布时其它实例看不见。"""

    def __init__(self) -> None:
        self._meta: dict[str, dict[str, Any]] = {}
        self._frames: dict[str, list[StreamFrame]] = {}

    def load_meta(self, session_id: str) -> dict[str, Any] | None:
        meta = self._meta.get(session_id)
        return dict(meta) if meta is not None else None

    def try_create(self, session_id: str, meta: dict[str, Any]) -> bool:
        if session_id in self._meta:
            return False
        self._meta[session_id] = dict(meta)
        self._frames[session_id] = []
        return True

    def replace(self, session_id: str, meta: dict[str, Any]) -> None:
        self._meta[session_id] = dict(meta)
        self._frames[session_id] = []

    def save_meta(self, session_id: str, meta: dict[str, Any]) -> None:
        if session_id not in self._meta:
            return
        self._meta[session_id] = dict(meta)

    def append(self, session_id: str, frame: StreamFrame) -> None:
        meta = self._meta.get(session_id)
        if meta is None or meta.get("done"):
            return
        self._frames.setdefault(session_id, []).append(frame)

    def get_frames(self, session_id: str, offset: int) -> list[StreamFrame]:
        frames = self._frames.get(session_id) or []
        return frames[max(0, offset) :]

    def delete(self, session_id: str) -> None:
        self._meta.pop(session_id, None)
        self._frames.pop(session_id, None)


class RedisStreamStore:
    """WeKnora 同款：事件 List RPUSH/LRANGE，写入时刷新 TTL。"""

    def __init__(self, client: Any, *, prefix: str, ttl_sec: float) -> None:
        self._client = client
        self._prefix = prefix.rstrip(":")
        self._ttl = max(1, int(ttl_sec))

    def _meta_key(self, session_id: str) -> str:
        return f"{self._prefix}:{session_id}:meta"

    def _events_key(self, session_id: str) -> str:
        return f"{self._prefix}:{session_id}:events"

    def load_meta(self, session_id: str) -> dict[str, Any] | None:
        raw = self._client.get(self._meta_key(session_id))
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    def try_create(self, session_id: str, meta: dict[str, Any]) -> bool:
        payload = json.dumps(meta, ensure_ascii=False, default=str)
        created = bool(
            self._client.set(self._meta_key(session_id), payload, nx=True, ex=self._ttl)
        )
        if created:
            self._client.delete(self._events_key(session_id))
        return created

    def replace(self, session_id: str, meta: dict[str, Any]) -> None:
        payload = json.dumps(meta, ensure_ascii=False, default=str)
        pipe = self._client.pipeline(transaction=True)
        pipe.delete(self._events_key(session_id))
        pipe.set(self._meta_key(session_id), payload, ex=self._ttl)
        pipe.execute()

    def save_meta(self, session_id: str, meta: dict[str, Any]) -> None:
        payload = json.dumps(meta, ensure_ascii=False, default=str)
        pipe = self._client.pipeline(transaction=True)
        pipe.set(self._meta_key(session_id), payload, ex=self._ttl)
        pipe.expire(self._events_key(session_id), self._ttl)
        pipe.execute()

    def append(self, session_id: str, frame: StreamFrame) -> None:
        meta = self.load_meta(session_id)
        if meta is None or meta.get("done"):
            return
        payload = json.dumps(
            {"event": frame.event, "data": frame.data},
            ensure_ascii=False,
            default=str,
        )
        events_key = self._events_key(session_id)
        pipe = self._client.pipeline(transaction=True)
        pipe.rpush(events_key, payload)
        pipe.expire(events_key, self._ttl)
        pipe.expire(self._meta_key(session_id), self._ttl)
        pipe.execute()

    def get_frames(self, session_id: str, offset: int) -> list[StreamFrame]:
        start = max(0, offset)
        try:
            rows = self._client.lrange(self._events_key(session_id), start, -1)
        except Exception:
            return []
        frames: list[StreamFrame] = []
        for raw in rows or []:
            try:
                item = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(item, dict):
                continue
            event = item.get("event")
            data = item.get("data")
            if not isinstance(event, str) or not isinstance(data, dict):
                continue
            frames.append(StreamFrame(event=event, data=data))
        return frames

    def delete(self, session_id: str) -> None:
        self._client.delete(self._meta_key(session_id), self._events_key(session_id))


class StreamManager:
    def __init__(
        self,
        *,
        ttl_sec: float = DEFAULT_TTL_SEC,
        poll_sec: float = DEFAULT_POLL_SEC,
        stop_poll_sec: float = DEFAULT_STOP_POLL_SEC,
        store: StreamStore | None = None,
    ) -> None:
        self._ttl_sec = ttl_sec
        self._poll_sec = poll_sec
        self._stop_poll_sec = stop_poll_sec
        self._store: StreamStore = store if store is not None else MemoryStreamStore()
        self._local: dict[str, SessionRun] = {}
        self._watchers: dict[str, asyncio.Task[None]] = {}

    def owns(self, run: SessionRun) -> bool:
        return self._local.get(run.session_id) is run

    def store_holds(self, run: SessionRun) -> bool:
        try:
            meta = self._store.load_meta(run.session_id)
        except Exception:
            return self.owns(run)
        return bool(meta) and meta.get("run_id") == run.run_id

    def drop_local(self, run: SessionRun) -> None:
        if self._local.get(run.session_id) is run:
            self._local.pop(run.session_id, None)
        self._cancel_watch(run.session_id)

    def get(self, session_id: str) -> SessionRun | None:
        local = self._local.get(session_id)
        if local is not None:
            self._sync_flags(local)
            return local
        return self._load_remote(session_id)

    def active(self, session_id: str) -> SessionRun | None:
        run = self.get(session_id)
        if run is None or run.done:
            return None
        return run

    def begin(self, session_id: str, user_preview: dict[str, Any] | None = None) -> SessionRun:
        meta: dict[str, Any] = {
            "run_id": str(uuid.uuid4()),
            "user_preview": dict(user_preview or {}),
            "done": False,
            "stopped": False,
        }
        if not self._store.try_create(session_id, meta):
            existing = self._store.load_meta(session_id)
            if existing is not None and not existing.get("done"):
                raise SessionRunBusy(session_id)
            self._store.replace(session_id, meta)
        self._cancel_watch(session_id)
        run = SessionRun(
            session_id=session_id,
            run_id=str(meta["run_id"]),
            user_preview=dict(meta["user_preview"]),
        )
        self._local[session_id] = run
        self._start_stop_watch(run)
        return run

    def append(self, session_id: str, event: str, data: dict[str, Any]) -> None:
        run = self._local.get(session_id)
        if run is not None:
            if run.done:
                return
            frame = StreamFrame(event=event, data=data)
            run.frames.append(frame)
        else:
            frame = StreamFrame(event=event, data=data)
        try:
            self._store.append(session_id, frame)
        except Exception:
            logger.warning("写入流事件失败 session=%s", session_id, exc_info=True)

    def set_user_preview(self, session_id: str, preview: dict[str, Any]) -> None:
        payload = dict(preview)
        run = self._local.get(session_id)
        if run is not None:
            run.user_preview = payload
        try:
            meta = self._store.load_meta(session_id)
            if meta is None:
                return
            meta["user_preview"] = payload
            self._store.save_meta(session_id, meta)
        except Exception:
            logger.warning("更新流预览失败 session=%s", session_id, exc_info=True)

    def finish(self, run: SessionRun) -> None:
        run.done = True
        if not self.owns(run):
            return
        try:
            meta = self._store.load_meta(run.session_id)
            if meta is not None and meta.get("run_id") == run.run_id:
                meta["done"] = True
                meta["stopped"] = run.stopped
                self._store.save_meta(run.session_id, meta)
        except Exception:
            logger.warning("标记流结束失败 session=%s", run.session_id, exc_info=True)
        self._cancel_watch(run.session_id)
        self._schedule_expire(run.session_id, run.run_id)

    def request_stop(self, session_id: str) -> SessionRun | None:
        run = self.active(session_id)
        if run is None:
            return None
        run.stopped = True
        try:
            meta = self._store.load_meta(session_id)
            if meta is not None:
                meta["stopped"] = True
                self._store.save_meta(session_id, meta)
        except Exception:
            logger.warning("写入停止标记失败 session=%s", session_id, exc_info=True)
        local = self._local.get(session_id)
        if local is not None:
            local.stopped = True
            task = local.task
            if task is not None and not task.done():
                task.cancel()
            return local
        return run

    def discard(self, session_id: str) -> SessionRun | None:
        run = self._local.pop(session_id, None)
        self._cancel_watch(session_id)
        try:
            self._store.delete(session_id)
        except Exception:
            logger.warning("删除流缓冲失败 session=%s", session_id, exc_info=True)
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
                try:
                    chunk = self._store.get_frames(run.session_id, offset)
                    meta = self._store.load_meta(run.session_id)
                except Exception:
                    logger.warning("读取流事件失败 session=%s", run.session_id, exc_info=True)
                    if self.owns(run):
                        chunk = run.frames[offset:]
                        meta = {"done": run.done, "run_id": run.run_id}
                    else:
                        await asyncio.sleep(self._poll_sec)
                        continue
                done = True if meta is None else bool(meta.get("done"))
                if self.owns(run) and run.done:
                    done = True
                for frame in chunk:
                    yield frame
                offset += len(chunk)
                if done:
                    return
                await asyncio.sleep(self._poll_sec)
        except asyncio.CancelledError:
            return

    def _load_remote(self, session_id: str) -> SessionRun | None:
        try:
            meta = self._store.load_meta(session_id)
        except Exception:
            logger.warning("读取流元数据失败 session=%s", session_id, exc_info=True)
            return None
        if meta is None:
            return None
        try:
            frames = self._store.get_frames(session_id, 0)
        except Exception:
            frames = []
        return SessionRun(
            session_id=session_id,
            run_id=str(meta.get("run_id") or ""),
            user_preview=dict(meta.get("user_preview") or {}),
            frames=frames,
            done=bool(meta.get("done")),
            stopped=bool(meta.get("stopped")),
        )

    def _sync_flags(self, run: SessionRun) -> None:
        try:
            meta = self._store.load_meta(run.session_id)
        except Exception:
            return
        if meta is None:
            return
        if meta.get("run_id") != run.run_id:
            return
        run.done = bool(meta.get("done"))
        run.stopped = bool(meta.get("stopped") or run.stopped)
        preview = meta.get("user_preview")
        if isinstance(preview, dict):
            run.user_preview = dict(preview)

    def _start_stop_watch(self, run: SessionRun) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._cancel_watch(run.session_id)
        task = loop.create_task(
            self._watch_stop(run),
            name=f"ks-stop-watch-{run.session_id}",
        )
        self._watchers[run.session_id] = task

        def _clear(done: asyncio.Task[None], sid: str = run.session_id) -> None:
            if self._watchers.get(sid) is done:
                self._watchers.pop(sid, None)

        task.add_done_callback(_clear)

    def _cancel_watch(self, session_id: str) -> None:
        task = self._watchers.pop(session_id, None)
        if task is not None and not task.done():
            task.cancel()

    async def _watch_stop(self, run: SessionRun) -> None:
        """别的实例 POST /stop 只写 Redis；本进程轮询到 stopped 后取消本地 task。"""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + STOP_WATCH_MAX_SEC
        try:
            while True:
                await asyncio.sleep(self._stop_poll_sec)
                if loop.time() >= deadline:
                    return
                task = run.task
                if task is not None and task.done():
                    return
                try:
                    meta = self._store.load_meta(run.session_id)
                except Exception:
                    logger.warning(
                        "stop watcher 读取失败 session=%s",
                        run.session_id,
                        exc_info=True,
                    )
                    continue
                if meta is None:
                    if task is not None and not task.done():
                        task.cancel()
                    return
                if meta.get("done"):
                    return
                if not meta.get("stopped"):
                    continue
                run.stopped = True
                if task is None:
                    continue
                if not task.done():
                    task.cancel()
                return
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
        current = self._local.get(session_id)
        if current is None or current.run_id != run_id or not current.done:
            return
        self._local.pop(session_id, None)
        try:
            meta = self._store.load_meta(session_id)
            if meta is not None and meta.get("run_id") == run_id and meta.get("done"):
                self._store.delete(session_id)
        except Exception:
            logger.warning("过期清理流缓冲失败 session=%s", session_id, exc_info=True)


_manager: StreamManager | None = None


def create_stream_manager() -> StreamManager:
    from config.settings import settings

    kind = (settings.stream_manager_type or "redis").strip().lower()
    ttl = float(settings.stream_ttl_sec)
    poll = DEFAULT_POLL_SEC
    stop_poll = float(settings.stream_stop_poll_sec)
    if kind == "memory":
        return StreamManager(ttl_sec=ttl, poll_sec=poll, stop_poll_sec=stop_poll)

    try:
        import redis

        client = redis.Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
        )
        client.ping()
        store = RedisStreamStore(
            client,
            prefix=settings.stream_key_prefix,
            ttl_sec=ttl,
        )
        logger.info(
            "会话续流使用 Redis List prefix=%s ttl=%ss",
            settings.stream_key_prefix,
            int(ttl),
        )
        return StreamManager(ttl_sec=ttl, poll_sec=poll, stop_poll_sec=stop_poll, store=store)
    except Exception:
        logger.warning("Redis 续流不可用，降级本机内存（多实例 continue-stream 会 404）", exc_info=True)
        return StreamManager(ttl_sec=ttl, poll_sec=poll, stop_poll_sec=stop_poll)


def get_stream_manager() -> StreamManager:
    global _manager
    if _manager is None:
        _manager = create_stream_manager()
    return _manager


def reset_stream_manager(manager: StreamManager | None = None) -> StreamManager:
    global _manager
    _manager = manager if manager is not None else StreamManager()
    return _manager
