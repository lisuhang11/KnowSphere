"""评测调用遇到供应商 TPM/429 时等待窗口后再试。"""

from __future__ import annotations

import logging
import random
import re
import threading
import time
from collections.abc import Callable
from typing import TypeVar

from evals.cancel import EvalCancelled

logger = logging.getLogger(__name__)

T = TypeVar("T")

_RATE_LIMIT_MARKERS = (
    "429",
    "50602",
    "rate limit",
    "rate_limit",
    "tpm",
    "tokens per minute",
    "too many requests",
    "quota exceeded",
)

_RETRY_AFTER_RE = re.compile(
    r"retry[-_ ]after[:\s]*(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)

_pause_lock = threading.Lock()
_pause_until = 0.0


def is_rate_limit_error(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if status == 429:
        return True
    if "RateLimit" in type(exc).__name__:
        return True
    text = str(exc).lower()
    if any(m in text for m in _RATE_LIMIT_MARKERS):
        return True
    cause = exc.__cause__ or getattr(exc, "__context__", None)
    if isinstance(cause, BaseException) and cause is not exc:
        return is_rate_limit_error(cause)
    return False


def retry_after_seconds(exc: BaseException) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) if response is not None else None
    if headers:
        raw = headers.get("retry-after") or headers.get("Retry-After")
        if raw is not None:
            try:
                return max(0.0, float(raw))
            except (TypeError, ValueError):
                pass
    match = _RETRY_AFTER_RE.search(str(exc))
    if match:
        return max(0.0, float(match.group(1)))
    return None


def wait_seconds(attempt: int, exc: BaseException, *, cap: float = 90.0) -> float:
    """第 N 次失败后的等待：优先 Retry-After，否则 30 → 60 → 90。"""
    hinted = retry_after_seconds(exc)
    if hinted is not None:
        return min(cap, max(hinted, 5.0))
    backoff = 30.0 * (2 ** max(0, attempt - 1))
    return min(cap, backoff)


def _remaining_pause() -> float:
    with _pause_lock:
        return _pause_until - time.monotonic()


def extend_tpm_pause(seconds: float) -> None:
    global _pause_until
    until = time.monotonic() + max(0.0, seconds)
    with _pause_lock:
        if until > _pause_until:
            _pause_until = until


def wait_for_tpm_window() -> None:
    while True:
        remaining = _remaining_pause()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 0.5))


def call_with_tpm_retry(
    fn: Callable[[], T],
    *,
    max_attempts: int = 4,
) -> T:
    """TPM/429 时全进程冷却后再调；其它异常原样抛出。"""
    for attempt in range(1, max_attempts + 1):
        wait_for_tpm_window()
        try:
            return fn()
        except EvalCancelled:
            raise
        except Exception as exc:
            if not is_rate_limit_error(exc) or attempt >= max_attempts:
                raise
            wait = wait_seconds(attempt, exc) + random.uniform(0, 2)
            logger.warning(
                "供应商 TPM/429，等待 %.0fs 后重试（%s/%s）：%s",
                wait,
                attempt,
                max_attempts,
                exc,
            )
            extend_tpm_pause(wait)
    raise RuntimeError("TPM 重试次数已用尽")
