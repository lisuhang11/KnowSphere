"""会话生成事件缓冲：断开订阅不取消 run，可从 offset 0 重放。"""

from __future__ import annotations

import asyncio

import pytest

from services.stream_manager import SessionRunBusy, StreamManager


def test_begin_rejects_second_running_run():
    mgr = StreamManager(ttl_sec=0.01, poll_sec=0.01)
    mgr.begin("s1", {"content": "hi"})
    with pytest.raises(SessionRunBusy):
        mgr.begin("s1", {"content": "again"})


def test_begin_replaces_finished_run():
    mgr = StreamManager(ttl_sec=0.01, poll_sec=0.01)
    first = mgr.begin("s1", {"content": "a"})
    mgr.finish(first)
    second = mgr.begin("s1", {"content": "b"})
    assert second.run_id != first.run_id
    assert mgr.active("s1") is second


def test_active_none_when_done():
    mgr = StreamManager(ttl_sec=60, poll_sec=0.01)
    run = mgr.begin("s1", {"content": "hi"})
    assert mgr.active("s1") is run
    mgr.finish(run)
    assert mgr.active("s1") is None
    assert mgr.get("s1") is run


def test_answer_text_concatenates_answer_frames():
    mgr = StreamManager(ttl_sec=0.01, poll_sec=0.01)
    run = mgr.begin("s1", {})
    mgr.append("s1", "messages", {"type": "thinking", "content": "think"})
    mgr.append("s1", "messages", {"type": "answer", "content": "你"})
    mgr.append("s1", "messages", {"type": "answer", "content": "好"})
    assert run.answer_text() == "你好"


def test_iter_frames_replays_then_follows_until_done():
    async def _go():
        mgr = StreamManager(ttl_sec=0.01, poll_sec=0.02)
        run = mgr.begin("s1", {"content": "q"})

        async def producer():
            await asyncio.sleep(0.03)
            mgr.append("s1", "messages", {"type": "answer", "content": "a"})
            mgr.append("s1", "messages", {"type": "answer", "content": "b"})
            mgr.finish(run)

        asyncio.create_task(producer())
        out: list[str] = []
        async for frame in mgr.iter_frames(run):
            out.append(str(frame.data["content"]))
        assert out == ["a", "b"]

    asyncio.run(_go())


def test_cancel_subscriber_does_not_finish_run():
    async def _go():
        mgr = StreamManager(ttl_sec=0.01, poll_sec=0.02)
        run = mgr.begin("s1", {})
        mgr.append("s1", "messages", {"type": "answer", "content": "x"})

        async def subscribe():
            async for _ in mgr.iter_frames(run):
                pass

        task = asyncio.create_task(subscribe())
        await asyncio.sleep(0.03)
        task.cancel()
        await task
        assert not run.done

        mgr.append("s1", "messages", {"type": "answer", "content": "y"})
        mgr.finish(run)
        got: list[str] = []
        async for frame in mgr.iter_frames(run):
            got.append(str(frame.data["content"]))
        assert got == ["x", "y"]

    asyncio.run(_go())


def test_discard_cancels_and_drops():
    async def _go():
        mgr = StreamManager(ttl_sec=0.01, poll_sec=0.01)
        run = mgr.begin("s1", {})

        async def sleeper():
            await asyncio.sleep(10)

        run.task = asyncio.create_task(sleeper())
        dropped = mgr.discard("s1")
        assert dropped is run
        assert mgr.get("s1") is None
        await asyncio.sleep(0.02)
        assert run.task.cancelled() or run.task.done()

    asyncio.run(_go())


def test_request_stop_marks_and_cancels_task():
    async def _go():
        mgr = StreamManager(ttl_sec=0.01, poll_sec=0.01)
        run = mgr.begin("s1", {})

        async def sleeper():
            await asyncio.sleep(10)

        run.task = asyncio.create_task(sleeper())
        stopped = mgr.request_stop("s1")
        assert stopped is run
        assert run.stopped
        await asyncio.sleep(0.02)
        assert run.task.cancelled() or run.task.done()

    asyncio.run(_go())
