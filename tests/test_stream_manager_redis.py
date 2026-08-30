"""Redis List 续流：两个 StreamManager 共享同一 Redis，模拟多 uvicorn。"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from services.stream_manager import RedisStreamStore, SessionRunBusy, StreamManager


def _pair(redis_available: bool) -> tuple[StreamManager, StreamManager, object, str]:
    if not redis_available:
        pytest.skip("redis unavailable")
    import redis

    from config.settings import settings

    prefix = f"ks:stream:test:{uuid.uuid4().hex}"
    client = redis.Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=2,
    )
    kwargs = {"ttl_sec": 30.0, "poll_sec": 0.02, "stop_poll_sec": 0.05}
    a = StreamManager(store=RedisStreamStore(client, prefix=prefix, ttl_sec=30), **kwargs)
    b = StreamManager(store=RedisStreamStore(client, prefix=prefix, ttl_sec=30), **kwargs)
    return a, b, client, prefix


def _cleanup(client: object, prefix: str) -> None:
    keys = list(client.scan_iter(match=f"{prefix}:*"))  # type: ignore[union-attr]
    if keys:
        client.delete(*keys)  # type: ignore[union-attr]


def test_redis_peer_sees_run_and_rejects_second_begin(redis_available):
    a, b, client, prefix = _pair(redis_available)
    try:
        run = a.begin("s1", {"content": "hi"})
        peer = b.get("s1")
        assert peer is not None
        assert peer.run_id == run.run_id
        assert peer is not run
        assert b.active("s1") is not None
        with pytest.raises(SessionRunBusy):
            b.begin("s1", {"content": "again"})
    finally:
        _cleanup(client, prefix)


def test_redis_continue_from_peer_replays_then_follows(redis_available):
    async def _go():
        a, b, client, prefix = _pair(redis_available)
        try:
            run = a.begin("s1", {"content": "q"})
            a.append("s1", "messages", {"type": "answer", "content": "a"})
            peer = b.get("s1")
            assert peer is not None

            async def producer():
                await asyncio.sleep(0.04)
                a.append("s1", "messages", {"type": "answer", "content": "b"})
                a.finish(run)

            asyncio.create_task(producer())
            out: list[str] = []
            async for frame in b.iter_frames(peer):
                out.append(str(frame.data["content"]))
            assert out == ["a", "b"]
            assert b.active("s1") is None
            done = b.get("s1")
            assert done is not None
            assert done.done
        finally:
            _cleanup(client, prefix)

    asyncio.run(_go())


def test_redis_stop_from_peer_cancels_local_task(redis_available):
    async def _go():
        a, b, client, prefix = _pair(redis_available)
        try:
            run = a.begin("s1", {})

            async def sleeper():
                await asyncio.sleep(10)

            run.task = asyncio.create_task(sleeper())
            stopped = b.request_stop("s1")
            assert stopped is not None
            assert stopped.stopped
            for _ in range(40):
                if run.task.done():
                    break
                await asyncio.sleep(0.05)
            assert run.stopped
            assert run.task.cancelled() or run.task.done()
        finally:
            _cleanup(client, prefix)

    asyncio.run(_go())


def test_redis_replace_finished_run_clears_events(redis_available):
    a, b, client, prefix = _pair(redis_available)
    try:
        first = a.begin("s1", {"content": "a"})
        a.append("s1", "messages", {"type": "answer", "content": "old"})
        a.finish(first)
        second = a.begin("s1", {"content": "b"})
        peer = b.get("s1")
        assert peer is not None
        assert peer.run_id == second.run_id
        assert peer.frames == []
        with pytest.raises(SessionRunBusy):
            b.begin("s1")
    finally:
        _cleanup(client, prefix)
