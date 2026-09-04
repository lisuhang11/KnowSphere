from __future__ import annotations

import evals.retry as retry_mod
from evals.cancel import EvalCancelled
from evals.retry import (
    call_with_tpm_retry,
    extend_tpm_pause,
    is_rate_limit_error,
    retry_after_seconds,
    wait_seconds,
)


def _reset_pause():
    retry_mod._pause_until = 0.0


def test_is_rate_limit_error_detects_tpm_and_429():
    assert is_rate_limit_error(RuntimeError("Error code: 429 - TPM limit reached"))
    assert is_rate_limit_error(RuntimeError("RateLimitError: too many requests"))
    assert not is_rate_limit_error(RuntimeError("connection reset"))


def test_wait_seconds_uses_retry_after_then_backoff():
    hinted = RuntimeError("please retry-after: 12")
    assert retry_after_seconds(hinted) == 12.0
    assert wait_seconds(1, hinted) == 12.0
    assert wait_seconds(1, RuntimeError("429")) == 30.0
    assert wait_seconds(2, RuntimeError("429")) == 60.0
    assert wait_seconds(3, RuntimeError("429")) == 90.0


def test_call_with_tpm_retry_succeeds_after_429(monkeypatch):
    _reset_pause()
    monkeypatch.setattr("evals.retry.random.uniform", lambda *_a, **_k: 0)
    clock = {"t": 0.0}
    sleeps: list[float] = []

    def fake_monotonic():
        return clock["t"]

    def fake_sleep(seconds: float):
        sleeps.append(seconds)
        clock["t"] += seconds

    monkeypatch.setattr("evals.retry.time.monotonic", fake_monotonic)
    monkeypatch.setattr("evals.retry.time.sleep", fake_sleep)

    n = {"i": 0}

    def flaky():
        n["i"] += 1
        if n["i"] < 3:
            raise RuntimeError("Error code: 429 - TPM limit reached")
        return "ok"

    assert call_with_tpm_retry(flaky, max_attempts=4) == "ok"
    assert n["i"] == 3
    assert sum(sleeps) >= 90.0  # 30s then 60s


def test_call_with_tpm_retry_does_not_retry_other_errors():
    def boom():
        raise ValueError("bad prompt")

    try:
        call_with_tpm_retry(boom, max_attempts=4)
    except ValueError as exc:
        assert "bad prompt" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_call_with_tpm_retry_does_not_retry_cancel():
    def cancelled():
        raise EvalCancelled()

    try:
        call_with_tpm_retry(cancelled, max_attempts=4)
    except EvalCancelled:
        pass
    else:
        raise AssertionError("expected EvalCancelled")


def test_extend_tpm_pause_is_monotonic(monkeypatch):
    _reset_pause()
    monkeypatch.setattr("evals.retry.time.monotonic", lambda: 100.0)
    extend_tpm_pause(10)
    first = retry_mod._pause_until
    extend_tpm_pause(5)
    assert retry_mod._pause_until == first
    extend_tpm_pause(20)
    assert retry_mod._pause_until == 120.0
    _reset_pause()
