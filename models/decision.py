"""OpenJev 式决策原语：读下一个 token 的 logprobs，不生成自然语言。

远程 OpenAI 兼容接口没有本地 logits，退化为一次 max_tokens 很小的补全，
从 top_logprobs 里取出预定义选项的分数再 Softmax。厂商不支持 logprobs 时
回退到解析生成出的字母/标签；仍然失败则返回 None，由调用方走旧路径。
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from typing import Any, Sequence

logger = logging.getLogger(__name__)

_LETTER_RE = re.compile(r"^\s*([A-Za-z])(?:[\s.:)\]}]|$)")
_LOGPROBS_ERROR_HINTS = ("logprob", "log_prob", "top_logprobs")


@dataclass(frozen=True)
class ChoiceResult:
    """Choice 原语：从预定义选项中选一个，并给出概率分布。"""

    label: str
    letter: str
    probs: dict[str, float]
    confidence: float
    token: str = ""


def softmax(logprobs: dict[str, float]) -> dict[str, float]:
    """数值稳定 Softmax；空输入返回空 dict。"""
    if not logprobs:
        return {}
    peak = max(logprobs.values())
    exps = {key: math.exp(value - peak) for key, value in logprobs.items()}
    total = sum(exps.values()) or 1.0
    return {key: value / total for key, value in exps.items()}


def extract_top_logprobs(message: Any) -> list[dict[str, Any]] | None:
    """从 ChatOpenAI / OpenAI 兼容响应里取出第一个生成 token 的 top_logprobs。"""
    payload = _logprobs_payload(message)
    if payload is None:
        return None
    content = payload.get("content") if isinstance(payload, dict) else None
    if not isinstance(content, list) or not content:
        return None
    first = content[0]
    if not isinstance(first, dict):
        return None
    top = first.get("top_logprobs")
    if isinstance(top, list) and top:
        return [item for item in top if isinstance(item, dict)]
    token = first.get("token")
    logprob = first.get("logprob")
    if isinstance(token, str) and isinstance(logprob, (int, float)):
        return [{"token": token, "logprob": float(logprob)}]
    return None


def classify_choice(
    llm: Any,
    messages: list[dict[str, str]],
    options: Sequence[tuple[str, str]],
    config: Any | None = None,
) -> ChoiceResult | None:
    """对 options=[(letter, label), ...] 做 Choice 分类。失败返回 None。"""
    if not options:
        return None
    letter_to_label = {letter.strip().upper(): label for letter, label in options if letter.strip()}
    label_to_letter = {label: letter.strip().upper() for letter, label in options}
    label_by_lower = {label.lower(): label for _, label in options}

    response = _invoke_choice(llm, messages, config=config)
    if response is None:
        return None

    scores = _scores_from_logprobs(
        extract_top_logprobs(response),
        letter_to_label,
        label_by_lower,
    )
    generated = _message_text(response)
    if not scores:
        label = _label_from_text(generated, letter_to_label, label_by_lower)
        if label is None:
            return None
        probs = {name: (1.0 if name == label else 0.0) for name in label_to_letter}
        return ChoiceResult(
            label=label,
            letter=label_to_letter.get(label, ""),
            probs=probs,
            confidence=1.0,
            token=generated,
        )

    floor = min(scores.values()) - 5.0
    for label in label_to_letter:
        scores.setdefault(label, floor)
    probs = softmax(scores)
    label = max(probs, key=probs.get)
    return ChoiceResult(
        label=label,
        letter=label_to_letter.get(label, ""),
        probs=probs,
        confidence=float(probs.get(label, 0.0)),
        token=generated,
    )


def _invoke_choice(
    llm: Any,
    messages: list[dict[str, str]],
    config: Any | None = None,
) -> Any | None:
    bind = getattr(llm, "bind", None)
    if not callable(bind):
        return None
    try:
        return bind(
            logprobs=True,
            top_logprobs=20,
            max_tokens=8,
            temperature=0,
        ).invoke(messages, config=config)
    except Exception as exc:
        if not _is_logprobs_error(exc):
            logger.warning("Choice 决策调用失败: %s", exc)
            return None
        logger.info("当前模型不支持 logprobs，回退为短补全: %s", exc)
        try:
            return bind(max_tokens=8, temperature=0).invoke(messages, config=config)
        except Exception as retry_exc:
            logger.warning("Choice 短补全回退失败: %s", retry_exc)
            return None


def _is_logprobs_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(hint in text for hint in _LOGPROBS_ERROR_HINTS)


def _logprobs_payload(message: Any) -> dict[str, Any] | None:
    for owner_name in ("response_metadata", "additional_kwargs"):
        owner = getattr(message, owner_name, None)
        if not isinstance(owner, dict):
            continue
        payload = owner.get("logprobs")
        if isinstance(payload, dict):
            return payload
    return None


def _message_text(message: Any) -> str:
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
        return "".join(parts).strip()
    return ""


def _token_to_label(
    token: str,
    letter_to_label: dict[str, str],
    label_by_lower: dict[str, str],
) -> str | None:
    raw = (token or "").strip().strip("`'\"")
    if not raw:
        return None
    match = _LETTER_RE.match(raw)
    if match:
        letter = match.group(1).upper()
        if letter in letter_to_label:
            return letter_to_label[letter]
    lowered = raw.lower().strip("`'\" ")
    if lowered in label_by_lower:
        return label_by_lower[lowered]
    for name in label_by_lower:
        if lowered.startswith(name):
            return label_by_lower[name]
    return None


def _scores_from_logprobs(
    top: list[dict[str, Any]] | None,
    letter_to_label: dict[str, str],
    label_by_lower: dict[str, str],
) -> dict[str, float]:
    if not top:
        return {}
    scores: dict[str, float] = {}
    for item in top:
        token = item.get("token")
        logprob = item.get("logprob")
        if not isinstance(token, str) or not isinstance(logprob, (int, float)):
            continue
        label = _token_to_label(token, letter_to_label, label_by_lower)
        if label is None:
            continue
        current = scores.get(label)
        value = float(logprob)
        if current is None or value > current:
            scores[label] = value
    return scores


def _label_from_text(
    text: str,
    letter_to_label: dict[str, str],
    label_by_lower: dict[str, str],
) -> str | None:
    return _token_to_label(text, letter_to_label, label_by_lower)
