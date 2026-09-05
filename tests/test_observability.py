"""Langfuse 开关与 config 挂载：未配密钥时必须 no-op。"""

from __future__ import annotations

from utils.observability import attach_langfuse, flush_langfuse, is_langfuse_enabled, observe


def test_disabled_without_keys(monkeypatch):
    from config.settings import settings

    monkeypatch.setattr(settings, "langfuse_public_key", "")
    monkeypatch.setattr(settings, "langfuse_secret_key", "")
    assert is_langfuse_enabled() is False


def test_attach_langfuse_adds_handler_when_enabled(monkeypatch):
    from config.settings import settings

    monkeypatch.setattr(settings, "langfuse_tracing_enabled", True)
    monkeypatch.setattr(settings, "langfuse_public_key", "pk-lf-test")
    monkeypatch.setattr(settings, "langfuse_secret_key", "sk-lf-test")

    class _FakeHandler:
        pass

    import utils.observability as obs

    monkeypatch.setattr(obs, "is_langfuse_enabled", lambda: True)

    import sys
    import types

    fake_mod = types.ModuleType("langfuse.langchain")
    fake_mod.CallbackHandler = _FakeHandler
    monkeypatch.setitem(sys.modules, "langfuse.langchain", fake_mod)

    out = attach_langfuse(
        {"configurable": {"thread_id": "t1"}},
        name="session_chat",
        user_id="default",
        session_id="t1",
        tags=["chat"],
    )
    assert len(out["callbacks"]) == 1
    assert isinstance(out["callbacks"][0], _FakeHandler)
    assert out["run_name"] == "session_chat"
    assert out["metadata"]["langfuse_session_id"] == "t1"
    assert out["metadata"]["langfuse_user_id"] == "default"
    assert out["metadata"]["langfuse_tags"] == ["chat"]


def test_attach_langfuse_noop_when_disabled(monkeypatch):
    from config.settings import settings

    monkeypatch.setattr(settings, "langfuse_public_key", "")
    monkeypatch.setattr(settings, "langfuse_secret_key", "")
    raw = {"configurable": {"thread_id": "t1"}, "recursion_limit": 8}
    out = attach_langfuse(raw, name="session_chat", session_id="t1", tags=["chat"])
    assert out == raw
    assert "callbacks" not in out


def test_observe_passthrough_when_disabled(monkeypatch):
    from config.settings import settings

    monkeypatch.setattr(settings, "langfuse_public_key", "")
    monkeypatch.setattr(settings, "langfuse_secret_key", "")
    calls = []

    @observe(name="unit")
    def add(a: int, b: int) -> int:
        calls.append((a, b))
        return a + b

    assert add(2, 3) == 5
    assert calls == [(2, 3)]


def test_observe_async_passthrough_when_disabled(monkeypatch):
    import asyncio

    from config.settings import settings

    monkeypatch.setattr(settings, "langfuse_public_key", "")
    monkeypatch.setattr(settings, "langfuse_secret_key", "")

    @observe(name="unit_async")
    async def add(a: int, b: int) -> int:
        return a + b

    assert asyncio.iscoroutinefunction(add)
    assert asyncio.run(add(2, 3)) == 5


def test_flush_langfuse_safe_when_disabled(monkeypatch):
    from config.settings import settings

    monkeypatch.setattr(settings, "langfuse_public_key", "")
    monkeypatch.setattr(settings, "langfuse_secret_key", "")
    flush_langfuse()
