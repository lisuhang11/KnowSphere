"""官方 OpenJEV 决策接口：POST /v1/systemone，返回带概率的 Choice。"""

from __future__ import annotations

import logging
import time
from typing import Any, Sequence

import httpx

from models.decision import ChoiceResult

logger = logging.getLogger(__name__)

_RETRY_STATUS = {429, 503}


def classify_openjev(
    *,
    state: str | dict[str, Any] | list[Any],
    criteria: dict[str, str],
    instructions: str,
    api_key: str,
    base_url: str = "https://api.openjev.sh",
    model: str = "openjev",
    timeout_sec: float = 20.0,
    question_id: str = "intent",
) -> ChoiceResult | None:
    """对同一份 state 做一道 choice。失败返回 None，由调用方回退。"""
    key = (api_key or "").strip()
    labels = [name for name, _desc in criteria.items() if name.strip()]
    if not key or not labels or not (instructions or "").strip():
        return None

    payload = _post_systemone(
        {
            "model": (model or "openjev").strip() or "openjev",
            "state": state,
            "questions": {
                question_id: {
                    "type": "choice",
                    "instructions": instructions.strip(),
                    "criteria": {name: criteria[name] for name in labels},
                }
            },
        },
        api_key=key,
        base_url=base_url,
        timeout_sec=timeout_sec,
    )
    if payload is None:
        return None
    return parse_choice_answer(payload, question_id=question_id, labels=labels)


def parse_choice_answer(
    payload: dict[str, Any],
    *,
    question_id: str,
    labels: Sequence[str],
) -> ChoiceResult | None:
    """把 answers.<id> 收成 ChoiceResult。选项不在给定集合里则视为失败。"""
    answers = payload.get("answers")
    if not isinstance(answers, dict):
        return None
    answer = answers.get(question_id)
    if not isinstance(answer, dict):
        return None
    known = list(labels)
    known_set = set(known)
    choice = str(answer.get("choice") or "").strip()
    if choice not in known_set:
        return None

    raw_probs = answer.get("probabilities")
    probs: dict[str, float] = {}
    if isinstance(raw_probs, dict):
        for name in known:
            value = raw_probs.get(name)
            if isinstance(value, (int, float)):
                probs[name] = float(value)
    if choice not in probs:
        probs = {name: (1.0 if name == choice else 0.0) for name in known}
    else:
        for name in known:
            probs.setdefault(name, 0.0)

    confidence = answer.get("confidence")
    if not isinstance(confidence, (int, float)):
        confidence = probs.get(choice, 0.0)
    confidence = max(0.0, min(1.0, float(confidence)))
    return ChoiceResult(
        label=choice,
        letter="",
        probs=probs,
        confidence=confidence,
        token=choice,
    )


def _post_systemone(
    body: dict[str, Any],
    *,
    api_key: str,
    base_url: str,
    timeout_sec: float,
) -> dict[str, Any] | None:
    url = f"{(base_url or 'https://api.openjev.sh').rstrip('/')}/v1/systemone"
    timeout = httpx.Timeout(timeout_sec, connect=5.0)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        with httpx.Client(timeout=timeout) as client:
            for attempt in range(2):
                response = client.post(url, headers=headers, json=body)
                if response.status_code == 200:
                    data = response.json()
                    return data if isinstance(data, dict) else None
                if response.status_code in _RETRY_STATUS and attempt == 0:
                    time.sleep(_retry_delay(response))
                    continue
                logger.warning("OpenJEV 意图分类 HTTP %s", response.status_code)
                return None
    except Exception as exc:
        logger.warning("OpenJEV 意图分类请求失败: %s", exc)
        return None
    return None


def _retry_delay(response: httpx.Response) -> float:
    raw = (response.headers.get("Retry-After") or "").strip()
    try:
        delay = float(raw)
    except ValueError:
        delay = 0.5
    return min(max(delay, 0.0), 2.0)
